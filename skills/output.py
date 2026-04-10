import os
from PIL import Image
from config import OUTPUT_DIR


def save_variants(theme: str, concept_idx: int, variants: list[tuple[str, Image.Image]]) -> list[str]:
    # Sanitize the theme string so it's safe to use as a directory name.
    safe_theme = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in theme.strip().replace(" ", "_"))
    dir_path = os.path.join(OUTPUT_DIR, safe_theme, f"concept_{concept_idx + 1}")
    os.makedirs(dir_path, exist_ok=True)  # create nested dirs if they don't exist

    paths = []
    for i, (_, img) in enumerate(variants):  # prompt is stored in session state, not needed here
        filepath = os.path.join(dir_path, f"variant_{i + 1}.png")
        img.save(filepath, "PNG")
        paths.append(filepath)

    return paths  # caller uses paths[0] to show the parent directory in the UI
