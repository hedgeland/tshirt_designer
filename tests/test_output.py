"""Tests for src/output.py — theme sanitization and file saving."""

import os
from pathlib import Path

import pytest
from PIL import Image

from src.output import safe_theme_name, save_variants


def test_safe_theme_name_replaces_spaces():
    assert safe_theme_name("space cats") == "space_cats"


def test_safe_theme_name_replaces_special_chars():
    assert safe_theme_name("hello/world!") == "hello_world_"


def test_safe_theme_name_strips_whitespace():
    assert safe_theme_name("  robots  ") == "robots"


def test_safe_theme_name_preserves_hyphens_and_underscores():
    assert safe_theme_name("retro-80s_vibes") == "retro-80s_vibes"


def test_safe_theme_name_handles_mixed():
    assert safe_theme_name("  Cats & Dogs! ") == "Cats___Dogs_"


def test_save_variants_creates_files(tmp_path, monkeypatch):
    monkeypatch.setattr("src.output.OUTPUT_DIR", str(tmp_path))

    img = Image.new("RGBA", (64, 64), (255, 0, 255, 255))
    paths = save_variants("test theme", 0, [img, img])

    assert len(paths) == 2
    for p in paths:
        assert Path(p).exists()
        assert p.endswith(".png")


def test_save_variants_directory_structure(tmp_path, monkeypatch):
    monkeypatch.setattr("src.output.OUTPUT_DIR", str(tmp_path))

    img = Image.new("RGBA", (64, 64), (0, 255, 0, 255))
    paths = save_variants("my theme", 1, [img])

    # Should be output/my_theme/concept_2/variant_1.png
    assert "my_theme" in paths[0]
    assert "concept_2" in paths[0]
    assert "variant_1.png" in paths[0]
