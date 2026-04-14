import asyncio
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.starlette_client import OAuth
from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
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
    FINAL_SIZE,
    FINAL_SIZES,
    GOOGLE_API_KEY,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    HTTPS_ONLY,
    MAX_COLORS,
    NUM_VARIANTS,
    OUTPUT_DIR,
    PRINTIFY_MIN_SIZE,
    PRINTIFY_SHOP_ID,
    PRINTIFY_SHOP_NAME,
    PRINTIFY_TOKEN,
    SIZE_PX,
    SECRET_KEY,
)
from src import presets, printify
from src.background import content_bounds, remove_background_color
from src.brainstorm import generate_concepts
from src.finalize import finalize_design
from src.image import generate_image
from src.output import safe_theme_name, save_variants, timestamp
from src.prompts import build_prompts

app = FastAPI()

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


# SessionMiddleware must be added after AuthMiddleware so it runs first (outer wraps inner).
app.add_middleware(AuthMiddleware)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, https_only=HTTPS_ONLY, same_site="lax")

# Ensure directories exist before mounting as static
Path(OUTPUT_DIR).mkdir(exist_ok=True)
Path("static").mkdir(exist_ok=True)

app.mount("/output", StaticFiles(directory=OUTPUT_DIR), name="output")
app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")


# ── Session store ─────────────────────────────────────────────────────────────
# Simple in-memory store keyed by a UUID the client generates on page load.
# Good enough for a single-user local tool; replace with Redis or a DB for multi-user.
sessions: dict[str, dict] = {}


def get_session(session_id: str) -> dict:
    if session_id not in sessions:
        sessions[session_id] = {
            "theme": "",
            "concepts": [],
            "prompts": [],
            "images": [],        # PIL Images kept in memory for bg removal + finalize
            "image_paths": [],   # on-disk paths, used to build static URLs
            "selected_idx": None,
            "final_image": None,
            "final_path": None,
        }
    return sessions[session_id]


def sse(data: dict) -> str:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(data)}\n\n"


def _no_bg_path(path: str) -> str:
    """Derive the no-background output path from a source image path."""
    return path.replace(".png", "_no_bg.png") if path else ""


def _has_transparency(img: Image.Image) -> bool:
    if img.mode != "RGBA":
        return False
    return img.split()[3].getextrema()[0] == 0



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
    return templates.TemplateResponse(request, "index.html", {
        "num_variants": NUM_VARIANTS,
        "bg_color": DEFAULT_BG_COLOR,
        "bg_color_name": DEFAULT_BG_COLOR_NAME,
        "bg_tolerance": BG_REMOVAL_TOLERANCE,
        "edge_erode": EDGE_ERODE_PX,
        "decontaminate": EDGE_DECONTAMINATE,
        "max_colors": MAX_COLORS,
        "output_dir": OUTPUT_DIR,
        "preset_names": presets.all_preset_names(),
        "builtin_name": presets.BUILTIN_NAME,
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
        "final_sizes": FINAL_SIZES,
        "final_size": FINAL_SIZE,
    })


@app.post("/brainstorm")
async def brainstorm(
    session_id: str = Form(...),
    theme: str = Form(...),
    concepts_template: str = Form(...),
):
    async def stream():
        if not GOOGLE_API_KEY:
            yield sse({"type": "error", "message": "GOOGLE_API_KEY is not set. Add it to your .env file."})
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

        session = get_session(session_id)
        session.update({
            "theme": theme.strip(),
            "concepts": concepts,
            "prompts": [],
            "images": [],
            "image_paths": [],
            "selected_idx": None,
            "final_image": None,
            "final_path": None,
        })

        yield sse({"type": "concepts", "concepts": concepts})

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/generate")
async def generate(
    session_id: str = Form(...),
    concept: str = Form(...),           # edited concept text → goes to build_prompts
    original_concept: str = Form(""),   # radio selection → used to find concept_idx
    bg_color: str = Form(...),
    num_variants: int = Form(...),
    max_colors: int = Form(...),
    variants_template: str = Form(...),
    style_template: str = Form(...),
    variant_size: str = Form(BRAINSTORM_SIZE),
    aspect_ratio: str = Form(DEFAULT_ASPECT_RATIO),
):
    async def stream():
        if not GOOGLE_API_KEY:
            yield sse({"type": "error", "message": "GOOGLE_API_KEY is not set."})
            return
        if not concept.strip():
            yield sse({"type": "error", "message": "No concept to generate from."})
            return

        session = get_session(session_id)
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
        yield sse({"type": "status", "message": f"Generating variant 1 of {num_variants} at {variant_size} ({aspect_ratio})..."})

        images: list[Image.Image] = []
        for i, prompt in enumerate(prompts):
            if i > 0:
                yield sse({"type": "status", "message": f"Generating variant {i + 1} of {num_variants} at {variant_size} ({aspect_ratio})..."})
            try:
                img = await asyncio.to_thread(generate_image, prompt, GOOGLE_API_KEY, size=variant_size, aspect_ratio=aspect_ratio)
            except Exception as e:
                yield sse({"type": "error", "message": str(e)})
                return
            images.append(img)

        # Use original_concept to find which concept slot to save into
        concepts = session.get("concepts", [])
        theme = session.get("theme", "unknown")
        try:
            concept_idx = concepts.index(original_concept.strip())
        except ValueError:
            concept_idx = 0

        paths = await asyncio.to_thread(
            save_variants, theme, concept_idx, images
        )

        session.update({
            "prompts": prompts,
            "images": images,
            "image_paths": paths,
            "original_images": list(images),       # preserved so bg removal is undoable
            "original_image_paths": list(paths),
            "no_bg_variant_cache": {},             # cleared on each new generate
            "selected_idx": 0 if num_variants == 1 else None,
            "final_image": None,
            "final_path": None,
            "original_final": None,
            "original_final_path": None,
            "no_bg_final_cache": None,
        })

        # paths are like "output/theme/concept_1/variant_1.png" — prepend / for URL
        urls = [f"/{p}" for p in paths]
        yield sse({"type": "variants", "urls": urls})

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/finalize")
async def finalize(
    session_id: str = Form(...),
    selected_idx: int = Form(...),
    bg_color: str = Form(...),
    bg_tolerance: int = Form(...),
    edge_erode: int = Form(...),
    decontaminate: int = Form(...),
    final_size: str = Form(FINAL_SIZE),
    aspect_ratio: str = Form(DEFAULT_ASPECT_RATIO),
):
    async def stream():
        session = get_session(session_id)
        images = session.get("images", [])
        prompts = session.get("prompts", [])
        theme = session.get("theme", "unknown")

        if not images:
            yield sse({"type": "error", "message": "Generate variants first."})
            return

        idx = selected_idx if 0 <= selected_idx < len(images) else 0
        variant = images[idx]
        bg_was_removed = _has_transparency(variant)

        yield sse({"type": "status", "message": f"Generating {final_size} design..."})

        try:
            final_img = await asyncio.to_thread(
                finalize_design, prompts[idx], variant, GOOGLE_API_KEY,
                size=final_size, aspect_ratio=aspect_ratio,
            )
        except Exception as e:
            yield sse({"type": "error", "message": str(e)})
            return

        if bg_was_removed:
            yield sse({"type": "status", "message": f"Removing background from {final_size} image..."})
            final_img = await asyncio.to_thread(
                remove_background_color, final_img, bg_color,
                tolerance=bg_tolerance, erode_px=edge_erode, decontaminate=decontaminate,
            )

        ts = timestamp()
        final_path = Path(OUTPUT_DIR) / safe_theme_name(theme) / f"final_{ts}.png"
        final_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(final_img.save, str(final_path), "PNG")

        prompt_path = final_path.with_suffix(".md")
        prompt_path.write_text(
            f"# Prompt — {theme}\n\n"
            f"| Field | Value |\n"
            f"|---|---|\n"
            f"| Theme | {theme} |\n"
            f"| Variant | {idx + 1} |\n"
            f"| Resolution | {final_size} |\n"
            f"| Aspect Ratio | {aspect_ratio} |\n"
            f"| Generated | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |\n\n"
            f"## Prompt\n\n"
            f"```\n{prompts[idx]}\n```\n",
            encoding="utf-8",
        )

        session["final_image"] = final_img
        session["final_path"] = str(final_path)
        session["original_final"] = final_img          # preserved so bg removal is undoable
        session["original_final_path"] = str(final_path)
        session["no_bg_final_cache"] = None            # stale on each new finalize

        yield sse({"type": "final", "url": f"/{final_path}"})

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/remove-bg/variant")
async def remove_variant_bg(
    session_id: str = Form(...),
    selected_idx: int = Form(...),
    bg_color: str = Form(...),
    bg_tolerance: int = Form(...),
    edge_erode: int = Form(...),
    decontaminate: int = Form(...),
):
    async def stream():
        session = get_session(session_id)
        images = session.get("images", [])
        paths = session.get("image_paths", [])

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
                result = await asyncio.to_thread(
                    remove_background_color, images[idx], bg_color,
                    tolerance=bg_tolerance, erode_px=edge_erode, decontaminate=decontaminate,
                )
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


@app.post("/remove-bg/final")
async def remove_final_bg(
    session_id: str = Form(...),
    bg_color: str = Form(...),
    bg_tolerance: int = Form(...),
    edge_erode: int = Form(...),
    decontaminate: int = Form(...),
):
    async def stream():
        session = get_session(session_id)
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
                result = await asyncio.to_thread(
                    remove_background_color, final_img, bg_color,
                    tolerance=bg_tolerance, erode_px=edge_erode, decontaminate=decontaminate,
                )
            except Exception as e:
                yield sse({"type": "error", "message": str(e)})
                return
            no_bg_path = _no_bg_path(final_path)
            if no_bg_path:
                await asyncio.to_thread(result.save, no_bg_path, "PNG")
            session["no_bg_final_cache"] = (result, no_bg_path)

        session["final_image"] = result
        session["final_path"] = no_bg_path

        url = f"/{no_bg_path}" if no_bg_path else ""
        yield sse({"type": "final_updated", "url": url, "bg_removed": True})

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/restore-bg/variant")
async def restore_variant_bg(session_id: str = Form(...), selected_idx: int = Form(...)):
    session = get_session(session_id)
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
async def restore_final_bg(session_id: str = Form(...)):
    session = get_session(session_id)
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
async def apply_cached_variant_bg(session_id: str = Form(...), selected_idx: int = Form(...)):
    """Swap session to the cached no-bg variant without re-running the algorithm."""
    session = get_session(session_id)
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
async def apply_cached_final_bg(session_id: str = Form(...)):
    """Swap session to the cached no-bg final image without re-running the algorithm."""
    session = get_session(session_id)
    cached = session.get("no_bg_final_cache")
    if not cached:
        return {"error": "No cached result for final image."}
    result, no_bg_path = cached
    session["final_image"] = result
    session["final_path"] = no_bg_path
    return {"ok": True}


# ── Image analysis endpoints ──────────────────────────────────────────────────

@app.get("/analysis/final")
async def analyze_final(session_id: str):
    """Return content bounding box of the final image for Printify placement.

    Fractions of image height: content_top is the first row with a visible pixel,
    content_bottom is the last. A fully opaque image returns (0.0, 1.0).
    """
    session = get_session(session_id)
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


@app.post("/presets")
async def save_preset_route(
    name: str = Form(...),
    concepts: str = Form(...),
    variants: str = Form(...),
    style: str = Form(...),
):
    name = name.strip()
    if not name or name == presets.BUILTIN_NAME:
        return {"error": "Name required (cannot overwrite built-in).", "names": presets.all_preset_names()}
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

@app.get("/printify/shops")
async def printify_shops():
    if not PRINTIFY_TOKEN:
        return JSONResponse({"error": "PRINTIFY_TOKEN not configured."}, status_code=503)
    try:
        shops = await asyncio.to_thread(printify.list_shops, PRINTIFY_TOKEN)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    return shops


@app.get("/printify/blueprints")
async def printify_blueprints(q: str = ""):
    """Return blueprints, optionally filtered by a search query."""
    if not PRINTIFY_TOKEN:
        return JSONResponse({"error": "PRINTIFY_TOKEN not configured."}, status_code=503)
    try:
        all_bps = await asyncio.to_thread(printify.list_blueprints, PRINTIFY_TOKEN)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)

    # Default filter: show shirt-like items unless user provides a custom query.
    search = q.strip().lower() if q.strip() else "shirt tee"
    terms = search.split()
    filtered = [
        {"id": bp["id"], "title": bp["title"], "brand": bp.get("brand", ""), "model": bp.get("model", "")}
        for bp in all_bps
        if any(t in bp.get("title", "").lower() for t in terms)
    ]
    return filtered


@app.get("/printify/blueprints/{blueprint_id}/providers")
async def printify_providers(blueprint_id: int):
    if not PRINTIFY_TOKEN:
        return JSONResponse({"error": "PRINTIFY_TOKEN not configured."}, status_code=503)
    try:
        providers = await asyncio.to_thread(
            printify.list_print_providers, PRINTIFY_TOKEN, blueprint_id
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    return providers


@app.get("/printify/blueprints/{blueprint_id}/providers/{provider_id}/variants")
async def printify_variants(blueprint_id: int, provider_id: int):
    if not PRINTIFY_TOKEN:
        return JSONResponse({"error": "PRINTIFY_TOKEN not configured."}, status_code=503)
    try:
        variants = await asyncio.to_thread(
            printify.list_variants, PRINTIFY_TOKEN, blueprint_id, provider_id
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    return variants


@app.post("/printify/publish")
async def printify_publish(
    session_id: str = Form(...),
    shop_id: str = Form(...),
    blueprint_id: int = Form(...),
    provider_id: int = Form(...),
    variant_ids: str = Form(...),   # JSON array of ints
    title: str = Form(...),
    description: str = Form(""),
    price_cents: int = Form(...),
    publish_now: bool = Form(False),
    design_x: float = Form(0.5),
    design_y: float = Form(0.5),
    design_scale: float = Form(0.8),
    final_url: str = Form(""),
):
    """Upload the session's final image to Printify and create (optionally publish) a product."""
    async def stream():
        if not PRINTIFY_TOKEN:
            yield sse({"type": "error", "message": "PRINTIFY_TOKEN not configured."})
            return

        session = get_session(session_id)
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
                yield sse({"type": "error", "message": f"Image must be at least {PRINTIFY_MIN_SIZE} ({min_px}px) to publish. Re-finalize at a higher resolution."})
                return

        try:
            ids: list[int] = json.loads(variant_ids)
        except json.JSONDecodeError:
            yield sse({"type": "error", "message": "Invalid variant selection."})
            return

        if not ids:
            yield sse({"type": "error", "message": "Select at least one color/size variant."})
            return

        # Step 1: Upload image
        yield sse({"type": "status", "message": "Uploading image to Printify..."})
        try:
            image_id = await asyncio.to_thread(
                printify.upload_image, PRINTIFY_TOKEN, final_path
            )
        except Exception as e:
            yield sse({"type": "error", "message": f"Image upload failed: {e}"})
            return

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
                image_id,
                ids,
                price_cents,
                design_x,
                design_y,
                design_scale,
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
        yield sse({
            "type": "done",
            "product_id": product_id,
            "product_url": product_url,
            "published": publish_now,
        })

    return StreamingResponse(stream(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
