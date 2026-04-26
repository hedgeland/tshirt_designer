import asyncio
import io
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.starlette_client import OAuth
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from config import (
    ALLOWED_EMAILS,
    ASPECT_RATIOS,
    BG_REMOVAL_TOLERANCE,
    BRAINSTORM_SIZE,
    BRAINSTORM_SIZES,
    DEFAULT_ASPECT_RATIO,
    DEFAULT_BG_COLOR,
    DEFAULT_BG_COLOR_NAME,
    EDGE_DECONTAMINATE,
    EDGE_ERODE_PX,
    EDIT_SIZE,
    EDIT_SIZES,
    FINAL_SIZE,
    FINAL_SIZES,
    GOOGLE_API_KEY,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    HTTPS_ONLY,
    MAX_COLORS,
    MAX_COLUMNS,
    MAX_VARIANTS,
    NUM_VARIANTS,
    OUTPUT_DIR,
    PRINTIFY_DEFAULT_SEARCH,
    PRINTIFY_MIN_SIZE,
    PRINTIFY_SHOP_ID,
    PRINTIFY_SHOP_NAME,
    PRINTIFY_TOKEN,
    SECRET_KEY,
    SESSION_CLEANUP_INTERVAL,
    SESSION_TTL_SECONDS,
    SIZE_PX,
)
from src import contrast, presets, printify, settings
from src.background import content_bounds, remove_background_color
from src.brainstorm import generate_concepts
from src.image import REFERENCE_INSTRUCTIONS, adapt_for_shirt, finalize_image as finalize_design
from src.image import generate_image
from src.output import (
    archive_design_session,
    archive_files,
    delete_files,
    delete_session,
    delete_variant,
    load_concept_to_session,
    load_image_to_session,
    record_iteration_variant,
    rename_design_session,
    safe_design_session_name,
    save_variants,
    scan_output,
)
from src.prompts import build_prompts

app = FastAPI()
logger = logging.getLogger(__name__)

# ── Auth ──────────────────────────────────────────────────────────────────────
# Auth is active only when GOOGLE_CLIENT_ID is set. Without it the app behaves
# as before — useful for local dev where OAuth isn't configured.

oauth = OAuth()
if GOOGLE_CLIENT_ID:
    oauth.register(
        name="google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email"},
    )

_PUBLIC_PATHS = {"/login", "/login/google", "/auth/callback"}
_PUBLIC_PREFIXES = ("/static/",)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Auth disabled in local dev (no client ID configured).
        if not GOOGLE_CLIENT_ID:
            return await call_next(request)
        path = request.url.path
        if path in _PUBLIC_PATHS or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)
        if not request.session.get("user"):
            return RedirectResponse("/login")
        return await call_next(request)


class OriginCheckMiddleware(BaseHTTPMiddleware):
    """Reject state-mutating requests whose Origin doesn't match this server.

    Only active when auth is enabled (GOOGLE_CLIENT_ID set); same_site="lax" already
    mitigates most CSRF risk in local-dev mode, but origin validation closes the remaining
    gap for authenticated deployments without adding token round-trips.
    """
    async def dispatch(self, request: Request, call_next):
        # Only enforce in auth mode; GET/HEAD/OPTIONS are safe methods.
        if not GOOGLE_CLIENT_ID or request.method in ("GET", "HEAD", "OPTIONS"):
            return await call_next(request)
        path = request.url.path
        if path in _PUBLIC_PATHS or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)
        origin = request.headers.get("origin", "")
        if origin:
            # Compare just scheme+host+port; strip any trailing slash from base_url.
            expected = str(request.base_url).rstrip("/")
            if origin.rstrip("/") != expected:
                return Response("Forbidden", status_code=403)
        return await call_next(request)


# Middleware execution order (last added = outermost = runs first):
#   SessionMiddleware → OriginCheckMiddleware → AuthMiddleware → app
app.add_middleware(AuthMiddleware)
app.add_middleware(OriginCheckMiddleware)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, https_only=HTTPS_ONLY, same_site="lax")

# Warn early rather than letting the first generation attempt fail inside an SSE stream.
if not GOOGLE_API_KEY:
    logger.warning(
        "GOOGLE_API_KEY is not set — generation endpoints will return errors until "
        "it is configured in .env"
    )

# Ensure directories exist before mounting as static
Path(OUTPUT_DIR).mkdir(exist_ok=True)
Path("static").mkdir(exist_ok=True)

app.mount("/output", StaticFiles(directory=OUTPUT_DIR), name="output")
app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")


# ── Session store ─────────────────────────────────────────────────────────────
# Simple in-memory store keyed by a UUID the client generates on page load.
# Each session holds N independent column workflow states rather than one flat dict.
# Good enough for a single-user local tool; replace with Redis or a DB for multi-user.
sessions: dict[str, dict] = {}

# Column fields that are JSON-serializable (PIL images and other binary objects excluded).
_SERIALIZABLE_COLUMN_KEYS = {
    "theme",
    "concepts",
    "prompts",
    "variant_size",
    "variant_aspect_ratio",  # aspect ratio used when variants were generated; needed to restore gallery label
    "image_paths",
    "original_image_paths",  # N original variants; anything beyond this in image_paths is an iteration
    "iteration_roots",       # rootIdx for each iteration in order; parallel to image_paths[len(original_image_paths):]
    "selected_idx",
    "final_path",
    "num_variants",
    "hasUnsubmittedText",
    "concept_dir",  # str path — needed by /render to locate/save variant files after page reload
}


def init_column_state() -> dict:
    """Return a fresh per-column workflow state dict."""
    return {
        "theme": "",
        "concepts": [],
        "prompts": [],
        "images": [],  # PIL Images kept in memory for bg removal
        "image_paths": [],  # on-disk paths, used to build static URLs
        "selected_idx": None,
        "num_variants": NUM_VARIANTS,
        "hasUnsubmittedText": False,
        "concept_dir": None,  # str path to concept_N/ dir; set by /generate, used by /render
        "final_image": None,  # kept for /finalize backward compat during transition
        "final_path": None,
        "reference_image": None,
    }


def get_session(session_id: str) -> dict:
    """Return the session-level dict (columns list + max_columns). Creates if missing.

    Updates _last_accessed on every call so the cleanup loop can evict idle sessions.
    """
    if session_id not in sessions:
        user_settings = settings.load_settings()
        
        # Clamp num_variants from settings against the current hard max
        loaded_num_variants = user_settings.get("default_num_variants", NUM_VARIANTS)
        clamped_num_variants = _clamp(loaded_num_variants, 1, MAX_VARIANTS)
        
        initial_col = init_column_state()
        initial_col["num_variants"] = clamped_num_variants
        
        sessions[session_id] = {
            "columns": [initial_col],  # start with one column
            "max_columns": user_settings.get("default_max_columns", MAX_COLUMNS),
            "min_columns": user_settings.get("default_min_columns", 1),
            "num_variants": clamped_num_variants,
        }
    sessions[session_id]["_last_accessed"] = time.time()
    return sessions[session_id]


def get_column(session_id: str, column_id: int) -> dict:
    """Return the state dict for a specific column.

    Auto-extends the columns list if column_id is within the session's max_columns cap.
    Falls back to column 0 if column_id is out of range — prevents KeyError on stale clients.
    """
    sess = get_session(session_id)
    columns = sess["columns"]
    # Grow the list to accommodate column_id if the cap allows it
    while len(columns) <= column_id and len(columns) < sess["max_columns"]:
        columns.append(init_column_state())
    if column_id >= len(columns):
        return columns[0]
    return columns[column_id]


def sse(data: dict) -> str:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(data)}\n\n"


def _clamp(val: int, lo: int, hi: int) -> int:
    return max(lo, min(val, hi))


def _no_bg_path(path: str) -> str:
    """Derive the no-background output path from a source image path."""
    return path.replace(".png", "_no_bg.png")


def _zip_response(data: bytes, filename: str) -> Response:
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _has_transparency(img: Image.Image) -> bool:
    if img.mode != "RGBA":
        return False
    return img.split()[3].getextrema()[0] == 0


def _scan_combo_files(directory: Path, glob_pattern: str) -> list[dict]:
    """Scan directory for PNG files matching glob_pattern and return sorted combo dicts.

    Parses deterministic 4-part stems (prefix_prefix_ar_safe_size) and returns
    [{size, aspectRatio, url}] sorted by resolution (highest first) then aspect
    ratio (dropdown order). Files containing '_no_bg' are skipped.
    """
    ar_order = {ar: i for i, ar in enumerate(ASPECT_RATIOS)}
    results = []
    for p in directory.glob(glob_pattern):
        if "_no_bg" in p.name:
            continue
        parts = p.stem.split("_")
        if len(parts) != 4:
            continue
        ar_safe, size = parts[2], parts[3]
        results.append({
            "size": size,
            "aspectRatio": ar_safe.replace("x", ":"),
            "url": f"/{p}",
        })
    results.sort(key=lambda r: (-SIZE_PX.get(r["size"], 0), ar_order.get(r["aspectRatio"], 99)))
    return results


def _scan_existing_finals(session_dir: Path, idx: int) -> list[dict]:
    """Return all finalized images for a given variant index in session_dir.

    NOTE: deprecated — used only by the legacy /finalize endpoint during transition.
    """
    return _scan_combo_files(session_dir, f"final_v{idx}_*.png")


def _scan_variant_combos(concept_dir: Path, variant_num: int) -> list[dict]:
    """Return all rendered combos for variant_num (1-indexed) in concept_dir."""
    return _scan_combo_files(concept_dir, f"variant_{variant_num}_*.png")


# ── Session cleanup ───────────────────────────────────────────────────────────

@app.on_event("startup")
async def start_session_cleanup():
    """Launch the background task that evicts idle sessions."""
    asyncio.create_task(_session_cleanup_loop())


async def _session_cleanup_loop():
    """Sweep `sessions` every SESSION_CLEANUP_INTERVAL and evict entries that have
    been idle longer than SESSION_TTL_SECONDS."""
    while True:
        await asyncio.sleep(SESSION_CLEANUP_INTERVAL)
        cutoff = time.time() - SESSION_TTL_SECONDS
        stale = [
            sid for sid, sess in sessions.items()
            if sess.get("_last_accessed", 0) < cutoff
        ]
        for sid in stale:
            sessions.pop(sid, None)
        if stale:
            logger.info("Session cleanup: evicted %d stale session(s)", len(stale))


# ── Shared helpers ────────────────────────────────────────────────────────────

def _validate_bg_params(bg_tolerance: int, edge_erode: int, decontaminate: int) -> str | None:
    """Return an error message if background-removal parameters are out of range, else None."""
    if not 0 <= bg_tolerance <= 255:
        return "bg_tolerance must be between 0 and 255."
    if not 0 <= edge_erode <= 50:
        return "edge_erode must be between 0 and 50."
    if not 0 <= decontaminate <= 100:
        return "decontaminate must be between 0 and 100."
    return None


async def _run_remove_bg(
    img: Image.Image,
    bg_color: str,
    bg_tolerance: int,
    edge_erode: int,
    decontaminate: int,
) -> Image.Image:
    """Run background removal in a thread pool and return the result image."""
    return await asyncio.to_thread(
        remove_background_color,
        img,
        bg_color,
        tolerance=bg_tolerance,
        erode_px=edge_erode,
        decontaminate=decontaminate,
    )


# ── Auth routes ───────────────────────────────────────────────────────────────


@app.get("/login")
async def login_page(request: Request, error: str = ""):
    return templates.TemplateResponse(request, "login.html", {"error": error})


@app.get("/login/google")
async def login_google(request: Request):
    redirect_uri = request.url_for("auth_callback")
    return await oauth.google.authorize_redirect(request, str(redirect_uri))


@app.get("/auth/callback", name="auth_callback")
async def auth_callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError:
        return RedirectResponse("/login?" + urlencode({"error": "OAuth flow failed. Try again."}))

    userinfo = token.get("userinfo") or {}
    email = userinfo.get("email", "")

    # Enforce allowlist — empty ALLOWED_EMAILS means no one can log in.
    if not email or email not in ALLOWED_EMAILS:
        return RedirectResponse("/login?" + urlencode({"error": f"Access denied for {email}."}))

    request.session["user"] = email
    return RedirectResponse("/")


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login")


# ── Routes ────────────────────────────────────────────────────────────────────


@app.get("/")
async def index(request: Request):
    builtin = presets.load_builtin()
    user_settings = settings.load_settings()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "num_variants": user_settings.get("default_num_variants", NUM_VARIANTS),
            "max_variants": MAX_VARIANTS,
            "bg_color": DEFAULT_BG_COLOR,
            "bg_color_name": DEFAULT_BG_COLOR_NAME,
            "bg_tolerance": BG_REMOVAL_TOLERANCE,
            "edge_erode": EDGE_ERODE_PX,
            "decontaminate": EDGE_DECONTAMINATE,
            "max_colors": MAX_COLORS,
            "output_dir": OUTPUT_DIR,
            "preset_names": presets.all_preset_names(),
            "builtin_name": presets.BUILTIN_NAME,
            "all_presets": presets.all_presets(),
            "concepts_template": builtin["concepts_prompt"],
            "variants_template": builtin["variants_prompt"],
            "style_template": builtin["style_suffix"],
            "printify_enabled": bool(PRINTIFY_TOKEN),
            "printify_shop_id": PRINTIFY_SHOP_ID,
            "printify_shop_name": PRINTIFY_SHOP_NAME,
            "printify_min_size": PRINTIFY_MIN_SIZE,
            "size_px": SIZE_PX,
            "aspect_ratios": ASPECT_RATIOS,
            "default_aspect_ratio": DEFAULT_ASPECT_RATIO,
            "brainstorm_sizes": BRAINSTORM_SIZES,
            "brainstorm_size": BRAINSTORM_SIZE,
            "edit_sizes": EDIT_SIZES,
            "edit_size": EDIT_SIZE,
            "final_sizes": FINAL_SIZES,
            "final_size": FINAL_SIZE,
            "max_columns": user_settings.get("default_max_columns", MAX_COLUMNS),
            "max_columns_hard_cap": MAX_COLUMNS,   # server-side ceiling; never changes at runtime
            "min_columns": user_settings.get("default_min_columns", 1),
            "printify_favorites": user_settings.get("printify_favorites", []),
            "printify_color_favorites": user_settings.get("printify_color_favorites", []),
        },
    )


@app.post("/brainstorm")
async def brainstorm(
    session_id: str = Form(...),
    column_id: int = Form(0),
    theme: str = Form(...),
    concepts_template: str = Form(...),
):
    async def stream():
        if not GOOGLE_API_KEY:
            yield sse(
                {"type": "error", "message": "GOOGLE_API_KEY is not set. Add it to your .env file."}
            )
            return
        if not theme.strip():
            yield sse({"type": "error", "message": "Enter a theme first."})
            return

        yield sse({"type": "status", "message": "Generating concepts..."})

        try:
            concepts = await asyncio.to_thread(
                generate_concepts, theme.strip(), GOOGLE_API_KEY, concepts_template
            )
        except Exception as e:
            yield sse({"type": "error", "message": str(e)})
            return

        session = get_column(session_id, column_id)
        session.update(
            {
                "theme": theme.strip(),
                "concepts": concepts,
                "prompts": [],
                "images": [],
                "image_paths": [],
                "selected_idx": None,
                "final_image": None,
                "final_path": None,
            }
        )

        yield sse({"type": "concepts", "concepts": concepts})

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/generate")
async def generate(
    session_id: str = Form(...),
    column_id: int = Form(0),
    concept: str = Form(...),  # edited concept text → goes to build_prompts
    original_concept: str = Form(""),  # radio selection → used to find concept_idx
    bg_color: str = Form(...),
    num_variants: int = Form(...),
    max_colors: int = Form(...),
    variants_template: str = Form(...),
    style_template: str = Form(...),
    variant_size: str = Form(BRAINSTORM_SIZE),
    aspect_ratio: str = Form(DEFAULT_ASPECT_RATIO),
    reference_mode: str = Form("style"),
    direct_mode: bool = Form(False),
    theme_form: str = Form(""),  # sent by client; used to seed session theme when brainstorm was skipped
):
    async def stream():
        if not GOOGLE_API_KEY:
            yield sse({"type": "error", "message": "GOOGLE_API_KEY is not set."})
            return
        if not concept.strip():
            yield sse({"type": "error", "message": "No concept to generate from."})
            return
        # Validate user-controlled numeric and enum inputs before any API call.
        if not 1 <= num_variants <= 8:
            yield sse({"type": "error", "message": "num_variants must be between 1 and 8."})
            return
        if not 1 <= max_colors <= 8:
            yield sse({"type": "error", "message": "max_colors must be between 1 and 8."})
            return
        if variant_size not in SIZE_PX:
            yield sse({"type": "error", "message": f"Invalid size '{variant_size}'."})
            return
        if aspect_ratio not in ASPECT_RATIOS:
            yield sse({"type": "error", "message": f"Invalid aspect ratio '{aspect_ratio}'."})
            return
        if reference_mode not in ("style", "copy", "edit"):
            yield sse({"type": "error", "message": "Invalid reference mode."})
            return

        session = get_column(session_id, column_id)
        # Reference image is only applied in Direct mode — when brainstorming, the
        # reference would pull the generated images toward its aesthetics before the
        # user has even chosen a concept, which is rarely the desired outcome.
        ref_image = session.get("reference_image") if direct_mode else None
        yield sse({"type": "status", "message": "Building prompts..."})

        try:
            prompts = await asyncio.to_thread(
                build_prompts,
                concept.strip(),
                GOOGLE_API_KEY,
                variants_template=variants_template,
                style_template=style_template,
                bg_color=bg_color,
                num_variants=num_variants,
                max_colors=max_colors,
            )
        except Exception as e:
            yield sse({"type": "error", "message": str(e)})
            return

        yield sse({"type": "prompts", "prompts": prompts})
        ref_note = " with reference image" if ref_image is not None else ""
        yield sse(
            {
                "type": "status",
                "message": f"Generating variant 1 of {num_variants} at {variant_size} ({aspect_ratio}){ref_note}...",
            }
        )

        images: list[Image.Image] = []
        for i, prompt in enumerate(prompts):
            if i > 0:
                yield sse(
                    {
                        "type": "status",
                        "message": f"Generating variant {i + 1} of {num_variants} at {variant_size} ({aspect_ratio}){ref_note}...",
                    }
                )
            try:
                img = await asyncio.to_thread(
                    generate_image,
                    prompt,
                    GOOGLE_API_KEY,
                    size=variant_size,
                    aspect_ratio=aspect_ratio,
                    reference_image=ref_image,
                    reference_mode=reference_mode,
                )
            except Exception as e:
                yield sse({"type": "error", "message": str(e)})
                return
            images.append(img)

        # Use original_concept to find which concept slot to save into
        concepts = session.get("concepts", [])
        # In direct mode the client sends theme_form; fall back to session theme
        # (set by /brainstorm) so the output dir always has a meaningful name.
        active_theme = session.get("theme", "") or theme_form.strip() or "unknown"
        if not session.get("theme"):
            session["theme"] = active_theme
        try:
            concept_idx = concepts.index(original_concept.strip())
        except ValueError:
            concept_idx = 0

        paths, concept_dir = await asyncio.to_thread(
            save_variants, active_theme, concept_idx, images, aspect_ratio, variant_size
        )

        # Save a prompt sidecar alongside the variants; overwritten if user re-generates
        if paths:
            # Mirror the instruction prefix that generate_image prepends when a reference
            # image is present, so the sidecar shows the exact text sent to the model.
            if ref_image is not None:
                # Mirror the exact prefix generate_image prepends so the sidecar matches what the model received.
                instruction = REFERENCE_INSTRUCTIONS.get(reference_mode, REFERENCE_INSTRUCTIONS["style"])
                full_prompts = [instruction + p for p in prompts]
            else:
                full_prompts = prompts

            sidecar_path = concept_dir / "prompts.md"
            sidecar = templates.get_template("variant_prompts.md").render(
                theme=active_theme,
                concept=concept.strip(),
                variant_count=len(full_prompts),
                size=variant_size,
                aspect_ratio=aspect_ratio,
                generated=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                prompts=full_prompts,
            )
            await asyncio.to_thread(sidecar_path.write_text, sidecar, "utf-8")

        session.update(
            {
                "prompts": prompts,
                "num_variants": num_variants,
                "variant_size": variant_size,
                "variant_aspect_ratio": aspect_ratio,
                "images": images,
                "image_paths": paths,
                "original_images": list(images),  # preserved so bg removal is undoable
                "original_image_paths": list(paths),
                "no_bg_variant_cache": {},  # cleared on each new generate
                "concept_dir": str(concept_dir),  # /render uses this to locate/save combos
                "selected_idx": 0 if num_variants == 1 else None,
                "final_image": None,
                "final_path": None,
                "original_final": None,
                "original_final_path": None,
                "no_bg_final_cache": None,
            }
        )

        # Build initial combo list: each variant starts with one combo (the generated size+ar)
        combo_lists = [_scan_variant_combos(concept_dir, i + 1) for i in range(len(images))]
        urls = [f"/{p}" for p in paths]
        yield sse({"type": "variants", "urls": urls, "combo_lists": combo_lists})

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/finalize")
async def finalize(
    session_id: str = Form(...),
    column_id: int = Form(0),
    selected_idx: int = Form(...),
    bg_color: str = Form(...),
    bg_tolerance: int = Form(...),
    edge_erode: int = Form(...),
    decontaminate: int = Form(...),
    final_size: str = Form(FINAL_SIZE),
    aspect_ratio: str = Form(DEFAULT_ASPECT_RATIO),
):
    async def stream():
        # Validate inputs before touching session state.
        if final_size not in SIZE_PX:
            yield sse({"type": "error", "message": f"Invalid size '{final_size}'."})
            return
        if aspect_ratio not in ASPECT_RATIOS:
            yield sse({"type": "error", "message": f"Invalid aspect ratio '{aspect_ratio}'."})
            return
        bg_err = _validate_bg_params(bg_tolerance, edge_erode, decontaminate)
        if bg_err:
            yield sse({"type": "error", "message": bg_err})
            return

        session = get_column(session_id, column_id)
        images = session.get("images", [])
        prompts = session.get("prompts", [])
        theme = session.get("theme", "unknown")

        # PIL images live only in memory — reload from disk if the session survived a
        # server restart or page reload but the in-memory images were lost.
        if not images:
            image_paths = session.get("image_paths", [])
            if image_paths:
                images = [Image.open(p).copy() for p in image_paths if Path(p).exists()]
                session["images"] = images

        if not images:
            yield sse({"type": "error", "message": "Generate variants first."})
            return

        idx = selected_idx if 0 <= selected_idx < len(images) else 0

        # Deterministic filename encodes the combo so the file itself is the cache.
        # Colon in aspect ratio is replaced with 'x' to keep filenames filesystem-safe.
        ar_safe = aspect_ratio.replace(":", "x")
        final_name = f"final_v{idx}_{ar_safe}_{final_size}.png"

        # Co-locate finals with their variant siblings: derive the theme dir from
        # the stored variant paths rather than calling safe_design_session_name() (which stamps
        # a fresh timestamp and would scatter finals into new directories).
        image_paths = session.get("image_paths", [])
        if image_paths:
            session_dir = Path(image_paths[0]).parent.parent
        else:
            session_dir = Path(OUTPUT_DIR) / safe_design_session_name(theme)

        final_path = session_dir / final_name

        # Return the existing file immediately if this combo was already generated
        if final_path.exists():
            final_img = Image.open(final_path).copy()
            session["final_image"] = final_img
            session["final_path"] = str(final_path)
            session["original_final"] = final_img
            session["original_final_path"] = str(final_path)
            session["no_bg_final_cache"] = None
            yield sse({"type": "final", "url": f"/{final_path}", "existing_finals": _scan_existing_finals(session_dir, idx)})
            return

        variant = images[idx]
        bg_was_removed = _has_transparency(variant)

        yield sse(
            {"type": "status", "message": f"Generating {final_size} ({aspect_ratio}) design..."}
        )

        try:
            final_img = await asyncio.to_thread(
                finalize_design,
                prompts[idx],
                variant,
                GOOGLE_API_KEY,
                size=final_size,
                aspect_ratio=aspect_ratio,
            )
        except Exception as e:
            yield sse({"type": "error", "message": str(e)})
            return

        if bg_was_removed:
            yield sse(
                {"type": "status", "message": f"Removing background from {final_size} image..."}
            )
            final_img = await asyncio.to_thread(
                remove_background_color,
                final_img,
                bg_color,
                tolerance=bg_tolerance,
                erode_px=edge_erode,
                decontaminate=decontaminate,
            )

        final_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(final_img.save, str(final_path), "PNG")

        prompt_path = final_path.with_suffix(".md")
        sidecar = templates.get_template("prompt_sidecar.md").render(
            theme=theme,
            variant=idx + 1,
            resolution=final_size,
            aspect_ratio=aspect_ratio,
            generated=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            prompt=prompts[idx],
        )
        prompt_path.write_text(sidecar, encoding="utf-8")

        final_url = f"/{final_path}"
        session["final_image"] = final_img
        session["final_path"] = str(final_path)
        session["original_final"] = final_img  # preserved so bg removal is undoable
        session["original_final_path"] = str(final_path)
        session["no_bg_final_cache"] = None  # stale on each new finalize

        yield sse({"type": "final", "url": final_url, "existing_finals": _scan_existing_finals(session_dir, idx)})

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/render")
async def render_combo(
    session_id: str = Form(...),
    column_id: int = Form(0),
    variant_idx: int = Form(...),  # 0-indexed, matching session["images"]
    aspect_ratio: str = Form(DEFAULT_ASPECT_RATIO),
    size: str = Form(FINAL_SIZE),
):
    """Render a variant at a new aspect-ratio/size combo.

    Uses the smallest existing render of that variant as a reference image so the
    model preserves composition rather than starting from scratch.  The filename
    variant_N_ARxAR_SIZE.png is deterministic, so file existence == cache hit.
    """

    async def stream():
        if size not in SIZE_PX:
            yield sse({"type": "error", "message": f"Invalid size '{size}'."})
            return
        if aspect_ratio not in ASPECT_RATIOS:
            yield sse({"type": "error", "message": f"Invalid aspect ratio '{aspect_ratio}'."})
            return

        session = get_column(session_id, column_id)
        concept_dir_str = session.get("concept_dir")
        if not concept_dir_str:
            yield sse({"type": "error", "message": "Generate variants first."})
            return

        concept_dir = Path(concept_dir_str)
        ar_safe = aspect_ratio.replace(":", "x")
        # Parse the on-disk variant number from the stored path — variant_idx + 1 is wrong
        # when numbering is non-consecutive (e.g. variants 2 and 3 remain after deleting 1).
        _stored = session.get("image_paths", [])
        if variant_idx < len(_stored):
            _m = re.match(r"variant_(\d+)_", Path(_stored[variant_idx]).name)
            variant_num = int(_m.group(1)) if _m else variant_idx + 1
        else:
            variant_num = variant_idx + 1
        target_path = concept_dir / f"variant_{variant_num}_{ar_safe}_{size}.png"

        # Disk cache hit — no API call needed
        if target_path.exists():
            combos = _scan_variant_combos(concept_dir, variant_num)
            yield sse({"type": "render", "url": f"/{target_path}", "combos": combos, "variant_idx": variant_idx})
            return

        # Find smallest existing render as reference to anchor the composition
        existing_renders: list[tuple[int, Path]] = []
        for p in concept_dir.glob(f"variant_{variant_num}_*.png"):
            if "_no_bg" in p.name:
                continue
            parts = p.stem.split("_")
            if len(parts) == 4:
                sz = parts[3]
                px = SIZE_PX.get(sz, 0)
                existing_renders.append((px, p))

        reference_img: Image.Image | None = None
        if existing_renders:
            smallest_path = min(existing_renders, key=lambda x: x[0])[1]
            try:
                with Image.open(smallest_path) as _ref:
                    reference_img = _ref.copy()
            except Exception:
                pass

        prompts = session.get("prompts", [])
        prompt = prompts[variant_idx] if variant_idx < len(prompts) else ""

        yield sse({"type": "status", "message": f"Rendering variant {variant_num} at {size} ({aspect_ratio})..."})

        try:
            if reference_img is not None:
                rendered = await asyncio.to_thread(
                    finalize_design,
                    prompt,
                    reference_img,
                    GOOGLE_API_KEY,
                    size=size,
                    aspect_ratio=aspect_ratio,
                )
            else:
                # No existing render to reference — generate fresh (shouldn't normally happen)
                rendered = await asyncio.to_thread(
                    generate_image,
                    prompt,
                    GOOGLE_API_KEY,
                    size=size,
                    aspect_ratio=aspect_ratio,
                )
        except Exception as e:
            yield sse({"type": "error", "message": str(e)})
            return

        concept_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(rendered.save, str(target_path), "PNG")

        combos = _scan_variant_combos(concept_dir, variant_num)
        yield sse({"type": "render", "url": f"/{target_path}", "combos": combos, "variant_idx": variant_idx})

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/stream/edit")
async def edit_variant(
    session_id: str = Form(...),
    column_id: int = Form(0),
    source_url: str = Form(...),   # URL of the variant to edit, e.g. /output/theme/concept_0/variant_1_1x1_512.png
    edit_prompt: str = Form(...),  # user-supplied change description
    size: str = Form(BRAINSTORM_SIZE),
    aspect_ratio: str = Form(DEFAULT_ASPECT_RATIO),
    root_idx: int = Form(0),       # index of the non-iteration ancestor; persisted so reload can restore the chain
):
    """Apply iterative edits to an existing variant and append the result as a new variant.

    Sends the source image + edit prompt to Gemini with reference_mode="edit" so the model
    applies only the requested changes while preserving everything else.  The result is saved
    using the standard variant_N_ARxAR_SIZE.png convention so /render can treat it identically
    to any brainstorm variant.
    """
    async def stream():
        if not GOOGLE_API_KEY:
            yield sse({"type": "error", "message": "GOOGLE_API_KEY is not set."})
            return
        if not edit_prompt.strip():
            yield sse({"type": "error", "message": "Enter an edit description."})
            return

        # Resolve the URL to a disk path by stripping the leading slash
        source_path = Path(source_url.lstrip("/"))
        if not source_path.exists():
            yield sse({"type": "error", "message": f"Source image not found: {source_url}"})
            return

        session = get_column(session_id, column_id)

        # Determine where to save the result.  Prefer the stored concept_dir (set by /generate);
        # fall back to the source image's parent directory for browser-loaded images.
        concept_dir_str = session.get("concept_dir")
        if concept_dir_str:
            concept_dir = Path(concept_dir_str)
        else:
            concept_dir = source_path.parent
            # Persist this fallback so /render can find edits on the same run
            session["concept_dir"] = str(concept_dir)

        concept_dir.mkdir(parents=True, exist_ok=True)

        # Assign the next variant number by scanning the concept dir — len(images)+1 breaks when
        # existing variants are non-consecutive (e.g. only variants 2 and 3 remain after deleting 1).
        _existing_nums = [
            int(_m2.group(1))
            for p in concept_dir.glob("variant_*.png")
            if (_m2 := re.match(r"variant_(\d+)_", p.name))
        ]
        next_variant_num = max(_existing_nums) + 1 if _existing_nums else len(session.get("images", [])) + 1
        ar_safe = aspect_ratio.replace(":", "x")
        save_path = concept_dir / f"variant_{next_variant_num}_{ar_safe}_{size}.png"

        yield sse({"type": "status", "message": f"Generating iteration at {size} ({aspect_ratio})..."})

        try:
            source_img = await asyncio.to_thread(lambda: Image.open(str(source_path)).copy())
            edited_img = await asyncio.to_thread(
                generate_image,
                edit_prompt.strip(),
                GOOGLE_API_KEY,
                size=size,
                aspect_ratio=aspect_ratio,
                reference_image=source_img,
                reference_mode="edit",
            )
        except Exception as e:
            yield sse({"type": "error", "message": str(e)})
            return

        await asyncio.to_thread(edited_img.save, str(save_path), "PNG")

        # Determine the root variant number (1-indexed filename number, not session array index)
        # so variants.json can store a file-independent reference that survives session resets.
        root_image_paths = session.get("image_paths", [])
        root_variant_num: int | None = None
        if root_idx < len(root_image_paths):
            rm = re.match(r"variant_(\d+)_", Path(root_image_paths[root_idx]).name)
            if rm:
                root_variant_num = int(rm.group(1))
        await asyncio.to_thread(record_iteration_variant, concept_dir, next_variant_num, root_variant_num)

        # Append to session so /render can index into images[] and prompts[] by variant_idx.
        # iteration_roots[j] mirrors the j-th appended iteration's rootIdx so the client
        # can reconstruct the edit chain correctly after a page reload.
        session.setdefault("images", []).append(edited_img)
        session.setdefault("image_paths", []).append(str(save_path))
        session.setdefault("prompts", []).append(edit_prompt.strip())
        session.setdefault("iteration_roots", []).append(root_idx)

        new_idx = len(session["images"]) - 1
        combos = _scan_variant_combos(concept_dir, next_variant_num)
        yield sse({"type": "edit_variant", "url": f"/{save_path}", "index": new_idx, "combos": combos})

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/remove-bg/variant")
async def remove_variant_bg(
    session_id: str = Form(...),
    column_id: int = Form(0),
    selected_idx: int = Form(...),
    bg_color: str = Form(...),
    bg_tolerance: int = Form(...),
    edge_erode: int = Form(...),
    decontaminate: int = Form(...),
):
    async def stream():
        bg_err = _validate_bg_params(bg_tolerance, edge_erode, decontaminate)
        if bg_err:
            yield sse({"type": "error", "message": bg_err})
            return

        session = get_column(session_id, column_id)
        images = session.get("images", [])
        paths = session.get("image_paths", [])

        # Reload PIL images from disk if the server restarted or page was reloaded.
        if not images and paths:
            images = [Image.open(p).copy() for p in paths if Path(p).exists()]
            session["images"] = images

        if not images:
            yield sse({"type": "error", "message": "Generate variants first."})
            return

        idx = selected_idx if 0 <= selected_idx < len(images) else 0

        # Use cached no-bg result if available — skip the algorithm on re-apply after undo.
        cache = session.setdefault("no_bg_variant_cache", {})
        if idx in cache:
            result, no_bg_path = cache[idx]
        else:
            yield sse({"type": "status", "message": "Removing background..."})
            try:
                result = await _run_remove_bg(images[idx], bg_color, bg_tolerance, edge_erode, decontaminate)
            except Exception as e:
                yield sse({"type": "error", "message": str(e)})
                return
            no_bg_path = _no_bg_path(paths[idx]) if idx < len(paths) else ""
            if no_bg_path:
                await asyncio.to_thread(result.save, no_bg_path, "PNG")
            cache[idx] = (result, no_bg_path)

        updated = list(images)
        updated[idx] = result
        session["images"] = updated

        if no_bg_path:
            updated_paths = list(paths)
            updated_paths[idx] = no_bg_path
            session["image_paths"] = updated_paths

        url = f"/{no_bg_path}" if no_bg_path else ""
        yield sse({"type": "variant_updated", "index": idx, "url": url, "bg_removed": True})

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/remove-bg/combo")
async def remove_combo_bg(
    combo_url: str = Form(...),
    bg_color: str = Form(...),
    bg_tolerance: int = Form(...),
    edge_erode: int = Form(...),
    decontaminate: int = Form(...),
):
    """Remove the background from a rendered combo image identified by its URL path.

    Combos are file-backed; the _no_bg file on disk acts as the cache — if it already
    exists we skip reprocessing and return it immediately.
    """
    async def stream():
        bg_err = _validate_bg_params(bg_tolerance, edge_erode, decontaminate)
        if bg_err:
            yield sse({"type": "error", "message": bg_err})
            return

        # Strip leading slash and resolve to an absolute path, rejecting anything
        # outside OUTPUT_DIR to prevent path-traversal.
        clean = combo_url.lstrip("/")
        abs_path = Path(clean).resolve()
        if not abs_path.is_relative_to(Path(OUTPUT_DIR).resolve()):
            yield sse({"type": "error", "message": "Invalid combo path."})
            return

        no_bg_path_str = _no_bg_path(clean)
        no_bg_abs = Path(no_bg_path_str).resolve() if no_bg_path_str else None

        # Return the cached _no_bg file if it already exists on disk.
        if no_bg_abs and no_bg_abs.exists():
            yield sse({"type": "combo_bg_removed", "url": f"/{no_bg_path_str}"})
            return

        if not abs_path.exists():
            yield sse({"type": "error", "message": "Combo file not found."})
            return

        yield sse({"type": "status", "message": "Removing background..."})
        try:
            img = await asyncio.to_thread(lambda: Image.open(abs_path).convert("RGBA"))
            result = await _run_remove_bg(img, bg_color, bg_tolerance, edge_erode, decontaminate)
        except Exception as e:
            yield sse({"type": "error", "message": str(e)})
            return

        if no_bg_abs:
            await asyncio.to_thread(result.save, str(no_bg_abs), "PNG")

        url = f"/{no_bg_path_str}" if no_bg_path_str else ""
        yield sse({"type": "combo_bg_removed", "url": url})

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/remove-bg/final")
async def remove_final_bg(
    session_id: str = Form(...),
    column_id: int = Form(0),
    bg_color: str = Form(...),
    bg_tolerance: int = Form(...),
    edge_erode: int = Form(...),
    decontaminate: int = Form(...),
):
    async def stream():
        bg_err = _validate_bg_params(bg_tolerance, edge_erode, decontaminate)
        if bg_err:
            yield sse({"type": "error", "message": bg_err})
            return

        session = get_column(session_id, column_id)
        final_img = session.get("final_image")
        final_path = session.get("final_path")

        if final_img is None:
            yield sse({"type": "error", "message": "Finalize a design first."})
            return

        # Use cached no-bg result if available — skip the algorithm on re-apply after undo.
        cached_final = session.get("no_bg_final_cache")
        if cached_final:
            result, no_bg_path = cached_final
        else:
            yield sse({"type": "status", "message": "Removing background..."})
            try:
                result = await _run_remove_bg(final_img, bg_color, bg_tolerance, edge_erode, decontaminate)
            except Exception as e:
                yield sse({"type": "error", "message": str(e)})
                return
            no_bg_path = _no_bg_path(final_path or "")
            if no_bg_path:
                await asyncio.to_thread(result.save, no_bg_path, "PNG")
            session["no_bg_final_cache"] = (result, no_bg_path)

        session["final_image"] = result
        session["final_path"] = no_bg_path

        url = f"/{no_bg_path}" if no_bg_path else ""
        yield sse({"type": "final_updated", "url": url, "bg_removed": True})

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/restore-bg/variant")
async def restore_variant_bg(
    session_id: str = Form(...), column_id: int = Form(0), selected_idx: int = Form(...)
):
    session = get_column(session_id, column_id)
    originals = session.get("original_images", [])
    orig_paths = session.get("original_image_paths", [])
    images = session.get("images", [])
    paths = session.get("image_paths", [])

    if not originals or selected_idx >= len(originals):
        return {"error": "No original to restore."}

    updated = list(images)
    updated[selected_idx] = originals[selected_idx]
    session["images"] = updated

    updated_paths = list(paths)
    updated_paths[selected_idx] = orig_paths[selected_idx]
    session["image_paths"] = updated_paths

    url = f"/{orig_paths[selected_idx]}"
    return {"url": url, "index": selected_idx}


@app.post("/restore-bg/final")
async def restore_final_bg(session_id: str = Form(...), column_id: int = Form(0)):
    session = get_column(session_id, column_id)
    original = session.get("original_final")
    orig_path = session.get("original_final_path")

    if original is None:
        return {"error": "No original to restore."}

    session["final_image"] = original
    session["final_path"] = orig_path

    url = f"/{orig_path}" if orig_path else ""
    return {"url": url}


# ── BG state sync endpoints (plain JSON, no algorithm — used by client cache-hit paths) ──


@app.post("/apply-cached-bg/variant")
async def apply_cached_variant_bg(
    session_id: str = Form(...), column_id: int = Form(0), selected_idx: int = Form(...)
):
    """Swap session to the cached no-bg variant without re-running the algorithm."""
    session = get_column(session_id, column_id)
    cache = session.get("no_bg_variant_cache", {})
    if selected_idx not in cache:
        return {"error": "No cached result for this variant."}
    result, no_bg_path = cache[selected_idx]
    images = list(session.get("images", []))
    paths = list(session.get("image_paths", []))
    if selected_idx < len(images):
        images[selected_idx] = result
        session["images"] = images
    if selected_idx < len(paths):
        paths[selected_idx] = no_bg_path
        session["image_paths"] = paths
    return {"ok": True}


@app.post("/apply-cached-bg/final")
async def apply_cached_final_bg(session_id: str = Form(...), column_id: int = Form(0)):
    """Swap session to the cached no-bg final image without re-running the algorithm."""
    session = get_column(session_id, column_id)
    cached = session.get("no_bg_final_cache")
    if not cached:
        return {"error": "No cached result for final image."}
    result, no_bg_path = cached
    session["final_image"] = result
    session["final_path"] = no_bg_path
    return {"ok": True}


# ── Image analysis endpoints ──────────────────────────────────────────────────


@app.get("/analysis/final")
async def analyze_final(session_id: str, column_id: int = 0):
    """Return content bounding box of the final image for Printify placement.

    Fractions of image height: content_top is the first row with a visible pixel,
    content_bottom is the last. A fully opaque image returns (0.0, 1.0).
    """
    session = get_column(session_id, column_id)
    final_img = session.get("final_image")
    if final_img is None:
        return {"content_top": 0.0, "content_bottom": 1.0}
    top, bottom = await asyncio.to_thread(content_bounds, final_img)
    return {"content_top": top, "content_bottom": bottom}


# ── Preset endpoints ──────────────────────────────────────────────────────────


@app.get("/presets/{name}")
async def get_preset(name: str):
    try:
        return presets.get_preset(name)
    except KeyError:
        return JSONResponse({"error": "Not found"}, status_code=404)


@app.get("/browse")
async def browse_output():
    """Return the structured output directory tree for the file browser drawer."""
    return await asyncio.to_thread(scan_output)


@app.delete("/browse/files")
async def delete_output_files(request: Request):
    body = await request.json()
    paths = body.get("paths", [])
    return await asyncio.to_thread(delete_files, paths)


@app.delete("/browse/variant")
async def delete_output_variant(request: Request):
    """Delete a variant and all its iterations from a concept directory."""
    body = await request.json()
    try:
        result = await asyncio.to_thread(
            delete_variant,
            body["session_dir"],
            body["concept_dir"],
            int(body["variant_num"]),
        )
    except (KeyError, ValueError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return result


@app.delete("/browse/session/{dir_name}")
async def delete_output_session(dir_name: str):
    """Delete an entire session directory and all its contents."""
    try:
        result = await asyncio.to_thread(delete_session, dir_name)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return result


@app.get("/browse/archive/{dir_name}")
async def archive_output_session(dir_name: str):
    data = await asyncio.to_thread(archive_design_session, dir_name)
    return _zip_response(data, f"{dir_name}.zip")


@app.post("/browse/archive/selection")
async def archive_output_selection(request: Request):
    body = await request.json()
    paths = body.get("paths", [])
    data = await asyncio.to_thread(archive_files, paths)
    return _zip_response(data, "selection.zip")


@app.post("/session/load-image")
async def session_load_image(request: Request):
    """Load an existing output image into the session as a variant, bypassing generation."""
    body = await request.json()
    session_id = body.get("session_id", "")
    column_id = int(body.get("column_id", 0))
    image_url = body.get("image_url", "")
    display_session = body.get("display_session", "")
    try:
        result = await asyncio.to_thread(
            load_image_to_session, get_column(session_id, column_id), image_url, display_session
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return result


@app.post("/session/reload")
async def session_reload(
    session_id: str = Form(...),
    column_id: int = Form(...),
    session_dir: str = Form(...),
    concept_dir: str = Form(...),
):
    """Reload an entire concept directory into a session/column."""
    try:
        session = get_column(session_id, column_id)
        result = await asyncio.to_thread(
            load_concept_to_session, session, session_dir, concept_dir
        )
        
        # Return serializable state for the frontend to rehydrate
        return {
            "theme": session["theme"],
            "concepts": session["concepts"],
            "image_paths": session["image_paths"],
            "original_image_paths": session["original_image_paths"],
            "selected_idx": session["selected_idx"],
            "variant_size": session.get("variant_size"),
            "variant_aspect_ratio": session.get("variant_aspect_ratio"),
            "combo_lists": session.get("combo_lists", []),
            "concept_dir": session.get("concept_dir"),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/session/set-reference-image")
async def session_set_reference_image(
    session_id: str = Form(...),
    column_id: int = Form(0),
    reference_path: str = Form(""),
    reference_file: UploadFile | None = File(None),
):
    """Store a reference image in session for use during variant generation.

    Accepts either a path to an existing output file or an uploaded file — not both.
    """
    session = get_column(session_id, column_id)

    if reference_file is not None:
        data = await reference_file.read()
        img = Image.open(io.BytesIO(data)).convert("RGBA")
    elif reference_path:
        # Reuse the same safety check as load_image_to_session: path must be within OUTPUT_DIR.
        clean = reference_path.lstrip("/")
        abs_path = Path(clean).resolve()
        if not abs_path.is_relative_to(Path(OUTPUT_DIR).resolve()):
            return JSONResponse({"error": "Invalid path"}, status_code=400)
        img = Image.open(abs_path).convert("RGBA")
    else:
        return JSONResponse({"error": "No image provided"}, status_code=400)

    session["reference_image"] = img
    return JSONResponse({"ok": True})


@app.get("/session/reference-image-preview")
async def session_reference_image_preview(session_id: str, column_id: int = 0):
    """Return the stored reference image as a PNG for thumbnail display."""
    session = get_column(session_id, column_id)
    img: Image.Image | None = session.get("reference_image")
    if img is None:
        return Response(status_code=204)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@app.post("/session/clear-reference-image")
async def session_clear_reference_image(request: Request):
    """Remove the reference image from session."""
    body = await request.json()
    column_id = int(body.get("column_id", 0))
    session = get_column(body.get("session_id", ""), column_id)
    session["reference_image"] = None
    return JSONResponse({"ok": True})


@app.patch("/browse/rename")
async def rename_output_session(request: Request):
    body = await request.json()
    old_dir = body.get("dir_name", "")
    new_name = body.get("new_name", "")
    try:
        result = await asyncio.to_thread(rename_design_session, old_dir, new_name)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return result


@app.post("/presets")
async def save_preset_route(
    name: str = Form(...),
    concepts: str = Form(...),
    variants: str = Form(...),
    style: str = Form(...),
):
    name = name.strip()
    if not name or name == presets.BUILTIN_NAME:
        return {
            "error": "Name required (cannot overwrite built-in).",
            "names": presets.all_preset_names(),
        }
    try:
        presets.save_preset(name, concepts, variants, style)
    except ValueError as e:
        return {"error": str(e), "names": presets.all_preset_names()}
    return {"names": presets.all_preset_names(), "saved": name}


@app.delete("/presets/{name}")
async def delete_preset_route(name: str):
    presets.delete_preset(name)
    return {"names": presets.all_preset_names()}


# ── Printify endpoints ────────────────────────────────────────────────────────
# These routes are only useful when PRINTIFY_TOKEN is set. Callers should check
# the `printify_enabled` flag from /config before hitting these.

def _require_printify_token():
    """FastAPI dependency that aborts with 503 when PRINTIFY_TOKEN is not configured."""
    if not PRINTIFY_TOKEN:
        raise HTTPException(status_code=503, detail="PRINTIFY_TOKEN not configured.")


@app.get("/printify/shops", dependencies=[Depends(_require_printify_token)])
async def printify_shops():
    try:
        shops = await asyncio.to_thread(printify.list_shops, PRINTIFY_TOKEN)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    return shops


@app.get("/printify/blueprints", dependencies=[Depends(_require_printify_token)])
async def printify_blueprints(q: str = ""):
    """Return blueprints, optionally filtered by a search query."""
    try:
        all_bps = await asyncio.to_thread(printify.list_blueprints, PRINTIFY_TOKEN)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)

    # Default filter: show shirt-like items unless user provides a custom query.
    search = q.strip().lower() if q.strip() else PRINTIFY_DEFAULT_SEARCH
    terms = search.split()
    filtered = [
        {
            "id": bp["id"],
            "title": bp["title"],
            "brand": bp.get("brand", ""),
            "model": bp.get("model", ""),
        }
        for bp in all_bps
        if any(t in bp.get("title", "").lower() for t in terms)
    ]
    return filtered


@app.get("/printify/blueprints/{blueprint_id}/providers", dependencies=[Depends(_require_printify_token)])
async def printify_providers(blueprint_id: int):
    try:
        providers = await asyncio.to_thread(
            printify.list_print_providers, PRINTIFY_TOKEN, blueprint_id
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    return providers


@app.get("/printify/blueprints/{blueprint_id}/providers/{provider_id}/variants", dependencies=[Depends(_require_printify_token)])
async def printify_variants(blueprint_id: int, provider_id: int):
    try:
        variants = await asyncio.to_thread(
            printify.list_variants, PRINTIFY_TOKEN, blueprint_id, provider_id
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    return variants



@app.get("/printify/blueprints/{blueprint_id}/providers/{provider_id}/print_details", dependencies=[Depends(_require_printify_token)])
async def printify_print_details(blueprint_id: int, provider_id: int):
    """Return authoritative print area profiles for a blueprint+provider pair."""
    try:
        details = await asyncio.to_thread(
            printify.get_print_details, PRINTIFY_TOKEN, blueprint_id, provider_id
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    return details


@app.post("/contrast/assess")
async def contrast_assess(
    session_id: str = Form(...),
    column_id: int = Form(0),
    shirt_colors: str = Form(...),  # JSON array of color name strings
    final_url: str = Form(""),
):
    """Ask Gemini whether the active design will be visible on each selected shirt color."""
    if not GOOGLE_API_KEY:
        return JSONResponse({"error": "GOOGLE_API_KEY is not set."}, status_code=500)

    try:
        colors: list[str] = json.loads(shirt_colors)
    except json.JSONDecodeError:
        return JSONResponse({"error": "Invalid shirt_colors format."}, status_code=400)

    if not colors:
        return JSONResponse({})

    session = get_column(session_id, column_id)
    final_path = session.get("final_path")
    if not final_path and final_url:
        recovered = final_url.lstrip("/")
        if Path(recovered).is_file():
            final_path = recovered

    if not final_path:
        return JSONResponse({"error": "No design available for assessment."}, status_code=400)

    try:
        img = await asyncio.to_thread(Image.open, final_path)
        result = await asyncio.to_thread(contrast.assess_contrast, img, colors, GOOGLE_API_KEY)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/contrast/adapt")
async def contrast_adapt(
    session_id: str = Form(...),
    column_id: int = Form(0),
    shirt_color: str = Form(...),
    final_url: str = Form(""),
    size: str = Form(BRAINSTORM_SIZE),
    aspect_ratio: str = Form(DEFAULT_ASPECT_RATIO),
):
    """Regenerate the design adapted for visibility on the given shirt color. Streams SSE progress."""

    async def stream():
        if not GOOGLE_API_KEY:
            yield sse({"type": "error", "message": "GOOGLE_API_KEY is not set."})
            return

        session = get_column(session_id, column_id)
        final_path = session.get("final_path")
        if not final_path and final_url:
            recovered = final_url.lstrip("/")
            if Path(recovered).is_file():
                final_path = recovered

        if not final_path:
            yield sse({"type": "error", "message": "No design available for adaptation."})
            return

        yield sse({"type": "status", "message": f"Adapting design for {shirt_color}…"})

        try:
            ref_img = await asyncio.to_thread(lambda: Image.open(str(final_path)).copy())
            adapted = await asyncio.to_thread(
                adapt_for_shirt, ref_img, shirt_color, GOOGLE_API_KEY, size, aspect_ratio
            )
        except Exception as e:
            yield sse({"type": "error", "message": f"Adaptation failed: {e}"})
            return

        # Save alongside the source image; color name normalized for a safe filename.
        color_safe = shirt_color.lower().replace(" ", "_").replace("/", "_")
        save_dir = Path(final_path).parent
        save_path = save_dir / f"adapted_{color_safe}.png"
        await asyncio.to_thread(adapted.save, str(save_path))

        yield sse({"type": "done", "url": "/" + str(save_path), "shirt_color": shirt_color})

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/printify/publish")
async def printify_publish(
    session_id: str = Form(...),
    column_id: int = Form(0),
    shop_id: str = Form(...),
    blueprint_id: int = Form(...),
    provider_id: int = Form(...),
    print_areas_json: str = Form(...),  # JSON: [{image_path: "original"|url, variant_ids: [int]}]
    title: str = Form(...),
    description: str = Form(""),
    price_cents: int = Form(...),
    publish_now: bool = Form(False),
    design_x: float = Form(0.5),
    design_y: float = Form(0.5),
    design_scale: float = Form(0.8),
    design_angle: float = Form(0),
    final_url: str = Form(""),
):
    """Upload the session's final image to Printify and create (optionally publish) a product."""

    async def stream():
        if not PRINTIFY_TOKEN:
            yield sse({"type": "error", "message": "PRINTIFY_TOKEN not configured."})
            return
        # Validate numeric and range inputs before any API calls.
        if price_cents < 1:
            yield sse({"type": "error", "message": "price_cents must be at least 1."})
            return
        if not 0.0 <= design_x <= 1.0 or not 0.0 <= design_y <= 1.0:
            yield sse({"type": "error", "message": "design_x and design_y must be between 0.0 and 1.0."})
            return
        if not 0.1 <= design_scale <= 2.0:
            yield sse({"type": "error", "message": "design_scale must be between 0.1 and 2.0."})
            return
        if not -360 <= design_angle <= 360:
            yield sse({"type": "error", "message": "design_angle must be between -360 and 360."})
            return

        session = get_column(session_id, column_id)
        final_path = session.get("final_path")

        # Session may have been wiped by a server restart. Recover the path from the
        # URL the client already has — the static file is still on disk.
        if not final_path and final_url:
            recovered = final_url.lstrip("/")
            if Path(recovered).is_file():
                final_path = recovered

        if not final_path:
            yield sse({"type": "error", "message": "Finalize a design first."})
            return

        # Enforce minimum resolution — open the file and check its actual dimensions
        # rather than trusting the session, so this holds even after a server restart.
        min_px = SIZE_PX.get(PRINTIFY_MIN_SIZE, 0)
        if min_px:
            img_check = await asyncio.to_thread(Image.open, final_path)
            w, h = img_check.size
            if max(w, h) < min_px:
                yield sse(
                    {
                        "type": "error",
                        "message": f"Image must be at least {PRINTIFY_MIN_SIZE} ({min_px}px) to publish. Re-finalize at a higher resolution.",
                    }
                )
                return

        try:
            raw_areas: list[dict] = json.loads(print_areas_json)
        except json.JSONDecodeError:
            yield sse({"type": "error", "message": "Invalid print_areas_json."})
            return

        if not raw_areas or not any(g.get("variant_ids") for g in raw_areas):
            yield sse({"type": "error", "message": "Select at least one color/size variant."})
            return

        # Resolve "original" image_path to the session's final image.
        # Deduplicate uploads: two color groups using the same file share one Printify image ID.
        def _resolve_path(image_path: str) -> str:
            if image_path == "original":
                return final_path
            resolved = image_path.lstrip("/")
            if not Path(resolved).is_file():
                raise FileNotFoundError(f"Adapted image not found: {image_path}")
            return resolved

        # Step 1: Upload each unique image
        yield sse({"type": "status", "message": "Uploading image(s) to Printify..."})
        path_to_image_id: dict[str, str] = {}
        try:
            for group in raw_areas:
                disk_path = _resolve_path(group["image_path"])
                if disk_path not in path_to_image_id:
                    path_to_image_id[disk_path] = await asyncio.to_thread(
                        printify.upload_image, PRINTIFY_TOKEN, disk_path
                    )
        except FileNotFoundError as e:
            yield sse({"type": "error", "message": str(e)})
            return
        except Exception as e:
            yield sse({"type": "error", "message": f"Image upload failed: {e}"})
            return

        # Build the print_areas list expected by create_product.
        # _resolve_path is pure path logic — safe to call synchronously here.
        resolved_areas = [
            {
                "image_id": path_to_image_id[_resolve_path(g["image_path"])],
                "variant_ids": g["variant_ids"],
            }
            for g in raw_areas
            if g.get("variant_ids")
        ]

        # Step 2: Create product draft
        yield sse({"type": "status", "message": "Creating product..."})
        try:
            product_id = await asyncio.to_thread(
                printify.create_product,
                PRINTIFY_TOKEN,
                shop_id,
                title.strip(),
                description.strip(),
                blueprint_id,
                provider_id,
                resolved_areas,
                price_cents,
                design_x,
                design_y,
                design_scale,
                design_angle,
            )
        except Exception as e:
            yield sse({"type": "error", "message": f"Product creation failed: {e}"})
            return

        # Step 3 (optional): Publish to store
        if publish_now:
            yield sse({"type": "status", "message": "Publishing to store..."})
            try:
                await asyncio.to_thread(
                    printify.publish_product, PRINTIFY_TOKEN, shop_id, product_id
                )
            except Exception as e:
                yield sse({"type": "error", "message": f"Publish failed: {e}"})
                return

        product_url = f"https://printify.com/app/editor/{product_id}"
        yield sse(
            {
                "type": "done",
                "product_id": product_id,
                "product_url": product_url,
                "published": publish_now,
            }
        )

    return StreamingResponse(stream(), media_type="text/event-stream")


# ── Column management endpoints ───────────────────────────────────────────────


@app.post("/columns")
async def add_column(
    session_id: str = Form(...),
    num_variants: int | None = Form(None)
):
    """Append a new column to the session up to the session's max_columns limit."""
    sess = get_session(session_id)
    columns = sess["columns"]
    if len(columns) >= sess["max_columns"]:
        return JSONResponse(
            {"error": f"Maximum of {sess['max_columns']} columns reached."},
            status_code=400,
        )
    new_col = init_column_state()
    if num_variants is not None:
        new_col["num_variants"] = _clamp(num_variants, 1, MAX_VARIANTS)
    else:
        new_col["num_variants"] = sess.get("num_variants", NUM_VARIANTS)
    columns.append(new_col)
    return {
        "column_id": len(columns) - 1,
        "count": len(columns),
        "column": _serialize_column(new_col)
    }


def _serialize_column(col: dict) -> dict:
    """Build a JSON-serializable column state dict for session restore.

    Includes the base serializable fields plus a combo_lists scan so the client
    can restore variantCombos without re-generating after a hard reload.
    """
    state = {k: col.get(k) for k in _SERIALIZABLE_COLUMN_KEYS}
    concept_dir_str = col.get("concept_dir")
    image_paths = col.get("image_paths") or []
    # Scan for rendered combo files on disk — concept_dir must exist and have images
    if concept_dir_str and image_paths:
        concept_dir = Path(concept_dir_str)
        if concept_dir.exists():
            # One entry per image_path (originals + iterations), 1-indexed to match filename convention
            state["combo_lists"] = [
                _scan_variant_combos(concept_dir, i + 1)
                for i in range(len(image_paths))
            ]
    return state


@app.get("/session/columns")
async def session_columns(session_id: str):
    """Return serializable column states for page-load restore.

    PIL Image objects are not JSON-serializable and are excluded — only text and
    path fields are returned. Images will need to be re-generated after a page reload.
    Rendered combo files are scanned from disk and included as combo_lists so the
    iterations step restores all previously rendered resolutions/aspect ratios.
    """
    sess = get_session(session_id)
    cols = [_serialize_column(col) for col in sess["columns"]]
    user_settings = settings.load_settings()
    return {
        "columns": cols,
        "max_columns": sess["max_columns"],
        "min_columns": sess.get("min_columns", 1),
        "num_variants": sess.get("num_variants", NUM_VARIANTS),
        "default_max_columns": user_settings.get("default_max_columns", MAX_COLUMNS),
        "default_num_variants": user_settings.get("default_num_variants", NUM_VARIANTS),
    }


@app.post("/session/remove-column")
async def remove_column(session_id: str = Form(...), column_id: int = Form(...)):
    """Remove a column from the session and compact the array.

    Returns the updated column list so the client can rebuild its columns array
    with stable indices — avoids a second round-trip to GET /session/columns.
    """
    sess = get_session(session_id)
    columns = sess["columns"]
    if len(columns) <= 1:
        return JSONResponse({"error": "Cannot remove the last column."}, status_code=400)
    if column_id < 0 or column_id >= len(columns):
        return JSONResponse({"error": "Column not found."}, status_code=404)
    columns.pop(column_id)
    # Return the compacted list so the client can reassign indices in one step
    cols = [_serialize_column(col) for col in columns]
    user_settings = settings.load_settings()
    return {
        "columns": cols,
        "max_columns": sess["max_columns"],
        "min_columns": sess.get("min_columns", 1),
        "num_variants": sess.get("num_variants", NUM_VARIANTS),
        "default_max_columns": user_settings.get("default_max_columns", MAX_COLUMNS),
        "default_num_variants": user_settings.get("default_num_variants", NUM_VARIANTS),
    }


@app.post("/session/max-columns")
async def set_max_columns(session_id: str = Form(...), max_columns: int = Form(...)):
    """Update the user's self-imposed column limit, clamped to the server-side MAX_COLUMNS cap."""
    sess = get_session(session_id)
    clamped = _clamp(max_columns, 1, MAX_COLUMNS)
    sess["max_columns"] = clamped
    # Persist as global default
    settings.save_settings({"default_max_columns": clamped})
    return {"max_columns": clamped}


@app.post("/session/min-columns")
async def set_min_columns(session_id: str = Form(...), min_columns: int = Form(...)):
    """Update the user's minimum column floor, clamped to [1, current max_columns]."""
    sess = get_session(session_id)
    clamped = _clamp(min_columns, 1, sess["max_columns"])
    sess["min_columns"] = clamped
    # Persist as global default
    settings.save_settings({"default_min_columns": clamped})
    return {"min_columns": clamped}


@app.post("/session/num-variants")
async def set_num_variants(session_id: str = Form(...), num_variants: int = Form(...)):
    """Update the user's default number of variants for new columns, clamped to [1, MAX_VARIANTS]."""
    sess = get_session(session_id)
    clamped = _clamp(num_variants, 1, MAX_VARIANTS)
    sess["num_variants"] = clamped
    # Persist as global default
    settings.save_settings({"default_num_variants": clamped})
    return {"num_variants": clamped}


def _toggle_favorites(settings_key: str, item_value, action: str) -> list:
    """Add or remove item_value from the list stored in user settings under settings_key."""
    user_settings = settings.load_settings()
    favorites = user_settings.get(settings_key, [])
    if action == "add":
        if item_value not in favorites:
            favorites.append(item_value)
    else:
        if item_value in favorites:
            favorites.remove(item_value)
    settings.save_settings({settings_key: favorites})
    return favorites


@app.post("/settings/printify-favorites")
async def toggle_printify_favorite(request: Request):
    """Add or remove a Printify blueprint ID from the global favorites list."""
    data = await request.json()
    blueprint_id = data.get("blueprint_id")
    action = data.get("action")  # "add" or "remove"
    if blueprint_id is None or action not in ("add", "remove"):
        return JSONResponse({"error": "Invalid request"}, status_code=400)
    favorites = _toggle_favorites("printify_favorites", blueprint_id, action)
    return {"printify_favorites": favorites}


@app.post("/settings/printify-color-favorites")
async def toggle_printify_color_favorite(request: Request):
    """Add or remove a color name from global favorites."""
    data = await request.json()
    color_name = data.get("color_name")
    action = data.get("action")  # "add" or "remove"
    if not color_name or action not in ("add", "remove"):
        return JSONResponse({"error": "Invalid request"}, status_code=400)
    favorites = _toggle_favorites("printify_color_favorites", color_name, action)
    return {"printify_color_favorites": favorites}


@app.post("/settings/printify-favorites/reorder")
async def reorder_printify_favorites(request: Request):
    """Overwrite the global favorites list with a new ordered list of blueprint IDs."""
    data = await request.json()
    new_favorites = data.get("favorites")

    if not isinstance(new_favorites, list):
        return JSONResponse({"error": "Invalid favorites list"}, status_code=400)

    # Filter to ensure we only have integers
    valid_ids = []
    for fid in new_favorites:
        try:
            valid_ids.append(int(fid))
        except (ValueError, TypeError):
            continue

    settings.save_settings({"printify_favorites": valid_ids})
    return {"printify_favorites": valid_ids}


@app.post("/session/select-variant")
async def select_variant(
    session_id: str = Form(...),
    column_id: int = Form(0),
    selected_idx: int = Form(...),
):
    """Persist the user's variant selection so it survives a hard refresh.

    Called fire-and-forget from the client's selectedVariant watcher; no response body needed.
    """
    session = get_column(session_id, column_id)
    session["selected_idx"] = selected_idx
    return {}


@app.post("/session/sync-state")
async def sync_column_state(
    session_id: str = Form(...),
    column_id: int = Form(0),
    field: str = Form(...),
    value: str = Form(...),  # Value is sent as string; parsed as JSON if possible
):
    """Generic endpoint to persist serializable column fields.
    
    Used for fire-and-forget updates of flags like hasUnsubmittedText or settings.
    """
    if field not in _SERIALIZABLE_COLUMN_KEYS:
        return JSONResponse({"error": f"Field '{field}' is not serializable."}, status_code=400)
    
    session = get_column(session_id, column_id)
    
    try:
        parsed_value = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        parsed_value = value

    session[field] = parsed_value
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
