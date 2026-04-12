import asyncio
import json
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
    BG_REMOVAL_TOLERANCE,
    BRAINSTORM_SIZE,
    DEFAULT_BG_COLOR,
    DEFAULT_BG_COLOR_NAME,
    EDGE_DECONTAMINATE,
    EDGE_ERODE_PX,
    GOOGLE_API_KEY,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    HTTPS_ONLY,
    MAX_COLORS,
    NUM_VARIANTS,
    OUTPUT_DIR,
    PRINTIFY_SHOP_ID,
    PRINTIFY_TOKEN,
    SECRET_KEY,
)
from src import presets, printify
from src.background import remove_background_color
from src.brainstorm import generate_concepts
from src.finalize import finalize_design
from src.image import generate_image
from src.output import safe_theme_name, save_variants
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
        yield sse({"type": "status", "message": f"Generating variant 1 of {num_variants}..."})

        images: list[Image.Image] = []
        for i, prompt in enumerate(prompts):
            if i > 0:
                yield sse({"type": "status", "message": f"Generating variant {i + 1} of {num_variants}..."})
            try:
                img = await asyncio.to_thread(generate_image, prompt, GOOGLE_API_KEY, size=BRAINSTORM_SIZE)
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
            "selected_idx": 0 if num_variants == 1 else None,
            "final_image": None,
            "final_path": None,
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

        yield sse({"type": "status", "message": "Generating 4K design..."})

        try:
            final_img = await asyncio.to_thread(
                finalize_design, prompts[idx], variant, GOOGLE_API_KEY
            )
        except Exception as e:
            yield sse({"type": "error", "message": str(e)})
            return

        if bg_was_removed:
            yield sse({"type": "status", "message": "Removing background from 4K image..."})
            final_img = await asyncio.to_thread(
                remove_background_color, final_img, bg_color,
                tolerance=bg_tolerance, erode_px=edge_erode, decontaminate=decontaminate,
            )

        final_path = Path(OUTPUT_DIR) / safe_theme_name(theme) / "final.png"
        final_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(final_img.save, str(final_path), "PNG")

        session["final_image"] = final_img
        session["final_path"] = str(final_path)

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
        yield sse({"type": "status", "message": "Removing background..."})

        try:
            result = await asyncio.to_thread(
                remove_background_color, images[idx], bg_color,
                tolerance=bg_tolerance, erode_px=edge_erode, decontaminate=decontaminate,
            )
        except Exception as e:
            yield sse({"type": "error", "message": str(e)})
            return

        updated = list(images)
        updated[idx] = result
        session["images"] = updated

        # Overwrite the on-disk file so the URL stays the same; client cache-busts with ?t=
        if idx < len(paths):
            await asyncio.to_thread(result.save, paths[idx], "PNG")

        url = f"/{paths[idx]}" if idx < len(paths) else ""
        yield sse({"type": "variant_updated", "index": idx, "url": url})

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

        yield sse({"type": "status", "message": "Removing background..."})

        try:
            result = await asyncio.to_thread(
                remove_background_color, final_img, bg_color,
                tolerance=bg_tolerance, erode_px=edge_erode, decontaminate=decontaminate,
            )
        except Exception as e:
            yield sse({"type": "error", "message": str(e)})
            return
        session["final_image"] = result

        if final_path:
            await asyncio.to_thread(result.save, final_path, "PNG")

        url = f"/{final_path}" if final_path else ""
        yield sse({"type": "final_updated", "url": url})

    return StreamingResponse(stream(), media_type="text/event-stream")


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
):
    """Upload the session's final image to Printify and create (optionally publish) a product."""
    async def stream():
        if not PRINTIFY_TOKEN:
            yield sse({"type": "error", "message": "PRINTIFY_TOKEN not configured."})
            return

        session = get_session(session_id)
        final_path = session.get("final_path")
        if not final_path:
            yield sse({"type": "error", "message": "Finalize a design first."})
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
