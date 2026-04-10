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
- `app.py` — Gradio UI, orchestrates all phases
- `config.py` — single source of truth for all constants

### src/
- `brainstorm.py` — generates text concepts from a theme
- `prompts.py` — builds image prompts from a selected concept
- `image.py` — calls Gemini image generation API
- `finalize.py` — regenerates the approved design at `FINAL_SIZE`
- `output.py` — saves images to disk

### .claude/commands/ (Claude Code slash commands)
- `commit.md` — `/commit` stages, commits, and pushes all changes

## Conventions

- Prefer inline comments. Explain the *why* — the intent, constraint, or non-obvious trade-off — not the *what* (the code already shows that).
- All new logic goes in `src/`. Do not add new top-level modules.
- `config.py` is the single source of truth for `MODEL`, `BRAINSTORM_SIZE`, `FINAL_SIZE`, `NUM_VARIANTS`, and `OUTPUT_DIR`.
- Image generation always produces square output (`width == height == size`).

## Output

Generated images are saved to `output/<theme>/concept_N/variant_N.png`. The `output/` directory is gitignored.
