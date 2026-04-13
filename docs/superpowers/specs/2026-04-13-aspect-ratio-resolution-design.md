# Design: Aspect Ratio and Resolution Selectors

**Date:** 2026-04-13  
**Scope:** Add user-selectable aspect ratio and resolution controls for both variant generation and finalization.

---

## Problem

Aspect ratio is hardcoded to `1:1` in both `generate_image` and `finalize_image`. Resolution is hardcoded to `1K` for brainstorm variants and `4K` for finalization. Users cannot adjust either without editing source code. At `1K`, brainstorm generation wastes tokens — `512` is sufficient for concept evaluation.

---

## Design

### `config.py`

Add or update the following constants:

```python
ASPECT_RATIOS = [
    "1:1", "3:2", "2:3", "3:4", "4:3", "4:5", "5:4",
    "9:16", "16:9", "21:9",
    "1:4", "4:1", "1:8", "8:1",   # Gemini 3.1 Flash Image Preview extras
]
DEFAULT_ASPECT_RATIO = "1:1"

BRAINSTORM_SIZES = ["512", "1K", "2K"]
BRAINSTORM_SIZE = "512"    # changed from "1K" — saves tokens during concept evaluation

FINAL_SIZES = ["1K", "2K", "4K"]
FINAL_SIZE = "4K"          # unchanged
```

`BRAINSTORM_SIZE` and `FINAL_SIZE` remain as the default values passed to the frontend; the actual API call values come from user-submitted form fields.

### `src/image.py`

Both functions accept explicit `aspect_ratio` and `size` parameters — no hardcoded values at call sites:

```python
def generate_image(prompt, api_key, size="512", aspect_ratio="1:1") -> Image.Image
def finalize_image(prompt, reference, api_key, size="4K", aspect_ratio="1:1") -> Image.Image
```

### `src/finalize.py`

Thin wrapper gains both parameters and passes them through:

```python
def finalize_design(prompt, reference, api_key, size="4K", aspect_ratio="1:1") -> Image.Image
```

### `main.py`

- `/generate` route: add `variant_size: str = Form(...)` and `aspect_ratio: str = Form(...)`. Pass both to `generate_image`. Remove `size=BRAINSTORM_SIZE` hardcoded call.
- `/finalize` route: add `final_size: str = Form(...)` and `aspect_ratio: str = Form(...)`. Pass both to `finalize_design`. Remove `size=FINAL_SIZE` hardcoded call.
- `/` index route: add `aspect_ratios`, `default_aspect_ratio`, `brainstorm_sizes`, `brainstorm_size`, `final_sizes`, `final_size` to the Jinja2 template context.

### `templates/index.html`

**`app-config` JSON block** gains six new keys:
```json
{
  "aspectRatios": [...],
  "defaultAspectRatio": "1:1",
  "brainstormSizes": ["512", "1K", "2K"],
  "defaultVariantSize": "512",
  "finalSizes": ["1K", "2K", "4K"],
  "defaultFinalSize": "4K"
}
```

**Sidebar** gets three `<select>` dropdowns added below the existing sliders, each bound with `x-model`:
- **Aspect ratio** — all 14 options, default `1:1`
- **Variant resolution** — 512 / 1K / 2K, default `512`
- **Final resolution** — 1K / 2K / 4K, default `4K`

### `static/app.js`

Three new state properties:
```js
aspectRatio: cfg.defaultAspectRatio,
variantSize: cfg.defaultVariantSize,
finalSize: cfg.defaultFinalSize,
```

`doGenerate()` appends to FormData:
```js
fd.append("aspect_ratio", this.aspectRatio);
fd.append("variant_size", this.variantSize);
```

`doFinalize()` appends to FormData:
```js
fd.append("aspect_ratio", this.aspectRatio);
fd.append("final_size", this.finalSize);
```

---

## Out of Scope

- No validation that a given aspect ratio is compatible with a given resolution (no documented restrictions)
- No per-step aspect ratio (same ratio applies to both variant generation and finalization)
- No changes to BG removal, Printify, or any other feature
