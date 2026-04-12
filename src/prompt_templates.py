"""Prompt templates for all Gemini text generation calls.

All prompt strings live here so they can be found, read, and tuned in one
place without touching API call logic. Each function returns a ready-to-send
string; callers pass the dynamic values and get back a complete prompt.
"""


def concepts_prompt(theme: str, num_concepts: int) -> str:
    """Prompt that generates distinct t-shirt design concepts for a theme."""
    return f"""You are a creative director for a print-on-demand t-shirt business.

Generate {num_concepts} distinct t-shirt design concepts for the theme: "{theme}"

Each concept should:
- Be vivid and specific (2-3 sentences)
- Work well as a vector graphic: bold, clear silhouettes, limited colors
- Be visually striking on a t-shirt

Return ONLY a JSON array of {num_concepts} strings. No other text.
Example: ["concept 1 description", "concept 2 description"]"""


def variants_prompt(concept: str, num_variants: int) -> str:
    """Prompt that expands a single concept into stylistically distinct image prompts."""
    return f"""You are an expert at writing prompts for Gemini image generation.
The images will be used as t-shirt designs, so vector style is critical.

Design concept: "{concept}"

Create {num_variants} distinct prompt variations for this concept. Each should:
- Describe the same core idea with a different stylistic angle
  (e.g. variant 1: vintage/retro, variant 2: bold/minimal, variant 3: illustrative/detailed)
- Be a single descriptive sentence (no bullet points)
- Focus only on the subject and any props — do NOT describe a background scene or environment
- NOT include style instructions — those will be appended automatically

Return ONLY a JSON array of {num_variants} prompt strings. No other text.
Example: ["prompt 1", "prompt 2", "prompt 3"]"""


def style_suffix(bg_color: str, max_colors: int) -> str:
    """Style constraints appended to every image prompt.

    Enforces POD-friendly output: flat vector, solid background, no garment mockup.
    bg_color is a hex string (e.g. "#00B140"). Explicit negatives are needed because
    "t-shirt design" in the prompt tends to make the model render a full garment or
    background scene. Any scene elements should appear as props around the subject,
    not as an environmental background.
    """
    return (
        f"flat vector graphic design, bold clean lines, "
        f"maximum {max_colors} colors total including background, limited color palette, "
        f"solid {bg_color} background filling the entire image with no scene or environment, "
        f"subject and props floating on the solid {bg_color} background only, "
        f"no background scenery, no landscape, no environment, no sky, no ground, no underwater scene, "
        f"no gradients, no t-shirt, no clothing, no garment, no mockup, "
        f"just the graphic artwork on the solid {bg_color} background, "
        f"high contrast, screen print ready"
    )
