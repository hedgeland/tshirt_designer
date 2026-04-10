import io
from functools import lru_cache
from PIL import Image
from google import genai
from google.genai import types
from config import MODEL


@lru_cache(maxsize=4)
def _get_client(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key)


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

    # The API returns multiple parts; find the first one that contains image bytes.
    for part in response.candidates[0].content.parts:
        if part.inline_data is not None:
            return Image.open(io.BytesIO(part.inline_data.data)).convert("RGBA")

    raise RuntimeError("No image returned from model")


def apply_background(img: Image.Image, color: tuple[int, int, int] = (0, 177, 64)) -> Image.Image:
    # Composite the design onto a solid color so apps like Canva can select
    # and remove it with one click — far more reliable than algorithmic removal.
    bg = Image.new("RGBA", img.size, (*color, 255))
    bg.paste(img, mask=img.split()[3])  # use alpha channel as mask
    return bg.convert("RGB")
