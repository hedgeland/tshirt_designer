# T-Shirt Designer

A FastAPI + HTMX web app that generates print-on-demand t-shirt designs using Google's Gemini API. The workflow is: enter a theme → brainstorm concepts → select one → generate image variants at low resolution → approve and finalize at 4K.

## Running the app

```bash
uv run uvicorn main:app --reload
```

Then open `http://127.0.0.1:8000`.

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

## Architecture & Logic
- **Logic Blueprint (overview):** `docs/workflow_overview.mmd`
- **Logic Blueprint (detail):** `docs/workflow_detail.mmd`
- **Rule:** All logic changes must be updated in the Mermaid file before implementation.
- **State Management:** Refer to the Mermaid flow for transition states (e.g., `LR_Choice`, `4K_Choice`).

### Root
- `main.py` — FastAPI app: routes, SSE streaming, session store, static file serving
- `config.py` — single source of truth for all constants

### templates/
- `index.html` — single-page Jinja2 template; Tailwind CSS (Play CDN) + Alpine.js for UI state

### static/
- `app.js` — Alpine.js component (`designer()`) and `streamSSE` helper for reading streamed responses

### src/
- `brainstorm.py` — generates text concepts from a theme
- `prompts.py` — builds image prompts from a selected concept
- `image.py` — calls Gemini image generation API
- `finalize.py` — regenerates the approved design at `FINAL_SIZE`
- `output.py` — saves images to disk
- `background.py` — removes a solid background color from an image
- `presets.py` — load/save named prompt template sets

### .claude/commands/ (Claude Code slash commands)
- `commit.md` — `/commit` stages, commits, and pushes all changes

### Ignore
- `.gemini/` and `gemini.md` — Gemini CLI configuration; not relevant to this codebase

## Streaming

Long-running operations (brainstorm, generate, finalize, background removal) are streamed as Server-Sent Events via FastAPI's `StreamingResponse`. Each event is a JSON line: `data: {"type": "...", ...}\n\n`. The client reads them with `fetch` + `ReadableStream` (see `streamSSE` in `static/app.js`).

## Session state

In-memory dict keyed by a UUID the browser generates on page load (`crypto.randomUUID()`). Stores PIL Images, prompts, and file paths for the current workflow. Single-user local tool — replace with Redis or a DB for multi-user.

## Conventions

- Prefer inline comments. Explain the *why* — the intent, constraint, or non-obvious trade-off — not the *what* (the code already shows that).
- All new backend logic goes in `src/`. `main.py` is routing + orchestration only.
- `config.py` is the single source of truth for `MODEL`, `BRAINSTORM_SIZE`, `FINAL_SIZE`, `NUM_VARIANTS`, and `OUTPUT_DIR`.
- Image generation always produces square output (`width == height == size`).

## Output

Generated images are saved to `output/<theme>/concept_N/variant_N.png`. The `output/` directory is gitignored.
