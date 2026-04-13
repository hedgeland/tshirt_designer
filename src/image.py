"""Gemini image generation — brainstorm variants and high-resolution finalization."""

import io

from google.genai import types
from PIL import Image

from config import MODEL
from src.client import get_client


def _extract_image(response) -> Image.Image:
    """Pull the first image out of a Gemini response, regardless of how it was generated."""
    candidates = response.candidates or []
    for candidate in candidates:
        content = candidate.content
        for part in (content.parts or []) if content else []:
            if part.inline_data is not None and part.inline_data.data is not None:
                return Image.open(io.BytesIO(part.inline_data.data)).convert("RGBA")
    raise RuntimeError("No image returned from model")


def generate_image(prompt: str, api_key: str, size: str = "512", aspect_ratio: str = "1:1") -> Image.Image:
    client = get_client(api_key)

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],  # text-only response modality is excluded
            image_config=types.ImageConfig(
                image_size=size,
                aspect_ratio=aspect_ratio,
            ),
        ),
    )

    return _extract_image(response)


def finalize_image(prompt: str, reference: Image.Image, api_key: str, size: str = "4K", aspect_ratio: str = "1:1") -> Image.Image:
    """Re-generate the approved variant at full resolution using the image as a visual anchor.

    Sending the reference alongside the prompt keeps the model from drifting to a new
    composition — it treats the variant as the target rather than starting from scratch.
    aspect_ratio is omitted from ImageConfig here because the API rejects it when an
    image part is present in the request (it infers the ratio from the input image).
    """
    client = get_client(api_key)

    # Flatten transparency before sending — Gemini doesn't support alpha channels and
    # would render transparent areas as black, misleading the model.
    ref = reference.convert("RGBA")
    if ref.getextrema()[3][0] == 0:
        background = Image.new("RGBA", ref.size, (255, 255, 255, 255))
        background.paste(ref, mask=ref.split()[3])
        ref = background.convert("RGB")
    buf = io.BytesIO()
    ref.save(buf, format="PNG")

    response = client.models.generate_content(
        model=MODEL,
        contents=[
            types.Part(inline_data=types.Blob(data=buf.getvalue(), mime_type="image/png")),
            types.Part(text=(
                f"Recreate this exact design at {size} resolution. "
                f"Preserve the composition, color palette, style, and every visual element exactly. "
                f"Original prompt for reference: {prompt}"
            )),
        ],
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(image_size=size, aspect_ratio=aspect_ratio),
        ),
    )

    return _extract_image(response)
