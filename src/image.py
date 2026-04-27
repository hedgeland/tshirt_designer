"""Gemini image generation — brainstorm variants and high-resolution finalization."""

import io

from google.genai import types
from PIL import Image

from config import BRAINSTORM_SIZE, DEFAULT_ASPECT_RATIO, FINAL_SIZE, MODEL
from src.client import get_client
from src.retry import with_retry

# Mode-specific prefix prepended to the prompt when a reference image is supplied.
# Exported so callers (e.g. sidecar writers in main.py) always use the same text.
REFERENCE_INSTRUCTIONS: dict[str, str] = {
    "copy": (
        "Use the provided image as a compositional reference — "
        "recreate a similar layout, subject placement, and design structure. "
        "Apply it to this design: "
    ),
    "edit": (
        "Apply the following changes to the provided image and return the modified design. "
        "Preserve all other visual elements — composition, color palette, style, and subject matter — exactly as they appear. "
        "Changes: "
    ),
    "style": (
        "Use the provided image as a visual style reference only. "
        "Do not reproduce its subject matter or composition. "
        "Match its color palette, line weight, and graphic aesthetic, "
        "then apply that style to this design: "
    ),
}


def _extract_image(response) -> Image.Image:
    """Pull the first image out of a Gemini response, regardless of how it was generated."""
    candidates = response.candidates or []
    for candidate in candidates:
        content = candidate.content
        for part in (content.parts or []) if content else []:
            if part.inline_data is not None and part.inline_data.data is not None:
                return Image.open(io.BytesIO(part.inline_data.data)).convert("RGBA")
    raise RuntimeError("No image returned from model")


def flatten_transparency(img: Image.Image) -> Image.Image:
    """Composite transparent pixels onto white before sending to Gemini.

    Gemini rejects alpha channels — transparent areas render as black and
    mislead the model. Flattening to white is the least surprising substitute.
    Returns an RGB image; no-ops if the image has no transparency.
    """
    rgba = img.convert("RGBA")
    if rgba.getextrema()[3][0] > 0:
        # Alpha channel minimum > 0 means fully opaque — nothing to flatten.
        return rgba.convert("RGB")
    background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    background.paste(rgba, mask=rgba.split()[3])
    return background.convert("RGB")


def generate_image(
    prompt: str,
    api_key: str,
    size: str = BRAINSTORM_SIZE,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    reference_image: Image.Image | None = None,
    reference_mode: str = "style",
) -> Image.Image:
    client = get_client(api_key)

    if reference_image is not None:
        buf = io.BytesIO()
        flatten_transparency(reference_image).save(buf, format="PNG")

        # Prepend a mode-specific instruction so the model knows whether to borrow
        # only the visual style or to replicate the composition and subject matter.
        # Defaults to "style" for unrecognised modes.
        instruction = REFERENCE_INSTRUCTIONS.get(reference_mode, REFERENCE_INSTRUCTIONS["style"])

        contents = [
            types.Part(inline_data=types.Blob(data=buf.getvalue(), mime_type="image/png")),
            types.Part(text=instruction + prompt),
        ]
    else:
        contents = prompt

    def _call():
        return client.models.generate_content(
            model=MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],  # text-only response modality is excluded
                image_config=types.ImageConfig(
                    image_size=size,
                    aspect_ratio=aspect_ratio,
                ),
            ),
        )

    return _extract_image(with_retry(_call))


def finalize_image(
    prompt: str,
    reference: Image.Image,
    api_key: str,
    size: str = FINAL_SIZE,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
) -> Image.Image:
    """Re-generate the approved variant at full resolution using the image as a visual anchor.

    Sending the reference alongside the prompt keeps the model from drifting to a new
    composition — it treats the variant as the target rather than starting from scratch.
    """
    client = get_client(api_key)

    buf = io.BytesIO()
    flatten_transparency(reference).save(buf, format="PNG")

    image_bytes = buf.getvalue()

    def _call():
        return client.models.generate_content(
            model=MODEL,
            contents=[
                types.Part(inline_data=types.Blob(data=image_bytes, mime_type="image/png")),
                types.Part(
                    text=(
                        f"Recreate this exact design at {size} resolution. "
                        f"Preserve the composition, color palette, style, and every visual element exactly. "
                        f"The background MUST be a perfectly flat, solid, uniform color — "
                        f"absolutely no texture, grunge, grain, noise, pattern, or color variation in the background whatsoever. "
                        f"Pure solid fill only. "
                        f"Original prompt for reference: {prompt}"
                    )
                ),
            ],
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                # aspect_ratio is accepted by the API even when an image part is present — confirmed empirically
                image_config=types.ImageConfig(image_size=size, aspect_ratio=aspect_ratio),
            ),
        )

    return _extract_image(with_retry(_call))
