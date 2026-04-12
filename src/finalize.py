"""Thin wrapper that re-generates the approved variant at FINAL_SIZE resolution."""

from PIL import Image

from config import FINAL_SIZE
from src.image import finalize_image


def finalize_design(prompt: str, reference: Image.Image, api_key: str) -> Image.Image:
    # Pass the selected variant as a visual anchor so the model upscales that specific
    # design rather than generating a new one from the text prompt alone.
    return finalize_image(prompt, reference, api_key, size=FINAL_SIZE)
