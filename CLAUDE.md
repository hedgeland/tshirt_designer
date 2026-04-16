# T-Shirt Designer

A FastAPI + HTMX web app that generates print-on-demand t-shirt designs using Google's Gemini API. The workflow is: enter a theme → brainstorm concepts → select one → generate image variants at low resolution → approve and finalize at 4K.

## Model

All generation — text and image — uses a single model:

```python
MODEL = "gemini-3.1-flash-image-preview"  # config.py
```

This is Google's Gemini 3.1 Flash Image Preview (internally called "Nano Banana 2"). Do not switch to Imagen or any other model. Do not hardcode the model ID anywhere outside `config.py`.

## Resolution contract

| Phase | Constant | Default value | User-selectable values |
|---|---|---|---|
| Brainstorm variants | `BRAINSTORM_SIZE` | `"512"` | `512`, `1K`, `2K` |
| Final approved design | `FINAL_SIZE` | `"1K"` | `1K`, `2K`, `4K` |

`ImageConfig.image_size` accepts `"512"`, `"1K"`, `"2K"`, or `"4K"`. The defaults (`BRAINSTORM_SIZE`, `FINAL_SIZE`) are the starting values; the user can change them via sidebar dropdowns at runtime. Always reference these constants — never hardcode resolution strings.

## Architecture & Logic

- **Logic Blueprint (overview):** `docs/workflow_overview.mmd`
- **Logic Blueprint (detail):** `docs/workflow_detail.mmd`
- **Rule:** All logic changes must be updated in the Mermaid files before implementation.
- **State Management:** Refer to the Mermaid flow for transition states (e.g., `LR_Choice`, `4K_Choice`).

### Files

- `main.py` — FastAPI routes, SSE streaming, session store, static file serving
- `config.py` — single source of truth for all constants
- `templates/index.html` — single-page Jinja2 template; Tailwind CSS + Alpine.js
- `static/app.js` — Alpine.js component (`designer()`) and `streamSSE` helper
- `src/brainstorm.py` — generates text concepts from a theme
- `src/prompts.py` — builds image prompts from a selected concept
- `src/image.py` — calls Gemini image generation API
- `src/finalize.py` — regenerates the approved design at `FINAL_SIZE`
- `src/output.py` — saves images to disk
- `src/background.py` — removes a solid background color from an image
- `src/presets.py` — load/save named prompt template sets
- `.claude/commands/` — project slash commands (`/commit`, `/tune-prompts`)

### Ignore
- `.gemini/` and `gemini.md` — Gemini CLI configuration; not relevant to this codebase

## Conventions

- Prefer inline comments. Explain the *why* — the intent, constraint, or non-obvious trade-off — not the *what* (the code already shows that).
- All new backend logic goes in `src/`. `main.py` is routing + orchestration only.
- `config.py` is the single source of truth for `MODEL`, `BRAINSTORM_SIZE`, `FINAL_SIZE`, `NUM_VARIANTS`, and `OUTPUT_DIR`.
- Image generation always produces square output (`width == height == size`).
