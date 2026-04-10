import tempfile

import gradio as gr

from config import BRAINSTORM_SIZE, GOOGLE_API_KEY, NUM_VARIANTS, OUTPUT_DIR
from src.brainstorm import generate_concepts
from src.finalize import finalize_design
from src.image import generate_image
from src.output import save_variants
from src.prompts import build_prompts


def brainstorm(theme, _bg_color):  # bg_color unused here — passed to generate(), not brainstorm()
    if not GOOGLE_API_KEY:
        raise gr.Error("GOOGLE_API_KEY is not set. Add it to your .env file.")
    if not theme.strip():
        raise gr.Error("Enter a theme first.")

    # Disable button and show status while working.
    yield (
        gr.update(),                          # concept_radio
        [], "", gr.update(visible=False),     # concepts_state, theme_state, generate_group
        gr.update(value=[], visible=False),   # gallery
        gr.update(visible=False),             # finalize_row
        gr.update(visible=False),             # final_group
        [], None,                             # prompts_state, selected_variant_state
        gr.update(interactive=False),         # brainstorm_btn
        gr.update(value="Generating concepts...", visible=True),  # status_md
    )

    concepts = generate_concepts(theme.strip(), GOOGLE_API_KEY)

    yield (
        gr.update(choices=concepts, value=None, visible=True),
        concepts, theme.strip(), gr.update(visible=False),
        gr.update(value=[], visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
        [], None,
        gr.update(interactive=True),
        gr.update(value="", visible=False),
    )


def select_concept(concept):
    if not concept:
        return gr.update(visible=False), ""
    return gr.update(visible=True), concept


def generate(edited_concept, bg_color, num_variants, theme, concepts, original_concept):
    if not edited_concept.strip():
        raise gr.Error("No concept to generate from.")
    num_variants = int(num_variants)

    yield (
        gr.update(value=[], visible=False),  # gallery
        gr.update(visible=False),            # finalize_row
        gr.update(visible=False),            # final_group
        [], None,                            # prompts_state, selected_variant_state
        gr.update(interactive=False),        # generate_btn
        gr.update(value="Building prompts...", visible=True),  # status_md
    )

    prompts = build_prompts(edited_concept.strip(), GOOGLE_API_KEY, bg_color=bg_color, num_variants=num_variants)
    images = []

    for i, prompt in enumerate(prompts):
        yield (
            gr.update(visible=False), gr.update(visible=False), gr.update(visible=False),
            [], None,
            gr.update(interactive=False),
            gr.update(value=f"Generating variant {i + 1} of {num_variants}...", visible=True),
        )
        img = generate_image(prompt, GOOGLE_API_KEY, size=BRAINSTORM_SIZE)
        images.append(img)

    concept_idx = concepts.index(original_concept) if original_concept in concepts else 0
    save_variants(theme, concept_idx, list(zip(prompts, images)))

    yield (
        gr.update(value=images, visible=True, columns=num_variants),
        gr.update(visible=True),
        gr.update(visible=False),
        prompts, None,
        gr.update(interactive=True),
        gr.update(value="", visible=False),
    )


def select_variant(evt: gr.SelectData):
    return evt.index


def do_finalize(selected_idx, prompts):
    if selected_idx is None:
        raise gr.Error("Click a variant image to select it first.")

    yield (
        gr.update(), gr.update(), gr.update(),   # final_image, final_group, download_btn
        gr.update(interactive=False),             # finalize_btn
        gr.update(value="Generating 4K design...", visible=True),  # status_md
    )

    final_img = finalize_design(prompts[selected_idx], GOOGLE_API_KEY)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        final_img.save(tmp.name, "PNG")

    yield (
        gr.update(value=final_img, visible=True),
        gr.update(visible=True),
        tmp.name,
        gr.update(interactive=True),
        gr.update(value="", visible=False),
    )


# ── Layout ────────────────────────────────────────────────────────────────────
with gr.Blocks(title="T-Shirt Design Generator") as app:

    concepts_state = gr.State([])
    prompts_state = gr.State([])
    theme_state = gr.State("")
    selected_variant_state = gr.State(None)

    gr.Markdown("# 👕 T-Shirt Design Generator\nBrainstorm → Select → Generate · Powered by Gemini 3.1 Flash Image Preview")

    with gr.Row():
        with gr.Column(scale=1, min_width=220):
            gr.Markdown("### ⚙️ Settings")
            bg_color = gr.ColorPicker(label="Background color", value="#00B140")
            gr.Markdown("*Pick a solid color easy to remove in Canva.*")
            num_variants_slider = gr.Slider(
                label="Number of variants",
                minimum=1, maximum=5, step=1, value=NUM_VARIANTS,
            )
            gr.Markdown(f"*Output: `{OUTPUT_DIR}/`*")

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

            # Shared status line — shown during any active operation.
            status_md = gr.Markdown("", visible=False)

            # Step 2
            concept_radio = gr.Radio(label="2 · Pick a concept", choices=[], visible=False)

            # Step 3
            with gr.Group(visible=False) as generate_group:
                gr.Markdown("### 3 · Refine & generate")
                concept_editor = gr.Textbox(label="Edit concept (optional)", lines=2)
                generate_btn = gr.Button("🎨 Generate Variants", variant="primary")

            # Step 4
            gallery = gr.Gallery(
                label="4 · Variants — click one to select for finalization",
                visible=False,
                columns=NUM_VARIANTS,
                allow_preview=True,
            )
            with gr.Row(visible=False) as finalize_row:
                finalize_btn = gr.Button("Finalize selected variant at 4K", variant="primary")

            # Step 5
            with gr.Group(visible=False) as final_group:
                gr.Markdown("### 5 · Final Design (4K)")
                final_image = gr.Image(show_label=False)
                download_btn = gr.DownloadButton("⬇ Download Final 4K PNG")

    # ── Events ────────────────────────────────────────────────────────────────
    brainstorm_outputs = [
        concept_radio, concepts_state, theme_state, generate_group,
        gallery, finalize_row, final_group, prompts_state, selected_variant_state,
        brainstorm_btn, status_md,
    ]
    brainstorm_btn.click(brainstorm, inputs=[theme_input, bg_color], outputs=brainstorm_outputs)
    theme_input.submit(brainstorm, inputs=[theme_input, bg_color], outputs=brainstorm_outputs)

    concept_radio.change(select_concept, inputs=[concept_radio], outputs=[generate_group, concept_editor])

    generate_btn.click(
        generate,
        inputs=[concept_editor, bg_color, num_variants_slider, theme_state, concepts_state, concept_radio],
        outputs=[gallery, finalize_row, final_group, prompts_state, selected_variant_state, generate_btn, status_md],
    )

    gallery.select(select_variant, outputs=[selected_variant_state])

    finalize_btn.click(
        do_finalize,
        inputs=[selected_variant_state, prompts_state],
        outputs=[final_image, final_group, download_btn, finalize_btn, status_md],
    )


if __name__ == "__main__":
    app.queue()  # required for generator (streaming) functions
    app.launch(theme=gr.themes.Soft())
