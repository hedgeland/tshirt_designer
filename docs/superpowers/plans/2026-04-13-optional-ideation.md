# Optional Ideation (Direct Generate) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Generate Direct" button to Step 1 that skips brainstorming and uses the theme text directly as the image concept, with a direct mode indicator in Step 2.

**Architecture:** Frontend-only change. `doGenerateDirect()` in `app.js` sets `editedConcept = theme`, flags `directMode = true`, and calls the existing `doGenerate()`. The HTML shows either the concepts list or a direct mode badge in Step 2 depending on `directMode`. No backend changes.

**Tech Stack:** Alpine.js, Tailwind CSS, Jinja2 (index.html), vanilla JS (app.js)

---

## File Map

| Action | Path | What changes |
|---|---|---|
| Modify | `static/app.js` | Add `directMode` state, `doGenerateDirect()`, reset flag in `doBrainstorm()` |
| Modify | `templates/index.html` | Add Generate Direct button in Step 1; add direct mode badge in Step 2 |

---

### Task 1: Add `directMode` state and `doGenerateDirect()` to `app.js`

**Files:**
- Modify: `static/app.js`

- [ ] **Step 1: Add `directMode: false` to the workflow state block**

In `static/app.js`, find this block (around line 54):

```js
        // ── Workflow state ─────────────────────────────────────────────────
        step: 1,               // controls which sections are visible
        theme: "",
        concepts: [],
        selectedConcept: null,
        editedConcept: "",
        variants: [],          // [{url, ts}] — ts is Date.now() for cache-busting
        prompts: [],
        selectedVariant: null,
        finalUrl: null,
        finalTs: 0,
```

Replace with:

```js
        // ── Workflow state ─────────────────────────────────────────────────
        step: 1,               // controls which sections are visible
        theme: "",
        concepts: [],
        selectedConcept: null,
        editedConcept: "",
        variants: [],          // [{url, ts}] — ts is Date.now() for cache-busting
        prompts: [],
        selectedVariant: null,
        finalUrl: null,
        finalTs: 0,
        directMode: false,     // true when user skips brainstorm and generates directly
```

- [ ] **Step 2: Reset `directMode` in `doBrainstorm()`**

In `static/app.js`, find this block inside `doBrainstorm()`:

```js
            // Reset everything below step 1
            this.concepts = [];
            this.selectedConcept = null;
            this.editedConcept = "";
            this.variants = [];
            this.prompts = [];
            this.selectedVariant = null;
            this.finalUrl = null;
            this.step = 1;
```

Replace with:

```js
            // Reset everything below step 1
            this.directMode = false;
            this.concepts = [];
            this.selectedConcept = null;
            this.editedConcept = "";
            this.variants = [];
            this.prompts = [];
            this.selectedVariant = null;
            this.finalUrl = null;
            this.step = 1;
```

- [ ] **Step 3: Add `doGenerateDirect()` after `doBrainstorm()`**

Find the closing brace of `doBrainstorm()`:

```js
            await streamSSE("/brainstorm", fd, {
                status: (e) => { this.loadingMsg = e.message; },
                concepts: (e) => {
                    this.concepts = e.concepts;
                    this.step = 2;
                    this._stopLoading();
                },
                error: (e) => { this._onError(e.message); },
            });
        },

        async doGenerate() {
```

Insert `doGenerateDirect()` between them:

```js
            await streamSSE("/brainstorm", fd, {
                status: (e) => { this.loadingMsg = e.message; },
                concepts: (e) => {
                    this.concepts = e.concepts;
                    this.step = 2;
                    this._stopLoading();
                },
                error: (e) => { this._onError(e.message); },
            });
        },

        async doGenerateDirect() {
            if (this.isLoading || !this.theme.trim()) return;
            // Skip brainstorm — use the theme text as the concept directly
            this.directMode = true;
            this.concepts = [];
            this.selectedConcept = null;
            this.editedConcept = this.theme.trim();
            this.variants = [];
            this.prompts = [];
            this.selectedVariant = null;
            this.finalUrl = null;
            this.step = 2;  // show Step 2 so the direct mode badge is visible
            await this.doGenerate();
        },

        async doGenerate() {
```

- [ ] **Step 4: Verify in browser — app.js side**

Start the app:
```bash
uv run uvicorn main:app --reload
```

Open `http://127.0.0.1:8000` in a browser. Open the browser console and run:

```js
document.querySelector('[x-data]').__x.$data.directMode
```

Expected: `false`

- [ ] **Step 5: Commit**

```bash
git add static/app.js
git commit -m "feat: add directMode state and doGenerateDirect() to app.js"
```

---

### Task 2: Add Generate Direct button to Step 1 in `index.html`

**Files:**
- Modify: `templates/index.html`

- [ ] **Step 1: Add the Generate Direct button**

In `templates/index.html`, find the Step 1 button area:

```html
                        <button @click="doBrainstorm()" :disabled="isLoading"
                            class="flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors">
                            <svg x-show="isLoading && loadingStep === 1" class="animate-spin h-4 w-4 flex-shrink-0" fill="none"
                                viewBox="0 0 24 24">
                                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor"
                                    stroke-width="4" />
                                <path class="opacity-75" fill="currentColor"
                                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                            </svg>
                            🧠 Brainstorm
                        </button>
```

Replace with:

```html
                        <button @click="doBrainstorm()" :disabled="isLoading"
                            class="flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors">
                            <svg x-show="isLoading && loadingStep === 1" class="animate-spin h-4 w-4 flex-shrink-0" fill="none"
                                viewBox="0 0 24 24">
                                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor"
                                    stroke-width="4" />
                                <path class="opacity-75" fill="currentColor"
                                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                            </svg>
                            🧠 Brainstorm
                        </button>
                        <button @click="doGenerateDirect()" :disabled="isLoading"
                            class="flex items-center gap-2 px-4 py-2 bg-slate-500 text-white text-sm font-medium rounded-lg hover:bg-slate-400 disabled:opacity-50 disabled:cursor-not-allowed transition-colors">
                            🎨 Generate Direct
                        </button>
```

Note: Generate Direct uses `bg-slate-500` (secondary style) so Brainstorm remains the visually dominant option.

- [ ] **Step 2: Verify in browser**

Reload the app. Step 1 should show two buttons side by side: "🧠 Brainstorm" (indigo) and "🎨 Generate Direct" (slate). Both should be disabled while loading.

- [ ] **Step 3: Commit**

```bash
git add templates/index.html
git commit -m "feat: add Generate Direct button to Step 1"
```

---

### Task 3: Add direct mode badge to Step 2 in `index.html`

**Files:**
- Modify: `templates/index.html`

- [ ] **Step 1: Add direct mode badge alongside concepts list**

In `templates/index.html`, find the Step 2 section:

```html
                <!-- Step 2: Pick a concept -->
                <section x-show="step >= 2" x-transition
                    class="bg-slate-700 rounded-xl border border-slate-200 shadow-sm p-5">
                    <h2 class="text-xs font-semibold uppercase tracking-wider text-slate-100 mb-3">2 · Pick a concept
                    </h2>
                    <div class="space-y-2">
                        <template x-for="concept in concepts" :key="concept">
                            <label class="flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors"
                                :class="selectedConcept === concept
                                   ? 'border-indigo-400 bg-indigo-50'
                                   : 'bg-slate-100 border-slate-300 hover:bg-white hover:border-slate-400'">
                                <input type="radio" name="concept" :value="concept" @change="selectConcept(concept)"
                                    :checked="selectedConcept === concept"
                                    class="mt-0.5 accent-indigo-600 flex-shrink-0">
                                <span x-text="concept" class="text-sm text-slate-700 leading-snug"></span>
                            </label>
                        </template>
                    </div>
                </section>
```

Replace with:

```html
                <!-- Step 2: Pick a concept -->
                <section x-show="step >= 2" x-transition
                    class="bg-slate-700 rounded-xl border border-slate-200 shadow-sm p-5">
                    <h2 class="text-xs font-semibold uppercase tracking-wider text-slate-100 mb-3">2 · Pick a concept
                    </h2>
                    <!-- Brainstorm mode: radio list of generated concepts -->
                    <div x-show="!directMode" class="space-y-2">
                        <template x-for="concept in concepts" :key="concept">
                            <label class="flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors"
                                :class="selectedConcept === concept
                                   ? 'border-indigo-400 bg-indigo-50'
                                   : 'bg-slate-100 border-slate-300 hover:bg-white hover:border-slate-400'">
                                <input type="radio" name="concept" :value="concept" @change="selectConcept(concept)"
                                    :checked="selectedConcept === concept"
                                    class="mt-0.5 accent-indigo-600 flex-shrink-0">
                                <span x-text="concept" class="text-sm text-slate-700 leading-snug"></span>
                            </label>
                        </template>
                    </div>
                    <!-- Direct mode: indicator badge -->
                    <div x-show="directMode" class="flex items-center gap-2 px-3 py-2 bg-slate-600 border border-slate-500 rounded-lg text-sm text-slate-300">
                        <span>🎨</span>
                        <span>Direct mode — generating from your prompt as-is.</span>
                    </div>
                </section>
```

- [ ] **Step 2: Verify full flow in browser**

Test the direct path:
1. Type a theme in Step 1
2. Click "🎨 Generate Direct"
3. Step 2 should appear showing the direct mode badge (not a concept list)
4. Step 3 should appear with the theme pre-filled in the edit concept textarea
5. Variants should generate and appear in Step 4

Test that brainstorm still works:
1. Click "🧠 Brainstorm" 
2. Step 2 should show the concepts radio list (not the badge)
3. Picking a concept advances to Step 3 as before

Test that switching resets correctly:
1. Click "Generate Direct" — direct badge appears
2. Click "🧠 Brainstorm" — concepts list appears (badge gone)

- [ ] **Step 3: Commit**

```bash
git add templates/index.html
git commit -m "feat: add direct mode indicator to Step 2"
```
