"""Save generated images to disk under output/<theme>/concept_N/variant_N.png."""

from pathlib import Path

from PIL import Image

from config import OUTPUT_DIR


def safe_theme_name(theme: str) -> str:
    """Return a filesystem-safe version of a theme string."""
    return "".join(
        c if c.isalnum() or c in ("-", "_") else "_"
        for c in theme.strip().replace(" ", "_")
    )


def save_variants(theme: str, concept_idx: int, images: list[Image.Image]) -> list[str]:
    dir_path = Path(OUTPUT_DIR) / safe_theme_name(theme) / f"concept_{concept_idx + 1}"
    dir_path.mkdir(parents=True, exist_ok=True)  # create nested dirs if they don't exist

    paths = []
    for i, img in enumerate(images):
        path = dir_path / f"variant_{i + 1}.png"
        img.save(path, "PNG")
        paths.append(str(path))

    return paths  # caller uses these to build static URLs for the UI
