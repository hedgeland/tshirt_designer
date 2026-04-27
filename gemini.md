# T-Shirt Designer

A FastAPI + HTMX web app that generates print-on-demand t-shirt designs using Google's Gemini API. The workflow is: enter a theme → brainstorm concepts → select one → generate image variants at low resolution → approve and finalize at 4K.

## Running the app

```bash
uv run python main.py
```

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

## Architecture

### Logic & State
- **Logic Blueprint (overview):** `docs/workflow_overview.mmd`
- **Logic Blueprint (detail):** `docs/workflow_detail.mmd`
- **Rule:** All logic changes must be updated in the Mermaid files before implementation.
- **State Management:** Refer to the Mermaid flow for transition states (e.g., `LR_Choice`, `4K_Choice`).

### Root
- `main.py` — FastAPI routes, SSE streaming, session store, static file serving
- `config.py` — single source of truth for all constants — never define them elsewhere.
- `templates/index.html` — single-page Jinja2 template; Tailwind CSS + Alpine.js
- `static/app.js` — Alpine.js component (`designer()`) and `streamSSE` helper

### src/
- `client.py` — shared `get_client()` singleton; all Gemini calls go through this
- `brainstorm.py` — generates text concepts from a theme
- `prompts.py` — builds image prompts from a selected concept
- `image.py` — calls Gemini image generation API; `generate_image()` for variants, `finalize_image()` for high-res approval
- `output.py` — saves images to disk under `output/<theme>/concept_N/`
- `background.py` — removes a solid background color from an image
- `presets.py` — load/save named prompt template sets
- `prompt_templates.py` — string substitution for prompt templates
- `printify.py` — Printify API client; synchronous, called via `asyncio.to_thread`

### .gemini/ (Gemini CLI configuration)
- `settings.json` — project-specific MCP server configuration (filesystem, google-genai)

## Conventions

- **Senior Engineer Persona:** When using Gemini CLI, expect a senior software engineer partner. Focus on intent and technical rationale.
- **Context Awareness:** Gemini CLI uses this `gemini.md` file as its foundational mandate.
- **Verification First:** All changes should be verified using the local app or unit tests.
- **Planning:** Always produce and approve an implementation plan for major features or architectural changes before modifying source code. Present the approach — files affected, key decisions, trade-offs — and wait for confirmation. Minor fixes and small UI tweaks (single-file, low-risk) can proceed with a simple explanation.
- **Documentation:** All logic changes must be updated in the Mermaid files (`docs/*.mmd`) before implementation.
- **Git Workflow:** We use **GitHub Flow**. All work happens on branches; `main` only receives changes via merged PRs.
    - **Never commit directly to `main`** (except housekeeping).
    - **Commit and push after every individual change** — each discrete edit gets its own commit and immediate `git push -u origin <branch>`. Don't batch.
    - **Use WIP prefixes** for incomplete work: `WIP: <description>`.
- **Branch Naming:**
    | Type | Pattern | Example |
    |---|---|---|
    | Feature | `feat/short-description` | `feat/contrast-assessment` |
    | Bug fix | `fix/short-description` | `fix/variant-upload-crash` |
    | Hotfix | `hotfix/short-description` | `hotfix/publish-500-error` |
- Prefer inline comments. Explain the *why* — the intent, constraint, or non-obvious trade-off — not the *what*.
- All new logic goes in `src/`. Do not add new top-level modules. `main.py` is for routing and orchestration only.
- `config.py` is the single source of truth for all constants.
- Image generation always produces square output (`width == height == size`).
- **Gemini rejects alpha channels** — always flatten transparency to a white background before sending any image to the model (`image.convert("RGBA")` → composite onto white → `.convert("RGB")`).
- **Streaming uses SSE** (`StreamingResponse` + `text/event-stream`). New real-time routes must follow this pattern — not websockets, not polling.

## Development

```bash
# Test
uv run pytest

# Lint
uv run ruff check .

# Format
uv run ruff format .
```

## Specialized Workflows

### Resuming Context
If resuming after an interruption:
1. Check `git log --oneline -10` for recent work.
2. Check `git status` and `git diff HEAD`.
3. Summarize status and next steps for the user.

### Tuning Prompts
When editing `src/prompt_templates.py`:
1. Read `src/prompt_templates.py`, `src/brainstorm.py`, and `src/prompts.py`.
2. Identify which prompt is underperforming and what the desired change is.
3. Suggest targeted edits with trade-off analysis.
4. Verify by running the app before committing.

## Output

Generated images are saved to `output/<theme>/concept_N/variant_N_ARxAR_SIZE.png`. The `output/` directory is gitignored.
