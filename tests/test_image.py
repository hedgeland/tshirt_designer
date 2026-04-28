from PIL import Image

from src.image import quantize_colors

def test_quantize_colors_reduces_palette():
    # Create an RGB image with many colors
    img = Image.new("RGB", (100, 100))
    for x in range(100):
        for y in range(100):
            # 10000 distinct colors
            img.putpixel((x, y), (x * 2, y * 2, (x + y)))

    # Quantize to 8 colors
    quantized = quantize_colors(img, 8)
    
    # Mode should remain RGB
    assert quantized.mode == "RGB"
    
    # Check that there are at most 8 unique colors
    # getcolors() returns a list of (count, pixel) tuples
    colors = quantized.getcolors(maxcolors=10000)
    assert len(colors) <= 8

def test_quantize_colors_rgba_limits_total_unique_colors():
    # Create an RGBA image with many colors and varying alpha
    img = Image.new("RGBA", (100, 100))
    for x in range(100):
        for y in range(100):
            img.putpixel((x, y), (x * 2, y * 2, (x + y), x + y))

    # Quantize to 4 colors
    quantized = quantize_colors(img, 4)
    
    # Mode should remain RGBA
    assert quantized.mode == "RGBA"
    
    # The total number of unique RGBA tuples should be at most max_colors + 1 (the transparent background).
    # We enforce a sharp alpha mask to prevent translucent pixels from multiplying the palette.
    colors = quantized.getcolors(maxcolors=10000)
    assert len(colors) <= 5

    # Check that alpha is sharp (only 0 or 255)
    alpha_data = quantized.getchannel("A").tobytes()
    assert all(a in (0, 255) for a in alpha_data)

def test_quantize_colors_zero_or_negative_returns_original():
    img = Image.new("RGB", (10, 10), "red")
    assert quantize_colors(img, 0) is img
    assert quantize_colors(img, -5) is img
