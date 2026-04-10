from collections import deque

import numpy as np
from PIL import Image, ImageColor, ImageFilter


def remove_background_color(
    image: Image.Image,
    hex_color: str,
    tolerance: int = 50,
    erode_px: int = 1,
    decontaminate: int = 50,
) -> Image.Image:
    """Remove the background color using normalization + edge flood fill.

    Pipeline:
    1. normalize   — snap near-background pixels to exact target color, flattening
                     AI-generated texture/noise so flood fill doesn't need wide tolerance
    2. flood fill  — BFS from image edges removes only connected background pixels,
                     preserving interior design elements that share the background hue
    3. decontaminate — subtracts background color spill from alpha boundary pixels
    4. erode_px    — shrinks alpha mask inward to clip any residual fringe ring

    Order matters: normalize before fill (cleaner seeds), decontaminate before erode
    (operates on full edge width), erode last (tightens the mask).
    """
    img = image.convert("RGBA")
    arr = np.array(img)
    target = ImageColor.getrgb(hex_color)

    arr = _normalize_background(arr, target, tolerance)
    arr = _flood_fill_remove(arr, target, tolerance)

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
    mask = (
        (np.abs(arr[:, :, 0].astype(np.int32) - r) <= tolerance) &
        (np.abs(arr[:, :, 1].astype(np.int32) - g) <= tolerance) &
        (np.abs(arr[:, :, 2].astype(np.int32) - b) <= tolerance)
    )
    result = arr.copy()
    result[mask, 0] = r
    result[mask, 1] = g
    result[mask, 2] = b
    return result


def _flood_fill_remove(
    arr: np.ndarray, target: tuple[int, int, int], tolerance: int
) -> np.ndarray:
    """BFS flood fill from image edges to remove connected background pixels.

    Seeds from every edge pixel and expands inward through neighbors that match the
    background color within tolerance. Pixels fully enclosed by design elements are
    never reached, so interior color matches (e.g. a magenta design detail) survive.
    The candidate mask is pre-computed with numpy for speed; BFS only visits
    background-colored pixels.
    """
    h, w = arr.shape[:2]
    r, g, b = target

    # Pre-compute candidate mask vectorized — much faster than per-pixel Python checks.
    candidate: np.ndarray = (
        (np.abs(arr[:, :, 0].astype(np.int32) - r) <= tolerance) &
        (np.abs(arr[:, :, 1].astype(np.int32) - g) <= tolerance) &
        (np.abs(arr[:, :, 2].astype(np.int32) - b) <= tolerance) &
        (arr[:, :, 3] > 0)
    )

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
    dilated = np.array(transparent_mask.filter(ImageFilter.MaxFilter(radius * 2 + 1)), dtype=np.float32)
    edge = (dilated > 0) & (alpha > 0)

    for c, bg_val in enumerate(bg_rgb):
        arr[:, :, c] = np.where(
            edge,
            np.clip(arr[:, :, c] - strength * bg_val, 0, 255),
            arr[:, :, c],
        )

    return Image.fromarray(arr.astype(np.uint8), "RGBA")


def _erode_alpha(img: Image.Image, pixels: int) -> Image.Image:
    """Shrink the alpha mask inward by `pixels` to clip the residual fringe ring.

    Each pass of MinFilter(3) shrinks by ~1 pixel. Stacking passes gives N-pixel erosion
    without a large kernel, which preserves sharp corners better than a single large filter.
    """
    r, g, b, a = img.split()
    for _ in range(pixels):
        a = a.filter(ImageFilter.MinFilter(3))
    return Image.merge("RGBA", (r, g, b, a))
