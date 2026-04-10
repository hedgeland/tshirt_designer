import io
from functools import lru_cache

from google import genai
from google.genai import types
from PIL import Image

from config import MODEL


@lru_cache(maxsize=4)
def _get_client(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key)


def _extract_image(response: genai.types.GenerateContentResponse) -> Image.Image:
    """Pull the first image out of a Gemini response, regardless of how it was generated."""
    candidates = response.candidates or []
    for candidate in candidates:
        content = candidate.content
        for part in (content.parts or []) if content else []:
            if part.inline_data is not None and part.inline_data.data is not None:
                return Image.open(io.BytesIO(part.inline_data.data)).convert("RGBA")
    raise RuntimeError("No image returned from model")


def generate_image(prompt: str, api_key: str, size: str = "1K") -> Image.Image:
    client = _get_client(api_key)

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],  # text-only response modality is excluded
            image_config=types.ImageConfig(
                image_size=size,    # "1K", "2K", or "4K" — no arbitrary pixel values
                aspect_ratio="1:1", # always square — t-shirt designs center better this way
            ),
        ),
    )

    return _extract_image(response)


def finalize_image(prompt: str, reference: Image.Image, api_key: str, size: str = "4K") -> Image.Image:
    """Re-generate a variant at higher resolution using the selected image as a visual anchor.

    Sending the reference image alongside the prompt keeps the model from drifting to an
    entirely different composition — it treats the variant as the target and upscales it
    rather than starting from scratch.
    """
    client = _get_client(api_key)

    # Encode the reference variant as PNG bytes for the multimodal request.
    # If the variant has transparency (bg was removed), flatten it onto white before
    # sending — Gemini can't use alpha channels and rendering transparent areas as
    # black would mislead the model.
    ref = reference.convert("RGBA")
    if ref.getextrema()[3][0] == 0:
        background = Image.new("RGBA", ref.size, (255, 255, 255, 255))
        background.paste(ref, mask=ref.split()[3])
        ref = background.convert("RGB")
    buf = io.BytesIO()
    ref.save(buf, format="PNG")
    img_bytes = buf.getvalue()

    response = client.models.generate_content(
        model=MODEL,
        contents=[
            types.Part(inline_data=types.Blob(data=img_bytes, mime_type="image/png")),
            types.Part(text=(
                f"Recreate this exact design at {size} resolution. "
                f"The only change is resolution — preserve the composition, color palette, style, "
                f"and every visual element exactly. Do not add or remove anything. "
                f"Original prompt for reference: {prompt}"
            )),
        ],
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(
                image_size=size,
                aspect_ratio="1:1",
            ),
        ),
    )

    return _extract_image(response)


