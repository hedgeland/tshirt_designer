import json
import re
from functools import lru_cache
from google import genai
from config import MODEL


@lru_cache(maxsize=4)
def _get_client(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key)


def generate_concepts(theme: str, api_key: str, num_concepts: int = 5) -> list[str]:
    client = _get_client(api_key)

    # Ask the model to return strict JSON so parsing is reliable.
    # The "creative director" framing keeps outputs POD-focused and printable.
    prompt = f"""You are a creative director for a print-on-demand t-shirt business.

Generate {num_concepts} distinct t-shirt design concepts for the theme: "{theme}"

Each concept should:
- Be vivid and specific (2-3 sentences)
- Work well as a vector graphic: bold, clear silhouettes, limited colors
- Be visually striking on a t-shirt

Return ONLY a JSON array of {num_concepts} strings. No other text.
Example: ["concept 1 description", "concept 2 description"]"""

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
    )

    text = response.text.strip()

    # Extract the JSON array even if the model wraps it in markdown fences.
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            concepts = json.loads(match.group())
            return [str(c) for c in concepts[:num_concepts]]
        except json.JSONDecodeError:
            pass

    # Fallback: strip leading numbering (e.g. "1. ", "2) ") and return plain lines.
    lines = [re.sub(r"^\d+[\.\)]\s*", "", line).strip() for line in text.split("\n") if line.strip()]
    return [line for line in lines if line][:num_concepts]
