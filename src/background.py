from PIL import Image, ImageColor


def remove_background_color(image: Image.Image, hex_color: str, tolerance: int = 50) -> Image.Image:
    """Remove all pixels within tolerance of hex_color, regardless of position.

    Global (non-contiguous) removal catches background pixels that show through
    gaps in the design. Trade-off: any design element that shares the background
    color will also be made transparent.
    """
    img = image.convert("RGBA")
    pixels = img.load()
    w, h = img.size

    # Parse hex → (r, g, b); ImageColor handles "#RRGGBB" and shorthand formats.
    target = ImageColor.getrgb(hex_color)

    for x in range(w):
        for y in range(h):
            if _within_tolerance(pixels[x, y], target, tolerance):
                pixels[x, y] = (0, 0, 0, 0)

    return img


def _within_tolerance(pixel: tuple[int, ...], target: tuple[int, ...], tolerance: int) -> bool:
    # Compare only RGB channels; ignore alpha so semi-transparent edges are handled correctly.
    return all(abs(int(a) - int(b)) <= tolerance for a, b in zip(pixel[:3], target[:3]))
