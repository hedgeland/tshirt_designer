# T-Shirt Designer

A FastAPI + HTMX web app that generates print-on-demand t-shirt designs using Google's Gemini AI. Enter a theme, brainstorm concepts, pick one, tweak it, and generate image variants — then approve and finalize at 4K resolution.

## Workflow

1. **Enter a theme** (e.g. "retro space cats") and click **Brainstorm**
2. **Select a concept** from the generated list and optionally edit the text
3. **Generate variants** — produces low-resolution previews for fast iteration
4. **Click a variant** in the gallery to select it
5. **Remove background** (optional) — floods-fills and removes the solid background color
6. **Finalize** — regenerates the approved design at full 4K resolution and saves it to `output/`

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (package manager)
- A Google AI Studio API key with access to Gemini 3.1 Flash Image Preview

Get a free API key at [aistudio.google.com](https://aistudio.google.com).

## Quickstart

```bash
git clone https://github.com/hedgeland/tshirt_designer.git
cd tshirt_designer

cp .env.example .env
# Edit .env and add your GOOGLE_API_KEY

uv run uvicorn main:app --reload
```

Then open `http://127.0.0.1:8000`.

## Authentication (optional)

By default the app runs without any login — suitable for local use. To restrict access when deployed, add Google OAuth credentials to `.env`:

```
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
ALLOWED_EMAILS=you@gmail.com
SECRET_KEY=a-long-random-string
HTTPS_ONLY=false   # set to true in production
```

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → **APIs & Services → Credentials**
2. Create an **OAuth 2.0 Client ID** (Web application)
3. Add `http://localhost:8000/auth/callback` as an authorized redirect URI (and your production URL if deploying)

Leave `GOOGLE_CLIENT_ID` unset to disable auth entirely.

## Prompt Presets

Open the **Prompt Presets** panel in the sidebar to edit the three AI prompt templates and save named presets. Presets persist across restarts in `prompt_presets.json` (not committed to git).

| Template | Controls |
|---|---|
| Brainstorm prompt | How concepts are generated from your theme |
| Variants prompt | How each concept expands into stylistic variations |
| Style suffix | Image constraints appended to every generation prompt |

Variables in `{curly_braces}` are filled in at runtime — don't remove them. The **Default (built-in)** preset cannot be overwritten or deleted; save under a new name to customize it.

## Configuration

All tunable constants live in `config.py`:

| Constant | Default | Description |
|---|---|---|
| `NUM_VARIANTS` | `1` | Default number of variants (overridden by UI slider) |
| `DEFAULT_BG_COLOR` | `"#FF00FF"` | Default background color for generation and removal |
| `BRAINSTORM_SIZE` | `"1K"` | Resolution for preview variants |
| `FINAL_SIZE` | `"4K"` | Resolution for the approved final design |
| `MAX_COLORS` | `6` | Max distinct colors in generated images (1–8) |
| `BG_REMOVAL_TOLERANCE` | `50` | Aggressiveness of background color removal (0–255) |
| `EDGE_ERODE_PX` | `1` | Pixels to shrink alpha mask inward after removal |
| `EDGE_DECONTAMINATE` | `50` | Background color spill reduction on edges (0–100) |
| `MAX_PRESETS` | `20` | Max number of saved user presets |
| `OUTPUT_DIR` | `"output"` | Root folder for saved PNGs |

## Output

Generated images are saved to `output/<theme>/concept_N/variant_N.png`. The `output/` directory is gitignored.

## Development

```bash
# Lint
uv run ruff check .

# Format
uv run ruff format .
```

## License

MIT — see [LICENSE](LICENSE).
