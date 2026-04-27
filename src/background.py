"""Background color removal via normalization, BFS flood fill, and edge decontamination."""

from collections import deque

import numpy as np
from PIL import Image, ImageFilter


_MAX_ADAPTIVE_TOLERANCE = 120  # cap so we don't eat design elements even on wild textures


def _sample_background_color(arr: np.ndarray) -> tuple[tuple[int, int, int], int]:
    """Estimate the background color and required flood-fill tolerance from the border strip.

    Returns (color, adaptive_tolerance).

    Corner pixels give a fast initial estimate. We then sample the full outer border
    strip to measure how much the background actually varies — high-resolution AI outputs
    often produce textured/grunge backgrounds where dark checker patches are 80+ units
    away from the corner average, far beyond the flat-background default of 50.

    The adaptive tolerance is the 95th-percentile max-channel deviation among border
    pixels that are already close to the corner estimate, plus a 10-unit margin.
    This self-corrects: flat backgrounds produce a small spread → small tolerance;
    textured ones produce a large spread → larger tolerance, capped at _MAX_ADAPTIVE_TOLERANCE.
    """
    h, w = arr.shape[:2]

    # Corner-based initial estimate — fast and reliable for the center hue.
    corners = np.array(
        [
            arr[0, 0, :3],
            arr[0, w - 1, :3],
            arr[h - 1, 0, :3],
            arr[h - 1, w - 1, :3],
        ],
        dtype=np.float32,
    )
    avg = np.mean(corners, axis=0)
    center = (int(round(float(avg[0]))), int(round(float(avg[1]))), int(round(float(avg[2]))))

    # Sample the full outer border strip (outer 1% of each dimension, min 5px, max 20px).
    # This captures the background variance that the 4-corner average masks.
    strip = max(5, min(20, int(min(h, w) * 0.01)))
    border_mask = np.zeros((h, w), dtype=bool)
    border_mask[:strip, :] = True
    border_mask[-strip:, :] = True
    border_mask[:, :strip] = True
    border_mask[:, -strip:] = True

    border_pixels = arr[border_mask, :3].astype(np.float32)
    cr, cg, cb = center

    # Keep only pixels plausibly in the background hue family (within a loose 80-unit
    # threshold per channel); design elements touching the border would differ further.
    close = (
        (np.abs(border_pixels[:, 0] - cr) <= 80)
        & (np.abs(border_pixels[:, 1] - cg) <= 80)
        & (np.abs(border_pixels[:, 2] - cb) <= 80)
    )
    bg_pixels = border_pixels[close]

    if len(bg_pixels) < 10:
        # Too few matching border pixels — fall back to corners with no adaptation.
        return center, 50

    # Spread = 95th percentile of the max per-channel deviation from the median.
    # Adding 10 gives a margin so the flood fill doesn't stall on the extreme tail.
    median = np.median(bg_pixels, axis=0)
    deviations = np.abs(bg_pixels - median).max(axis=1)
    spread = int(np.percentile(deviations, 95)) + 10

    adaptive_tolerance = min(spread, _MAX_ADAPTIVE_TOLERANCE)
    return center, adaptive_tolerance


def _color_mask(
    arr: np.ndarray,
    target: tuple[int, int, int],
    tolerance: int,
    require_opaque: bool = False,
) -> np.ndarray:
    """Boolean mask of pixels whose RGB channels are within tolerance of target.

    Used by normalize, flood fill, and interior-pocket removal to avoid repeating
    the same three-channel abs-delta expression.
    """
    r, g, b = target
    mask = (
        (np.abs(arr[:, :, 0].astype(np.int32) - r) <= tolerance)
        & (np.abs(arr[:, :, 1].astype(np.int32) - g) <= tolerance)
        & (np.abs(arr[:, :, 2].astype(np.int32) - b) <= tolerance)
    )
    if require_opaque:
        mask = mask & (arr[:, :, 3] > 0)
    return mask


def remove_background_color(
    image: Image.Image,
    hex_color: str,
    tolerance: int = 50,
    erode_px: int = 1,
    decontaminate: int = 50,
) -> Image.Image:
    """Remove the background color using normalization + edge flood fill.

    Pipeline:
    1. normalize      — snap near-background pixels to exact target color, flattening
                        AI-generated texture/noise so flood fill doesn't need wide tolerance
    2. flood fill     — BFS from image edges removes only connected background pixels,
                        preserving interior design elements that share the background hue
    3. interior fill  — removes isolated pockets (letter joins, enclosed gaps) not
                        reachable from edges; uses tolerance//2 to catch anti-aliased
                        stroke edges that weren't fully snapped by normalization
    4. decontaminate  — subtracts background color spill from alpha boundary pixels
    5. erode_px       — shrinks alpha mask inward to clip any residual fringe ring

    Order matters: normalize before fill (cleaner seeds), decontaminate before erode
    (operates on full edge width), erode last (tightens the mask).
    """
    if not 0 <= tolerance <= 255:
        raise ValueError(f"tolerance must be between 0 and 255, got {tolerance}")

    img = image.convert("RGBA")
    arr = np.array(img)

    # Detect the actual background color from corner pixels rather than trusting
    # hex_color — the model often renders the prompt color slightly off, and a
    # mismatch in any channel beyond tolerance means the flood fill finds no seeds.
    # The adaptive tolerance from border sampling handles textured 4K backgrounds
    # (where dark checker patches can be 80+ units from the corner average).
    target, adaptive_tolerance = _sample_background_color(arr)
    effective_tolerance = max(tolerance, adaptive_tolerance)

    arr = _normalize_background(arr, target, effective_tolerance)
    arr = _flood_fill_remove(arr, target, effective_tolerance)
    arr = _remove_interior_bg(arr, target, tolerance=effective_tolerance // 2)

    img = Image.fromarray(arr, "RGBA")

    if decontaminate > 0:
        img = _decontaminate_edges(img, target, decontaminate / 100.0)

    if erode_px > 0:
        img = _erode_alpha(img, erode_px)

    return img


def _normalize_background(
    arr: np.ndarray, target: tuple[int, int, int], tolerance: int
) -> np.ndarray:
    """Snap pixels within tolerance of target to the exact target color.

    Gemini doesn't render perfectly flat backgrounds — there's subtle hue variation
    (e.g. #FA00F8 instead of #FF00FF) that causes artifacts to survive removal.
    Normalizing first gives flood fill clean, uniform pixels to work with.
    """
    r, g, b = target
    mask = _color_mask(arr, target, tolerance)
    result = arr.copy()
    result[mask, 0] = r
    result[mask, 1] = g
    result[mask, 2] = b
    return result


def _flood_fill_remove(arr: np.ndarray, target: tuple[int, int, int], tolerance: int) -> np.ndarray:
    """BFS flood fill from image edges to remove connected background pixels.

    Seeds from every edge pixel and expands inward through neighbors that match the
    background color within tolerance. Pixels fully enclosed by design elements are
    never reached, so interior color matches (e.g. a magenta design detail) survive.
    The candidate mask is pre-computed with numpy for speed; BFS only visits
    background-colored pixels.
    """
    h, w = arr.shape[:2]

    # Pre-compute candidate mask vectorized — much faster than per-pixel Python checks.
    candidate: np.ndarray = _color_mask(arr, target, tolerance, require_opaque=True)

    visited = np.zeros((h, w), dtype=bool)
    queue: deque[tuple[int, int]] = deque()

    # Seed with edge pixels that are background candidates.
    for col in range(w):
        if candidate[0, col] and not visited[0, col]:
            visited[0, col] = True
            queue.append((0, col))
        if candidate[h - 1, col] and not visited[h - 1, col]:
            visited[h - 1, col] = True
            queue.append((h - 1, col))
    for row in range(1, h - 1):
        if candidate[row, 0] and not visited[row, 0]:
            visited[row, 0] = True
            queue.append((row, 0))
        if candidate[row, w - 1] and not visited[row, w - 1]:
            visited[row, w - 1] = True
            queue.append((row, w - 1))

    while queue:
        row, col = queue.popleft()
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nr, nc = row + dr, col + dc
            if 0 <= nr < h and 0 <= nc < w and not visited[nr, nc] and candidate[nr, nc]:
                visited[nr, nc] = True
                queue.append((nr, nc))

    result = arr.copy()
    result[visited, 3] = 0
    return result


def _remove_interior_bg(
    arr: np.ndarray, target: tuple[int, int, int], tolerance: int = 0
) -> np.ndarray:
    """Transparent-ize isolated background-colored pockets not reachable from the edges.

    After flood fill removes edge-connected background, any remaining opaque pixel
    within tolerance of target is an interior island — background surrounded by design
    elements. A tolerance slightly looser than zero catches anti-aliased stroke edges
    inside enclosed pockets (e.g. joins of letters like K or N) that weren't fully
    snapped by the normalization pass.
    """
    mask = _color_mask(arr, target, tolerance, require_opaque=True)
    result = arr.copy()
    result[mask, 3] = 0
    return result


def _decontaminate_edges(
    img: Image.Image,
    bg_rgb: tuple[int, int, int],
    strength: float,
    radius: int = 2,
) -> Image.Image:
    """Subtract background color spill from pixels near the alpha boundary.

    Anti-aliasing blends edge pixels with the background color. This finds those
    boundary pixels (opaque pixels within `radius` of a transparent one) and reduces
    each channel by strength × the background channel value, pushing the hue away
    from the background color. Trade-off: any design element that genuinely shares
    the background hue near an edge will also shift slightly.
    """
    arr = np.array(img, dtype=np.float32)
    alpha = arr[:, :, 3]

    # Dilate the transparent region outward to find nearby opaque edge pixels.
    transparent_mask = Image.fromarray((alpha < 1).astype(np.uint8) * 255, "L")
    dilated = np.array(
        transparent_mask.filter(ImageFilter.MaxFilter(radius * 2 + 1)), dtype=np.float32
    )
    edge = (dilated > 0) & (alpha > 0)

    for c, bg_val in enumerate(bg_rgb):
        arr[:, :, c] = np.where(
            edge,
            np.clip(arr[:, :, c] - strength * bg_val, 0, 255),
            arr[:, :, c],
        )

    return Image.fromarray(arr.astype(np.uint8), "RGBA")


def content_bounds(image: Image.Image) -> tuple[float, float]:
    """Return (top, bottom) as fractions of image height for non-transparent content.

    Used to compute Printify design_y so that empty transparent space above the
    subject doesn't create a visible gap at the top of the print area.
    Returns (0.0, 1.0) if the image is fully opaque or has no opaque pixels.
    """
    arr = np.array(image.convert("RGBA"))
    alpha = arr[:, :, 3]

    if alpha.min() == 255:  # no transparency — content fills the image
        return (0.0, 1.0)

    non_transparent = np.any(alpha > 0, axis=1)  # rows containing any visible pixel

    if not non_transparent.any():  # fully transparent — shouldn't happen, but safe
        return (0.0, 1.0)

    h = arr.shape[0]
    rows = np.where(non_transparent)[0]
    return (float(rows[0]) / h, float(rows[-1]) / h)


def _erode_alpha(img: Image.Image, pixels: int) -> Image.Image:
    """Shrink the alpha mask inward by `pixels` to clip the residual fringe ring.

    Each pass of MinFilter(3) shrinks by ~1 pixel. Stacking passes gives N-pixel erosion
    without a large kernel, which preserves sharp corners better than a single large filter.
    """
    r, g, b, a = img.split()
    for _ in range(pixels):
        a = a.filter(ImageFilter.MinFilter(3))
    return Image.merge("RGBA", (r, g, b, a))
