import io
from PIL import Image
from google import genai
from google.genai import types
from config import MODEL


def generate_image(prompt: str, api_key: str, size: int = 512) -> Image.Image:
    client = genai.Client(api_key=api_key)

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(
                width=size,
                height=size,
            ),
        ),
    )

    for part in response.candidates[0].content.parts:
        if part.inline_data is not None:
            return Image.open(io.BytesIO(part.inline_data.data)).convert("RGBA")

    raise RuntimeError("No image returned from model")


def remove_background(img: Image.Image) -> Image.Image:
    """Remove background using rembg. Falls back to original image if unavailable."""
    try:
        from rembg import remove
        return remove(img)
    except Exception:
        return img
