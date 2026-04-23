"""Generate design concept names from a theme using the Gemini text model."""

import json
import logging
import re

from config import MODEL, NUM_CONCEPTS
from src.client import get_client
from src.prompt_templates import concepts_prompt

logger = logging.getLogger(__name__)


def generate_concepts(theme: str, api_key: str, concepts_template: str, num_concepts: int = NUM_CONCEPTS) -> list[str]:
    client = get_client(api_key)

    # Ask the model to return strict JSON so parsing is reliable.
    # The "creative director" framing keeps outputs POD-focused and printable.
    response = client.models.generate_content(
        model=MODEL,
        contents=concepts_prompt(concepts_template, theme, num_concepts),
    )

    text = (response.text or "").strip()

    # Extract the JSON array even if the model wraps it in markdown fences.
    result: list[str] = []
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            concepts = json.loads(match.group())
            result = [str(c) for c in concepts[:num_concepts]]
        except json.JSONDecodeError:
            pass

    if not result:
        # Fallback: strip leading numbering (e.g. "1. ", "2) ") and return plain lines.
        lines = [re.sub(r"^\d+[\.\)]\s*", "", line).strip() for line in text.split("\n") if line.strip()]
        result = [line for line in lines if line][:num_concepts]

    if len(result) < num_concepts:
        logger.warning(
            "generate_concepts returned %d concept(s) but %d were requested — "
            "the model may have returned a shorter list",
            len(result), num_concepts,
        )

    return result
