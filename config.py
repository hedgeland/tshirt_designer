import os
import secrets

from dotenv import load_dotenv

load_dotenv()  # reads .env if present; no-op if missing

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

# OAuth / session auth — leave GOOGLE_CLIENT_ID unset to run without auth (local dev).
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
# Comma-separated list of Google account emails allowed to log in.
ALLOWED_EMAILS: list[str] = [
    e.strip() for e in os.getenv("ALLOWED_EMAILS", "").split(",") if e.strip()
]
# Signs session cookies — must be stable across restarts (set SECRET_KEY in .env).
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_hex(32))
# Set HTTPS_ONLY=false in .env for local dev; defaults to true for safe production cookies.
HTTPS_ONLY = os.getenv("HTTPS_ONLY", "true").lower() == "true"

# All generation (text + image) goes through this single model — do not split.
# Gemini 3.1 Flash Image Preview; not Imagen.
MODEL = "gemini-3.1-flash-image-preview"

NUM_VARIANTS = 1        # default; overridden by the UI slider at runtime
BG_REMOVAL_TOLERANCE = 50  # 0–255; higher = removes more color variation at edges
EDGE_ERODE_PX = 1       # shrink alpha mask inward after removal to clip fringe ring
EDGE_DECONTAMINATE = 50 # 0–100; subtracts background color spill from boundary pixels
MAX_COLORS = 6          # max distinct colors in generated image; 1–8
MAX_PRESETS = 20        # max number of saved user presets (excludes built-in default)
OUTPUT_DIR = "output"   # root folder for saved PNGs; gitignored

# Two-phase resolution: fast previews during exploration, full quality on approval.
# ImageConfig.image_size accepts "1K", "2K", or "4K" — no arbitrary pixel values.
BRAINSTORM_SIZE = "1K"  # smallest available; fast previews during variant exploration
FINAL_SIZE = "4K"       # full quality for approved final design
