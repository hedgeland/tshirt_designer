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


def get_preset(name: str) -> dict[str, str]:
    if name == BUILTIN_NAME:
        return load_builtin()
    return load_user_presets()[name]


def save_preset(name: str, concepts: str, variants: str, style: str) -> None:
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
