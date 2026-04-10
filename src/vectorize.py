import io

import vtracer
from PIL import Image


def raster_to_svg(image: Image.Image, max_colors: int = 6) -> str:
    """Convert a PIL image to an SVG string using vtracer.

    vtracer traces color regions into filled vector paths. It works best on images
    with flat colors, bold lines, and no gradients — exactly what we prompt for.
    Background should be removed (transparent) before calling this so the tracer
    doesn't include it as a filled region.
    """
    img = image.convert("RGBA")

    # Quantize to max_colors before tracing. Fewer input colors means fewer spurious
    # SVG layers and cleaner paths. We handle RGBA by separating the alpha channel,
    # quantizing RGB only, then reapplying the mask.
    alpha = img.split()[3]
    quantized = img.convert("RGB").quantize(colors=max_colors).convert("RGB")
    quantized = quantized.convert("RGBA")
    quantized.putalpha(alpha)
    img = quantized

    # vtracer expects raw image bytes.
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    img_bytes = buf.getvalue()

    return vtracer.convert_raw_image_to_svg(
        img_bytes,
        img_format="png",
        colormode="color",
        hierarchical="stacked",   # nested paths for overlapping color regions
        mode="spline",            # smooth curves rather than jagged polygons
        filter_speckle=16,        # discard noise regions smaller than 16px — eliminates background artifacts
        color_precision=4,        # fewer distinct colors; merges near-identical hues (kills bg remnants)
        layer_difference=32,      # require more color distance between layers — fewer spurious layers
        corner_threshold=90,      # higher = fewer hard corners = smoother curves on organic shapes
        length_threshold=4.0,     # minimum path segment length in pixels
        splice_threshold=45,      # angle to splice a spline into two
        path_precision=8,         # decimal places in SVG path coordinates
    )
