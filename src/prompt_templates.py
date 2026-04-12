"""Prompt template formatters.

Each function accepts the raw template string (from the UI) and the dynamic
values, and returns the final prompt. Template loading is handled by
src/presets.py; this module only does string substitution.
"""


def concepts_prompt(template: str, theme: str, num_concepts: int) -> str:
    return template.format(theme=theme, num_concepts=num_concepts)


def variants_prompt(template: str, concept: str, num_variants: int) -> str:
    return template.format(concept=concept, num_variants=num_variants)


def style_suffix(template: str, bg_color: str, max_colors: int) -> str:
    return template.format(bg_color=bg_color, max_colors=max_colors)
