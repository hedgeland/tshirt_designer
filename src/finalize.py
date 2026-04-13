"""Thin wrapper that re-generates the approved variant at the requested resolution."""

from PIL import Image

from config import FINAL_SIZE
from src.image import finalize_image


def finalize_design(
    prompt: str,
    reference: Image.Image,
    api_key: str,
    size: str = FINAL_SIZE,
    aspect_ratio: str = "1:1",
) -> Image.Image:
    return finalize_image(prompt, reference, api_key, size=size, aspect_ratio=aspect_ratio)
