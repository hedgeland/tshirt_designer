import json
import re
from google import genai
from config import MODEL


def generate_concepts(theme: str, api_key: str, num_concepts: int = 5) -> list[str]:
    client = genai.Client(api_key=api_key)

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
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            concepts = json.loads(match.group())
            return [str(c) for c in concepts[:num_concepts]]
        except json.JSONDecodeError:
            pass

    # Fallback: split numbered lines
    lines = [re.sub(r"^\d+[\.\)]\s*", "", l).strip() for l in text.split("\n") if l.strip()]
    return [l for l in lines if l][:num_concepts]
