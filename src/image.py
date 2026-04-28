"""Gemini image generation — brainstorm variants and high-resolution finalization."""

import io

from google.genai import types
from PIL import Image

from config import BRAINSTORM_SIZE, DEFAULT_ASPECT_RATIO, FINAL_SIZE, MODEL
from src.client import get_client
from src.retry import with_retry

# Mode-specific prefix prepended to the prompt when a reference image is supplied.
# Exported so callers (e.g. sidecar writers in main.py) always use the same text.
REFERENCE_INSTRUCTIONS: dict[str, str] = {
    "copy": (
        "Use the provided image as a compositional reference — "
        "recreate a similar layout, subject placement, and design structure. "
        "Apply it to this design: "
    ),
    "edit": (
        "Apply the following changes to the provided image and return the modified design. "
        "Preserve all other visual elements — composition, color palette, style, and subject matter — exactly as they appear. "
        "Changes: "
    ),
    "style": (
        "Use the provided image as a visual style reference only. "
        "Do not reproduce its subject matter or composition. "
        "Match its color palette, line weight, and graphic aesthetic, "
        "then apply that style to this design: "
    ),
}


def _extract_image(response) -> Image.Image:
    """Pull the first image out of a Gemini response, regardless of how it was generated."""
    candidates = response.candidates or []
    for candidate in candidates:
        content = candidate.content
        for part in (content.parts or []) if content else []:
            if part.inline_data is not None and part.inline_data.data is not None:
                return Image.open(io.BytesIO(part.inline_data.data)).convert("RGBA")
    raise RuntimeError("No image returned from model")


def quantize_colors(img: Image.Image, max_colors: int) -> Image.Image:
    """Enforce a maximum number of distinct colors using PIL's quantize method.

    If the image has transparency (mode RGBA), the alpha channel is sharpened
    to a 1-bit mask (0 or 255) to prevent anti-aliasing from exponentially
    increasing the unique color count. Dithering is disabled to preserve solid
    color blocks for screen printing.
    """
    if max_colors <= 0:
        return img

    if img.mode == "RGBA":
        r, g, b, a = img.split()
        has_transparency = a.getextrema()[0] < 255
        
        if not has_transparency:
            # Fully opaque, just quantize RGB without dithering
            rgb = Image.merge("RGB", (r, g, b))
            q_rgb = rgb.quantize(colors=max_colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).convert("RGB")
            return Image.merge("RGBA", (*q_rgb.split(), a))
            
        # 1. Make alpha 1-bit (sharp edges) so translucent edges don't multiply colors
        a_sharp = a.point(lambda p: 255 if p > 127 else 0)
        
        # 2. Composite onto a mask color (magenta) that is unlikely to be in the design.
        # This prevents the RGB values of completely transparent pixels from polluting the palette.
        mask_color = (255, 0, 255)
        bg = Image.new("RGB", img.size, mask_color)
        rgb = Image.merge("RGB", (r, g, b))
        bg.paste(rgb, mask=a_sharp)
        
        # 3. Quantize to max_colors + 1 (to accommodate the magenta mask color).
        # Disable dithering to get solid, screen-printable color blocks.
        q = bg.quantize(colors=max_colors + 1, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        q_rgb = q.convert("RGB")
        
        # 4. Re-apply the sharp alpha mask. The magenta background becomes fully transparent,
        # leaving at most `max_colors` opaque colors + 1 transparent color.
        return Image.merge("RGBA", (*q_rgb.split(), a_sharp))

    # For RGB images
    return img.quantize(colors=max_colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).convert("RGB")


def flatten_transparency(img: Image.Image) -> Image.Image:
    """Composite transparent pixels onto white before sending to Gemini.

    Gemini rejects alpha channels — transparent areas render as black and
    mislead the model. Flattening to white is the least surprising substitute.
    Returns an RGB image; no-ops if the image has no transparency.
    """
    rgba = img.convert("RGBA")
    if rgba.getextrema()[3][0] > 0:
        # Alpha channel minimum > 0 means fully opaque — nothing to flatten.
        return rgba.convert("RGB")
    background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    background.paste(rgba, mask=rgba.split()[3])
    return background.convert("RGB")


def generate_image(
    prompt: str,
    api_key: str,
    size: str = BRAINSTORM_SIZE,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    reference_image: Image.Image | None = None,
    reference_mode: str = "style",
) -> Image.Image:
    client = get_client(api_key)

    if reference_image is not None:
        buf = io.BytesIO()
        flatten_transparency(reference_image).save(buf, format="PNG")

        # Prepend a mode-specific instruction so the model knows whether to borrow
        # only the visual style or to replicate the composition and subject matter.
        # Defaults to "style" for unrecognised modes.
        instruction = REFERENCE_INSTRUCTIONS.get(reference_mode, REFERENCE_INSTRUCTIONS["style"])

        contents = [
            types.Part(inline_data=types.Blob(data=buf.getvalue(), mime_type="image/png")),
            types.Part(text=instruction + prompt),
        ]
    else:
        contents = prompt

    def _call():
        return client.models.generate_content(
            model=MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],  # text-only response modality is excluded
                image_config=types.ImageConfig(
                    image_size=size,
                    aspect_ratio=aspect_ratio,
                ),
            ),
        )

    return _extract_image(with_retry(_call))


def finalize_image(prompt: str, reference: Image.Image, api_key: str, size: str = FINAL_SIZE, aspect_ratio: str = DEFAULT_ASPECT_RATIO) -> Image.Image:
    """Re-generate the approved variant at full resolution using the image as a visual anchor.

    Sending the reference alongside the prompt keeps the model from drifting to a new
    composition — it treats the variant as the target rather than starting from scratch.
    """
    client = get_client(api_key)

    buf = io.BytesIO()
    flatten_transparency(reference).save(buf, format="PNG")

    image_bytes = buf.getvalue()

    def _call():
        return client.models.generate_content(
            model=MODEL,
            contents=[
                types.Part(inline_data=types.Blob(data=image_bytes, mime_type="image/png")),
                types.Part(text=(
                    f"Recreate this exact design at {size} resolution. "
                    f"Preserve the composition, color palette, style, and every visual element exactly. "
                    f"Original prompt for reference: {prompt}"
                )),
            ],
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                # aspect_ratio is accepted by the API even when an image part is present — confirmed empirically
                image_config=types.ImageConfig(image_size=size, aspect_ratio=aspect_ratio),
            ),
        )

    return _extract_image(with_retry(_call))


def adapt_for_shirt(
    reference: Image.Image,
    shirt_color: str,
    api_key: str,
    size: str = BRAINSTORM_SIZE,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
) -> Image.Image:
    """Regenerate the design adapted for visibility on a specific shirt color.

    The shirt color fills the negative space, so elements that match or are
    close to that color need to be shifted or outlined to remain legible.
    """
    client = get_client(api_key)

    buf = io.BytesIO()
    flatten_transparency(reference).save(buf, format="PNG")

    def _call():
        return client.models.generate_content(
            model=MODEL,
            contents=[
                types.Part(inline_data=types.Blob(data=buf.getvalue(), mime_type="image/png")),
                types.Part(text=(
                    f"This t-shirt print design will be printed on a {shirt_color} shirt. "
                    f"The transparent areas of the original represent where the {shirt_color} shirt shows through. "
                    f"Redesign this so every element is clearly visible on a {shirt_color} shirt. "
                    f"Preserve the original composition, subject, and graphic style exactly — "
                    f"only adjust colors, add outlines, glows, or contrast details as needed for visibility. "
                    f"Output the adapted design at {size} resolution."
                )),
            ],
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(image_size=size, aspect_ratio=aspect_ratio),
            ),
        )

    return _extract_image(with_retry(_call))
