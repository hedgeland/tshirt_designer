"""Thin wrapper that re-generates the approved variant at the requested resolution."""

from PIL import Image

from config import DEFAULT_ASPECT_RATIO, FINAL_SIZE
from src.image import finalize_image


def finalize_design(
    prompt: str,
    reference: Image.Image,
    api_key: str,
    size: str = FINAL_SIZE,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
) -> Image.Image:
    return finalize_image(prompt, reference, api_key, size=size, aspect_ratio=aspect_ratio)
