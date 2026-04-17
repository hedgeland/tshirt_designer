"""Save generated images to disk under output/<theme>/concept_N/variant_N_<ts>.png."""

import io
import zipfile
from datetime import datetime
from pathlib import Path

from PIL import Image

from config import OUTPUT_DIR


def _resolve_output_path(url: str) -> Path:
    """Resolve a URL path to an absolute Path, raising ValueError if outside OUTPUT_DIR."""
    root = Path(OUTPUT_DIR).resolve()
    p = Path(url.lstrip("/")).resolve()
    if not p.is_relative_to(root):
        raise ValueError(f"Path not in output directory: {url}")
    return p


def scan_output() -> list[dict]:
    """Walk the output directory and return a structured list of themes, finals, and variants.

    Each theme dict contains:
      - theme:            display name (underscores → spaces)
      - dir_name:         raw directory name (used to build archive URLs)
      - theme_size_bytes: total bytes of all files under this theme
      - finals:   list of {png_url, png_size, md_url, no_bg_url, no_bg_size, ts, width, height}
      - concepts: list of {name, variants: [{url, size, no_bg_url, no_bg_size, ts, width, height}]}

    Themes are sorted newest-first by directory mtime.
    """
    root = Path(OUTPUT_DIR)
    if not root.exists():
        return []

    def _url(p: Path) -> str:
        return "/" + p.as_posix()

    def _dims(p: Path) -> tuple[int, int]:
        # PIL.Image.open is lazy — reading .size only parses the image header.
        with Image.open(p) as im:
            return im.size  # (width, height)

    def _opt_stat(p: Path) -> tuple[int, str | None]:
        """Return (size_bytes, url) for p, or (0, None) if it doesn't exist."""
        try:
            return p.stat().st_size, _url(p)
        except FileNotFoundError:
            return 0, None

    themes = []
    for theme_dir in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not theme_dir.is_dir():
            continue

        theme_bytes = 0

        finals = []
        finals_with_stat = [
            (f, f.stat()) for f in theme_dir.glob("final_*.png")
            if "_no_bg" not in f.name
        ]
        for f, f_stat in sorted(finals_with_stat, key=lambda x: x[1].st_mtime, reverse=True):
            md = f.with_suffix(".md")
            no_bg = f.with_name(f.stem + "_no_bg.png")
            size = f_stat.st_size
            ts = datetime.fromtimestamp(f_stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            no_bg_size, no_bg_url = _opt_stat(no_bg)
            md_size, md_url = _opt_stat(md)
            theme_bytes += size + no_bg_size + md_size
            w, h = _dims(f)
            finals.append({
                "png_url": _url(f),
                "png_size": size,
                "md_url": md_url,
                "no_bg_url": no_bg_url,
                "no_bg_size": no_bg_size,
                "ts": ts,
                "width": w,
                "height": h,
            })

        concepts = []
        for concept_dir in sorted(theme_dir.iterdir(), key=lambda p: p.name):
            if not concept_dir.is_dir() or not concept_dir.name.startswith("concept_"):
                continue
            variants = []
            variants_with_stat = [
                (v, v.stat()) for v in concept_dir.glob("variant_*.png")
                if "_no_bg" not in v.name
            ]
            for v, v_stat in sorted(variants_with_stat, key=lambda x: x[1].st_mtime, reverse=True):
                no_bg = v.with_name(v.stem + "_no_bg.png")
                size = v_stat.st_size
                ts = datetime.fromtimestamp(v_stat.st_mtime).strftime("%Y-%m-%d %H:%M")
                no_bg_size, no_bg_url = _opt_stat(no_bg)
                theme_bytes += size + no_bg_size
                vw, vh = _dims(v)
                variants.append({
                    "url": _url(v),
                    "size": size,
                    "no_bg_url": no_bg_url,
                    "no_bg_size": no_bg_size,
                    "ts": ts,
                    "width": vw,
                    "height": vh,
                })
            if variants:
                concepts.append({"name": concept_dir.name.replace("_", " ").title(), "variants": variants})

        if finals or concepts:
            themes.append({
                "theme": display_theme_name(theme_dir.name),
                "dir_name": theme_dir.name,
                "theme_size_bytes": theme_bytes,
                "finals": finals,
                "concepts": concepts,
            })

    return themes


def delete_files(paths: list[str]) -> dict:
    """Delete files by URL path. Refuses any path outside OUTPUT_DIR."""
    deleted = 0
    freed = 0
    errors = []

    for p_str in paths:
        try:
            resolved = _resolve_output_path(p_str)
            if resolved.is_file():
                freed += resolved.stat().st_size
                resolved.unlink()
                deleted += 1
        except ValueError:
            errors.append(f"Refused: {p_str}")
        except Exception as e:
            errors.append(str(e))

    # Prune empty directories bottom-up.
    for theme_dir in Path(OUTPUT_DIR).iterdir():
        if not theme_dir.is_dir():
            continue
        for concept_dir in theme_dir.iterdir():
            if concept_dir.is_dir() and not any(concept_dir.iterdir()):
                concept_dir.rmdir()
        if not any(theme_dir.iterdir()):
            theme_dir.rmdir()

    return {"deleted": deleted, "freed_bytes": freed, "errors": errors}


def archive_theme(dir_name: str) -> bytes:
    """Zip all files under a theme directory and return the bytes."""
    theme_dir = Path(OUTPUT_DIR) / dir_name
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(theme_dir.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(Path(OUTPUT_DIR)))
    return buf.getvalue()


def archive_files(paths: list[str]) -> bytes:
    """Zip a specific list of files (by URL path) and return the bytes."""
    root = Path(OUTPUT_DIR).resolve()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p_str in paths:
            try:
                p = _resolve_output_path(p_str)
                if p.is_file():
                    zf.write(p, p.relative_to(root))
            except ValueError:
                pass  # silently skip paths outside OUTPUT_DIR
    return buf.getvalue()


def rename_theme(old_dir_name: str, new_display_name: str) -> dict:
    """Rename a theme directory. Converts new_display_name to a safe dir name."""
    root = Path(OUTPUT_DIR)
    old_path = root / old_dir_name
    new_dir_name = safe_theme_name(new_display_name)
    new_path = root / new_dir_name

    if not old_path.is_dir():
        raise ValueError(f"Theme not found: {old_dir_name}")
    if new_path.exists() and new_path != old_path:
        raise ValueError("A theme with that name already exists.")

    old_path.rename(new_path)
    return {"dir_name": new_dir_name, "theme": new_display_name}


def load_image_to_session(session: dict, image_url: str, display_theme: str) -> dict:
    """Load an on-disk image into a session as a single variant.

    Validates that the URL resolves to a file inside OUTPUT_DIR, then resets the
    session to a clean variants-only state (no concepts, no prompts, no prior final).
    Returns {url, width, height} for the frontend.
    """
    p = _resolve_output_path(image_url)
    if not p.is_file():
        raise ValueError(f"File not found: {image_url}")

    img = Image.open(p)
    img.load()  # force full read so the file handle can be closed safely
    w, h = img.size

    # Reset session to a clean variant-only state.
    session["theme"] = display_theme
    session["concepts"] = []
    session["prompts"] = []
    # Use the URL-relative path (strip leading /) so it matches the format
    # save_variants produces — downstream code like _no_bg_path() and URL
    # construction all expect a relative path, not an absolute one.
    rel_path = image_url.lstrip("/")
    session["images"] = [img]
    session["image_paths"] = [rel_path]
    session["original_images"] = [img]
    session["original_image_paths"] = [rel_path]
    session["no_bg_variant_cache"] = {}
    session["selected_idx"] = 0
    session["final_image"] = None
    session["final_path"] = None
    session["original_final"] = None
    session["original_final_path"] = None
    session["no_bg_final_cache"] = None

    return {"url": image_url, "width": w, "height": h}


def safe_theme_name(theme: str) -> str:
    """Return a short, filesystem-safe directory name: first 10 sanitized chars + YYYYMMDD.

    The date suffix keeps same-theme runs from different days in separate folders
    while ensuring variants and finals generated in the same session (same day) share
    one directory.
    """
    sanitized = "".join(
        c if c.isalnum() or c in ("-", "_") else "_"
        for c in theme.strip().replace(" ", "_")
    )
    date = datetime.now().strftime("%Y%m%d")
    return f"{sanitized[:10]}_{date}"


def display_theme_name(dir_name: str) -> str:
    """Convert a directory name back to a human-readable theme string.

    Strips the trailing _YYYYMMDD date suffix added by safe_theme_name before
    converting underscores to spaces.
    """
    import re
    name = re.sub(r"_\d{8}$", "", dir_name)  # remove date suffix if present
    return name.replace("_", " ").strip()


def timestamp() -> str:
    """Return a sortable timestamp string for use in filenames."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def save_variants(theme: str, concept_idx: int, images: list[Image.Image]) -> list[str]:
    dir_path = Path(OUTPUT_DIR) / safe_theme_name(theme) / f"concept_{concept_idx + 1}"
    dir_path.mkdir(parents=True, exist_ok=True)  # create nested dirs if they don't exist

    ts = timestamp()  # one timestamp per batch so variants are grouped by generation run
    paths = []
    for i, img in enumerate(images):
        path = dir_path / f"variant_{i + 1}_{ts}.png"
        img.save(path, "PNG")
        paths.append(str(path))

    return paths  # caller uses these to build static URLs for the UI
