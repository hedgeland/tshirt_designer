import tempfile
import gradio as gr
from config import NUM_VARIANTS, OUTPUT_DIR, BRAINSTORM_SIZE, FINAL_SIZE, GOOGLE_API_KEY
from agents.brainstorm_agent import generate_concepts
from agents.prompt_agent import build_prompts
from agents.image_agent import generate_image
from agents.finalize_agent import finalize_design
from skills.output import save_variants


def brainstorm(theme, bg_color):
    if not GOOGLE_API_KEY:
        raise gr.Error("GOOGLE_API_KEY is not set. Add it to your .env file.")
    if not theme.strip():
        raise gr.Error("Enter a theme first.")
    concepts = generate_concepts(theme.strip(), GOOGLE_API_KEY)
    return (
        gr.update(choices=concepts, value=None, visible=True),  # concept_radio
        concepts,                                                 # concepts_state
        theme.strip(),                                           # theme_state
        gr.update(visible=False),                               # generate_group
        gr.update(value=[], visible=False),                     # gallery
        gr.update(visible=False),                               # finalize_row
        gr.update(visible=False),                               # final_group
        [],                                                      # prompts_state
        None,                                                    # selected_variant_state
    )


def select_concept(concept):
    if not concept:
        return gr.update(visible=False), ""
    return gr.update(visible=True), concept


def generate(edited_concept, bg_color, theme, concepts, original_concept):
    if not edited_concept.strip():
        raise gr.Error("No concept to generate from.")
    prompts = build_prompts(edited_concept.strip(), GOOGLE_API_KEY, bg_color=bg_color)
    images = []
    for prompt in prompts:
        img = generate_image(prompt, GOOGLE_API_KEY, size=BRAINSTORM_SIZE)
        images.append(img)
    concept_idx = concepts.index(original_concept) if original_concept in concepts else 0
    save_variants(theme, concept_idx, list(zip(prompts, images)))
    return (
        gr.update(value=images, visible=True),  # gallery
        gr.update(visible=True),                 # finalize_row
        gr.update(visible=False),                # final_group
        prompts,                                 # prompts_state
        None,                                    # selected_variant_state
    )


def select_variant(evt: gr.SelectData):
    # Track which gallery image the user clicked so finalize knows which prompt to use.
    return evt.index


def do_finalize(selected_idx, prompts):
    if selected_idx is None:
        raise gr.Error("Click a variant image to select it first.")
    final_img = finalize_design(prompts[selected_idx], GOOGLE_API_KEY)
    # Save to a temp file — gr.DownloadButton needs a file path, not bytes.
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    final_img.save(tmp.name, "PNG")
    return (
        gr.update(value=final_img, visible=True),  # final_image
        gr.update(visible=True),                    # final_group
        tmp.name,                                   # download_btn
    )


# ── Layout ────────────────────────────────────────────────────────────────────
with gr.Blocks(title="T-Shirt Design Generator", theme=gr.themes.Soft()) as app:

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

            # Step 2
            concept_radio = gr.Radio(label="2 · Pick a concept", choices=[], visible=False)

            # Step 3
            with gr.Group(visible=False) as generate_group:
                gr.Markdown("### 3 · Refine & generate")
                concept_editor = gr.Textbox(label="Edit concept (optional)", lines=2)
                generate_btn = gr.Button(f"🎨 Generate {NUM_VARIANTS} Variants", variant="primary")

            # Step 4
            gallery = gr.Gallery(
                label="4 · Variants — click one to select for finalization",
                visible=False,
                columns=NUM_VARIANTS,
                allow_preview=True,
                show_download_button=True,
            )
            with gr.Row(visible=False) as finalize_row:
                finalize_btn = gr.Button("Finalize selected variant at 4K", variant="primary")

            # Step 5
            with gr.Group(visible=False) as final_group:
                gr.Markdown("### 5 · Final Design (4K)")
                final_image = gr.Image(show_label=False, show_download_button=True)
                download_btn = gr.DownloadButton("⬇ Download Final 4K PNG")

    # ── Events ────────────────────────────────────────────────────────────────
    brainstorm_btn.click(
        brainstorm,
        inputs=[theme_input, bg_color],
        outputs=[concept_radio, concepts_state, theme_state, generate_group,
                 gallery, finalize_row, final_group, prompts_state, selected_variant_state],
    )
    concept_radio.change(
        select_concept,
        inputs=[concept_radio],
        outputs=[generate_group, concept_editor],
    )
    generate_btn.click(
        generate,
        inputs=[concept_editor, bg_color, theme_state, concepts_state, concept_radio],
        outputs=[gallery, finalize_row, final_group, prompts_state, selected_variant_state],
    )
    gallery.select(
        select_variant,
        outputs=[selected_variant_state],
    )
    finalize_btn.click(
        do_finalize,
        inputs=[selected_variant_state, prompts_state],
        outputs=[final_image, final_group, download_btn],
    )


if __name__ == "__main__":
    app.launch()
