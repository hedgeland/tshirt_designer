"""Tests for src/output.py — theme sanitization and file saving."""

import re
from pathlib import Path

import pytest
from PIL import Image

from src.output import safe_theme_name, save_variants


# ── safe_theme_name ───────────────────────────────────────────────────────────
# The function appends _YYYYMMDD_HHMMSS, so tests check the sanitized prefix only.

def _prefix(name: str) -> str:
    """Strip the trailing _YYYYMMDD_HHMMSS timestamp from a safe_theme_name result."""
    return re.sub(r"_\d{8}_\d{6}$", "", name)


def test_safe_theme_name_replaces_spaces():
    assert _prefix(safe_theme_name("space cats")) == "space_cats"


def test_safe_theme_name_replaces_special_chars():
    assert _prefix(safe_theme_name("hello/world!")) == "hello_world_"


def test_safe_theme_name_strips_whitespace():
    assert _prefix(safe_theme_name("  robots  ")) == "robots"


def test_safe_theme_name_preserves_hyphens_and_underscores():
    assert _prefix(safe_theme_name("retro-80s_vibes")) == "retro-80s_vibes"


def test_safe_theme_name_handles_mixed():
    assert _prefix(safe_theme_name("  Cats & Dogs! ")) == "Cats___Dogs_"


def test_safe_theme_name_includes_timestamp():
    """Result must end with _YYYYMMDD_HHMMSS so output dirs sort chronologically."""
    result = safe_theme_name("test")
    assert re.search(r"_\d{8}_\d{6}$", result), f"No timestamp in: {result}"


# ── save_variants ─────────────────────────────────────────────────────────────

def test_save_variants_creates_files(tmp_path, monkeypatch):
    monkeypatch.setattr("src.output.OUTPUT_DIR", str(tmp_path))

    img = Image.new("RGBA", (64, 64), (255, 0, 255, 255))
    paths, concept_dir = save_variants("test theme", 0, [img, img], "1:1", "512")

    assert len(paths) == 2
    for p in paths:
        assert Path(p).exists()
        assert p.endswith(".png")


def test_save_variants_directory_structure(tmp_path, monkeypatch):
    monkeypatch.setattr("src.output.OUTPUT_DIR", str(tmp_path))

    img = Image.new("RGBA", (64, 64), (0, 255, 0, 255))
    paths, concept_dir = save_variants("my theme", 1, [img], "1:1", "512")

    assert "my_theme" in paths[0]
    assert "concept_2" in paths[0]   # concept_idx=1 → concept_2
    assert "variant_1_" in paths[0]
    assert paths[0].endswith(".png")


def test_save_variants_encodes_aspect_ratio_and_size(tmp_path, monkeypatch):
    monkeypatch.setattr("src.output.OUTPUT_DIR", str(tmp_path))

    img = Image.new("RGBA", (64, 64), (0, 0, 255, 255))
    paths, _ = save_variants("theme", 0, [img], "16:9", "1K")

    # Colon in aspect ratio becomes 'x' in the filename
    assert "16x9" in paths[0]
    assert "1K" in paths[0]
