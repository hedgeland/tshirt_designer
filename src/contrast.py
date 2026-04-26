"""Contrast assessment — asks Gemini whether the design will be visible on each shirt color.

Designs have intentional negative space (transparent areas) where the shirt shows through,
so pixel math alone can't determine visibility. Gemini reasons about the full composition.
"""

import io
import json
import logging
import re

from google.genai import types
from PIL import Image

from config import MODEL
from src.client import get_client
from src.retry import with_retry

logger = logging.getLogger(__name__)

# Mid-gray used when compositing transparency for assessment. Gray is neutral: it reveals
# both light and dark design elements equally, unlike white (hides light) or black (hides dark).
_ASSESSMENT_BG = (128, 128, 128)


def _flatten_for_assessment(img: Image.Image) -> Image.Image:
    rgba = img.convert("RGBA")
    background = Image.new("RGBA", rgba.size, (*_ASSESSMENT_BG, 255))
    background.paste(rgba, mask=rgba.split()[3])
    return background.convert("RGB")


def assess_contrast(image: Image.Image, shirt_colors: list[str], api_key: str) -> dict[str, dict]:
    """Ask Gemini whether the design will be clearly visible on each shirt color.

    Returns {color_name: {"ok": bool, "reason": str}} for every color in shirt_colors.
    Falls back to ok=True with a note if the response can't be parsed.
    """
    client = get_client(api_key)

    buf = io.BytesIO()
    _flatten_for_assessment(image).save(buf, format="PNG")

    color_list = ", ".join(shirt_colors)
    prompt = (
        "This is a t-shirt print design. The mid-gray areas represent transparent negative space "
        "where the shirt color will show through as part of the design.\n\n"
        f"Assess whether this design will be clearly visible on each of these shirt colors: {color_list}\n\n"
        "Consider: will the design's key elements be legible? Will the negative space (shirt color) "
        "wash out or hide any part of the design?\n\n"
        "Return ONLY a JSON object — one key per color name exactly as provided. Example:\n"
        '{"Black": {"ok": false, "reason": "Dark elements will be invisible on black."}, '
        '"White": {"ok": true, "reason": "Strong contrast throughout."}}\n\n'
        "Return only the JSON object, no markdown, no extra text."
    )

    def _call():
        return client.models.generate_content(
            model=MODEL,
            contents=[
                types.Part(inline_data=types.Blob(data=buf.getvalue(), mime_type="image/png")),
                types.Part(text=prompt),
            ],
        )

    response = with_retry(_call)
    text = (response.text or "").strip()

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    logger.warning("assess_contrast: could not parse model response: %.200s", text)
    return {color: {"ok": True, "reason": "Assessment unavailable."} for color in shirt_colors}
