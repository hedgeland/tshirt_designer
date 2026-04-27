"""Global settings management: load/save user preferences.

Settings are stored in settings.json (gitignored).
"""

import json
from pathlib import Path

from config import MAX_COLUMNS, NUM_VARIANTS

_SETTINGS_PATH = Path(__file__).parent.parent / "settings.json"

# Default values for settings
_DEFAULT_SETTINGS = {
    "default_min_columns": 1,
    "default_max_columns": MAX_COLUMNS,
    "default_num_variants": NUM_VARIANTS,
    "printify_favorites": [],
    "printify_color_favorites": [],
}


def load_settings() -> dict:
    """Load settings from settings.json, falling back to defaults if missing or invalid."""
    settings = _DEFAULT_SETTINGS.copy()
    if _SETTINGS_PATH.exists():
        try:
            with open(_SETTINGS_PATH) as f:
                user_settings = json.load(f)
                # Only update keys that exist in _DEFAULT_SETTINGS to avoid pollution
                for key in _DEFAULT_SETTINGS:
                    if key in user_settings:
                        settings[key] = user_settings[key]
        except (json.JSONDecodeError, IOError):
            pass  # Fall back to defaults on error
    return settings


def save_settings(settings: dict) -> None:
    """Save settings to settings.json. Only saves keys that exist in _DEFAULT_SETTINGS."""
    current = load_settings()
    for key in _DEFAULT_SETTINGS:
        if key in settings:
            current[key] = settings[key]

    with open(_SETTINGS_PATH, "w") as f:
        json.dump(current, f, indent=2)
