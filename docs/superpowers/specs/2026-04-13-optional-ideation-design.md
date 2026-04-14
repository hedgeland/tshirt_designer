# Design: Optional Ideation (Direct Generate)

**Date:** 2026-04-13  
**Scope:** Frontend-only change to make the brainstorm step optional.

---

## Problem

Users must always brainstorm concepts before generating images. If they already know what they want, the brainstorm step is friction.

---

## Design

### User-facing behavior

**Step 1** gains a second button alongside "🧠 Brainstorm":
- `🎨 Generate Direct` — skips concept selection and generates immediately using the theme text as the concept

**Step 2** displays one of two states:
- **Brainstorm mode** (existing): the radio list of generated concepts
- **Direct mode** (new): a small indicator badge — "Direct mode — generating from your prompt as-is."

Default behavior is unchanged — brainstorm still works exactly as before.

### Changes

#### `templates/index.html`

- Add `🎨 Generate Direct` button in Step 1 next to the Brainstorm button. Same disabled-while-loading behavior.
- In Step 2, wrap the concepts list in `x-show="!directMode"` and add a sibling div `x-show="directMode"` with the direct mode badge.

#### `static/app.js`

- Add `directMode: false` to the Alpine component state.
- Add `doGenerateDirect()` method:
  - Guards: `if (this.isLoading || !this.theme.trim()) return`
  - Sets `this.directMode = true`
  - Sets `this.editedConcept = this.theme.trim()`
  - Sets `this.selectedConcept = null`
  - Resets `concepts`, `variants`, `prompts`, `selectedVariant`, `finalUrl`
  - Sets `this.step = Math.max(this.step, 2)` — advances to step 2 so the direct mode badge is visible
  - Calls `this.doGenerate()` — reuses existing generate logic unchanged
- In `doBrainstorm()`: set `this.directMode = false` (reset on new brainstorm)

#### No backend changes

The `/generate` endpoint already accepts any concept string. `doGenerateDirect()` passes `theme` as the concept — no new routes, no session changes.

---

## Out of Scope

- No changes to the brainstorm flow
- No persistence of direct/brainstorm mode preference
- No changes to the sidebar, settings, presets, finalize, or Printify flows
