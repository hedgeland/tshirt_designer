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
DEFAULT_BG_COLOR = "#FF00FF"  # solid magenta — easy to key out in Canva
DEFAULT_BG_COLOR_NAME = "Magenta"
BG_REMOVAL_TOLERANCE = 50  # 0–255; higher = removes more color variation at edges
EDGE_ERODE_PX = 1       # shrink alpha mask inward after removal to clip fringe ring
EDGE_DECONTAMINATE = 50 # 0–100; subtracts background color spill from boundary pixels
MAX_COLORS = 6          # max distinct colors in generated image; 1–8
MAX_PRESETS = 20        # max number of saved user presets (excludes built-in default)
MAX_COLUMNS = 4         # hard server-side cap on columns per session; user sets their own limit up to this value

# In-memory session lifecycle: evict sessions idle longer than TTL (seconds).
# Cleanup runs every INTERVAL seconds on a background asyncio task.
SESSION_TTL_SECONDS = 2 * 60 * 60       # 2 hours idle before eviction
SESSION_CLEANUP_INTERVAL = 30 * 60      # sweep frequency: every 30 minutes

# Printify integration — leave PRINTIFY_TOKEN unset to hide publishing features.
PRINTIFY_TOKEN = os.getenv("PRINTIFY_TOKEN", "")
# Pre-set your shop ID to skip the shop-selector step in the publish modal.
PRINTIFY_SHOP_ID = os.getenv("PRINTIFY_SHOP_ID", "")
# Auto-select this shop by name when the publish modal opens (case-insensitive).
PRINTIFY_SHOP_NAME = os.getenv("PRINTIFY_SHOP_NAME", "")
# Default blueprint search term shown before the user types a query.
PRINTIFY_DEFAULT_SEARCH = "shirt tee"
# Timeout for standard Printify API calls; image uploads use the longer value.
PRINTIFY_API_TIMEOUT = 30
PRINTIFY_UPLOAD_TIMEOUT = 120
OUTPUT_DIR = "output"   # root folder for saved PNGs; gitignored

# Aspect ratio options supported by the Gemini 3.1 Flash Image Preview model.
# The last four are model-specific extras not available in standard Imagen.
ASPECT_RATIOS = [
    "1:1", "3:2", "2:3", "3:4", "4:3", "4:5", "5:4",
    "9:16", "16:9", "21:9",
    "1:4", "4:1", "1:8", "8:1",
]
DEFAULT_ASPECT_RATIO = "1:1"

# Two-phase resolution: fast previews during exploration, full quality on approval.
# BRAINSTORM_SIZE / FINAL_SIZE are the defaults passed to the frontend as starting values;
# the actual API call values come from user-submitted form fields.
BRAINSTORM_SIZES = ["512", "1K", "2K"]
BRAINSTORM_SIZE = "512"   # 512px is enough for concept evaluation — saves tokens vs "1K"

FINAL_SIZES = ["1K", "2K", "4K"]
FINAL_SIZE = "1K"         # full quality for approved final design

EDIT_SIZES = ["512", "1K", "2K", "4K"]  # iteration edits include 512 for fast drafts
EDIT_SIZE = "512"         # default edit resolution — cheap for iteration, bump up before publishing

# Maps Gemini size tokens to their square pixel dimension.
# Add new entries here if the model gains higher-resolution support (e.g. "8K": 8192).
SIZE_PX: dict[str, int] = {"512": 512, "1K": 1024, "2K": 2048, "4K": 4096}

# Minimum size required to publish to Printify. Must be a key in SIZE_PX.
PRINTIFY_MIN_SIZE = "4K"
