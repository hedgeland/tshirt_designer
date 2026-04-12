"""Tests for src/prompt_templates.py — pure string formatting, no API calls."""

from src.prompt_templates import concepts_prompt, style_suffix, variants_prompt


def test_concepts_prompt_substitutes_theme_and_count():
    result = concepts_prompt("hello {theme} {num_concepts}", "space cats", 3)
    assert "space cats" in result
    assert "3" in result


def test_concepts_prompt_uses_template_as_is():
    template = "Give me {num_concepts} ideas for {theme}."
    result = concepts_prompt(template, "robots", 5)
    assert result == "Give me 5 ideas for robots."


def test_variants_prompt_substitutes_concept_and_count():
    result = variants_prompt("concept={concept} n={num_variants}", "dragon", 2)
    assert "dragon" in result
    assert "2" in result


def test_style_suffix_substitutes_color_and_max_colors():
    result = style_suffix("bg={bg_color} colors={max_colors}", "#FF00FF", 6)
    assert "#FF00FF" in result
    assert "6" in result


def test_style_suffix_real_template():
    """Smoke test with a realistic template string."""
    template = "solid {bg_color} background, max {max_colors} colors"
    result = style_suffix(template, "#00FF00", 4)
    assert result == "solid #00FF00 background, max 4 colors"
