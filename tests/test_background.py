"""Tests for src/background.py — image processing pipeline."""

import numpy as np
import pytest
from PIL import Image

from src.background import remove_background_color


def _solid_image(color: tuple, size: int = 64) -> Image.Image:
    """Create a solid RGBA image."""
    img = Image.new("RGBA", (size, size), color)
    return img


def _image_with_design(bg: tuple, fg: tuple, size: int = 64) -> Image.Image:
    """Create an image with a solid background and a centered foreground square."""
    img = Image.new("RGBA", (size, size), bg)
    # Draw a centered design square (25% of image size inset from edges)
    margin = size // 4
    for y in range(margin, size - margin):
        for x in range(margin, size - margin):
            img.putpixel((x, y), fg)
    return img


def test_solid_background_fully_removed():
    """A solid magenta image should become fully transparent."""
    img = _solid_image((255, 0, 255, 255))
    result = remove_background_color(img, "#FF00FF", tolerance=10)
    arr = np.array(result)
    assert arr[:, :, 3].max() == 0, "All pixels should be transparent"


def test_background_removed_design_preserved():
    """Background pixels should be transparent; interior design pixels should remain opaque."""
    bg = (255, 0, 255, 255)   # magenta background
    fg = (0, 0, 255, 255)     # blue design
    img = _image_with_design(bg, fg, size=64)

    result = remove_background_color(img, "#FF00FF", tolerance=10, erode_px=0, decontaminate=0)
    arr = np.array(result)

    # Corner pixels are background — should be transparent
    assert arr[0, 0, 3] == 0, "Top-left corner should be transparent"
    assert arr[0, -1, 3] == 0, "Top-right corner should be transparent"

    # Center pixel is the design — should remain opaque
    cx, cy = 32, 32
    assert arr[cy, cx, 3] > 0, "Center design pixel should not be transparent"


def test_output_is_rgba():
    img = _solid_image((255, 0, 255, 255))
    result = remove_background_color(img, "#FF00FF")
    assert result.mode == "RGBA"


def test_rgb_input_is_handled():
    """Input images that are RGB (not RGBA) should be accepted."""
    img = Image.new("RGB", (32, 32), (255, 0, 255))
    result = remove_background_color(img, "#FF00FF", tolerance=10)
    assert result.mode == "RGBA"


def test_tolerance_zero_removes_exact_matches_only():
    """With tolerance=0, only exact background color pixels are removed."""
    bg = (255, 0, 255, 255)
    # Slightly off background (should survive with tolerance=0)
    near_bg = (254, 0, 255, 255)
    img = Image.new("RGBA", (8, 8), bg)
    # Put a slightly-off pixel in the corner
    img.putpixel((0, 0), near_bg)

    result = remove_background_color(img, "#FF00FF", tolerance=0, erode_px=0, decontaminate=0)
    arr = np.array(result)
    # The near-bg corner pixel should NOT have been removed (tolerance=0)
    assert arr[0, 0, 3] > 0


def test_different_background_colors():
    for hex_color, rgb in [("#FF0000", (255, 0, 0)), ("#00FF00", (0, 255, 0)), ("#0000FF", (0, 0, 255))]:
        img = _solid_image((*rgb, 255))
        result = remove_background_color(img, hex_color, tolerance=5)
        arr = np.array(result)
        assert arr[:, :, 3].max() == 0, f"Background {hex_color} should be fully removed"


def test_fully_opaque_image_corners_become_transparent():
    """A fully opaque solid-color image should have its background removed entirely."""
    img = Image.new("RGBA", (32, 32), (255, 0, 255, 255))
    assert img.split()[3].getextrema() == (255, 255)  # confirm fully opaque before removal

    result = remove_background_color(img, "#FF00FF", tolerance=10, erode_px=0, decontaminate=0)
    arr = np.array(result)
    assert arr[:, :, 3].max() == 0, "Fully opaque solid bg should become fully transparent"


def test_interior_pocket_removed():
    """Background-colored pixels enclosed by design elements (e.g. letter 'O') are also removed.

    Constructs a frame image:
      - outer background (magenta)
      - a ring of design pixels (blue)
      - inner background pocket (magenta) surrounded by design pixels

    The flood fill only removes edge-connected background. The interior pocket
    (not reachable from the edge) must be cleared by _remove_interior_bg.
    """
    size = 16
    bg = (255, 0, 255, 255)
    fg = (0, 0, 255, 255)
    img = Image.new("RGBA", (size, size), bg)

    # Draw a 2-pixel-wide frame of blue design pixels; inner area stays magenta
    for y in range(size):
        for x in range(size):
            if 3 <= x < size - 3 and 3 <= y < size - 3:
                if x < 5 or x >= size - 5 or y < 5 or y >= size - 5:
                    img.putpixel((x, y), fg)

    result = remove_background_color(img, "#FF00FF", tolerance=10, erode_px=0, decontaminate=0)
    arr = np.array(result)

    # Outer corners — edge-connected background — must be transparent
    assert arr[0, 0, 3] == 0, "Outer corner should be transparent"

    # Center pixel — interior magenta pocket — must also be transparent
    cx, cy = size // 2, size // 2
    assert arr[cy, cx, 3] == 0, "Interior pocket pixel should be transparent"


def test_tolerance_out_of_range_raises():
    """tolerance outside 0–255 should raise ValueError."""
    img = _solid_image((255, 0, 255, 255))
    with pytest.raises(ValueError, match="tolerance"):
        remove_background_color(img, "#FF00FF", tolerance=256)
    with pytest.raises(ValueError, match="tolerance"):
        remove_background_color(img, "#FF00FF", tolerance=-1)
