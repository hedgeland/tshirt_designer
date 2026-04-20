// ── Alpine global stores ──────────────────────────────────────────────────────
// Declare stores before Alpine processes the DOM so $store.* refs in templates
// never see undefined on first render. Values are updated by designer.init().
document.addEventListener('alpine:init', () => {
    Alpine.store('columnCount', 1); // updated to actual count after session restore
    Alpine.store('minColumns', 1);  // updated after session restore; drives close-button disable
    // Tracks whether the output browser is open — combined with activeColIdx in templates
    // to highlight the targeted column header without a separate colIdx in the store.
    Alpine.store('browserOpen', false);
    // Tracks which column was last clicked — drives the active-column visual
    Alpine.store('activeColIdx', 0);
    Alpine.store('presetsOpen', false);
    // Tracks which image URLs were downloaded this browser session; cleared on close.
    Alpine.store('downloadedUrls', {});
});


// ── Column cycling keyboard shortcuts ────────────────────────────────────────
// Ctrl+] → next column, Ctrl+[ → previous column. Wraps around.
// Guard: skip when focus is inside an input/textarea so the keys still work normally there.
document.addEventListener('keydown', (e) => {
    if (!e.ctrlKey) return;
    if (e.key !== ']' && e.key !== '[') return;
    e.preventDefault();
    const count = Alpine.store('columnCount');
    if (count < 2) return; // nothing to cycle when there's only one column
    const current = Alpine.store('activeColIdx');
    const next = e.key === ']'
        ? (current + 1) % count          // forward, wrap around
        : (current - 1 + count) % count; // backward, wrap around
    Alpine.store('activeColIdx', next);
    // Scroll the newly active column header into view
    const header = document.querySelector(`[data-col-idx="${next}"]`);
    header?.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' });
});

// ── SSE helper ────────────────────────────────────────────────────────────────
// Streams a POST request as Server-Sent Events and dispatches each parsed event
// to the matching handler in `handlers`. Uses fetch + ReadableStream rather than
// EventSource because EventSource only supports GET requests.
async function streamSSE(url, formData, handlers) {
    let response;
    try {
        response = await fetch(url, { method: "POST", body: formData });
    } catch (err) {
        if (handlers.error) handlers.error({ message: `Network error: ${err.message}` });
        return;
    }

    if (!response.ok) {
        if (handlers.error) handlers.error({ message: `Request failed: HTTP ${response.status}` });
        return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        // SSE events are separated by blank lines; split on newlines and process complete lines
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? ""; // keep the last (possibly incomplete) line
        for (const line of lines) {
            if (!line.startsWith("data: ")) continue;
            try {
                const event = JSON.parse(line.slice(6));
                if (handlers[event.type]) handlers[event.type](event);
            } catch {
                // malformed JSON — skip
            }
        }
    }
}


// ── Per-column Alpine component ───────────────────────────────────────────────
// Each column rendered in the UI gets its own columnDesigner instance.
// colIdx scopes all API calls to the correct server-side column state.
function columnDesigner(colIdx, sessionId, cfg, initialState = {}) {
    return {
        colIdx,      // index into the session's columns array
        sessionId,   // shared UUID from parent designer()

        // ── Workflow state ─────────────────────────────────────────────────
        step: 1,               // controls which sections are visible
        theme: "",
        concepts: [],
        selectedConcept: null,
        editedConcept: "",
        variants: [],          // [{url, origUrl, noBgUrl, ts}]
        prompts: [],
        selectedVariant: null,
        directMode: false,     // true when user skips brainstorm and generates directly

        // ── UI state ───────────────────────────────────────────────────────
        isLoading: false,
        loadingMsg: "",
        loadingStep: 0,   // which step triggered the current load — controls status bar placement
        error: "",
        promptLog: "",
        showPromptLog: false,
        showPresets: false,

        // ── Settings ───────────────────────────────────────────────────────
        bgColor: cfg.bgColor,
        numVariants: cfg.numVariants,
        bgTolerance: cfg.bgTolerance,
        edgeErode: cfg.edgeErode,
        decontaminate: cfg.decontaminate,
        maxColors: cfg.maxColors,
        aspectRatio: cfg.defaultAspectRatio,
        variantSize: cfg.defaultVariantSize,
        generatedVariantSize: null,        // set when variants arrive; drives step-4 title badge
        generatedVariantAspectRatio: null, // aspect ratio used when variants were generated
        renderSize: cfg.defaultFinalSize,  // size picker for rendering new combos / publishing
        editSize: "512",                   // default edit size — fast for iteration; user can bump up before submitting
        editAspectRatio: cfg.defaultAspectRatio, // aspect ratio for edits — independent of render combo settings

        // ── Unified render state ───────────────────────────────────────────
        // variantCombos[i] = [{size, aspectRatio, url}] for all renders of variant i.
        // Populated by /generate and updated by /render.  The file's existence on disk
        // IS the cache — rendering an existing combo returns instantly from the server.
        variantCombos: {},
        activeComboUrl: null,  // URL of the selected combo (for download / BG removal / Printify)
        activeComboSize: "",   // resolution of selected combo (for Printify upscale gate)

        // ── Prompt templates ───────────────────────────────────────────────
        // Populated from cfg defaults; replaced when user applies a preset from the global panel.
        conceptsTemplate: cfg.conceptsTemplate,
        variantsTemplate: cfg.variantsTemplate,
        styleTemplate: cfg.styleTemplate,

        // ── Edit state ────────────────────────────────────────────────────
        editPrompt: "",         // user's change description for iterative editing
        editModeActive: false,  // true after the first variant edit completes; switches gallery to single-image + iterations layout

        // ── Reference image state ─────────────────────────────────────────
        refImageUrl: null,      // preview URL when a reference image is set; null otherwise
        referenceMode: "style", // "style" = borrow aesthetic only; "copy" = replicate composition

        // ── Settings panel state ──────────────────────────────────────────
        settingsOpen: false,    // controls collapsible per-column settings panel

        // ── Load-from-browser state ───────────────────────────────────────
        loadedImageRes: null,   // {width, height} when variants came from the output browser

        // ── Drag state (Printify placement preview) ───────────────────────
        pDrag: null,            // null when idle; { startX, startY, startPX, startPY } while dragging

        // ── Printify state ─────────────────────────────────────────────────
        showPrintify: false,
        printifyBusy: false,
        printifyStatus: "",
        printifyError: "",
        printifyDone: null,

        pShops: [],
        pShopId: cfg.printifyShopId || "",

        pAllBlueprints: [],         // full catalog (fetched once per column)
        pFilteredBlueprints: [],    // filtered by search term
        pBlueprintSearch: "",
        pBlueprint: null,

        pProviders: [],
        pProviderId: "",

        pAllVariants: [],           // raw variant list from API
        pColors: [],                // unique color names
        pSizes: [],                 // unique size names (in natural order)
        pSelectedColors: [],
        pSelectedSizes: [],

        // Print area dimensions — extracted from the variant placeholder data
        pPrintWidth: 0,
        pPrintHeight: 0,

        pXPx: 0,                    // left edge of design in print-area pixels
        pYPx: 0,                    // top edge of image in print-area pixels
        pTopAllowance: 10,          // px gap from print-area top for "Align to Top" preset
        pIsTopPreset: true,         // true when X/Y match the "Align to Top" formula
        pScale: 1.0,                // fraction of print area width the design occupies
        pContentTop: 0,             // fraction of image height above first visible pixel

        pOverrideMinRes: false,     // dev override: skip the resolution gate when testing

        pTitle: "",
        pDescription: "",
        pPrice: "29.99",

        // ── Lifecycle ──────────────────────────────────────────────────────
        init() {
            // Restore server-persisted state from a prior session (e.g. after page refresh).
            // The server returns serializable fields only — PIL images are gone and must be
            // re-generated, but text state and saved file paths survive the reload.
            this._restoreInitialState(initialState);

            // Focus the theme input only for the first column to avoid competing focus
            if (this.colIdx === 0) {
                this.$nextTick(() => this.$refs.themeInput?.focus());
            }

            // Bound drag handlers — stored so removeEventListener can match by identity
            this._boundDragMove = this._onDragMove.bind(this);
            this._boundDragUp = this._onDragUp.bind(this);

            // Keep "Align to Top" preset in sync when scale or allowance changes
            this.$watch('pScale', () => {
                if (this.pIsTopPreset && this.pPrintWidth) this.applyTopPreset();
            });
            this.$watch('pTopAllowance', () => {
                if (this.pIsTopPreset && this.pPrintWidth) this.applyTopPreset();
            });
            // pContentTop arrives async after modal open; re-calibrate preset if still active
            this.$watch('pContentTop', () => {
                if (this.pIsTopPreset && this.pPrintWidth) this.applyTopPreset();
            });

            // Receive "load image as variant" events dispatched by the output browser
            window.addEventListener('col-load-image', (e) => {
                if (e.detail.colIdx === this.colIdx) this._applyLoadedImage(e.detail);
            });

            // Receive "set reference image" events dispatched by the output browser
            window.addEventListener('col-set-reference', (e) => {
                if (e.detail.colIdx === this.colIdx) this._doSetReferenceFromBrowser(e.detail.imageUrl);
            });

            // Receive "apply preset" events dispatched by the global presets panel
            window.addEventListener('col-apply-preset', (e) => {
                if (e.detail.colIdx === this.colIdx) this._applyPreset(e.detail);
            });

            // When switching variants, restore or clear the active combo from variantCombos.
            // Combos are sorted largest-first so index 0 is the highest-quality render.
            this.$watch('selectedVariant', (val) => {
                const combos = this.variantCombos[val ?? 0] || [];
                if (combos.length > 0) {
                    this.activeComboUrl = combos[0].url;
                    this.activeComboSize = combos[0].size;
                } else {
                    this.activeComboUrl = null;
                    this.activeComboSize = "";
                }
            });

        },

        // ── Computed ───────────────────────────────────────────────────────
        get generateBtnLabel() {
            const n = this.numVariants;
            return `Generate ${n} ${n === 1 ? "Variant" : "Variants"}`;
        },

        get selectedVariantObj() {
            return this.variants[this.selectedVariant ?? 0];
        },

        // Finds the combo object whose url or origUrl matches the active combo URL.
        get activeComboObj() {
            if (!this.activeComboUrl) return null;
            const combos = this.variantCombos[this.selectedVariant ?? 0] || [];
            return combos.find(c => c.url === this.activeComboUrl || c.origUrl === this.activeComboUrl) ?? null;
        },

        get pDesignPx() {
            return this.pScale * this.pPrintWidth;
        },

        get pNeedsUpscale() {
            return (cfg.sizePx[this.activeComboSize] ?? 0) < cfg.sizePx[cfg.printifyMinSize];
        },

        get pImageOutOfBounds() {
            if (!this.pPrintWidth || !this.pPrintHeight) return false;
            const designPx = this.pDesignPx;
            return this.pXPx < 0 || this.pXPx + designPx > this.pPrintWidth
                || this.pYPx < 0 || this.pYPx + designPx > this.pPrintHeight;
        },

        get selectedVariantCount() {
            // Count variants whose color AND size are both selected
            return this.pAllVariants.filter(v => {
                const color = v.options?.color ?? "";
                const size = v.options?.size ?? "";
                return this.pSelectedColors.includes(color) && this.pSelectedSizes.includes(size);
            }).length;
        },

        // ── Internal helpers ───────────────────────────────────────────────
        _startLoading(msg) {
            this.isLoading = true;
            this.loadingMsg = msg;
            this.error = "";
        },

        _stopLoading() {
            this.isLoading = false;
            this.loadingMsg = "";
            // loadingStep intentionally NOT cleared here — errors need it to know
            // which section to display in. Clear only when the error is dismissed.
        },

        _onError(msg) {
            this._stopLoading();
            this.error = msg;
        },

        dismissError() {
            this.error = "";
            this.loadingStep = 0;
        },

        // Format a Unix-ms timestamp as a short locale date+time string for edit history links.
        formatEditTs(ts) {
            return new Date(ts).toLocaleString([], {
                month: 'short', day: 'numeric',
                hour: '2-digit', minute: '2-digit', second: '2-digit',
            });
        },

        // Apply server-persisted column state on init.  Determines which workflow
        // step to show based on what data is available (final > variants > concepts > theme).
        _restoreInitialState(state) {
            if (!state || typeof state !== 'object') return;

            // Restore text state unconditionally
            if (state.theme)    this.theme    = state.theme;
            if (state.concepts) this.concepts = state.concepts;
            if (state.prompts)  this.prompts  = state.prompts;

            // Restore variant thumbnails from saved paths (PIL images are gone after reload;
            // the static files are still on disk so the URLs remain valid).
            if (Array.isArray(state.image_paths) && state.image_paths.length) {
                this.variants = state.image_paths.map(p => ({
                    url: "/" + p.replace(/^\//, ""), origUrl: "/" + p.replace(/^\//, ""), noBgUrl: null, ts: 0,
                }));
                if (state.selected_idx != null) this.selectedVariant = state.selected_idx;
                if (state.variant_size) this.generatedVariantSize = state.variant_size;
            }

            // Advance the step indicator to match the most progressed phase (4 is the max)
            if (this.variants.length) {
                this.step = 4;
            } else if (this.concepts.length) {
                // If a concept was previously selected, pre-populate editedConcept
                if (state.selected_idx != null && this.concepts[state.selected_idx]) {
                    this.selectedConcept = state.selected_idx;
                    this.editedConcept   = this.concepts[state.selected_idx];
                    this.step = 3;
                } else {
                    this.step = 2;
                }
            } else if (state.theme) {
                this.step = 1; // theme present but no concepts yet — stay at step 1
            }
        },

        // Builds FormData with session/column identity and BG settings.
        // Every streaming workflow route expects these base fields.
        _bgFormData() {
            const fd = new FormData();
            fd.append("session_id", this.sessionId);
            fd.append("column_id", this.colIdx);
            fd.append("bg_color", this.bgColor);
            fd.append("bg_tolerance", this.bgTolerance);
            fd.append("edge_erode", this.edgeErode);
            fd.append("decontaminate", this.decontaminate);
            return fd;
        },

        // Apply an image loaded from the output browser — resets workflow to variant-only step
        async _applyLoadedImage({ url, width, height, displayTheme }) {
            const hasWork = this.concepts.length || this.variants.length;
            if (hasWork && !confirm("Load this image as a variant? Your current column session will be cleared.")) return;

            const res = await fetch("/session/load-image", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    session_id: this.sessionId,
                    column_id: this.colIdx,
                    image_url: url,
                    display_theme: displayTheme,
                }),
            });
            if (!res.ok) { alert(`Failed to load image (${res.status})`); return; }
            const data = await res.json();
            if (data.error) { alert(data.error); return; }

            // Reset workflow state to variant-only
            this.concepts = [];
            this.selectedConcept = null;
            this.editedConcept = "";
            this.prompts = [];
            this.variantCombos = {};
            this.activeComboUrl = null;
            this.activeComboSize = "";
            this.error = "";
            this.editModeActive = false;
            this.theme = displayTheme;
            this.variants = [{ url, origUrl: url, noBgUrl: null, ts: Date.now() }];
            this.selectedVariant = 0;
            this.loadedImageRes = { width, height };
            this.step = 4;
            this.$nextTick(() => this.$refs.step4?.scrollIntoView({ behavior: "smooth", block: "start" }));
        },

        // Set a reference image picked from the output browser (dispatched via custom event)
        async _doSetReferenceFromBrowser(imageUrl) {
            const fd = new FormData();
            fd.append("session_id", this.sessionId);
            fd.append("column_id", this.colIdx);
            fd.append("reference_path", imageUrl);
            const res = await fetch("/session/set-reference-image", { method: "POST", body: fd });
            if (!res.ok) { alert(`Failed to set reference image (${res.status})`); return; }
            // Bust cache with timestamp so the thumbnail refreshes if the ref changes
            this.refImageUrl = `/session/reference-image-preview?session_id=${encodeURIComponent(this.sessionId)}&column_id=${this.colIdx}&ts=${Date.now()}`;
        },

        async setReferenceFromFile(file) {
            if (!file) return;
            const fd = new FormData();
            fd.append("session_id", this.sessionId);
            fd.append("column_id", this.colIdx);
            fd.append("reference_file", file);
            const res = await fetch("/session/set-reference-image", { method: "POST", body: fd });
            if (!res.ok) { alert(`Failed to set reference image (${res.status})`); return; }
            this.refImageUrl = `/session/reference-image-preview?session_id=${encodeURIComponent(this.sessionId)}&column_id=${this.colIdx}&ts=${Date.now()}`;
        },

        // Dispatch browser-open events to the parent designer() which owns the browser
        // Ask the parent designer() to close this column.
        // Guard here prevents the event from firing at all while loading — the button
        // should be disabled too, but this is a belt-and-suspenders check.
        requestClose() {
            if (this.isLoading) return;
            window.dispatchEvent(new CustomEvent('designer-close-column', { detail: { colIdx: this.colIdx } }));
        },

        openMyBrowser() {
            window.dispatchEvent(new CustomEvent('designer-open-browser', { detail: { colIdx: this.colIdx } }));
        },

        openMyPresets() {
            window.dispatchEvent(new CustomEvent('designer-open-presets'));
        },

        openMyBrowserForRef() {
            window.dispatchEvent(new CustomEvent('designer-open-browser-for-ref', { detail: { colIdx: this.colIdx } }));
        },

        async clearReference() {
            await fetch("/session/clear-reference-image", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ session_id: this.sessionId, column_id: this.colIdx }),
            });
            this.refImageUrl = null;
        },

        // ── Workflow actions ───────────────────────────────────────────────
        selectConcept(concept) {
            this.selectedConcept = concept;
            this.editedConcept = concept;
            this.step = Math.max(this.step, 3);
        },

        async doBrainstorm() {
            if (this.isLoading || !this.theme.trim()) return;
            this.loadingStep = 1;
            this._startLoading("Generating concepts...");

            // Reset everything below step 1
            this.directMode = false;
            this.concepts = [];
            this.selectedConcept = null;
            this.editedConcept = "";
            this.variants = [];
            this.prompts = [];
            this.selectedVariant = null;
            this.variantCombos = {};
            this.activeComboUrl = null;
            this.activeComboSize = "";
            this.editModeActive = false;
            this.loadedImageRes = null;
            this.step = 1;

            const fd = new FormData();
            fd.append("session_id", this.sessionId);
            fd.append("column_id", this.colIdx);
            fd.append("theme", this.theme);
            fd.append("concepts_template", this.conceptsTemplate);

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

        doGenerateDirect() {
            if (this.isLoading || !this.theme.trim()) return;
            // Skip brainstorm — use the theme text as the concept directly
            this.directMode = true;
            this.concepts = [];
            this.selectedConcept = null;
            this.editedConcept = this.theme.trim();
            this.variants = [];
            this.prompts = [];
            this.selectedVariant = null;
            this.variantCombos = {};
            this.activeComboUrl = null;
            this.activeComboSize = "";
            this.editModeActive = false;
            this.loadedImageRes = null;
            // Advance to step 3 so the aspect ratio/resolution selectors and Generate button are visible,
            // but don't auto-generate — let the user configure first
            this.step = 3;
        },

        async doGenerate() {
            if (this.isLoading || !this.editedConcept.trim()) return;
            this.loadingStep = 3;
            this._startLoading("Building prompts...");

            this.variants = [];
            this.prompts = [];
            this.selectedVariant = null;
            this.variantCombos = {};
            this.activeComboUrl = null;
            this.activeComboSize = "";
            this.editModeActive = false;
            this.loadedImageRes = null;
            this.step = Math.max(this.step, 3);

            const fd = new FormData();
            fd.append("session_id", this.sessionId);
            fd.append("column_id", this.colIdx);
            fd.append("concept", this.editedConcept);
            fd.append("original_concept", this.selectedConcept ?? this.editedConcept);
            fd.append("bg_color", this.bgColor);
            fd.append("num_variants", this.numVariants);
            fd.append("max_colors", this.maxColors);
            fd.append("variants_template", this.variantsTemplate);
            fd.append("style_template", this.styleTemplate);
            fd.append("aspect_ratio", this.aspectRatio);
            fd.append("variant_size", this.variantSize);
            fd.append("reference_mode", this.referenceMode);
            fd.append("direct_mode", this.directMode);

            await streamSSE("/generate", fd, {
                status: (e) => { this.loadingMsg = e.message; },
                prompts: (e) => {
                    this.prompts = e.prompts;
                    this.promptLog = e.prompts
                        .map((p, i) => `── Variant ${i + 1} ──\n${p}`)
                        .join("\n\n");
                },
                variants: (e) => {
                    const ts = Date.now();
                    this.variants = e.urls.map((url) => ({ url, origUrl: url, noBgUrl: null, ts }));
                    this.selectedVariant = e.urls.length === 1 ? 0 : null;
                    this.generatedVariantSize = this.variantSize;
                    this.generatedVariantAspectRatio = this.aspectRatio;
                    // Store initial combo lists from backend (one combo per variant after /generate)
                    if (e.combo_lists) {
                        const combos = {};
                        e.combo_lists.forEach((list, i) => { combos[i] = list; });
                        this.variantCombos = combos;
                    }
                    // The selectedVariant watcher fires asynchronously after Alpine's reactive
                    // flush, at which point variantCombos is already populated — so it would
                    // auto-select the first combo and show the image twice. $nextTick runs after
                    // the watcher, giving us a chance to clear the auto-selection.
                    this.$nextTick(() => {
                        this.activeComboUrl = null;
                        this.activeComboSize = "";
                    });
                    this.step = 4;
                    this._stopLoading();
                },
                error: (e) => { this._onError(e.message); },
            });
        },

        async doRender(sizeOverride = null) {
            if (this.isLoading) return;
            const size = sizeOverride ?? this.renderSize;
            const idx = this.selectedVariant ?? 0;
            // Skip if this combo already exists
            const existing = this.variantCombos[idx] || [];
            if (existing.some(c => c.size === size && c.aspectRatio === this.aspectRatio)) return;

            this.loadingStep = 4;
            this._startLoading(`Rendering variant ${idx + 1} at ${size} (${this.aspectRatio})...`);

            const fd = new FormData();
            fd.append("session_id", this.sessionId);
            fd.append("column_id", this.colIdx);
            fd.append("variant_idx", idx);
            fd.append("aspect_ratio", this.aspectRatio);
            fd.append("size", size);

            await streamSSE("/render", fd, {
                status: (e) => { this.loadingMsg = e.message; },
                render: (e) => {
                    const updated = { ...this.variantCombos };
                    updated[e.variant_idx] = e.combos;
                    this.variantCombos = updated;
                    // Auto-select the newly rendered combo as active
                    this.activeComboUrl = e.url;
                    this.activeComboSize = size;
                    this._stopLoading();
                },
                error: (e) => { this._onError(e.message); },
            });
        },

        async doEditVariant() {
            if (this.isLoading || !this.editPrompt.trim()) return;

            // Use the selected variant's thumbnail URL as the source image for editing.
            // origUrl is the un-bg-removed version, which avoids sending transparent pixels
            // that would just be flattened to white on the server.
            const sourceVariant = this.variants[this.selectedVariant ?? 0];
            if (!sourceVariant) return;
            const sourceUrl = sourceVariant.origUrl || sourceVariant.url;

            this.loadingStep = 4;
            this._startLoading(`Applying edits at ${this.editSize} (${this.editAspectRatio})...`);

            const fd = new FormData();
            fd.append("session_id", this.sessionId);
            fd.append("column_id", this.colIdx);
            fd.append("source_url", sourceUrl);
            fd.append("edit_prompt", this.editPrompt.trim());
            fd.append("size", this.editSize);
            fd.append("aspect_ratio", this.editAspectRatio);

            await streamSSE("/stream/edit", fd, {
                status: (e) => { this.loadingMsg = e.message; },
                edit_variant: (e) => {
                    const ts = Date.now();
                    // Append the new variant and auto-select it so the user can immediately
                    // see the result and continue editing or render at a higher resolution.
                    // parentIdx records the direct parent; rootIdx traces to the non-iteration
                    // ancestor so the full edit chain is always visible when the original is selected.
                    const parentIdx = this.selectedVariant ?? 0;
                    const parentVariant = this.variants[parentIdx];
                    const rootIdx = parentVariant?.isIteration ? parentVariant.rootIdx : parentIdx;
                    this.variants.push({ url: e.url, origUrl: e.url, noBgUrl: null, ts, parentIdx, rootIdx, isIteration: true });
                    const newIdx = e.index;
                    const updated = { ...this.variantCombos };
                    updated[newIdx] = e.combos || [];
                    this.variantCombos = updated;
                    this.selectedVariant = newIdx;
                    this.editPrompt = "";  // clear so the user types a fresh instruction next time
                    this.editModeActive = true;

                    this._stopLoading();
                },
                error: (e) => { this._onError(e.message); },
            });
        },

        async selectCombo(combo) {
            // Activate an already-rendered combo (instant — no API call)
            this.activeComboUrl = combo.url;
            this.activeComboSize = combo.size;
            this.aspectRatio = combo.aspectRatio;
        },

        async doRenderForPrintify() {
            // Called from the Printify modal when the active combo is below the minimum resolution.
            if (this.printifyBusy) return;
            this.printifyBusy = true;
            this.printifyError = "";
            this.printifyStatus = `Rendering at ${cfg.printifyMinSize}…`;

            const idx = this.selectedVariant ?? 0;
            const fd = new FormData();
            fd.append("session_id", this.sessionId);
            fd.append("column_id", this.colIdx);
            fd.append("variant_idx", idx);
            fd.append("aspect_ratio", this.aspectRatio);
            fd.append("size", cfg.printifyMinSize);

            await streamSSE("/render", fd, {
                status: (e) => { this.printifyStatus = e.message; },
                render: (e) => {
                    const updated = { ...this.variantCombos };
                    updated[e.variant_idx] = e.combos;
                    this.variantCombos = updated;
                    this.activeComboUrl = e.url;
                    this.activeComboSize = cfg.printifyMinSize;
                    this.printifyStatus = "";
                },
                error: (e) => { this.printifyError = e.message; },
            });

            this.printifyBusy = false;
        },

        async doRemoveVariantBg() {
            if (this.isLoading || !this.activeComboUrl) return;
            const variantIdx = this.selectedVariant ?? 0;
            const combo = this.activeComboObj;
            if (!combo) return;

            // If we already processed this combo before (noBgUrl cached), just swap URLs.
            if (combo.noBgUrl) {
                const updated = { ...this.variantCombos };
                updated[variantIdx] = (updated[variantIdx] || []).map(c =>
                    // origUrl may have been cleared by a prior undo; fall back to activeComboUrl
                    c === combo ? { ...c, url: c.noBgUrl, origUrl: c.origUrl ?? this.activeComboUrl } : c
                );
                this.variantCombos = updated;
                this.activeComboUrl = combo.noBgUrl;
                return;
            }

            this.loadingStep = 4;
            this._startLoading("Removing background...");

            const fd = this._bgFormData();
            fd.append("combo_url", this.activeComboUrl);

            await streamSSE("/remove-bg/combo", fd, {
                status: (e) => { this.loadingMsg = e.message; },
                combo_bg_removed: (e) => {
                    // Store origUrl so undo works, and noBgUrl so re-apply is instant.
                    const updated = { ...this.variantCombos };
                    updated[variantIdx] = (updated[variantIdx] || []).map(c =>
                        c === combo
                            ? { ...c, origUrl: this.activeComboUrl, noBgUrl: e.url, url: e.url }
                            : c
                    );
                    this.variantCombos = updated;
                    this.activeComboUrl = e.url;
                    this._stopLoading();
                },
                error: (e) => { this._onError(e.message); },
            });
        },

        async doRestoreVariantBg() {
            if (this.isLoading || !this.activeComboUrl) return;
            const variantIdx = this.selectedVariant ?? 0;
            const combo = this.activeComboObj;
            if (!combo?.origUrl) return;

            // Instant swap — clear origUrl so the button state resets to "Remove BG".
            const updated = { ...this.variantCombos };
            updated[variantIdx] = (updated[variantIdx] || []).map(c =>
                c === combo ? { ...c, url: c.origUrl, origUrl: undefined } : c
            );
            this.variantCombos = updated;
            this.activeComboUrl = combo.origUrl;
        },

        // Apply a preset dispatched from the global presets panel
        _applyPreset({ conceptsTemplate, variantsTemplate, styleTemplate }) {
            this.conceptsTemplate = conceptsTemplate;
            this.variantsTemplate = variantsTemplate;
            this.styleTemplate = styleTemplate;
        },

        // ── Printify actions ───────────────────────────────────────────────

        async openPrintify() {
            // Reset publish result each time the modal opens
            this.printifyError = "";
            this.printifyStatus = "";
            this.printifyDone = null;
            this.pBlueprint = null;
            this.pProviders = [];
            this.pProviderId = "";
            this.pAllVariants = [];
            this.pColors = [];
            this.pSizes = [];
            this.pSelectedColors = [];
            this.pSelectedSizes = [];
            this.pContentTop = 0;
            this.pXPx = 0;
            this.pYPx = this.pTopAllowance;  // pTopAllowance intentionally not reset — persists
            this.pIsTopPreset = true;
            this.pTitle = this.theme || "Custom T-Shirt";
            this.showPrintify = true;

            // Fetch content bounds from the active combo image alpha channel for Y placement
            if (this.activeComboUrl) {
                fetch(`/analysis/final?session_id=${encodeURIComponent(this.sessionId)}&column_id=${this.colIdx}`)
                    .then(r => r.json())
                    .then(data => { this.pContentTop = data.content_top ?? 0; })
                    .catch(() => { });
            }

            // Load shops (skip if shop ID already configured server-side)
            if (!cfg.printifyShopId && this.pShops.length === 0) {
                const res = await fetch("/printify/shops");
                const data = await res.json();
                if (data.error) { this.printifyError = data.error; return; }
                this.pShops = data;
                // Auto-select by configured name then fall back to selecting the only shop
                const preferredName = (cfg.printifyShopName || "").toLowerCase();
                const match = preferredName && data.find(s => s.title.toLowerCase() === preferredName);
                if (match) this.pShopId = String(match.id);
                else if (data.length === 1) this.pShopId = String(data[0].id);
            }

            // Load blueprint catalog (fetched once per column; subsequent opens reuse cache)
            if (this.pAllBlueprints.length === 0) {
                this.printifyStatus = "Loading catalog…";
                const res = await fetch("/printify/blueprints");
                const data = await res.json();
                this.printifyStatus = "";
                if (data.error) { this.printifyError = data.error; return; }
                this.pAllBlueprints = data;
            }

            this.pBlueprintSearch = "shirt";
            this.filterBlueprints();
        },

        filterBlueprints() {
            const q = this.pBlueprintSearch.trim().toLowerCase();
            const pool = q ? (() => {
                const terms = q.split(/\s+/);
                return this.pAllBlueprints.filter(bp =>
                    terms.every(t => bp.title.toLowerCase().includes(t) || (bp.brand || "").toLowerCase().includes(t))
                );
            })() : this.pAllBlueprints;
            // Sort by blueprint ID ascending — lower IDs are older, more established products
            this.pFilteredBlueprints = pool.slice().sort((a, b) => a.id - b.id).slice(0, 50);
        },

        async selectBlueprint(bp) {
            this.pBlueprint = bp;
            this.pProviders = [];
            this.pProviderId = "";
            this.pAllVariants = [];
            this.pColors = [];
            this.pSizes = [];
            this.pSelectedColors = [];
            this.pSelectedSizes = [];
            this.pPrintWidth = 0;
            this.pPrintHeight = 0;
            this.printifyError = "";

            this.printifyStatus = "Loading print providers…";
            const res = await fetch(`/printify/blueprints/${bp.id}/providers`);
            const data = await res.json();
            this.printifyStatus = "";

            if (data.error) { this.printifyError = data.error; return; }
            this.pProviders = data;
            if (data.length > 0) {
                this.pProviderId = String(data[0].id);
                await this.loadVariants();
            }
        },

        async loadVariants() {
            if (!this.pBlueprint || !this.pProviderId) return;
            this.pAllVariants = [];
            this.pColors = [];
            this.pSizes = [];
            this.pSelectedColors = [];
            this.pSelectedSizes = [];
            this.pPrintWidth = 0;
            this.pPrintHeight = 0;
            this.printifyError = "";

            this.printifyStatus = "Loading variants…";
            const url = `/printify/blueprints/${this.pBlueprint.id}/providers/${this.pProviderId}/variants`;
            const res = await fetch(url);
            const data = await res.json();
            this.printifyStatus = "";

            if (data.error) { this.printifyError = data.error; return; }
            this.pAllVariants = data;

            // Extract the front print area dimensions from the first variant that has them
            for (const v of data) {
                const front = (v.placeholders ?? []).find(p => p.position === "front");
                if (front?.width && front?.height) {
                    this.pPrintWidth = front.width;
                    this.pPrintHeight = front.height;
                    break;
                }
            }
            if (this.pPrintWidth) this.applyTopPreset();

            // Extract unique colors and sizes, preserving natural order from the API
            const colors = [], sizes = [], seenC = new Set(), seenS = new Set();
            for (const v of data) {
                const c = v.options?.color ?? "";
                const s = v.options?.size ?? "";
                if (c && !seenC.has(c)) { seenC.add(c); colors.push(c); }
                if (s && !seenS.has(s)) { seenS.add(s); sizes.push(s); }
            }
            this.pColors = colors;
            this.pSizes = sizes;
            this.pSelectedColors = [];
            // Default sizes: no colors selected; sizes default to S–2XL only
            const defaultSizes = new Set(["S", "M", "L", "XL", "2XL", "XXL"]);
            this.pSelectedSizes = sizes.filter(s => defaultSizes.has(s.toUpperCase()));
        },

        startDrag(e) {
            const scale = 220 / this.pPrintWidth;  // preview px per print px
            const designPx = this.pDesignPx;
            // Cache all per-drag constants so _onDragMove doesn't recompute on every mousemove
            this.pDrag = {
                startX: e.clientX,
                startY: e.clientY,
                startPX: this.pXPx,
                startPY: this.pYPx,
                scale,
                designPx,
                snapThreshold: 8 / scale,  // 8 screen px → print px; feels consistent across sizes
                centerX: Math.round((this.pPrintWidth - designPx) / 2),
                centerY: Math.round((this.pPrintHeight - designPx) / 2),
                minX: -(designPx - 1),
                maxX: this.pPrintWidth - 1,
                minY: -(designPx - 1),
                maxY: this.pPrintHeight - 1,
            };
            document.addEventListener('mousemove', this._boundDragMove);
            document.addEventListener('mouseup', this._boundDragUp);
        },

        _onDragMove(e) {
            if (!this.pDrag) return;
            const { scale, snapThreshold, centerX, centerY, minX, maxX, minY, maxY } = this.pDrag;

            const rawX = this.pDrag.startPX + (e.clientX - this.pDrag.startX) / scale;
            const rawY = this.pDrag.startPY + (e.clientY - this.pDrag.startY) / scale;
            // Clamp so at least 1 print-pixel of the image stays inside the print area
            let x = Math.round(Math.min(Math.max(rawX, minX), maxX));
            let y = Math.round(Math.min(Math.max(rawY, minY), maxY));

            // Snap to center when within threshold
            if (Math.abs(rawX - centerX) <= snapThreshold) x = centerX;
            if (Math.abs(rawY - centerY) <= snapThreshold) y = centerY;

            this.pXPx = x;
            this.pYPx = y;
            this.pIsTopPreset = false;
        },

        _onDragUp() {
            this.pDrag = null;
            document.removeEventListener('mousemove', this._boundDragMove);
            document.removeEventListener('mouseup', this._boundDragUp);
        },

        // Keyboard nudge for the placement preview image; dx/dy in print pixels
        nudgeDrag(dx, dy) {
            if (!this.pPrintWidth || !this.pPrintHeight) return;
            const designPx = this.pDesignPx;
            const minX = -(designPx - 1), maxX = this.pPrintWidth - 1;
            const minY = -(designPx - 1), maxY = this.pPrintHeight - 1;
            this.pXPx = Math.round(Math.min(Math.max(this.pXPx + dx, minX), maxX));
            this.pYPx = Math.round(Math.min(Math.max(this.pYPx + dy, minY), maxY));
            this.pIsTopPreset = false;
        },

        applyTopPreset() {
            if (!this.pPrintWidth) return;
            // Center horizontally; back out transparent padding so visible content
            // lands at pTopAllowance from the top of the print area
            this.pXPx = Math.round((this.pPrintWidth - this.pDesignPx) / 2);
            this.pYPx = Math.round(this.pTopAllowance - this.pContentTop * this.pDesignPx);
            this.pIsTopPreset = true;
        },

        centerH() {
            if (!this.pPrintWidth) return;
            this.pXPx = Math.round((this.pPrintWidth - this.pDesignPx) / 2);
            this.pIsTopPreset = false;
        },

        centerV() {
            if (!this.pPrintWidth) return;
            this.pYPx = Math.round((this.pPrintHeight - this.pDesignPx) / 2);
            this.pIsTopPreset = false;
        },

        togglePColor(color) {
            const i = this.pSelectedColors.indexOf(color);
            if (i === -1) this.pSelectedColors.push(color);
            else this.pSelectedColors.splice(i, 1);
        },

        togglePSize(size) {
            const i = this.pSelectedSizes.indexOf(size);
            if (i === -1) this.pSelectedSizes.push(size);
            else this.pSelectedSizes.splice(i, 1);
        },

        async doPublish(publishNow) {
            if (this.printifyBusy) return;
            this.printifyError = "";
            this.printifyDone = null;

            const shopId = cfg.printifyShopId || this.pShopId;
            if (!shopId) { this.printifyError = "Select a shop first."; return; }
            if (!this.pBlueprint) { this.printifyError = "Select a t-shirt style first."; return; }
            if (!this.pProviderId) { this.printifyError = "Select a print provider first."; return; }
            if (this.selectedVariantCount === 0) { this.printifyError = "Select at least one color and size."; return; }
            if (!this.pTitle.trim()) { this.printifyError = "Enter a product title."; return; }

            // Gather the variant IDs matching the selected color+size combinations
            const variantIds = this.pAllVariants
                .filter(v =>
                    this.pSelectedColors.includes(v.options?.color ?? "") &&
                    this.pSelectedSizes.includes(v.options?.size ?? "")
                )
                .map(v => v.id);

            const priceCents = Math.round(parseFloat(this.pPrice) * 100);
            if (!priceCents || priceCents < 1) { this.printifyError = "Enter a valid price."; return; }

            // Convert pixel coords to Printify normalized center coordinates (0–1)
            const scale = this.pScale;
            const W = this.pPrintWidth;
            const H = this.pPrintHeight;
            if (!W || !H) { this.printifyError = "Print dimensions not loaded."; return; }
            const designX = (this.pXPx + this.pDesignPx / 2) / W;
            const designY = (this.pYPx + this.pDesignPx / 2) / H;
            this.printifyBusy = true;

            const fd = new FormData();
            fd.append("session_id", this.sessionId);
            fd.append("column_id", this.colIdx);
            fd.append("shop_id", shopId);
            fd.append("blueprint_id", this.pBlueprint.id);
            fd.append("provider_id", this.pProviderId);
            fd.append("variant_ids", JSON.stringify(variantIds));
            fd.append("title", this.pTitle.trim());
            fd.append("description", this.pDescription.trim());
            fd.append("price_cents", priceCents);
            fd.append("publish_now", publishNow);
            fd.append("design_x", designX.toFixed(4));
            fd.append("design_y", designY.toFixed(4));
            fd.append("design_scale", scale);
            fd.append("final_url", this.activeComboUrl ?? "");
            fd.append("override_min_res", this.pOverrideMinRes);

            await streamSSE("/printify/publish", fd, {
                status: (e) => { this.printifyStatus = e.message; },
                done: (e) => {
                    this.printifyStatus = "";
                    this.printifyDone = e;
                },
                error: (e) => {
                    this.printifyStatus = "";
                    this.printifyError = e.message;
                },
            });

            this.printifyBusy = false;
        },
    };
}


// ── Session / column-manager Alpine component ─────────────────────────────────
// designer() owns session identity, column list, max-columns setting, and the
// output browser drawer. Per-column workflow state lives in columnDesigner().
function designer() {
    const cfg = JSON.parse(document.getElementById('app-config').textContent);

    // Reuse the tab's session ID across soft refreshes so server-side state
    // (concepts, image paths, column count) survives a reload. sessionStorage is
    // cleared when the tab closes, matching the server's in-memory lifetime.
    const storedId = sessionStorage.getItem('designer_session_id');
    const sessionId = storedId || crypto.randomUUID();
    if (!storedId) sessionStorage.setItem('designer_session_id', sessionId);

    return {
        // ── Session ────────────────────────────────────────────────────────
        sessionId,

        // Expose cfg so the template can read printifyEnabled, etc.
        cfg,

        // ── Column management ──────────────────────────────────────────────
        // Each entry is { id } — actual workflow state lives in columnDesigner instances.
        // Starts empty; init() populates after fetching session state so x-data on each
        // column element is always evaluated with the correct initialState from the server
        // (if columns were pre-seeded here, Alpine would reuse the key-0 node and never
        // re-evaluate x-data, causing column 1 to ignore the restored state).
        columns: [],
        maxColumns: cfg.maxColumns,
        minColumns: 1,

        // ── Output browser ────────────────────────────────────────────────
        showBrowser: false,
        browserPinned: false,   // when true, no close trigger works until unpinned first
        browserMode: null,      // null = normal, "reference" = picking a reference image
        browserTargetColIdx: 0, // which column's button triggered this browser open
        browserThemes: [],
        browserFilter: "",
        browserLoading: false,
        manageMode: false,
        selectedFiles: {},      // url → size_bytes; object for Alpine reactivity
        storageStats: null,     // {totalBytes, themeCount}
        renamingDir: "",        // dir_name of the theme currently being renamed
        renameValue: "",        // current value of the rename input

        // ── Presets panel ─────────────────────────────────────────────────
        showPresetsPanel: false,
        presetsActive: cfg.builtinName,
        presetsNames: cfg.presetNames,
        presetsNewName: "",
        presetsStatus: "",
        panelConceptsTemplate: cfg.conceptsTemplate,
        panelVariantsTemplate: cfg.variantsTemplate,
        panelStyleTemplate: cfg.styleTemplate,
        // Snapshot of the last-loaded preset values — compared against panel fields to
        // detect unsaved changes. Using reactive properties (not cfg) so Alpine tracks them.
        _loadedConceptsTemplate: cfg.conceptsTemplate,
        _loadedVariantsTemplate: cfg.variantsTemplate,
        _loadedStyleTemplate: cfg.styleTemplate,
        // Persisted in localStorage; empty string means "use built-in default"
        userDefaultPreset: "",

        // ── Lifecycle ──────────────────────────────────────────────────────
        async init() {
            // Route browser-open and presets-open requests dispatched by column components
            window.addEventListener('designer-open-browser', (e) => this.openBrowser(e.detail.colIdx));
            window.addEventListener('designer-open-browser-for-ref', (e) => this.openBrowserForReference(e.detail.colIdx));
            window.addEventListener('designer-close-column', (e) => this.closeColumn(e.detail.colIdx));
            window.addEventListener('designer-open-presets', () => this.openPresetsPanel());

            // Warn before unload if any column has started work — reloading clears
            // server-side PIL images, but text state and paths survive via session restore.
            window.addEventListener('beforeunload', (e) => {
                // Check live Alpine state for active columns; fall back to columns array length
                const anyWork = this.columns.some((col) => {
                    const el = document.querySelector(`[data-col-idx="${col.id}"]`);
                    const data = el?._x_dataStack?.[0];
                    // Consider a column "in progress" if it has a theme, concepts, variants, or final image
                    return data?.theme?.trim() || data?.concepts?.length || data?.variants?.length;
                });
                if (anyWork) {
                    e.preventDefault();
                    e.returnValue = '';
                }
            });

            // Restore prior session state (survives soft refresh because sessionId is
            // persisted in sessionStorage). On a fresh tab, the server returns one empty column.
            try {
                const res = await fetch(`/session/columns?session_id=${encodeURIComponent(this.sessionId)}`);
                if (res.ok) {
                    const data = await res.json();
                    // Clamp to the configured hard cap in case server state differs
                    this.maxColumns = data.max_columns ?? cfg.maxColumns;
                    this.minColumns = data.min_columns ?? 1;

                    // Override with localStorage values when they exist — they survive new
                    // tabs and server restarts, whereas the server session resets to defaults.
                    const lsMax = parseInt(localStorage.getItem('designer_max_columns'), 10);
                    const lsMin = parseInt(localStorage.getItem('designer_min_columns'), 10);
                    if (!isNaN(lsMax)) {
                        this.maxColumns = Math.max(1, Math.min(lsMax, cfg.maxColumns));
                        const fdMax = new FormData();
                        fdMax.append("session_id", this.sessionId);
                        fdMax.append("max_columns", this.maxColumns);
                        fetch("/session/max-columns", { method: "POST", body: fdMax });
                    }
                    if (!isNaN(lsMin)) {
                        this.minColumns = Math.max(1, Math.min(lsMin, this.maxColumns));
                        const fdMin = new FormData();
                        fdMin.append("session_id", this.sessionId);
                        fdMin.append("min_columns", this.minColumns);
                        fetch("/session/min-columns", { method: "POST", body: fdMin });
                    }
                    Alpine.store('minColumns', this.minColumns);
                    // Re-create the column list from persisted server state; each entry
                    // carries its initialState so columnDesigner can restore the workflow step.
                    if (Array.isArray(data.columns) && data.columns.length > 0) {
                        this.columns = data.columns.map((state, i) => ({
                            id: i,
                            initialState: state,
                        }));
                    } else {
                        this.columns = [{ id: 0, initialState: {} }];
                    }
                }
            } catch {
                // Network or parse error — start fresh with a single empty column
                this.columns = [{ id: 0, initialState: {} }];
            }

            // After a hard reload the server session is empty, so we may have fewer
            // columns than minColumns. Pad up to the floor before rendering.
            while (this.columns.length < this.minColumns) {
                this.columns.push({ id: this.columns.length, initialState: {} });
            }

            // Publish column count to a global Alpine store so column components can
            // disable the close button reactively without needing parent scope access.
            Alpine.store('columnCount', this.columns.length);

            // Restore user's preferred default preset from localStorage. Validate it still
            // exists in the current preset list (user may have deleted it since last visit).
            const savedDefault = localStorage.getItem('userDefaultPreset');
            if (savedDefault && this.presetsNames.includes(savedDefault)) {
                this.userDefaultPreset = savedDefault;
                this.presetsActive = savedDefault;
                this.loadPresetToPanel(savedDefault);
            } else if (savedDefault) {
                // Preset was deleted — clear the stale entry
                localStorage.removeItem('userDefaultPreset');
            }

            // Keep browserOpen store in sync — column headers combine it with activeColIdx
            // to highlight the active column whenever the browser is open.
            this.$watch('showBrowser', (v) => {
                Alpine.store('browserOpen', v);
                if (!v) Alpine.store('downloadedUrls', {}); // reset on close
            });
            this.$watch('showPresetsPanel', (v) => Alpine.store('presetsOpen', v));
            this.$watch('minColumns', (v) => Alpine.store('minColumns', v));
        },

        // ── Computed ───────────────────────────────────────────────────────
        get filteredBrowserThemes() {
            const q = this.browserFilter.trim().toLowerCase();
            if (!q) return this.browserThemes;
            return this.browserThemes.filter(t => t.theme.toLowerCase().includes(q));
        },

        get selectedCount() {
            return Object.keys(this.selectedFiles).length;
        },

        get selectedBytes() {
            return Object.values(this.selectedFiles).reduce((a, b) => a + b, 0);
        },

        // ── Column management ──────────────────────────────────────────────
        async addColumn() {
            if (this.columns.length >= this.maxColumns) return;
            const fd = new FormData();
            fd.append("session_id", this.sessionId);
            const res = await fetch("/columns", { method: "POST", body: fd });
            const data = await res.json();
            if (data.error) return;
            this.columns.push({ id: data.column_id });
            Alpine.store('columnCount', this.columns.length);
        },

        // Remove a column — blocked while the column is loading or it's the last one.
        // Rebuilds the columns array from the server's compacted response so indices
        // stay in sync after the gap is closed.
        async closeColumn(colIdx) {
            if (this.columns.length <= 1) return; // guard — can't close the last column
            // Read live Alpine state from the column DOM to decide whether to confirm
            const el = document.querySelector(`[data-col-idx="${colIdx}"]`);
            const colData = el?._x_dataStack?.[0];
            const hasWork = colData?.theme?.trim() || colData?.concepts?.length
                         || colData?.variants?.length;
            const label = `Design ${colIdx + 1}`;
            if (hasWork && !confirm(`Close ${label}? Work in this column will be lost.`)) return;
            const fd = new FormData();
            fd.append("session_id", this.sessionId);
            fd.append("column_id", colIdx);
            const res = await fetch("/session/remove-column", { method: "POST", body: fd });
            if (!res.ok) return;
            const data = await res.json();
            if (data.error) return;
            // Rebuild from the server's compacted array — surviving columns get new indices
            // starting at 0; _restoreInitialState in each columnDesigner handles the re-mount.
            this.columns = data.columns.map((state, i) => ({ id: i, initialState: state }));
            Alpine.store('columnCount', this.columns.length);
            // If closing dropped below the minColumns floor, lower it to match so the
            // preference doesn't re-add the column on next load.
            if (this.columns.length < this.minColumns) {
                this.minColumns = this.columns.length;
                localStorage.setItem('designer_min_columns', this.minColumns);
                const fd2 = new FormData();
                fd2.append("session_id", this.sessionId);
                fd2.append("min_columns", this.minColumns);
                fetch("/session/min-columns", { method: "POST", body: fd2 });
            }
        },

        // Persist the user's max-columns preference to the server; trims excess columns.
        async updateMaxColumns() {
            // Clamp minColumns down if it would exceed the new max
            if (this.minColumns > this.maxColumns) this.minColumns = this.maxColumns;

            // Identify columns that need to be removed (rightmost first)
            const excess = this.columns.length - this.maxColumns;
            if (excess > 0) {
                // Warn once if any of the columns being removed have work in progress
                const hasWip = Array.from({ length: excess }, (_, i) => {
                    const colIdx = this.columns.length - excess + i;
                    const el = document.querySelector(`[data-col-idx="${colIdx}"]`);
                    const d = el?._x_dataStack?.[0];
                    return d?.theme?.trim() || d?.concepts?.length || d?.variants?.length;
                }).some(Boolean);
                if (hasWip && !confirm(`Lowering the max will close ${excess} column${excess > 1 ? 's' : ''} with work in progress. Continue?`)) {
                    // Revert the input to the current actual column count
                    this.maxColumns = this.columns.length;
                    return;
                }
                // Close excess columns from the right, one at a time
                while (this.columns.length > this.maxColumns) {
                    const fd = new FormData();
                    fd.append("session_id", this.sessionId);
                    fd.append("column_id", this.columns.length - 1);
                    const res = await fetch("/session/remove-column", { method: "POST", body: fd });
                    if (!res.ok) break;
                    const data = await res.json();
                    if (data.error) break;
                    this.columns = data.columns.map((state, i) => ({ id: i, initialState: state }));
                    Alpine.store('columnCount', this.columns.length);
                }
            }

            const fd = new FormData();
            fd.append("session_id", this.sessionId);
            fd.append("max_columns", this.maxColumns);
            await fetch("/session/max-columns", { method: "POST", body: fd });
            localStorage.setItem('designer_max_columns', this.maxColumns);
        },

        // Persist the user's min-columns floor to the server
        async updateMinColumns() {
            const fd = new FormData();
            fd.append("session_id", this.sessionId);
            fd.append("min_columns", this.minColumns);
            await fetch("/session/min-columns", { method: "POST", body: fd });
            localStorage.setItem('designer_min_columns', this.minColumns);
            // Bring column count up to the new floor if needed
            while (this.columns.length < this.minColumns) {
                await this.addColumn();
            }
        },

        // ── Output browser ─────────────────────────────────────────────────
        closeBrowser() {
            // No-op while pinned — user must unpin first.
            if (this.browserPinned) return;
            this.showBrowser = false;
            this.browserMode = null;
        },

        openBrowser(colIdx) {
            // Toggle closed if this column's browser button is clicked while already open
            if (this.showBrowser && this.browserMode === null && this.browserTargetColIdx === (colIdx ?? 0)) {
                this.closeBrowser();
                return;
            }
            this.browserMode = null;
            this.browserTargetColIdx = colIdx ?? 0;
            this.showBrowser = true;
            if (this.browserThemes.length > 0) return; // already loaded
            this.reloadBrowser();
        },

        openBrowserForReference(colIdx) {
            this.browserMode = "reference";
            this.browserTargetColIdx = colIdx ?? 0;
            this.showBrowser = true;
            if (this.browserThemes.length > 0) return;
            this.reloadBrowser();
        },

        async reloadBrowser() {
            this.browserLoading = true;
            this.selectedFiles = {};
            try {
                const res = await fetch("/browse");
                const data = await res.json();
                this.browserThemes = data.map((t, ti) => ({
                    ...t,
                    expanded: ti === 0,
                }));
                this.storageStats = {
                    totalBytes: data.reduce((s, t) => s + t.theme_size_bytes, 0),
                    themeCount: data.length,
                };
            } finally {
                this.browserLoading = false;
            }
        },

        _fmtBytes(bytes) {
            if (bytes < 1024) return `${bytes} B`;
            if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
            return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
        },

        toggleManageMode() {
            this.manageMode = !this.manageMode;
            if (!this.manageMode) {
                this.selectedFiles = {};
                this.renamingDir = "";
                this.renameValue = "";
            }
        },

        toggleFile(url, sizeBytes) {
            const updated = { ...this.selectedFiles };
            if (updated[url] !== undefined) delete updated[url];
            else updated[url] = sizeBytes;
            this.selectedFiles = updated;
        },

        isSelected(url) {
            return this.selectedFiles[url] !== undefined;
        },

        _themeFileList(theme) {
            const files = [];
            theme.finals.forEach(f => {
                files.push([f.png_url, f.png_size]);
                if (f.no_bg_url) files.push([f.no_bg_url, f.no_bg_size]);
            });
            (theme.images || []).forEach(v => {
                // Walk all renders so manage-mode select-all captures every AR/size file.
                (v.renders || [{ url: v.url, size: v.size, no_bg_url: v.no_bg_url, no_bg_size: v.no_bg_size }]).forEach(r => {
                    files.push([r.url, r.size]);
                    if (r.no_bg_url) files.push([r.no_bg_url, r.no_bg_size]);
                });
            });
            return files;
        },

        toggleThemeFiles(theme) {
            const files = this._themeFileList(theme);
            const allSelected = files.every(([url]) => this.selectedFiles[url] !== undefined);
            const updated = { ...this.selectedFiles };
            if (allSelected) files.forEach(([url]) => delete updated[url]);
            else files.forEach(([url, size]) => updated[url] = size);
            this.selectedFiles = updated;
        },

        themeAllSelected(theme) {
            const files = this._themeFileList(theme);
            return files.length > 0 && files.every(([url]) => this.selectedFiles[url] !== undefined);
        },

        async deleteSelected() {
            const paths = Object.keys(this.selectedFiles);
            if (!paths.length) return;
            const label = `${paths.length} file${paths.length > 1 ? 's' : ''} (${this._fmtBytes(this.selectedBytes)})`;
            if (!confirm(`Delete ${label}? This cannot be undone.`)) return;
            await fetch("/browse/files", {
                method: "DELETE",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ paths }),
            });
            this.manageMode = false;
            await this.reloadBrowser();
        },

        downloadThemeZip(dirName) {
            const a = document.createElement("a");
            a.href = `/browse/archive/${encodeURIComponent(dirName)}`;
            a.download = `${dirName}.zip`;
            a.click();
        },

        async downloadSelectedZip() {
            const paths = Object.keys(this.selectedFiles);
            if (!paths.length) return;
            const res = await fetch("/browse/archive/selection", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ paths }),
            });
            const blob = await res.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = "selection.zip";
            a.click();
            URL.revokeObjectURL(url);
        },

        startRename(dirName, displayName) {
            this.renamingDir = dirName;
            this.renameValue = displayName;
        },

        cancelRename() {
            this.renamingDir = "";
            this.renameValue = "";
        },

        async commitRename() {
            const newName = this.renameValue.trim();
            if (!newName) return;
            const res = await fetch("/browse/rename", {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ dir_name: this.renamingDir, new_name: newName }),
            });
            const data = await res.json();
            if (data.error) { alert(data.error); return; }
            this.renamingDir = "";
            this.renameValue = "";
            await this.reloadBrowser();
        },

        // Dispatch a load-image event to the currently active column
        loadToVariants(url, width, height, displayTheme) {
            window.dispatchEvent(new CustomEvent('col-load-image', {
                detail: { colIdx: Alpine.store('activeColIdx'), url, width, height, displayTheme },
            }));
            this.closeBrowser();
        },

        // Dispatch a set-reference event to the currently active column
        setReferenceFromBrowser(imageUrl) {
            window.dispatchEvent(new CustomEvent('col-set-reference', {
                detail: { colIdx: Alpine.store('activeColIdx'), imageUrl },
            }));
            this.closeBrowser();
        },

        // ── Presets panel actions ──────────────────────────────────────────

        get sortedPresetsNames() {
            const ud = this.userDefaultPreset;
            const bi = cfg.builtinName;
            const rest = this.presetsNames
                .filter(n => n !== ud && n !== bi)
                .sort((a, b) => a.localeCompare(b));
            const head = [];
            if (ud && this.presetsNames.includes(ud)) head.push(ud);
            if (this.presetsNames.includes(bi) && bi !== ud) head.push(bi);
            return [...head, ...rest];
        },

        get presetsHasChanges() {
            return this.panelConceptsTemplate !== this._loadedConceptsTemplate
                || this.panelVariantsTemplate !== this._loadedVariantsTemplate
                || this.panelStyleTemplate !== this._loadedStyleTemplate;
        },

        openPresetsPanel() {
            if (this.showPresetsPanel) {
                this.closePresetsPanel();
            } else {
                this.showPresetsPanel = true;
            }
        },

        closePresetsPanel(skipConfirm = false) {
            // On mouse-out we skip the confirm so the panel doesn't trap the cursor;
            // unsaved changes are still present if the user re-opens.
            if (!skipConfirm && this.presetsHasChanges && !confirm('You have unsaved changes. Close anyway?')) return;
            this.showPresetsPanel = false;
        },

        // Toggle the user's default preset. When set, it loads automatically on page load.
        // When cleared, the built-in default is used instead.
        toggleUserDefault() {
            if (this.presetsActive === this.userDefaultPreset) {
                this.userDefaultPreset = "";
                localStorage.removeItem('userDefaultPreset');
            } else {
                this.userDefaultPreset = this.presetsActive;
                localStorage.setItem('userDefaultPreset', this.presetsActive);
            }
        },

        // Load a named preset into the panel editor fields — synchronous lookup from cfg.allPresets
        loadPresetToPanel(name) {
            if (!name) return;
            const data = cfg.allPresets[name];
            if (!data) return;
            this.panelConceptsTemplate = data.concepts_prompt;
            this.panelVariantsTemplate = data.variants_prompt;
            this.panelStyleTemplate = data.style_suffix;
            this._loadedConceptsTemplate = data.concepts_prompt;
            this._loadedVariantsTemplate = data.variants_prompt;
            this._loadedStyleTemplate = data.style_suffix;
            // Pre-fill the save name so editing a user preset and re-saving is one click.
            // Leave blank for the built-in since it can't be overwritten.
            this.presetsNewName = (name !== cfg.builtinName) ? name : "";
            // Selecting a preset immediately applies it to all columns — no separate Apply step needed.
            this.applyPresetToColumn();
        },

        // Save the current panel templates under a new (or overwrite existing) name
        async savePresetFromPanel() {
            const name = this.presetsNewName.trim();
            if (!name) { this.presetsStatus = "Enter a preset name."; return; }
            if (name === cfg.builtinName) { this.presetsStatus = "Cannot overwrite the built-in preset."; return; }
            if (!this.panelConceptsTemplate.trim() || !this.panelVariantsTemplate.trim() || !this.panelStyleTemplate.trim()) {
                this.presetsStatus = "Prompt fields cannot be blank."; return;
            }
            if (this.presetsNames.includes(name) && !confirm(`Overwrite preset "${name}"?`)) return;

            const fd = new FormData();
            fd.append("name", name);
            fd.append("concepts", this.panelConceptsTemplate);
            fd.append("variants", this.panelVariantsTemplate);
            fd.append("style", this.panelStyleTemplate);

            const res = await fetch("/presets", { method: "POST", body: fd });
            const data = await res.json();
            if (data.error) {
                this.presetsStatus = data.error;
            } else {
                // Keep local allPresets cache in sync so switching to this preset is instant
                cfg.allPresets[data.saved] = {
                    concepts_prompt: this.panelConceptsTemplate,
                    variants_prompt: this.panelVariantsTemplate,
                    style_suffix: this.panelStyleTemplate,
                };
                // Sync snapshot so presetsHasChanges clears immediately
                this._loadedConceptsTemplate = this.panelConceptsTemplate;
                this._loadedVariantsTemplate = this.panelVariantsTemplate;
                this._loadedStyleTemplate = this.panelStyleTemplate;
                this.presetsNames = data.names;
                this.presetsActive = data.saved;
                this.presetsNewName = "";
                this.presetsStatus = `Saved "${name}".`;
            }
        },

        // Delete the currently selected preset (builtin is protected)
        async deletePresetFromPanel() {
            const name = this.presetsActive;
            if (name === cfg.builtinName) { this.presetsStatus = "Cannot delete the built-in preset."; return; }
            if (!confirm(`Delete preset "${name}"? This cannot be undone.`)) return;

            const res = await fetch(`/presets/${encodeURIComponent(name)}`, { method: "DELETE" });
            const data = await res.json();
            delete cfg.allPresets[name];
            // Clear user default if the deleted preset was it
            if (this.userDefaultPreset === name) {
                this.userDefaultPreset = "";
                localStorage.removeItem('userDefaultPreset');
            }
            this.presetsNames = data.names;
            const fallback = (this.userDefaultPreset && this.userDefaultPreset !== name)
                ? this.userDefaultPreset : cfg.builtinName;
            this.presetsActive = fallback;
            this.loadPresetToPanel(fallback);
            this.presetsStatus = `Deleted "${name}".`;
        },

        // Broadcast the current panel templates to every column
        applyPresetToColumn() {
            const count = Alpine.store('columnCount');
            for (let i = 0; i < count; i++) {
                window.dispatchEvent(new CustomEvent('col-apply-preset', {
                    detail: {
                        colIdx: i,
                        conceptsTemplate: this.panelConceptsTemplate,
                        variantsTemplate: this.panelVariantsTemplate,
                        styleTemplate: this.panelStyleTemplate,
                    },
                }));
            }
            this.presetsStatus = `Applied to all columns.`;
        },
    };
}
