"""Gemini image generation — brainstorm variants and high-resolution finalization."""

import io

from google.genai import types
from PIL import Image

from config import BRAINSTORM_SIZE, DEFAULT_ASPECT_RATIO, FINAL_SIZE, MODEL
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
        # Flatten transparency before sending — same reason as finalize_image().
        ref = reference_image.convert("RGBA")
        if ref.getextrema()[3][0] == 0:
            background = Image.new("RGBA", ref.size, (255, 255, 255, 255))
            background.paste(ref, mask=ref.split()[3])
            ref = background.convert("RGB")
        buf = io.BytesIO()
        ref.save(buf, format="PNG")

        # Prepend a mode-specific instruction so the model knows whether to borrow
        # only the visual style or to replicate the composition and subject matter.
        if reference_mode == "copy":
            instruction = (
                "Use the provided image as a compositional reference — "
                "recreate a similar layout, subject placement, and design structure. "
                "Apply it to this design: "
            )
        else:  # "style"
            instruction = (
                "Use the provided image as a visual style reference only. "
                "Do not reproduce its subject matter or composition. "
                "Match its color palette, line weight, and graphic aesthetic, "
                "then apply that style to this design: "
            )

        contents = [
            types.Part(inline_data=types.Blob(data=buf.getvalue(), mime_type="image/png")),
            types.Part(text=instruction + prompt),
        ]
    else:
        contents = prompt

    response = client.models.generate_content(
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

    return _extract_image(response)


def finalize_image(prompt: str, reference: Image.Image, api_key: str, size: str = FINAL_SIZE, aspect_ratio: str = DEFAULT_ASPECT_RATIO) -> Image.Image:
    """Re-generate the approved variant at full resolution using the image as a visual anchor.

    Sending the reference alongside the prompt keeps the model from drifting to a new
    composition — it treats the variant as the target rather than starting from scratch.
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
            # aspect_ratio is accepted by the API even when an image part is present — confirmed empirically
            image_config=types.ImageConfig(image_size=size, aspect_ratio=aspect_ratio),
        ),
    )

    return _extract_image(response)
