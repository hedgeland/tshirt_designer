import tempfile
from collections.abc import Generator
from typing import Any

import gradio as gr
import numpy as np
from gradio.themes import Soft
from PIL import Image

from config import (
    BG_REMOVAL_TOLERANCE,
    BRAINSTORM_SIZE,
    EDGE_DECONTAMINATE,
    EDGE_ERODE_PX,
    GOOGLE_API_KEY,
    MAX_COLORS,
    NUM_VARIANTS,
    OUTPUT_DIR,
)
from src.background import remove_background_color
from src.brainstorm import generate_concepts
from src.finalize import finalize_design
from src.image import generate_image
from src.output import save_variants
from src.prompts import build_prompts


def brainstorm(theme: str) -> Generator[Any, None, None]:
    if not GOOGLE_API_KEY:
        raise gr.Error("GOOGLE_API_KEY is not set. Add it to your .env file.")
    if not theme.strip():
        raise gr.Error("Enter a theme first.")

    # Disable button and show status while working.
    yield (
        gr.update(choices=[], value=None, visible=False),  # concept_radio
        [],
        "",
        gr.update(visible=False),  # concepts_state, theme_state, generate_group
        gr.update(value=[], visible=False),  # gallery
        gr.update(visible=False),  # finalize_row
        gr.update(visible=False),  # final_group
        [],
        [],
        None,  # prompts_state, images_state, selected_variant_state
        gr.update(interactive=False),  # brainstorm_btn
        gr.update(value="Generating concepts...", visible=True),  # brainstorm_status
        gr.update(value=""),  # prompt_log
    )

    concepts = generate_concepts(theme.strip(), GOOGLE_API_KEY)

    yield (
        gr.update(choices=concepts, value=None, visible=True),
        concepts,
        theme.strip(),
        gr.update(visible=False),
        gr.update(value=[], visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
        [],
        [],
        None,
        gr.update(interactive=True),
        gr.update(value="", visible=False),
        gr.update(value=""),  # prompt_log
    )


def _generate_btn_label(n: int) -> str:
    plural = "Variant" if n == 1 else "Variants"
    return f"🎨 Generate {n} {plural}"


def update_generate_btn_label(n: float) -> Any:
    return gr.update(value=_generate_btn_label(int(n)))


def select_concept(concept: str | None) -> tuple[Any, str]:
    if not concept:
        return gr.update(visible=False), ""
    return gr.update(visible=True), concept


def generate(
    edited_concept: str,
    bg_color: str,
    num_variants: float,
    theme: str,
    concepts: list[str],
    original_concept: str | None,
    max_colors: float,
) -> Generator[Any, None, None]:
    if not edited_concept.strip():
        raise gr.Error("No concept to generate from.")
    num_variants = int(num_variants)

    yield (
        gr.update(value=[], visible=False),  # gallery
        gr.update(visible=False),  # finalize_row
        gr.update(visible=False),  # final_group
        [],
        [],
        None,  # prompts_state, images_state, selected_variant_state
        gr.update(interactive=False),  # generate_btn
        gr.update(value="Building prompts...", visible=True),  # generate_status
        gr.update(),  # prompt_log
        gr.update(visible=False),  # remove_variant_bg_btn
    )

    prompts = build_prompts(
        edited_concept.strip(),
        GOOGLE_API_KEY,
        bg_color=bg_color,
        num_variants=num_variants,
        max_colors=int(max_colors),
    )
    images: list[Image.Image] = []

    # Show prompts immediately — before any image API calls so the user can read
    # them while generation runs and cancel early if something looks wrong.
    yield (
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
        [],
        [],
        None,
        gr.update(interactive=False),
        gr.update(value=f"Generating variant 1 of {num_variants}...", visible=True),
        gr.update(value=_format_prompts(prompts)),  # prompt_log
        gr.update(visible=False),  # remove_variant_bg_btn
    )

    for i, prompt in enumerate(prompts):
        if i > 0:  # first status already yielded above
            yield (
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(visible=False),
                [],
                [],
                None,
                gr.update(interactive=False),
                gr.update(value=f"Generating variant {i + 1} of {num_variants}...", visible=True),
                gr.update(),  # prompt_log
                gr.update(visible=False),  # remove_variant_bg_btn
            )
        img = generate_image(prompt, GOOGLE_API_KEY, size=BRAINSTORM_SIZE)
        images.append(img)

    concept_idx = concepts.index(original_concept) if original_concept in concepts else 0
    save_variants(theme, concept_idx, list(zip(prompts, images)))

    yield (
        gr.update(value=images, visible=True, columns=num_variants),
        gr.update(visible=True),
        gr.update(visible=False),
        prompts,
        images,
        0 if num_variants == 1 else None,  # auto-select when only one variant
        gr.update(interactive=True),
        gr.update(value="", visible=False),
        gr.update(value=_format_prompts(prompts)),  # prompt_log
        gr.update(visible=True),  # remove_variant_bg_btn
    )


def select_variant(evt: gr.SelectData) -> int:
    return evt.index


def _has_transparency(img: Image.Image) -> bool:
    """Return True if the image has any fully-transparent pixels."""
    if img.mode != "RGBA":
        return False
    # Split channels so getextrema() returns an unambiguous single-band
    # tuple[float, float] — avoids the multi-band overload that Pylance can't resolve.
    return img.split()[3].getextrema()[0] == 0  # min alpha value


def do_finalize(
    selected_idx: int | None,
    prompts: list[str],
    images: list[Image.Image],
    hex_color: str,
    tolerance: float,
    erode_px: float,
    decontaminate: float,
) -> Generator[Any, None, None]:
    yield (
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(interactive=False),
        gr.update(value="Generating 4K design...", visible=True),
    )

    if not images:
        yield (
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(interactive=True),
            gr.update(value="Generate variants first.", visible=True),
        )
        return

    if selected_idx is None:
        selected_idx = 0

    variant = images[selected_idx]
    bg_was_removed = _has_transparency(variant)

    # Finalize uses the original variant as a visual reference. If the user removed
    # the background, pass the original colors back so the model isn't confused by
    # transparency — we'll re-apply removal afterwards.
    final_img = finalize_design(prompts[selected_idx], variant, GOOGLE_API_KEY)

    if bg_was_removed:
        yield (
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(interactive=False),
            gr.update(value="Removing background from 4K image...", visible=True),
        )
        final_img = remove_background_color(
            final_img,
            hex_color,
            tolerance=int(tolerance),
            erode_px=int(erode_px),
            decontaminate=int(decontaminate),
        )

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        final_img.save(tmp.name, "PNG")

    yield (
        gr.update(value=final_img, visible=True),
        gr.update(visible=True),
        tmp.name,
        gr.update(interactive=True),
        gr.update(value="", visible=False),
    )


def do_remove_variant_bg(
    selected_idx: int | None,
    images: list[Image.Image],
    hex_color: str,
    tolerance: float,
    erode_px: float,
    decontaminate: float,
) -> Generator[Any, None, None]:
    yield (
        gr.update(),
        gr.update(),
        gr.update(interactive=False),
        gr.update(value="Removing background from variant...", visible=True),
    )

    if not images:
        yield (
            gr.update(),
            gr.update(),
            gr.update(interactive=True),
            gr.update(value="Generate variants first.", visible=True),
        )
        return

    if selected_idx is None:
        selected_idx = 0

    updated = list(images)
    updated[selected_idx] = remove_background_color(
        images[selected_idx],
        hex_color,
        tolerance=int(tolerance),
        erode_px=int(erode_px),
        decontaminate=int(decontaminate),
    )

    yield (
        gr.update(value=updated, columns=len(updated)),
        updated,
        gr.update(interactive=True),
        gr.update(value="", visible=False),
    )


def _numpy_to_pil(arr: Any) -> Image.Image:
    """Convert a Gradio image value (numpy array) to a PIL Image, preserving RGBA if present."""
    a = np.asarray(arr)
    if a.ndim == 3 and a.shape[2] == 4:
        return Image.fromarray(a, mode="RGBA")
    return Image.fromarray(a).convert("RGBA")


def do_remove_bg(
    current_image: Any | None,
    hex_color: str,
    tolerance: float,
    erode_px: float,
    decontaminate: float,
) -> Generator[Any, None, None]:
    if current_image is None:
        raise gr.Error("No final image to process.")

    yield (
        gr.update(),
        gr.update(),  # final_image, download_btn
        gr.update(interactive=False),  # remove_bg_btn
        gr.update(value="Removing background...", visible=True),  # remove_bg_status
    )

    result = remove_background_color(
        _numpy_to_pil(current_image),
        hex_color,
        tolerance=int(tolerance),
        erode_px=int(erode_px),
        decontaminate=int(decontaminate),
    )
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        result.save(tmp.name, "PNG")

    yield (
        gr.update(value=result),
        tmp.name,
        gr.update(interactive=True),
        gr.update(value="", visible=False),
    )


def _format_prompts(prompts: list[str]) -> str:
    """Format image generation prompts for the left-panel prompt log."""
    parts = []
    for i, p in enumerate(prompts, 1):
        parts.append(f"── Variant {i} ──\n{p}")
    return "\n\n".join(parts)


# ── Layout ────────────────────────────────────────────────────────────────────
SLIDER_CSS = """
    .slider-only input[type='number'] {
        border: none !important;
        box-shadow: none !important;
        background: transparent !important;
        pointer-events: none;
        font-weight: 600;
        width: 3em !important;
        text-align: center;
    }
    .slider-only input[type='number']::-webkit-inner-spin-button,
    .slider-only input[type='number']::-webkit-outer-spin-button { -webkit-appearance: none; }
"""

with gr.Blocks(title="T-Shirt Design Generator") as app:
    concepts_state = gr.State([])
    prompts_state = gr.State([])
    images_state = gr.State([])
    theme_state = gr.State("")
    selected_variant_state = gr.State(None)

    gr.Markdown(
        "# 👕 T-Shirt Design Generator\nBrainstorm → Select → Generate · Powered by Gemini 3.1 Flash Image Preview"
    )

    with gr.Row():
        with gr.Column(scale=1, min_width=220):
            gr.Markdown("### ⚙️ Settings")
            bg_color = gr.ColorPicker(label="Background color", value="#FF00FF")
            gr.Markdown("*Pick a solid color easy to remove in Canva.*")
            num_variants_slider = gr.Slider(
                label="Number of variants",
                minimum=1,
                maximum=5,
                step=1,
                value=NUM_VARIANTS,
                elem_classes="slider-only",
            )
            bg_tolerance_slider = gr.Slider(
                label="Background removal tolerance",
                minimum=0,
                maximum=128,
                step=1,
                value=BG_REMOVAL_TOLERANCE,
                elem_classes="slider-only",
            )
            gr.Markdown("*Higher = removes more color variation at edges.*")
            decontaminate_slider = gr.Slider(
                label="Color spill removal",
                minimum=0,
                maximum=100,
                step=5,
                value=EDGE_DECONTAMINATE,
                elem_classes="slider-only",
            )
            gr.Markdown("*Reduces background hue bleed on edges.*")
            erode_slider = gr.Slider(
                label="Edge shrink (px)",
                minimum=0,
                maximum=5,
                step=1,
                value=EDGE_ERODE_PX,
                elem_classes="slider-only",
            )
            gr.Markdown("*Clips residual fringe by shrinking the alpha mask.*")
            max_colors_slider = gr.Slider(
                label="Max colors",
                minimum=1,
                maximum=8,
                step=1,
                value=MAX_COLORS,
                elem_classes="slider-only",
            )
            gr.Markdown("*Applies to image generation.*")
            gr.Markdown(f"*Output: `{OUTPUT_DIR}/`*")

            with gr.Accordion("📋 Prompts sent to Gemini", open=False):
                prompt_log = gr.Textbox(
                    label="",
                    lines=20,
                    max_lines=40,
                    interactive=False,
                    placeholder="Prompts appear here after generating variants.",
                )

        with gr.Column(scale=4):
            # Step 1
            gr.Markdown("### 1 · Enter a theme")
            with gr.Row():
                theme_input = gr.Textbox(
                    placeholder="e.g. vintage motorcycles, funny cats, 90s hip-hop...",
                    show_label=False,
                    scale=4,
                )
                brainstorm_btn = gr.Button("🧠 Brainstorm", variant="primary", scale=1)

            brainstorm_status = gr.Markdown("", visible=False)

            # Step 2
            concept_radio = gr.Radio(label="2 · Pick a concept", choices=[], visible=False)

            # Step 3
            with gr.Group(visible=False) as generate_group:
                gr.Markdown("### 3 · Refine & generate")
                concept_editor = gr.Textbox(label="Edit concept (optional)", lines=2)
                generate_btn = gr.Button(_generate_btn_label(NUM_VARIANTS), variant="primary")
                generate_status = gr.Markdown("", visible=False)

            # Step 4
            gallery = gr.Gallery(
                label="4 · Variants — click one to select for finalization",
                visible=False,
                columns=NUM_VARIANTS,
                allow_preview=True,
                height=500,
                object_fit="contain",
            )
            with gr.Row(visible=False) as finalize_row:
                finalize_btn = gr.Button("Finalize selected variant at 4K", variant="primary")
                remove_variant_bg_btn = gr.Button(
                    "✂ Remove BG from Selected", variant="secondary", visible=False
                )
            variant_bg_status = gr.Markdown("", visible=False)
            finalize_status = gr.Markdown("", visible=False)

            # Step 5
            with gr.Group(visible=False) as final_group:
                gr.Markdown("### 5 · Final Design (4K)")
                final_image = gr.Image(show_label=False)
                with gr.Row():
                    download_btn = gr.DownloadButton("⬇ Download Final 4K PNG")
                    remove_bg_btn = gr.Button("✂ Remove Background", variant="secondary")
                remove_bg_status = gr.Markdown("", visible=False)

    # ── Events ────────────────────────────────────────────────────────────────
    brainstorm_outputs: list[Any] = [
        concept_radio,
        concepts_state,
        theme_state,
        generate_group,
        gallery,
        finalize_row,
        final_group,
        prompts_state,
        images_state,
        selected_variant_state,
        brainstorm_btn,
        brainstorm_status,
        prompt_log,
    ]
    brainstorm_btn.click(brainstorm, inputs=[theme_input], outputs=brainstorm_outputs)
    theme_input.submit(brainstorm, inputs=[theme_input], outputs=brainstorm_outputs)

    concept_radio.change(
        select_concept, inputs=[concept_radio], outputs=[generate_group, concept_editor]
    )

    num_variants_slider.change(
        update_generate_btn_label, inputs=[num_variants_slider], outputs=[generate_btn]
    )

    generate_btn.click(
        generate,
        inputs=[
            concept_editor,
            bg_color,
            num_variants_slider,
            theme_state,
            concepts_state,
            concept_radio,
            max_colors_slider,
        ],
        outputs=[
            gallery,
            finalize_row,
            final_group,
            prompts_state,
            images_state,
            selected_variant_state,
            generate_btn,
            generate_status,
            prompt_log,
            remove_variant_bg_btn,
        ],
    )

    gallery.select(select_variant, outputs=[selected_variant_state])

    remove_variant_bg_btn.click(
        do_remove_variant_bg,
        inputs=[
            selected_variant_state,
            images_state,
            bg_color,
            bg_tolerance_slider,
            erode_slider,
            decontaminate_slider,
        ],
        outputs=[gallery, images_state, remove_variant_bg_btn, variant_bg_status],
    )

    finalize_btn.click(
        do_finalize,
        inputs=[
            selected_variant_state,
            prompts_state,
            images_state,
            bg_color,
            bg_tolerance_slider,
            erode_slider,
            decontaminate_slider,
        ],
        outputs=[final_image, final_group, download_btn, finalize_btn, finalize_status],
    )

    remove_bg_btn.click(
        do_remove_bg,
        inputs=[final_image, bg_color, bg_tolerance_slider, erode_slider, decontaminate_slider],
        outputs=[final_image, download_btn, remove_bg_btn, remove_bg_status],
    )


if __name__ == "__main__":
    app.queue()  # required for generator (streaming) functions
    app.launch(theme=Soft(), css=SLIDER_CSS)
