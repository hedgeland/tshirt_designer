import json
import re
from functools import lru_cache
from google import genai
from config import MODEL, NUM_VARIANTS


@lru_cache(maxsize=4)
def _get_client(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key)

def _style_suffix(bg_color: str) -> str:
    # bg_color is a hex string (e.g. "#00B140"). Telling the model the exact color
    # is more reliable than trying to composite it in post — the model renders it natively.
    return (
        f"flat vector illustration, bold clean lines, limited color palette, "
        f"solid {bg_color} background, no gradients, suitable for t-shirt screen printing, "
        f"high contrast, graphic art style"
    )


def build_prompts(concept: str, api_key: str, bg_color: str = "#FFFFFF") -> list[str]:
    client = _get_client(api_key)

    # Each variant gets a different stylistic angle on the same concept,
    # which gives the user meaningful visual choice without re-describing the idea.
    prompt = f"""You are an expert at writing prompts for Gemini image generation.
The images will be used as t-shirt designs and converted to SVG, so vector style is critical.

Design concept: "{concept}"

Create {NUM_VARIANTS} distinct prompt variations for this concept. Each should:
- Describe the same core idea with a different stylistic angle
  (e.g. variant 1: vintage/retro, variant 2: bold/minimal, variant 3: illustrative/detailed)
- Be a single descriptive sentence (no bullet points)
- NOT include style instructions — those will be appended automatically

Return ONLY a JSON array of {NUM_VARIANTS} prompt strings. No other text.
Example: ["prompt 1", "prompt 2", "prompt 3"]"""

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
    )

    text = response.text.strip()

    # Extract JSON array; fall back to repeating the raw concept if parsing fails.
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            prompts = json.loads(match.group())
            base_prompts = [str(p) for p in prompts[:NUM_VARIANTS]]
        except json.JSONDecodeError:
            base_prompts = [concept] * NUM_VARIANTS  # graceful degradation
    else:
        base_prompts = [concept] * NUM_VARIANTS

    # Append shared style constraints so the model targets POD-friendly output.
    return [f"{p}, {_style_suffix(bg_color)}" for p in base_prompts]
