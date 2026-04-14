# Design: Update Workflow Diagrams to Reflect Current Logic

**Date:** 2026-04-13  
**Scope:** Replace the single outdated `docs/workflow.mmd` with two accurate Mermaid diagrams.

---

## Problem

`docs/workflow.mmd` was written before the brainstorm phase, dual-point BG removal, and Printify integration were fully implemented. It is missing several phases and contains steps that no longer exist in the code.

Key gaps:
- No auth step (Google OAuth, optional)
- No brainstorm phase (theme → text concepts → concept selection)
- No prompt-building step (concept + templates → N prompts)
- BG removal only shown once; code has it at both variant and final stages
- Archive/timestamped-directory step shown but removed from codebase
- Printify flow is a single box; real flow has shop/blueprint/provider/variant selection

---

## Design

### Files

| Action | File |
|---|---|
| Rename | `docs/workflow.mmd` → `docs/workflow_overview.mmd` |
| Create | `docs/workflow_detail.mmd` |
| Update | `CLAUDE.md` — point Logic Blueprint to both files |

### `workflow_overview.mmd` — High-Level

Five sequential phases, linear, no decision diamonds. Auth shown with dashed border to indicate it is optional (disabled in local dev when `GOOGLE_CLIENT_ID` is unset).

Phases:
1. **Auth** *(optional)* — Google OAuth gate
2. **Ideation** — Enter theme, brainstorm concepts, select one
3. **Variant Generation** — Build prompts, generate N low-res images, optional BG removal
4. **Finalization** — Upscale selected variant to 4K, optional BG removal
5. **Publish** — Printify product configuration and upload

### `workflow_detail.mmd` — Per-Phase Detail

Six subgraphs with all decision points and loop-backs:

1. **Auth subgraph** — page load → OAuth redirect (if enabled) → allowlist check → app home; bypass if no client ID configured
2. **Ideation subgraph** — enter theme → `/brainstorm` → view concepts list → pick/edit concept; loop back to re-brainstorm with new theme
3. **Prompt Building subgraph** — concept + templates/presets → `/generate` builds N prompts → fires variant generation; presets can be loaded/saved at this point
4. **Variant Generation subgraph** — generate N low-res images → view variants → pick one; decision: remove BG (`/remove-bg/variant`, loops back to view), rerender (back to generate), or proceed to finalize
5. **Finalization subgraph** — `/finalize` → 4K returned; decision: remove BG (`/remove-bg/final`, loops back), re-finalize with edited prompt, or proceed to Printify
6. **Printify subgraph** — select shop → search/select blueprint → select provider → select color/size variants → set title/price → optional publish toggle → `/printify/publish` (upload image → create product → optionally publish)

Subgraphs connect sequentially at their exit points. Loop-backs stay within the owning subgraph.

### `CLAUDE.md` update

Replace the single "Logic Blueprint" line:
```
- **Logic Blueprint:** `docs/workflow.mmd`
```
With:
```
- **Logic Blueprint (overview):** `docs/workflow_overview.mmd`
- **Logic Blueprint (detail):** `docs/workflow_detail.mmd`
```

---

## Out of Scope

- No changes to application code
- No changes to session state, routes, or UI
- Presets panel shown as an entry point in detail diagram but its internal save/load/delete loop is not diagrammed (too granular for workflow context)
