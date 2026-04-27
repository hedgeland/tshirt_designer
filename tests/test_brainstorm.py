"""Tests for src/brainstorm.py — JSON parsing and fallback logic.

The Gemini client is mocked so no API calls are made.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.brainstorm import generate_concepts

TEMPLATE = "Give me {num_concepts} concepts for {theme}."


def _mock_response(text: str):
    return SimpleNamespace(text=text)


def _patched_client(text: str):
    client = MagicMock()
    client.models.generate_content.return_value = _mock_response(text)
    return client


def test_parses_clean_json_array():
    with patch("src.brainstorm.get_client", return_value=_patched_client('["A", "B", "C"]')):
        result = generate_concepts("robots", "key", TEMPLATE, num_concepts=3)
    assert result == ["A", "B", "C"]


def test_parses_json_wrapped_in_markdown():
    text = '```json\n["X", "Y"]\n```'
    with patch("src.brainstorm.get_client", return_value=_patched_client(text)):
        result = generate_concepts("cats", "key", TEMPLATE, num_concepts=2)
    assert result == ["X", "Y"]


def test_truncates_to_num_concepts():
    text = '["A", "B", "C", "D", "E"]'
    with patch("src.brainstorm.get_client", return_value=_patched_client(text)):
        result = generate_concepts("dogs", "key", TEMPLATE, num_concepts=3)
    assert len(result) == 3


def test_fallback_numbered_list():
    text = "1. First idea\n2. Second idea\n3. Third idea"
    with patch("src.brainstorm.get_client", return_value=_patched_client(text)):
        result = generate_concepts("space", "key", TEMPLATE, num_concepts=3)
    assert "First idea" in result
    assert "Second idea" in result
    assert len(result) == 3


def test_fallback_strips_numbering():
    text = "1) Alpha\n2) Beta"
    with patch("src.brainstorm.get_client", return_value=_patched_client(text)):
        result = generate_concepts("theme", "key", TEMPLATE, num_concepts=2)
    assert result == ["Alpha", "Beta"]


def test_empty_response_returns_empty_list():
    with patch("src.brainstorm.get_client", return_value=_patched_client("")):
        result = generate_concepts("theme", "key", TEMPLATE)
    assert result == []


def test_short_response_logs_warning(caplog):
    """When the model returns fewer concepts than requested a warning is logged."""
    import logging

    text = '["Only one"]'
    with patch("src.brainstorm.get_client", return_value=_patched_client(text)):
        with caplog.at_level(logging.WARNING, logger="src.brainstorm"):
            result = generate_concepts("theme", "key", TEMPLATE, num_concepts=5)
    assert len(result) == 1
    # caplog.messages contains pre-formatted strings (getMessage() already called)
    assert any("1" in m and "5" in m for m in caplog.messages), (
        "Expected a warning mentioning the actual vs requested count"
    )
