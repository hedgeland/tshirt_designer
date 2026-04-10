# T-Shirt Designer

A Gradio app that generates print-on-demand t-shirt designs using Google's Gemini API. The workflow is: enter a theme → brainstorm concepts → select one → generate image variants at low resolution → approve and finalize at 4K.

## Running the app

```bash
uv run python app.py
```

## Model

All generation — text and image — uses a single model:

```python
MODEL = "gemini-3.1-flash-image-preview"  # config.py
```

This is Google's Gemini 3.1 Flash Image Preview (internally called "Nano Banana 2"). Do not switch to Imagen or any other model. Do not hardcode the model ID anywhere outside `config.py`.

## Resolution contract

| Phase | Constant | Value |
|---|---|---|
| Brainstorm variants | `BRAINSTORM_SIZE` | `"1K"` (smallest available) |
| Final approved design | `FINAL_SIZE` | `"4K"` |

`ImageConfig.image_size` accepts `"1K"`, `"2K"`, or `"4K"` — no arbitrary pixel values. Always pass `size=BRAINSTORM_SIZE` during variant generation and `size=FINAL_SIZE` during finalization. Never hardcode these strings.

## Architecture

### Root
- `app.py` — Streamlit UI, orchestrates all phases
- `config.py` — single source of truth for all constants

### agents/ (one concern per agent)
- `brainstorm_agent.py` — generates text concepts from a theme
- `prompt_agent.py` — builds image prompts from a selected concept
- `image_agent.py` — calls Gemini image generation API; also handles background removal
- `finalize_agent.py` — regenerates the approved design at `FINAL_SIZE`

### skills/ (reusable utilities shared across agents)
- `output.py` — saves images to disk; `image_to_bytes` helper for downloads
- `theme_analyzer.py` — parse and enrich user theme input
- `style_mixer.py` — generate stylistic variation across prompts
- `quality_checker.py` — validate output before saving

### .claude/agents/ (Claude Code sub-agent definitions)
Markdown files that define specialized Claude sub-agents for development tasks within this project (brainstorming, prompt engineering, image generation).

### .claude/commands/ (Claude Code slash commands)
Markdown files that define project-specific slash commands: `/brainstorm`, `/generate`, `/finalize`.

## Conventions

- Prefer inline comments. Explain the *why* — the intent, constraint, or non-obvious trade-off — not the *what* (the code already shows that).
- All new logic goes in `agents/` or `skills/`. Do not add new top-level modules.
- `config.py` is the single source of truth for `MODEL`, `BRAINSTORM_SIZE`, `FINAL_SIZE`, `NUM_VARIANTS`, and `OUTPUT_DIR`.
- Image generation always produces square output (`width == height == size`).
- Background removal (`rembg`) is optional and user-controlled via the sidebar toggle. It should never be forced.

## Output

Generated images are saved to `output/<theme>/concept_N/variant_N.png`. The `output/` directory is gitignored.
