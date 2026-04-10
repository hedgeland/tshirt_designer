import os
import streamlit as st
from config import NUM_VARIANTS, OUTPUT_DIR, BRAINSTORM_SIZE, FINAL_SIZE, GOOGLE_API_KEY
from agents.brainstorm_agent import generate_concepts
from agents.prompt_agent import build_prompts
from agents.image_agent import generate_image, remove_background
from agents.finalize_agent import finalize_design
from skills.output import save_variants, image_to_bytes

st.set_page_config(
    page_title="T-Shirt Design Generator",
    page_icon="👕",
    layout="wide",
)

# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚙️ Settings")
    api_key = st.text_input(
        "Google API Key",
        value=GOOGLE_API_KEY,
        type="password",
        help="Get your key at aistudio.google.com",
    )
    remove_bg = st.checkbox("Remove background (transparent PNG)", value=True)
    st.caption(
        "Note: Background removal downloads a ~170 MB model on first run."
    )
    st.divider()
    st.caption(f"Output saved to `{OUTPUT_DIR}/`")

# ── Session state ─────────────────────────────────────────────────────────────
# All mutable UI state lives here so Streamlit reruns don't reset it.
defaults = {
    "concepts": [],
    "theme": "",
    "selected_concept": "",
    "variants": [],      # list of (prompt, PIL.Image) at BRAINSTORM_SIZE
    "final_image": None, # PIL.Image at FINAL_SIZE, set when user finalizes
    "final_prompt": "",  # prompt used to generate final_image
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── Header ────────────────────────────────────────────────────────────────────
st.title("👕 T-Shirt Design Generator")
st.caption("Brainstorm → Select → Generate · Powered by Gemini 3.1 Flash Image Preview")
st.divider()

# ── Step 1: Theme ─────────────────────────────────────────────────────────────
st.subheader("1 · Enter a theme")
col_input, col_btn = st.columns([4, 1])
with col_input:
    theme = st.text_input(
        "Theme",
        placeholder="e.g. vintage motorcycles, funny cats, 90s hip-hop...",
        label_visibility="collapsed",
    )
with col_btn:
    brainstorm_clicked = st.button(
        "🧠 Brainstorm", use_container_width=True, type="primary"
    )

if brainstorm_clicked:
    if not api_key:
        st.error("Add your Google API Key in the sidebar first.")
    elif not theme.strip():
        st.error("Enter a theme to brainstorm.")
    else:
        with st.spinner("Generating concepts with Gemini..."):
            try:
                st.session_state.concepts = generate_concepts(theme.strip(), api_key)
                st.session_state.theme = theme.strip()
                # Reset downstream state so stale variants don't show for a new theme.
                st.session_state.selected_concept = ""
                st.session_state.variants = []
            except Exception as e:
                st.error(f"Brainstorm failed: {e}")

# ── Step 2: Concept selection ─────────────────────────────────────────────────
if st.session_state.concepts:
    st.divider()
    st.subheader("2 · Pick a concept")

    for i, concept in enumerate(st.session_state.concepts):
        is_selected = st.session_state.selected_concept == concept
        with st.container(border=True):
            c1, c2 = st.columns([6, 1])
            with c1:
                st.markdown(f"**{i + 1}.** {concept}")
            with c2:
                label = "✓ Selected" if is_selected else "Select"
                if st.button(label, key=f"sel_{i}", use_container_width=True):
                    st.session_state.selected_concept = concept
                    # Clear variants and final image so the new concept starts fresh.
                    st.session_state.variants = []
                    st.session_state.final_image = None
                    st.session_state.final_prompt = ""
                    st.rerun()

# ── Step 3: Edit + Generate ───────────────────────────────────────────────────
if st.session_state.selected_concept:
    st.divider()
    st.subheader("3 · Refine & generate")

    # Let the user tweak the concept text before it goes to the prompt builder.
    edited = st.text_area(
        "Edit concept (optional):",
        value=st.session_state.selected_concept,
        height=80,
    )

    if st.button(
        f"🎨 Generate {NUM_VARIANTS} Variants", type="primary", use_container_width=True
    ):
        if not api_key:
            st.error("Add your Google API Key in the sidebar first.")
        else:
            variants = []
            progress = st.progress(0, text="Building prompts with Gemini...")
            status = st.empty()  # reusable status line — avoids stacking info messages

            try:
                prompts = build_prompts(edited.strip(), api_key)

                for i, prompt in enumerate(prompts):
                    status.info(f"Generating variant {i + 1} of {NUM_VARIANTS} at {BRAINSTORM_SIZE}...")
                    img = generate_image(prompt, api_key, size=BRAINSTORM_SIZE)  # low-res for speed

                    if remove_bg:
                        status.info(
                            f"Removing background for variant {i + 1}..."
                            + (" (downloading model on first run)" if i == 0 else "")
                        )
                        try:
                            img = remove_background(img)
                        except Exception as e:
                            st.warning(f"Background removal failed for variant {i + 1}: {e}")

                    variants.append((prompt, img))
                    progress.progress((i + 1) / NUM_VARIANTS)

                status.empty()
                progress.empty()
                st.session_state.variants = variants

                # Find the concept index so the save path reflects the right concept folder.
                concept_idx = (
                    st.session_state.concepts.index(st.session_state.selected_concept)
                    if st.session_state.selected_concept in st.session_state.concepts
                    else 0
                )
                saved = save_variants(st.session_state.theme, concept_idx, variants)
                st.success(f"Saved to: {os.path.dirname(saved[0])}/")

            except Exception as e:
                status.empty()
                progress.empty()
                st.error(f"Generation failed: {e}")

# ── Step 4: Results ───────────────────────────────────────────────────────────
if st.session_state.variants:
    st.divider()
    st.subheader("4 · Results")
    st.caption(f"Preview variants at {BRAINSTORM_SIZE} — select one to finalize at {FINAL_SIZE}.")

    cols = st.columns(NUM_VARIANTS)
    for i, (prompt, img) in enumerate(st.session_state.variants):
        with cols[i]:
            st.image(img, caption=f"Variant {i + 1}", use_container_width=True)
            st.download_button(
                label=f"⬇ Download Variant {i + 1}",
                data=image_to_bytes(img),
                file_name=f"variant_{i + 1}.png",
                mime="image/png",
                use_container_width=True,
                key=f"dl_{i}",
            )
            if st.button(
                f"Finalize Variant {i + 1} at 4K",
                key=f"finalize_{i}",
                use_container_width=True,
            ):
                if not api_key:
                    st.error("Add your Google API Key in the sidebar first.")
                else:
                    with st.spinner(f"Regenerating at {FINAL_SIZE}×{FINAL_SIZE} (4K)..."):
                        try:
                            final_img = finalize_design(prompt, api_key)
                            if remove_bg:
                                try:
                                    final_img = remove_background(final_img)
                                except Exception as e:
                                    st.warning(f"Background removal failed: {e}")
                            st.session_state.final_image = final_img
                            st.session_state.final_prompt = prompt
                            st.rerun()  # scroll down to show Step 5
                        except Exception as e:
                            st.error(f"Finalization failed: {e}")
            with st.expander("View prompt"):
                st.caption(prompt)

# ── Step 5: Final 4K design ───────────────────────────────────────────────────
if st.session_state.final_image is not None:
    st.divider()
    st.subheader("5 · Final Design (4K)")
    st.image(st.session_state.final_image, use_container_width=True)
    st.download_button(
        label="⬇ Download Final 4K Design",
        data=image_to_bytes(st.session_state.final_image),
        file_name="final_design_4k.png",
        mime="image/png",
        use_container_width=True,
    )
    with st.expander("View prompt"):
        st.caption(st.session_state.final_prompt)
