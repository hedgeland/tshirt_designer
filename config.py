import os
from dotenv import load_dotenv

load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
MODEL = "gemini-3.1-flash-image-preview"
NUM_VARIANTS = 3
OUTPUT_DIR = "output"
BRAINSTORM_SIZE = 512   # pixels per side during variant exploration
FINAL_SIZE = 4096       # pixels per side for approved final design (4K)
