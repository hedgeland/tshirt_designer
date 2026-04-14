# Aspect Ratio and Resolution Selectors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add user-selectable aspect ratio (14 options) and resolution dropdowns for both variant generation and finalization, and change the default variant size from "1K" to "512" to save tokens during concept evaluation.

**Architecture:** Six-file change — constants in `config.py` flow through to `src/image.py` and `src/finalize.py` signatures, then `main.py` reads them from form fields and passes them down. The index route gains six new template context keys. The frontend reads them from the `app-config` JSON block into Alpine state, appends them to FormData in `doGenerate()` and `doFinalize()`, and renders three `<select>` dropdowns in the sidebar.

**Tech Stack:** Python (FastAPI, Pydantic Form), Jinja2, Alpine.js, Tailwind CSS

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Modify | `config.py` | Add ASPECT_RATIOS, DEFAULT_ASPECT_RATIO, BRAINSTORM_SIZES, FINAL_SIZES; change BRAINSTORM_SIZE to "512" |
| Modify | `src/image.py` | Add `aspect_ratio` param to `generate_image` and `finalize_image` |
| Modify | `src/finalize.py` | Add `size` and `aspect_ratio` params to `finalize_design` |
| Modify | `main.py` | Import new constants; wire `variant_size`/`final_size`/`aspect_ratio` form fields in `/generate` and `/finalize`; extend index context |
| Modify | `static/app.js` | Add `aspectRatio`, `variantSize`, `finalSize` state; append to FormData in `doGenerate()`/`doFinalize()` |
| Modify | `templates/index.html` | Extend `app-config` JSON; add three `<select>` dropdowns in sidebar |

---

### Task 1: Update `config.py`

**Files:**
- Modify: `config.py`

- [ ] **Step 1: Add ASPECT_RATIOS, DEFAULT_ASPECT_RATIO, BRAINSTORM_SIZES, FINAL_SIZES; change BRAINSTORM_SIZE**

Find these two lines at the bottom of `config.py`:

```python
# Two-phase resolution: fast previews during exploration, full quality on approval.
# ImageConfig.image_size accepts "1K", "2K", or "4K" — no arbitrary pixel values.
BRAINSTORM_SIZE = "1K"  # smallest available; fast previews during variant exploration
FINAL_SIZE = "4K"       # full quality for approved final design
```

Replace with:

```python
# Aspect ratio options supported by the Gemini 3.1 Flash Image Preview model.
# The last four are model-specific extras not available in standard Imagen.
ASPECT_RATIOS = [
    "1:1", "3:2", "2:3", "3:4", "4:3", "4:5", "5:4",
    "9:16", "16:9", "21:9",
    "1:4", "4:1", "1:8", "8:1",
]
DEFAULT_ASPECT_RATIO = "1:1"

# Two-phase resolution: fast previews during exploration, full quality on approval.
# BRAINSTORM_SIZE / FINAL_SIZE are the defaults passed to the frontend as starting values;
# the actual API call values come from user-submitted form fields.
BRAINSTORM_SIZES = ["512", "1K", "2K"]
BRAINSTORM_SIZE = "512"   # 512px is enough for concept evaluation — saves tokens vs "1K"

FINAL_SIZES = ["1K", "2K", "4K"]
FINAL_SIZE = "4K"         # full quality for approved final design
```

- [ ] **Step 2: Verify the constants load correctly**

```bash
uv run python -c "from config import ASPECT_RATIOS, DEFAULT_ASPECT_RATIO, BRAINSTORM_SIZES, BRAINSTORM_SIZE, FINAL_SIZES, FINAL_SIZE; print(BRAINSTORM_SIZE, FINAL_SIZE, len(ASPECT_RATIOS))"
```

Expected output: `512 4K 14`

- [ ] **Step 3: Commit**

```bash
git add config.py
git commit -m "config: add ASPECT_RATIOS, BRAINSTORM_SIZES, FINAL_SIZES; change BRAINSTORM_SIZE to 512"
```

---

### Task 2: Update `src/image.py`

**Files:**
- Modify: `src/image.py`

- [ ] **Step 1: Add `aspect_ratio` param to `generate_image`**

Find:

```python
def generate_image(prompt: str, api_key: str, size: str = "1K") -> Image.Image:
    client = get_client(api_key)

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],  # text-only response modality is excluded
            image_config=types.ImageConfig(
                image_size=size,    # "1K", "2K", or "4K" — no arbitrary pixel values
                aspect_ratio="1:1", # always square — t-shirt designs center better this way
            ),
        ),
    )

    return _extract_image(response)
```

Replace with:

```python
def generate_image(prompt: str, api_key: str, size: str = "512", aspect_ratio: str = "1:1") -> Image.Image:
    client = get_client(api_key)

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],  # text-only response modality is excluded
            image_config=types.ImageConfig(
                image_size=size,
                aspect_ratio=aspect_ratio,
            ),
        ),
    )

    return _extract_image(response)
```

- [ ] **Step 2: Add `aspect_ratio` param to `finalize_image`**

Find:

```python
def finalize_image(prompt: str, reference: Image.Image, api_key: str, size: str = "4K") -> Image.Image:
```

Replace with:

```python
def finalize_image(prompt: str, reference: Image.Image, api_key: str, size: str = "4K", aspect_ratio: str = "1:1") -> Image.Image:
```

Then find inside `finalize_image`:

```python
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(image_size=size, aspect_ratio="1:1"),
        ),
```

Replace with:

```python
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(image_size=size, aspect_ratio=aspect_ratio),
        ),
```

- [ ] **Step 3: Verify signatures are importable**

```bash
uv run python -c "from src.image import generate_image, finalize_image; import inspect; print(inspect.signature(generate_image)); print(inspect.signature(finalize_image))"
```

Expected:
```
(prompt: str, api_key: str, size: str = '512', aspect_ratio: str = '1:1') -> PIL.Image.Image
(prompt: str, reference: PIL.Image.Image, api_key: str, size: str = '4K', aspect_ratio: str = '1:1') -> PIL.Image.Image
```

- [ ] **Step 4: Commit**

```bash
git add src/image.py
git commit -m "feat: add aspect_ratio param to generate_image and finalize_image"
```

---

### Task 3: Update `src/finalize.py`

**Files:**
- Modify: `src/finalize.py`

- [ ] **Step 1: Add `size` and `aspect_ratio` params and update the import**

Replace the entire file content:

```python
"""Thin wrapper that re-generates the approved variant at the requested resolution."""

from PIL import Image

from config import FINAL_SIZE
from src.image import finalize_image


def finalize_design(
    prompt: str,
    reference: Image.Image,
    api_key: str,
    size: str = FINAL_SIZE,
    aspect_ratio: str = "1:1",
) -> Image.Image:
    return finalize_image(prompt, reference, api_key, size=size, aspect_ratio=aspect_ratio)
```

- [ ] **Step 2: Verify**

```bash
uv run python -c "from src.finalize import finalize_design; import inspect; print(inspect.signature(finalize_design))"
```

Expected:
```
(prompt: str, reference: PIL.Image.Image, api_key: str, size: str = '4K', aspect_ratio: str = '1:1') -> PIL.Image.Image
```

- [ ] **Step 3: Commit**

```bash
git add src/finalize.py
git commit -m "feat: add size and aspect_ratio params to finalize_design"
```

---

### Task 4: Update `main.py`

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Add new constants to the config import block**

Find:

```python
from config import (
    ALLOWED_EMAILS,
    BG_REMOVAL_TOLERANCE,
    BRAINSTORM_SIZE,
    DEFAULT_BG_COLOR,
    DEFAULT_BG_COLOR_NAME,
    EDGE_DECONTAMINATE,
    EDGE_ERODE_PX,
    GOOGLE_API_KEY,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    HTTPS_ONLY,
    MAX_COLORS,
    NUM_VARIANTS,
    OUTPUT_DIR,
    PRINTIFY_SHOP_ID,
    PRINTIFY_TOKEN,
    SECRET_KEY,
)
```

Replace with:

```python
from config import (
    ALLOWED_EMAILS,
    ASPECT_RATIOS,
    BG_REMOVAL_TOLERANCE,
    BRAINSTORM_SIZE,
    BRAINSTORM_SIZES,
    DEFAULT_ASPECT_RATIO,
    DEFAULT_BG_COLOR,
    DEFAULT_BG_COLOR_NAME,
    EDGE_DECONTAMINATE,
    EDGE_ERODE_PX,
    FINAL_SIZE,
    FINAL_SIZES,
    GOOGLE_API_KEY,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    HTTPS_ONLY,
    MAX_COLORS,
    NUM_VARIANTS,
    OUTPUT_DIR,
    PRINTIFY_SHOP_ID,
    PRINTIFY_TOKEN,
    SECRET_KEY,
)
```

- [ ] **Step 2: Extend the index route context**

Find:

```python
    return templates.TemplateResponse(request, "index.html", {
        "num_variants": NUM_VARIANTS,
        "bg_color": DEFAULT_BG_COLOR,
        "bg_color_name": DEFAULT_BG_COLOR_NAME,
        "bg_tolerance": BG_REMOVAL_TOLERANCE,
        "edge_erode": EDGE_ERODE_PX,
        "decontaminate": EDGE_DECONTAMINATE,
        "max_colors": MAX_COLORS,
        "output_dir": OUTPUT_DIR,
        "preset_names": presets.all_preset_names(),
        "builtin_name": presets.BUILTIN_NAME,
        "concepts_template": builtin["concepts_prompt"],
        "variants_template": builtin["variants_prompt"],
        "style_template": builtin["style_suffix"],
        "printify_enabled": bool(PRINTIFY_TOKEN),
        "printify_shop_id": PRINTIFY_SHOP_ID,
    })
```

Replace with:

```python
    return templates.TemplateResponse(request, "index.html", {
        "num_variants": NUM_VARIANTS,
        "bg_color": DEFAULT_BG_COLOR,
        "bg_color_name": DEFAULT_BG_COLOR_NAME,
        "bg_tolerance": BG_REMOVAL_TOLERANCE,
        "edge_erode": EDGE_ERODE_PX,
        "decontaminate": EDGE_DECONTAMINATE,
        "max_colors": MAX_COLORS,
        "output_dir": OUTPUT_DIR,
        "preset_names": presets.all_preset_names(),
        "builtin_name": presets.BUILTIN_NAME,
        "concepts_template": builtin["concepts_prompt"],
        "variants_template": builtin["variants_prompt"],
        "style_template": builtin["style_suffix"],
        "printify_enabled": bool(PRINTIFY_TOKEN),
        "printify_shop_id": PRINTIFY_SHOP_ID,
        "aspect_ratios": ASPECT_RATIOS,
        "default_aspect_ratio": DEFAULT_ASPECT_RATIO,
        "brainstorm_sizes": BRAINSTORM_SIZES,
        "brainstorm_size": BRAINSTORM_SIZE,
        "final_sizes": FINAL_SIZES,
        "final_size": FINAL_SIZE,
    })
```

- [ ] **Step 3: Add `variant_size` and `aspect_ratio` to the `/generate` route**

Find:

```python
@app.post("/generate")
async def generate(
    session_id: str = Form(...),
    concept: str = Form(...),           # edited concept text → goes to build_prompts
    original_concept: str = Form(""),   # radio selection → used to find concept_idx
    bg_color: str = Form(...),
    num_variants: int = Form(...),
    max_colors: int = Form(...),
    variants_template: str = Form(...),
    style_template: str = Form(...),
):
```

Replace with:

```python
@app.post("/generate")
async def generate(
    session_id: str = Form(...),
    concept: str = Form(...),           # edited concept text → goes to build_prompts
    original_concept: str = Form(""),   # radio selection → used to find concept_idx
    bg_color: str = Form(...),
    num_variants: int = Form(...),
    max_colors: int = Form(...),
    variants_template: str = Form(...),
    style_template: str = Form(...),
    variant_size: str = Form(BRAINSTORM_SIZE),
    aspect_ratio: str = Form(DEFAULT_ASPECT_RATIO),
):
```

- [ ] **Step 4: Pass `variant_size` and `aspect_ratio` into `generate_image`**

Find:

```python
                img = await asyncio.to_thread(generate_image, prompt, GOOGLE_API_KEY, size=BRAINSTORM_SIZE)
```

Replace with:

```python
                img = await asyncio.to_thread(generate_image, prompt, GOOGLE_API_KEY, size=variant_size, aspect_ratio=aspect_ratio)
```

- [ ] **Step 5: Add `final_size` and `aspect_ratio` to the `/finalize` route**

Find:

```python
@app.post("/finalize")
async def finalize(
    session_id: str = Form(...),
    selected_idx: int = Form(...),
    bg_color: str = Form(...),
    bg_tolerance: int = Form(...),
    edge_erode: int = Form(...),
    decontaminate: int = Form(...),
):
```

Replace with:

```python
@app.post("/finalize")
async def finalize(
    session_id: str = Form(...),
    selected_idx: int = Form(...),
    bg_color: str = Form(...),
    bg_tolerance: int = Form(...),
    edge_erode: int = Form(...),
    decontaminate: int = Form(...),
    final_size: str = Form(FINAL_SIZE),
    aspect_ratio: str = Form(DEFAULT_ASPECT_RATIO),
):
```

- [ ] **Step 6: Pass `final_size` and `aspect_ratio` into `finalize_design`**

Find:

```python
            final_img = await asyncio.to_thread(
                finalize_design, prompts[idx], variant, GOOGLE_API_KEY
            )
```

Replace with:

```python
            final_img = await asyncio.to_thread(
                finalize_design, prompts[idx], variant, GOOGLE_API_KEY,
                size=final_size, aspect_ratio=aspect_ratio,
            )
```

- [ ] **Step 7: Write a test verifying the index page now includes the new config keys**

In `tests/test_routes.py`, add after `test_index_contains_config_block`:

```python
def test_index_config_contains_aspect_ratio_keys():
    import json
    response = client.get("/")
    assert response.status_code == 200
    # Extract the app-config JSON block from the rendered HTML
    html = response.text
    start = html.index('id="app-config">') + len('id="app-config">')
    end = html.index("</script>", start)
    cfg = json.loads(html[start:end])
    assert "aspectRatios" in cfg
    assert "defaultAspectRatio" in cfg
    assert cfg["defaultAspectRatio"] == "1:1"
    assert "brainstormSizes" in cfg
    assert "defaultVariantSize" in cfg
    assert cfg["defaultVariantSize"] == "512"
    assert "finalSizes" in cfg
    assert "defaultFinalSize" in cfg
    assert cfg["defaultFinalSize"] == "4K"
```

- [ ] **Step 8: Run the new test to make sure it fails (app-config not updated yet)**

```bash
uv run pytest tests/test_routes.py::test_index_config_contains_aspect_ratio_keys -v
```

Expected: FAIL — `KeyError` or `AssertionError` because the template doesn't have those keys yet.

- [ ] **Step 9: Verify the app imports cleanly**

```bash
uv run python -c "import main; print('OK')"
```

Expected: `OK`

- [ ] **Step 10: Commit**

```bash
git add main.py tests/test_routes.py
git commit -m "feat: wire aspect_ratio and size form fields in /generate and /finalize; extend index context"
```

---

### Task 5: Update `static/app.js`

**Files:**
- Modify: `static/app.js`

- [ ] **Step 1: Add three state properties to the Settings block**

Find:

```javascript
        // ── Settings ───────────────────────────────────────────────────────
        bgColor: cfg.bgColor,
        numVariants: cfg.numVariants,
        bgTolerance: cfg.bgTolerance,
        edgeErode: cfg.edgeErode,
        decontaminate: cfg.decontaminate,
        maxColors: cfg.maxColors,
```

Replace with:

```javascript
        // ── Settings ───────────────────────────────────────────────────────
        bgColor: cfg.bgColor,
        numVariants: cfg.numVariants,
        bgTolerance: cfg.bgTolerance,
        edgeErode: cfg.edgeErode,
        decontaminate: cfg.decontaminate,
        maxColors: cfg.maxColors,
        aspectRatio: cfg.defaultAspectRatio,
        variantSize: cfg.defaultVariantSize,
        finalSize: cfg.defaultFinalSize,
```

- [ ] **Step 2: Append `aspect_ratio` and `variant_size` to `doGenerate()` FormData**

Find inside `doGenerate()`:

```javascript
            fd.append("variants_template", this.variantsTemplate);
            fd.append("style_template", this.styleTemplate);

            await streamSSE("/generate", fd, {
```

Replace with:

```javascript
            fd.append("variants_template", this.variantsTemplate);
            fd.append("style_template", this.styleTemplate);
            fd.append("aspect_ratio", this.aspectRatio);
            fd.append("variant_size", this.variantSize);

            await streamSSE("/generate", fd, {
```

- [ ] **Step 3: Append `aspect_ratio` and `final_size` to `doFinalize()` FormData**

Find inside `doFinalize()`:

```javascript
            const fd = this._bgFormData();
            fd.append("selected_idx", idx);

            await streamSSE("/finalize", fd, {
```

Replace with:

```javascript
            const fd = this._bgFormData();
            fd.append("selected_idx", idx);
            fd.append("aspect_ratio", this.aspectRatio);
            fd.append("final_size", this.finalSize);

            await streamSSE("/finalize", fd, {
```

- [ ] **Step 4: Commit**

```bash
git add static/app.js
git commit -m "feat: add aspectRatio/variantSize/finalSize state and pass to generate/finalize"
```

---

### Task 6: Update `templates/index.html`

**Files:**
- Modify: `templates/index.html`

- [ ] **Step 1: Extend the `app-config` JSON block with six new keys**

Find:

```html
            "printifyEnabled": {{ printify_enabled | tojson }},
            "printifyShopId": {{ printify_shop_id | tojson }}
        }
```

Replace with:

```html
            "printifyEnabled": {{ printify_enabled | tojson }},
            "printifyShopId": {{ printify_shop_id | tojson }},
            "aspectRatios": {{ aspect_ratios | tojson }},
            "defaultAspectRatio": {{ default_aspect_ratio | tojson }},
            "brainstormSizes": {{ brainstorm_sizes | tojson }},
            "defaultVariantSize": {{ brainstorm_size | tojson }},
            "finalSizes": {{ final_sizes | tojson }},
            "defaultFinalSize": {{ final_size | tojson }}
        }
```

- [ ] **Step 2: Run the failing test — it should now pass**

```bash
uv run pytest tests/test_routes.py::test_index_config_contains_aspect_ratio_keys -v
```

Expected: PASS

- [ ] **Step 3: Add three dropdown `<select>` elements in the sidebar**

Find in `index.html` (after the sliders `</div>` and before the Output `<p>`):

```html
                    </div>

                    <p class="text-xs text-slate-100">Output: <code
```

Replace with:

```html
                    </div>

                    <!-- Generation options -->
                    <div class="space-y-3">
                        <div>
                            <label class="text-xs font-medium block mb-1.5">Aspect ratio</label>
                            <select x-model="aspectRatio"
                                class="w-full text-xs border border-slate-500 rounded-md px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-400">
                                <template x-for="r in cfg.aspectRatios" :key="r">
                                    <option :value="r" x-text="r"></option>
                                </template>
                            </select>
                        </div>
                        <div>
                            <label class="text-xs font-medium block mb-1.5">Variant resolution</label>
                            <select x-model="variantSize"
                                class="w-full text-xs border border-slate-500 rounded-md px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-400">
                                <template x-for="s in cfg.brainstormSizes" :key="s">
                                    <option :value="s" x-text="s"></option>
                                </template>
                            </select>
                        </div>
                        <div>
                            <label class="text-xs font-medium block mb-1.5">Final resolution</label>
                            <select x-model="finalSize"
                                class="w-full text-xs border border-slate-500 rounded-md px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-400">
                                <template x-for="s in cfg.finalSizes" :key="s">
                                    <option :value="s" x-text="s"></option>
                                </template>
                            </select>
                        </div>
                    </div>

                    <p class="text-xs text-slate-100">Output: <code
```

- [ ] **Step 4: Run full test suite**

```bash
uv run pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 5: Start the app and verify the dropdowns in the browser**

```bash
uv run uvicorn main:app --reload
```

Open `http://127.0.0.1:8000`. In the sidebar, verify:
- "Aspect ratio" dropdown shows 14 options, defaulting to `1:1`
- "Variant resolution" dropdown shows `512 / 1K / 2K`, defaulting to `512`
- "Final resolution" dropdown shows `1K / 2K / 4K`, defaulting to `4K`

Change aspect ratio to `16:9` and generate — confirm the generated images are landscape. Change back to `1:1` and confirm square output.

- [ ] **Step 6: Commit**

```bash
git add templates/index.html
git commit -m "feat: add aspect ratio and resolution dropdowns to sidebar"
```
