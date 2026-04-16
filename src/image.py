"""Gemini image generation — brainstorm variants and high-resolution finalization."""

import io
import time

from google.genai import types
from PIL import Image

from config import BRAINSTORM_SIZE, DEFAULT_ASPECT_RATIO, FINAL_SIZE, MODEL
from src.client import get_client


def _with_retry(fn, retries: int = 3, base_delay: float = 5.0):
    """Call fn(), retrying up to `retries` times on transient 5xx / deadline errors.

    4K generation takes 20-40 s and occasionally hits Google's deadline on the first
    attempt.  A short pause lets the service recover before we try again.
    Delays: 5 s → 10 s → 20 s (exponential backoff, capped at three attempts).
    """
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            msg = str(e).lower()
            # Retry on service-unavailable, deadline-exceeded, and generic 5xx signals
            is_transient = any(k in msg for k in ("503", "unavailable", "deadline", "timeout", "500", "internal"))
            if is_transient and attempt < retries - 1:
                time.sleep(base_delay * (2 ** attempt))
                continue
            raise


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

    return _extract_image(_with_retry(_call))


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

    image_bytes = buf.getvalue()

    def _call():
        return client.models.generate_content(
            model=MODEL,
            contents=[
                types.Part(inline_data=types.Blob(data=image_bytes, mime_type="image/png")),
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

    return _extract_image(_with_retry(_call))
