# T-Shirt Designer

A Gradio app that generates print-on-demand t-shirt designs using Google's Gemini AI. Enter a theme, brainstorm concepts, pick one, tweak it, and generate image variants — then approve and finalize at 4K resolution.

## Workflow

1. **Enter a theme** (e.g. "retro space cats") and click **Brainstorm Concepts**
2. **Select a concept** from the generated list and optionally edit the text
3. **Generate variants** — produces low-resolution previews for fast iteration
4. **Click a variant** in the gallery to select it
5. **Finalize** — regenerates the approved design at full 4K resolution and saves it to `output/`

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (package manager)
- A Google AI Studio API key with access to Gemini 3.1 Flash Image Preview

Get a free API key at [aistudio.google.com](https://aistudio.google.com).

## Quickstart

```bash
git clone https://github.com/hedgeland/tshirt_designer.git
cd tshirt-designer

cp .env.example .env
# Edit .env and add your GOOGLE_API_KEY

uv run python app.py
```

Then open the URL printed in the terminal (default: `http://127.0.0.1:7860`).

## Configuration

All tunable constants live in `config.py`:

| Constant | Default | Description |
|---|---|---|
| `NUM_VARIANTS` | `1` | Default number of variants (overridden by UI slider) |
| `BRAINSTORM_SIZE` | `"1K"` | Resolution for preview variants |
| `FINAL_SIZE` | `"4K"` | Resolution for the approved final design |
| `MAX_COLORS` | `6` | Max distinct colors in generated images |
| `BG_REMOVAL_TOLERANCE` | `50` | Aggressiveness of background removal (0–255) |
| `OUTPUT_DIR` | `"output"` | Root folder for saved PNGs |

## Output

Finalized images are saved to `output/<theme>/concept_N/variant_N.png`.

## Development

```bash
# Lint
uv run ruff check .

# Format
uv run ruff format .
```

## License

MIT — see [LICENSE](LICENSE).
