"""Preset management: load/save named prompt template sets.

Built-in default comes from prompts.toml (ships with the repo, read-only from
the UI). User presets are stored in prompt_presets.json (gitignored, created
on first save).
"""

import json
import tomllib
from pathlib import Path

from config import MAX_PRESETS

_DEFAULTS_PATH = Path(__file__).parent.parent / "prompts.toml"
_PRESETS_PATH = Path(__file__).parent.parent / "prompt_presets.json"
BUILTIN_NAME = "Default (built-in)"

# Required placeholders for each template key — saving a preset that drops any of
# these would cause a silent KeyError when the template is rendered at generation time.
_REQUIRED_PLACEHOLDERS: dict[str, list[str]] = {
    "concepts_prompt": ["{theme}", "{num_concepts}"],
    "variants_prompt": ["{concept}", "{num_variants}"],
    "style_suffix": ["{bg_color}", "{max_colors}"],
}


def _validate_template(key: str, template: str) -> None:
    """Raise ValueError if template is missing any required placeholder."""
    missing = [p for p in _REQUIRED_PLACEHOLDERS.get(key, []) if p not in template]
    if missing:
        raise ValueError(
            f"Template '{key}' is missing required placeholder(s): {', '.join(missing)}"
        )


def load_builtin() -> dict[str, str]:
    with open(_DEFAULTS_PATH, "rb") as f:
        return tomllib.load(f)["templates"]


def load_user_presets() -> dict[str, dict[str, str]]:
    if not _PRESETS_PATH.exists():
        return {}
    with open(_PRESETS_PATH) as f:
        return json.load(f)


def all_preset_names() -> list[str]:
    return [BUILTIN_NAME] + list(load_user_presets().keys())


def all_presets() -> dict[str, dict[str, str]]:
    """Return every preset (builtin + user) keyed by name for embedding in page config."""
    result = {BUILTIN_NAME: load_builtin()}
    result.update(load_user_presets())
    return result


def get_preset(name: str) -> dict[str, str]:
    if name == BUILTIN_NAME:
        return load_builtin()
    return load_user_presets()[name]


def save_preset(name: str, concepts: str, variants: str, style: str) -> None:
    # Validate placeholders before writing — a missing {theme} etc. would fail silently
    # at generation time, far from where the preset was saved.
    _validate_template("concepts_prompt", concepts)
    _validate_template("variants_prompt", variants)
    _validate_template("style_suffix", style)

    user_presets = load_user_presets()
    # Overwriting an existing preset is always allowed; only new entries count.
    if name not in user_presets and len(user_presets) >= MAX_PRESETS:
        raise ValueError(f"Preset limit reached ({MAX_PRESETS}). Delete one before saving a new preset.")
    user_presets[name] = {
        "concepts_prompt": concepts,
        "variants_prompt": variants,
        "style_suffix": style,
    }
    with open(_PRESETS_PATH, "w") as f:
        json.dump(user_presets, f, indent=2)


def delete_preset(name: str) -> None:
    # Built-in is not stored in the JSON file, so this is a no-op for it.
    user_presets = load_user_presets()
    user_presets.pop(name, None)
    with open(_PRESETS_PATH, "w") as f:
        json.dump(user_presets, f, indent=2)
