"""Thin wrapper that re-generates the approved variant at FINAL_SIZE resolution."""

from PIL import Image

from config import FINAL_SIZE
from src.image import finalize_image


def finalize_design(prompt: str, reference: Image.Image, api_key: str) -> Image.Image:
    return finalize_image(prompt, reference, api_key, size=FINAL_SIZE)
