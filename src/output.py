"""Save generated images to disk under output/<theme>/concept_N/variant_N_ARxAR_SIZE.png."""

import io
import re
import zipfile
from datetime import datetime
from pathlib import Path

from PIL import Image

from config import ASPECT_RATIOS, OUTPUT_DIR, SIZE_PX


def _resolve_output_path(url: str) -> Path:
    """Resolve a URL path to an absolute Path, raising ValueError if outside OUTPUT_DIR."""
    root = Path(OUTPUT_DIR).resolve()
    p = Path(url.lstrip("/")).resolve()
    if not p.is_relative_to(root):
        raise ValueError(f"Path not in output directory: {url}")
    return p


def parse_concept_from_prompts(concept_dir: Path) -> tuple[str, str, int]:
    """Parse theme, concept text, and original variant count from prompts.md sidecar."""
    prompts_path = concept_dir / "prompts.md"
    theme = ""
    concept = ""
    variant_count = 0
    if prompts_path.exists():
        content = prompts_path.read_text()
        # Look for | Theme | value | and | Concept | value |
        theme_match = re.search(r"\|\s*Theme\s*\|\s*([^|]+)\|", content)
        concept_match = re.search(r"\|\s*Concept\s*\|\s*([^|]+)\|", content)
        count_match = re.search(r"\|\s*Variants\s*\|\s*(\d+)\s*\|", content)
        if theme_match:
            theme = theme_match.group(1).strip()
        if concept_match:
            concept = concept_match.group(1).strip()
        if count_match:
            variant_count = int(count_match.group(1))
    return theme, concept, variant_count


def scan_output() -> list[dict]:
    """Walk the output directory and return a structured list of themes, finals, and variants.

    Each theme dict contains:
      - theme:            display name (underscores → spaces)
      - dir_name:         raw directory name (used to build archive URLs)
      - session_size_bytes: total bytes of all files under this theme
      - finals:   list of {png_url, png_size, md_url, no_bg_url, no_bg_size, ts, width, height}
      - concepts: list of {name, display_session, concept_text, images: [{url, ...}]}

    Themes are sorted newest-first by directory mtime.
    """
    root = Path(OUTPUT_DIR)
    if not root.exists():
        return []

    def _url(p: Path) -> str:
        return "/" + p.as_posix()

    def _dims(p: Path) -> tuple[int, int]:
        with Image.open(p) as im:
            return im.size

    def _opt_stat(p: Path) -> tuple[int, str | None]:
        try:
            return p.stat().st_size, _url(p)
        except FileNotFoundError:
            return 0, None

    design_sessions = []
    for session_dir in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not session_dir.is_dir():
            continue

        session_bytes = 0
        finals = []
        finals_with_stat = [
            (f, f.stat()) for f in session_dir.glob("final_*.png")
            if "_no_bg" not in f.name
        ]
        for f, f_stat in sorted(finals_with_stat, key=lambda x: x[1].st_mtime, reverse=True):
            md = f.with_suffix(".md")
            no_bg = f.with_name(f.stem + "_no_bg.png")
            size = f_stat.st_size
            ts = datetime.fromtimestamp(f_stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            no_bg_size, no_bg_url = _opt_stat(no_bg)
            md_size, md_url = _opt_stat(md)
            session_bytes += size + no_bg_size + md_size
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
        # Find all concept_N directories
        concept_dirs = sorted(
            [d for d in session_dir.iterdir() if d.is_dir() and d.name.startswith("concept_")],
            key=lambda d: d.stat().st_mtime,
            reverse=True
        )

        for concept_dir in concept_dirs:
            display_session, concept_text, _ = parse_concept_from_prompts(concept_dir)
            
            # Group variants in this concept
            _AR_ORDER = {ar: i for i, ar in enumerate(ASPECT_RATIOS)}
            groups: dict[int, list] = {}
            for v in concept_dir.glob("variant_*.png"):
                if "_no_bg" in v.name:
                    continue
                m = re.match(r"variant_(\d+)_(.+)_([^_]+)$", v.stem)
                if not m:
                    continue
                groups.setdefault(int(m.group(1)), []).append((v, v.stat(), m.group(2), m.group(3)))

            concept_images = []
            for _n, renders in sorted(
                groups.items(),
                key=lambda kv: max(r[1].st_mtime for r in kv[1]),
                reverse=True,
            ):
                # Sort renders by resolution (highest first) then aspect ratio (dropdown order)
                renders_sorted = sorted(
                    renders, 
                    key=lambda r: (
                        -SIZE_PX.get(r[3], 0),
                        _AR_ORDER.get(r[2].replace("x", ":"), 99)
                    )                )
                render_list = []
                for v, v_stat, ar_safe, res in renders_sorted:
                    no_bg = v.with_name(v.stem + "_no_bg.png")
                    size = v_stat.st_size
                    no_bg_size, no_bg_url = _opt_stat(no_bg)
                    session_bytes += size + no_bg_size
                    vw, vh = _dims(v)
                    render_list.append({
                        "url": _url(v),
                        "size": size,
                        "no_bg_url": no_bg_url,
                        "no_bg_size": no_bg_size,
                        "ar": ar_safe.replace("x", ":"),
                        "res": res,
                        "width": vw,
                        "height": vh,
                    })

                rep = render_list[0]
                ts = datetime.fromtimestamp(renders_sorted[0][1].st_mtime).strftime("%Y-%m-%d %H:%M")
                concept_images.append({
                    "url": rep["url"],
                    "size": rep["size"],
                    "no_bg_url": rep["no_bg_url"],
                    "no_bg_size": rep["no_bg_size"],
                    "ts": ts,
                    "width": rep["width"],
                    "height": rep["height"],
                    "renders": render_list,
                })

            if concept_images:
                concepts.append({
                    "name": concept_dir.name,
                    "display_session": display_session,
                    "concept_text": concept_text,
                    "images": concept_images,
                })

        if finals or concepts:
            parts = session_dir.name.rsplit("_", 2)
            if len(parts) == 3 and parts[1].isdigit() and len(parts[1]) == 8:
                d = parts[1]
                t = parts[2]
                ts_str = f"{d[4:6]}/{d[6:8]}/{d[2:4]} {t[0:2]}:{t[2:4]}:{t[4:6]}"
                words = [w for w in parts[0].split("_") if w][:3]
                prefix = " ".join(words) if words else parts[0]
                display_name = f"{prefix} - {ts_str}"
            else:
                display_name = session_dir.name

            # Compatibility: 'images' is all variants across all concepts
            all_images = []
            for c in concepts:
                all_images.extend(c["images"])

            design_sessions.append({
                "design_session": display_name,
                "dir_name": session_dir.name,
                "session_size_bytes": session_bytes,
                "finals": finals,
                "concepts": concepts,
                "images": all_images,
            })

    return design_sessions


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
    # Collect first, then remove — modifying a directory while iterating it is
    # undefined behaviour on some filesystems and can raise StopIteration early.
    empty_concept_dirs = []
    for session_dir in Path(OUTPUT_DIR).iterdir():
        if not session_dir.is_dir():
            continue
        for concept_dir in session_dir.iterdir():
            if concept_dir.is_dir() and not any(concept_dir.iterdir()):
                empty_concept_dirs.append(concept_dir)
    for concept_dir in empty_concept_dirs:
        try:
            concept_dir.rmdir()
        except OSError:
            pass  # already gone or not empty; safe to skip

    # Re-scan theme dirs after child removal
    for session_dir in Path(OUTPUT_DIR).iterdir():
        if session_dir.is_dir() and not any(session_dir.iterdir()):
            try:
                session_dir.rmdir()
            except OSError:
                pass

    return {"deleted": deleted, "freed_bytes": freed, "errors": errors}


def archive_design_session(dir_name: str) -> bytes:
    """Zip all files under a session directory and return the bytes."""
    session_dir = Path(OUTPUT_DIR) / dir_name
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(session_dir.rglob("*")):
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


def rename_design_session(old_dir_name: str, new_display_name: str) -> dict:
    """Rename a session directory. Converts new_display_name to a safe dir name."""
    root = Path(OUTPUT_DIR)
    old_path = root / old_dir_name
    new_dir_name = safe_design_session_name(new_display_name)
    new_path = root / new_dir_name

    if not old_path.is_dir():
        raise ValueError(f"Session not found: {old_dir_name}")
    if new_path.exists() and new_path != old_path:
        raise ValueError("A session with that name already exists.")

    old_path.rename(new_path)
    return {"dir_name": new_dir_name, "design_session": new_display_name}


def load_image_to_session(session: dict, image_url: str, display_session: str) -> dict:
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
    session["theme"] = display_session
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


def load_concept_to_session(session: dict, session_dir_name: str, concept_dir_name: str) -> dict:
    """Load an entire concept directory into a session.

    Restores theme, concept text, variants, and their rendered combos.
    """
    root = Path(OUTPUT_DIR).resolve()
    session_dir = root / session_dir_name
    concept_dir = session_dir / concept_dir_name

    if not concept_dir.is_dir():
        raise ValueError(f"Concept directory not found: {concept_dir_name}")

    display_session, concept_text, variant_count = parse_concept_from_prompts(concept_dir)

    # Group variants
    groups: dict[int, list[Path]] = {}
    for v in concept_dir.glob("variant_*.png"):
        if "_no_bg" in v.name:
            continue
        m = re.match(r"variant_(\d+)_(.+)_([^_]+)$", v.stem)
        if not m:
            continue
        groups.setdefault(int(m.group(1)), []).append(v)

    if not groups:
        raise ValueError("No variants found in concept directory.")

    # Sort groups and identify "original" variants vs iterations
    _AR_ORDER = {ar: i for i, ar in enumerate(ASPECT_RATIOS)}
    
    variant_indices = sorted(groups.keys())
    image_paths = []
    combo_lists = []
    
    for idx in variant_indices:
        renders = groups[idx]
        # Sort renders by resolution (highest first) then aspect ratio (dropdown order)
        renders_sorted = sorted(
            renders,
            key=lambda p: (
                -SIZE_PX.get(p.stem.split("_")[-1], 0),
                _AR_ORDER.get(p.stem.split("_")[2].replace("x", ":"), 99)
            )
        )
        
        # The first render is our "primary" one for this variant slot
        primary = renders_sorted[0]
        # Store paths relative to project root (e.g. "output/theme/concept/...")
        image_paths.append(primary.relative_to(root.parent).as_posix())
        
        # Build the combo list for this variant
        combos = []
        for r in renders_sorted:
            m = re.match(r"variant_(\d+)_(.+)_([^_]+)$", r.stem)
            ar = m.group(2).replace("x", ":")
            res = m.group(3)
            no_bg = r.with_name(r.stem + "_no_bg.png")
            combos.append({
                "url": "/" + r.relative_to(root.parent).as_posix(),
                "size": res,
                "aspectRatio": ar,
                "noBgUrl": "/" + no_bg.relative_to(root.parent).as_posix() if no_bg.exists() else None
            })
        combo_lists.append(combos)

    # Load PIL images for the primary variants
    images = []
    for p_str in image_paths:
        img = Image.open(p_str)
        img.load()
        images.append(img)

    # Differentiate originals vs iterations
    # If variant_count is 0 (missing metadata), assume all are originals (old session)
    orig_count = variant_count if variant_count > 0 else len(image_paths)
    original_image_paths = image_paths[:orig_count]
    
    # Best-effort iteration_roots: assume iterations come from the first original variant
    # since we don't have true per-iteration rootIdx metadata on disk yet.
    iteration_roots = [0] * (len(image_paths) - orig_count)

    # Reset and populate session
    session["theme"] = display_session or session_dir_name
    session["concepts"] = [concept_text] if concept_text else []
    session["prompts"] = [] # We don't store prompts in session usually, they are built on the fly
    session["images"] = images
    session["image_paths"] = image_paths
    session["original_images"] = list(images[:orig_count])
    session["original_image_paths"] = list(original_image_paths)
    session["iteration_roots"] = iteration_roots
    session["no_bg_variant_cache"] = {}
    session["selected_idx"] = 0
    session["concept_dir"] = concept_dir.relative_to(root.parent).as_posix()
    session["combo_lists"] = combo_lists
    
    # Extract variant size and aspect ratio from the first primary variant
    m = re.match(r"variant_(\d+)_(.+)_([^_]+)$", Path(image_paths[0]).stem)
    if m:
        session["variant_aspect_ratio"] = m.group(2).replace("x", ":")
        session["variant_size"] = m.group(3)

    return {"theme": session["theme"], "concept": concept_text}


def safe_design_session_name(theme: str) -> str:
    """Return a filesystem-safe directory name: sanitized theme text + YYYYMMDD_HHMMSS.

    Full theme text is preserved (not truncated) so the browser can extract the
    first 3 meaningful words for display.  Non-alphanumeric chars become underscores.
    """
    sanitized = "".join(
        c if c.isalnum() or c in ("-", "_") else "_"
        for c in theme.strip().replace(" ", "_")
    )
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{sanitized}_{ts}"


def save_variants(
    theme: str,
    concept_idx: int,
    images: list[Image.Image],
    aspect_ratio: str,
    size: str,
) -> tuple[list[str], Path]:
    """Save variant images with deterministic filenames encoding the aspect/size combo.

    Returns (paths, concept_dir) so the caller can store concept_dir in session for
    subsequent /render calls on the same variant set.
    """
    ar_safe = aspect_ratio.replace(":", "x")  # "1:1" → "1x1", filesystem-safe
    dir_path = Path(OUTPUT_DIR) / safe_design_session_name(theme) / f"concept_{concept_idx + 1}"
    dir_path.mkdir(parents=True, exist_ok=True)

    paths = []
    for i, img in enumerate(images):
        # Deterministic filename: file existence on disk IS the cache
        path = dir_path / f"variant_{i + 1}_{ar_safe}_{size}.png"
        img.save(path, "PNG")
        paths.append(str(path))

    return paths, dir_path
