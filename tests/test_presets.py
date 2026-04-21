"""Tests for src/presets.py — load/save/delete user presets."""

import pytest

import src.presets as presets_module
from src.presets import (
    BUILTIN_NAME,
    all_preset_names,
    delete_preset,
    get_preset,
    load_builtin,
    load_user_presets,
    save_preset,
)

# Minimal valid templates satisfying the required-placeholder check in save_preset().
_C = "Give me {num_concepts} ideas for {theme}."
_V = "Create {num_variants} variants for: {concept}."
_S = "Use {bg_color} background with max {max_colors} colors."


def test_load_builtin_returns_required_keys():
    builtin = load_builtin()
    assert "concepts_prompt" in builtin
    assert "variants_prompt" in builtin
    assert "style_suffix" in builtin


def test_load_builtin_templates_are_non_empty():
    builtin = load_builtin()
    for key, value in builtin.items():
        assert value.strip(), f"{key} should not be empty"


def test_builtin_name_in_all_preset_names():
    names = all_preset_names()
    assert BUILTIN_NAME in names
    assert names[0] == BUILTIN_NAME  # built-in is always first


def test_get_preset_returns_builtin():
    preset = get_preset(BUILTIN_NAME)
    assert "concepts_prompt" in preset


def test_get_preset_raises_for_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(presets_module, "_PRESETS_PATH", tmp_path / "presets.json")
    with pytest.raises(KeyError):
        get_preset("nonexistent preset")


def test_save_and_load_user_preset(tmp_path, monkeypatch):
    monkeypatch.setattr(presets_module, "_PRESETS_PATH", tmp_path / "presets.json")

    save_preset("My Preset", _C, _V, _S)
    result = get_preset("My Preset")

    assert result["concepts_prompt"] == _C
    assert result["variants_prompt"] == _V
    assert result["style_suffix"] == _S


def test_save_preset_appears_in_all_names(tmp_path, monkeypatch):
    monkeypatch.setattr(presets_module, "_PRESETS_PATH", tmp_path / "presets.json")

    save_preset("Cool Preset", _C, _V, _S)
    assert "Cool Preset" in all_preset_names()


def test_delete_preset_removes_it(tmp_path, monkeypatch):
    monkeypatch.setattr(presets_module, "_PRESETS_PATH", tmp_path / "presets.json")

    save_preset("Temp", _C, _V, _S)
    delete_preset("Temp")
    assert "Temp" not in all_preset_names()


def test_delete_nonexistent_preset_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(presets_module, "_PRESETS_PATH", tmp_path / "presets.json")
    delete_preset("does not exist")  # should not raise


def test_overwriting_existing_preset_does_not_count_toward_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(presets_module, "_PRESETS_PATH", tmp_path / "presets.json")
    monkeypatch.setattr(presets_module, "MAX_PRESETS", 1)

    _C2 = "Different {num_concepts} template for {theme}."
    save_preset("Only One", _C, _V, _S)
    # Overwriting same name should not raise even though limit is 1
    save_preset("Only One", _C2, _V, _S)
    assert get_preset("Only One")["concepts_prompt"] == _C2


def test_preset_limit_enforced(tmp_path, monkeypatch):
    monkeypatch.setattr(presets_module, "_PRESETS_PATH", tmp_path / "presets.json")
    monkeypatch.setattr(presets_module, "MAX_PRESETS", 2)

    save_preset("P1", _C, _V, _S)
    save_preset("P2", _C, _V, _S)

    with pytest.raises(ValueError, match="limit"):
        save_preset("P3", _C, _V, _S)


def test_save_preset_rejects_missing_theme_placeholder(tmp_path, monkeypatch):
    """Concepts template without {theme} should raise before writing."""
    monkeypatch.setattr(presets_module, "_PRESETS_PATH", tmp_path / "presets.json")
    with pytest.raises(ValueError, match=r"\{theme\}"):
        save_preset("Bad", "Give me {num_concepts} ideas.", _V, _S)


def test_save_preset_rejects_missing_variants_placeholder(tmp_path, monkeypatch):
    """Variants template without {concept} should raise before writing."""
    monkeypatch.setattr(presets_module, "_PRESETS_PATH", tmp_path / "presets.json")
    with pytest.raises(ValueError, match=r"\{concept\}"):
        save_preset("Bad", _C, "Create {num_variants} variants.", _S)


def test_save_preset_rejects_missing_style_placeholder(tmp_path, monkeypatch):
    """Style template without {bg_color} should raise before writing."""
    monkeypatch.setattr(presets_module, "_PRESETS_PATH", tmp_path / "presets.json")
    with pytest.raises(ValueError, match=r"\{bg_color\}"):
        save_preset("Bad", _C, _V, "Max {max_colors} colors.")
