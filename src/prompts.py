import json
import re
from functools import lru_cache

from google import genai

from config import MODEL
from src.prompt_templates import style_suffix, variants_prompt


@lru_cache(maxsize=4)
def _get_client(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key)


def build_prompts(
    concept: str,
    api_key: str,
    variants_template: str,
    style_template: str,
    bg_color: str = "#FFFFFF",
    num_variants: int = 3,
    max_colors: int = 6,
) -> list[str]:
    client = _get_client(api_key)

    # Each variant gets a different stylistic angle on the same concept,
    # which gives the user meaningful visual choice without re-describing the idea.
    response = client.models.generate_content(
        model=MODEL,
        contents=variants_prompt(variants_template, concept, num_variants),
    )

    text = (response.text or "").strip()

    # Extract JSON array; fall back to repeating the raw concept if parsing fails.
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            prompts = json.loads(match.group())
            base_prompts = [str(p) for p in prompts[:num_variants]]
        except json.JSONDecodeError:
            base_prompts = [concept] * num_variants  # graceful degradation
    else:
        base_prompts = [concept] * num_variants

    # Append shared style constraints so the model targets POD-friendly output.
    return [f"{p}, {style_suffix(style_template, bg_color, max_colors)}" for p in base_prompts[:num_variants]]
