import os

from dotenv import load_dotenv

load_dotenv()  # reads .env if present; no-op if missing

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

# All generation (text + image) goes through this single model — do not split.
# Gemini 3.1 Flash Image Preview ("Nano Banana 2"); not Imagen.
MODEL = "gemini-3.1-flash-image-preview"

NUM_VARIANTS = 3        # default; overridden by the UI slider at runtime
OUTPUT_DIR = "output"   # root folder for saved PNGs; gitignored

# Two-phase resolution: fast previews during exploration, full quality on approval.
# ImageConfig.image_size accepts "1K", "2K", or "4K" — no arbitrary pixel values.
BRAINSTORM_SIZE = "1K"  # smallest available; fast previews during variant exploration
FINAL_SIZE = "4K"       # full quality for approved final design
