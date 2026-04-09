import io
import os
from PIL import Image
from config import OUTPUT_DIR


def save_variants(theme: str, concept_idx: int, variants: list[tuple[str, Image.Image]]) -> list[str]:
    safe_theme = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in theme.strip().replace(" ", "_"))
    dir_path = os.path.join(OUTPUT_DIR, safe_theme, f"concept_{concept_idx + 1}")
    os.makedirs(dir_path, exist_ok=True)

    paths = []
    for i, (_, img) in enumerate(variants):
        filepath = os.path.join(dir_path, f"variant_{i + 1}.png")
        img.save(filepath, "PNG")
        paths.append(filepath)

    return paths


def image_to_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
