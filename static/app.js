// ── Alpine global stores ──────────────────────────────────────────────────────
// Declare stores before Alpine processes the DOM so $store.* refs in templates
// never see undefined on first render. Values are updated by designer.init().
let colUidSeq = 1;

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
//
// noDataTimeoutMs: abort and fire handlers.error if no data chunk arrives within
// this window. The timer resets on every received chunk, so long-running but
// actively-streaming requests (e.g. 4K generation) are not affected.
async function streamSSE(url, formData, handlers, noDataTimeoutMs = 60000) {
    const controller = new AbortController();
    let timeoutId = null;

    // Reset the watchdog timer; called on connection and on each received chunk.
    const resetTimeout = () => {
        clearTimeout(timeoutId);
        timeoutId = setTimeout(() => {
            controller.abort();
        }, noDataTimeoutMs);
    };

    resetTimeout();

    let response;
    try {
        response = await fetch(url, { method: "POST", body: formData, signal: controller.signal });
        resetTimeout(); // connection established — restart the window
    } catch (err) {
        clearTimeout(timeoutId);
        if (err.name === 'AbortError') {
            if (handlers.error) handlers.error({ message: "Server stopped responding — please try again." });
        } else {
            if (handlers.error) handlers.error({ message: `Network error: ${err.message}` });
        }
        return;
    }

    if (!response.ok) {
        clearTimeout(timeoutId);
        if (handlers.error) handlers.error({ message: `Request failed: HTTP ${response.status}` });
        return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            resetTimeout(); // data arrived — push the watchdog window forward
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
    } catch (err) {
        // reader.read() throws when the AbortController fires mid-stream
        if (err.name === 'AbortError') {
            if (handlers.error) handlers.error({ message: "Server stopped responding — please try again." });
        } else {
            if (handlers.error) handlers.error({ message: `Stream error: ${err.message}` });
        }
    } finally {
        clearTimeout(timeoutId);
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

        get visibleSteps() {
            const steps = [1];
            if (this.step >= 2) steps.push(2);
            if (this.step >= 3) steps.push(3);
            if (this.step >= 4 || this.loadedImageRes) steps.push(4);
            if ((this.step >= 4 || this.loadedImageRes) && this.selectedVariant !== null) {
                steps.push(5, 6);
            }
            return steps;
        },

        // ── UI state ───────────────────────────────────────────────────────
        isLoading: false,
        loadingMsg: "",
        loadingStep: 0,   // which step triggered the current load — controls status bar placement
        error: "",
        promptLog: "",
        showPromptLog: false,
        showPresets: false,
        hasUnsubmittedText: false,

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
        printifyOverride: false,           // testing override — bypasses the 4K minimum requirement
        editSize: cfg.editSize,            // default edit size — fast for iteration; user can bump up before submitting
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

        // ── Drag / rotate state (Printify placement preview) ──────────────
        pDrag: null,            // null when idle; { startX, startY, startPX, startPY } while dragging
        pRotate: null,          // null when idle; { centerX, centerY, startAngle } while rotating
        pRotateDeg: 0,          // current rotation angle in degrees (−180 to 180; 0 = no rotation)

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
        pTightBounds: null,         // { top, bottom, left, right } fractions from canvas alpha scan; null = use full square

        pTitle: "",
        pDescription: "",
        pPrice: "29.99",

        // ── Lifecycle ──────────────────────────────────────────────────────
        init() {
            // Restore server-persisted state from a prior session (e.g. after page refresh).
            // The server returns serializable fields only — PIL images are gone and must be
            // re-generated, but text state and saved file paths survive the reload.
            this._restoreInitialState(initialState);

            // Focus the theme input only for the first column to avoid competing focus.
            // Double-tick + setTimeout handles x-cloak removal and x-for template rendering delays.
            if (this.colIdx === 0) {
                this.$nextTick(() => this.$nextTick(() =>
                    setTimeout(() => this.$refs.themeInput?.focus(), 50)
                ));
            }

            // No bound drag handlers needed — startDrag captures `this` via closure instead

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
            // pTightBounds also arrives async (bg-removed alpha scan); it overrides pContentTop
            this.$watch('pTightBounds', () => {
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

            // Receive "rehydrate column" events from designer.reloadSession()
            window.addEventListener('rehydrate-column', (e) => {
                if (e.detail.columnIdx === this.colIdx) {
                    this._restoreInitialState(e.detail.state);
                    // Also scroll to gallery
                    this.$nextTick(() => this.$refs.step4?.scrollIntoView({ behavior: "smooth", block: "start" }));
                }
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
                // Persist selection so hard refresh restores the correct variant + iteration chain.
                // Skip null — that's a reset on new generate, not a meaningful user selection.
                if (val !== null) {
                    const fd = new FormData();
                    fd.append("session_id", this.sessionId);
                    fd.append("column_id", this.colIdx);
                    fd.append("selected_idx", val);
                    fetch("/session/select-variant", { method: "POST", body: fd });
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
            const rad = this.pRotateDeg * Math.PI / 180;
            const cos = Math.cos(rad), sin = Math.sin(rad);

            if (this.pTightBounds) {
                // BG removed: rotate the 4 corners of the content sub-rectangle around the
                // design-square center, then check if any corner escapes the print area.
                const b = this.pTightBounds;
                // Corner positions relative to design-square center (before rotation)
                const left_rel   = (b.left   - 0.5) * designPx;
                const right_rel  = (b.right  - 0.5) * designPx;
                const top_rel    = (b.top    - 0.5) * designPx;
                const bottom_rel = (b.bottom - 0.5) * designPx;
                // Rotate all 4 corners and collect absolute print-space coordinates
                const dcx = this.pXPx + designPx / 2;
                const dcy = this.pYPx + designPx / 2;
                const corners = [
                    [left_rel, top_rel], [right_rel, top_rel],
                    [right_rel, bottom_rel], [left_rel, bottom_rel],
                ];
                const xs = corners.map(([x, y]) => dcx + x * cos - y * sin);
                const ys = corners.map(([x, y]) => dcy + x * sin + y * cos);
                const minX = Math.min(...xs), maxX = Math.max(...xs);
                const minY = Math.min(...ys), maxY = Math.max(...ys);
                return minX < 0 || maxX > this.pPrintWidth
                    || minY < 0 || maxY > this.pPrintHeight;
            }

            // No BG removal: axis-aligned bounding box of the full rotated square
            // Each side of the AABB expands by |cos θ| + |sin θ| relative to the square side
            const aabb = designPx * (Math.abs(cos) + Math.abs(sin));
            const cx = this.pXPx + designPx / 2;
            const cy = this.pYPx + designPx / 2;
            return cx - aabb / 2 < 0 || cx + aabb / 2 > this.pPrintWidth
                || cy - aabb / 2 < 0 || cy + aabb / 2 > this.pPrintHeight;
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
            this.hasUnsubmittedText = false;
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
            this.hasUnsubmittedText = false;
            if (!state || typeof state !== 'object') return;

            // Restore text state unconditionally
            if (state.theme)    this.theme    = state.theme;
            if (state.concepts) this.concepts = state.concepts;
            if (state.prompts)  this.prompts  = state.prompts;

            // Restore variant thumbnails from saved paths (PIL images are gone after reload;
            // the static files are still on disk so the URLs remain valid).
            // original_image_paths holds the N brainstorm variants; anything appended beyond
            // that count in image_paths is an edit iteration — mark it so it stays out of the
            // main gallery and shows in the iterations panel instead.
            if (Array.isArray(state.image_paths) && state.image_paths.length) {
                const origCount = Array.isArray(state.original_image_paths)
                    ? state.original_image_paths.length
                    : state.image_paths.length;  // fallback: treat all as originals (old sessions)
                // iteration_roots[j] is the rootIdx for the j-th iteration (0-based among iterations only)
                const iterRoots = Array.isArray(state.iteration_roots) ? state.iteration_roots : [];
                this.variants = state.image_paths.map((p, i) => {
                    const isIter = i >= origCount;
                    return {
                        url: "/" + p.replace(/^\//, ""),
                        origUrl: "/" + p.replace(/^\//, ""),
                        noBgUrl: null,
                        ts: isIter ? 1 : 0,  // non-zero so iterations render in the list
                        isIteration: isIter,
                        rootIdx: isIter ? (iterRoots[i - origCount] ?? Math.max(0, origCount - 1)) : undefined,
                    };
                });
                if (state.selected_idx != null) this.selectedVariant = state.selected_idx;
                if (state.variant_size) this.generatedVariantSize = state.variant_size;
                if (state.variant_aspect_ratio) this.generatedVariantAspectRatio = state.variant_aspect_ratio;
                // Re-enable edit mode if iterations exist so the iterations panel is visible
                if (state.image_paths.length > origCount) this.editModeActive = true;

                // Restore rendered combos scanned from disk — combo_lists[i] = [{size, aspectRatio, url}]
                // Without this, variantCombos stays empty on hard reload and the iterations step
                // only shows the original 512 thumbnail instead of all previously rendered sizes.
                if (Array.isArray(state.combo_lists)) {
                    const combos = {};
                    state.combo_lists.forEach((list, i) => { combos[i] = list; });
                    this.variantCombos = combos;
                    // $watch('selectedVariant') only fires on changes, not on the initial value set
                    // above, so we must manually seed activeComboUrl/activeComboSize here.
                    const selIdx = this.selectedVariant ?? 0;
                    const selCombos = combos[selIdx] || [];
                    if (selCombos.length > 0) {
                        this.activeComboUrl = selCombos[0].url;
                        this.activeComboSize = selCombos[0].size;
                    }
                }
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
            this.hasUnsubmittedText = false;
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
            fd.append("theme_form", this.theme.trim());

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

            this.loadingStep = 5;
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
            this._startLoading(`Generating iteration at ${this.editSize} (${this.editAspectRatio})...`);

            // Compute rootIdx here so it can be sent to the server for persistence
            const parentIdx = this.selectedVariant ?? 0;
            const parentVariant = this.variants[parentIdx];
            const rootIdx = parentVariant?.isIteration ? parentVariant.rootIdx : parentIdx;

            const fd = new FormData();
            fd.append("session_id", this.sessionId);
            fd.append("column_id", this.colIdx);
            fd.append("source_url", sourceUrl);
            fd.append("edit_prompt", this.editPrompt.trim());
            fd.append("size", this.editSize);
            fd.append("aspect_ratio", this.editAspectRatio);
            fd.append("root_idx", rootIdx);

            await streamSSE("/stream/edit", fd, {
                status: (e) => { this.loadingMsg = e.message; },
                edit_variant: (e) => {
                    const ts = Date.now();
                    // Append the new variant and auto-select it so the user can immediately
                    // see the result and continue editing or render at a higher resolution.
                    // rootIdx was computed before the request so the server could persist it.
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

            this.loadingStep = 5;
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
            this.pTightBounds = null;  // reset; will be recomputed below if bg is removed
            this.pXPx = 0;
            this.pYPx = this.pTopAllowance;  // pTopAllowance intentionally not reset — persists
            this.pRotateDeg = 0;
            this.pIsTopPreset = true;
            this.pTitle = this.theme || "Custom T-Shirt";
            this.showPrintify = true;

            // Fetch content bounds from the active combo image alpha channel for Y placement
            if (this.activeComboUrl) {
                fetch(`/analysis/final?session_id=${encodeURIComponent(this.sessionId)}&column_id=${this.colIdx}`)
                    .then(r => r.json())
                    .then(data => { this.pContentTop = data.content_top ?? 0; })
                    .catch(() => { });

                // If the active combo is currently showing its bg-removed version, compute
                // tight pixel bounds so the OOB check ignores transparent areas.
                const combo = this.activeComboObj;
                if (combo?.noBgUrl && combo.url === combo.noBgUrl) {
                    this._computeTightBounds(this.activeComboUrl)
                        .then(b => { this.pTightBounds = b; })
                        .catch(() => { });
                }
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
            // Alpine's x-teleport creates a different scope proxy than init()'s `this`, so
            // _boundDragMove bound in init() can't see state set here. Capture self = this
            // (the teleported proxy) in a closure so all drag handlers share the same context.
            const self = this;
            const scale = 220 / self.pPrintWidth;  // preview px per print px
            const designPx = self.pDesignPx;
            const drag = {
                startX: e.clientX,
                startY: e.clientY,
                startPX: self.pXPx,
                startPY: self.pYPx,
                scale,
                snapThreshold: 8 / scale,  // 8 screen px → print px; feels consistent across sizes
                centerX: Math.round((self.pPrintWidth - designPx) / 2),
                centerY: Math.round((self.pPrintHeight - designPx) / 2),
                minX: -(designPx - 1),
                maxX: self.pPrintWidth - 1,
                minY: -(designPx - 1),
                maxY: self.pPrintHeight - 1,
            };
            self.pDrag = drag;  // reactive: drives cursor style and snap guide visibility

            function onMove(ev) {
                const { scale, snapThreshold, centerX, centerY, minX, maxX, minY, maxY } = drag;
                const rawX = drag.startPX + (ev.clientX - drag.startX) / scale;
                const rawY = drag.startPY + (ev.clientY - drag.startY) / scale;
                // Clamp so at least 1 print-pixel of the image stays inside the print area
                let x = Math.round(Math.min(Math.max(rawX, minX), maxX));
                let y = Math.round(Math.min(Math.max(rawY, minY), maxY));
                // Snap to center when within threshold
                if (Math.abs(rawX - centerX) <= snapThreshold) x = centerX;
                if (Math.abs(rawY - centerY) <= snapThreshold) y = centerY;
                self.pXPx = x;
                self.pYPx = y;
                self.pIsTopPreset = false;
            }

            function onUp() {
                self.pDrag = null;
                document.removeEventListener('mousemove', onMove);
                document.removeEventListener('mouseup', onUp);
            }

            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onUp);
        },

        // Snap rotation angle to 0 when within ±5°; called from slider @input and rotate mousemove
        snapRotation(raw) {
            this.pRotateDeg = Math.abs(raw) <= 5 ? 0 : Math.round(raw);
        },

        startRotate(e) {
            // Same closure pattern as startDrag — x-teleport proxy mismatch means we must
            // capture `this` before registering document-level listeners.
            const self = this;

            // The handle's parent is the image+handle wrapper div; its center == design center.
            const wrapper = e.currentTarget.parentElement;
            const rect = wrapper.getBoundingClientRect();
            const cx = rect.left + rect.width / 2;
            const cy = rect.top  + rect.height / 2;

            // Record the angle from design center → pointer at drag start so subsequent
            // moves are expressed as a delta rather than an absolute angle.
            const startAngle = Math.atan2(e.clientY - cy, e.clientX - cx) * 180 / Math.PI;
            const startDeg = self.pRotateDeg;

            self.pRotate = {};  // truthy: drives grabbing cursor on the handle

            function onMove(ev) {
                const currentAngle = Math.atan2(ev.clientY - cy, ev.clientX - cx) * 180 / Math.PI;
                // Wrap result into −180…180 so the slider thumb tracks correctly
                let raw = startDeg + (currentAngle - startAngle);
                raw = ((raw + 180) % 360 + 360) % 360 - 180;
                self.snapRotation(raw);
            }

            function onUp() {
                self.pRotate = null;
                document.removeEventListener('mousemove', onMove);
                document.removeEventListener('mouseup', onUp);
            }

            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onUp);
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
            // When bg is removed pTightBounds.top gives pixel-accurate content top from
            // the alpha scan; fall back to server-side pContentTop for fully-opaque images.
            const contentTop = this.pTightBounds ? this.pTightBounds.top : this.pContentTop;
            // Center horizontally; subtract the transparent top padding so visible content
            // lands exactly pTopAllowance px from the top of the print area.
            this.pXPx = Math.round((this.pPrintWidth - this.pDesignPx) / 2);
            this.pYPx = Math.round(this.pTopAllowance - contentTop * this.pDesignPx);
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

        // Scan the alpha channel of an image via canvas and return tight content bounds
        // as { top, bottom, left, right } fractions (0–1). Returns null if the image
        // is fully opaque or fully transparent (fall back to full-square bounds check).
        async _computeTightBounds(url) {
            return new Promise((resolve) => {
                const img = new Image();
                img.onload = () => {
                    const w = img.naturalWidth, h = img.naturalHeight;
                    const canvas = document.createElement('canvas');
                    canvas.width = w;
                    canvas.height = h;
                    const ctx = canvas.getContext('2d');
                    ctx.drawImage(img, 0, 0);
                    // Read every pixel's alpha byte (index 3 of each RGBA quad)
                    const data = ctx.getImageData(0, 0, w, h).data;
                    let minX = w, maxX = -1, minY = h, maxY = -1;
                    for (let y = 0; y < h; y++) {
                        for (let x = 0; x < w; x++) {
                            if (data[(y * w + x) * 4 + 3] > 0) {
                                if (x < minX) minX = x;
                                if (x > maxX) maxX = x;
                                if (y < minY) minY = y;
                                if (y > maxY) maxY = y;
                            }
                        }
                    }
                    // Fully opaque or fully transparent — no meaningful tight bounds
                    if (maxX === -1 || (minX === 0 && maxX === w - 1 && minY === 0 && maxY === h - 1)) {
                        resolve(null);
                        return;
                    }
                    // +1 to maxX/maxY so the fraction covers the last pixel's far edge
                    resolve({ top: minY / h, bottom: (maxY + 1) / h, left: minX / w, right: (maxX + 1) / w });
                };
                img.onerror = () => resolve(null);
                img.src = url;
            });
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
            fd.append("design_angle", this.pRotateDeg);
            fd.append("final_url", this.activeComboUrl ?? "");

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
                const anyWork = this.columns.some((col, i) => {
                    const el = document.querySelector(`[data-col-idx="${i}"]`);
                    const data = el?._x_dataStack?.[0];
                    return data?.hasUnsubmittedText;
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
                        this.columns = data.columns.map((state) => ({
                            uid: colUidSeq++,
                            initialState: state,
                        }));
                    } else {
                        this.columns = [{ uid: colUidSeq++, initialState: {} }];
                    }
                }
            } catch {
                // Network or parse error — start fresh with a single empty column
                this.columns = [{ uid: colUidSeq++, initialState: {} }];
            }

            // After a hard reload the server session is empty, so we may have fewer
            // columns than minColumns. Pad up to the floor before rendering.
            while (this.columns.length < this.minColumns) {
                this.columns.push({ uid: colUidSeq++, initialState: {} });
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
            this.columns.push({ uid: colUidSeq++ });
            Alpine.store('columnCount', this.columns.length);
        },

        // Remove a column — blocked while the column is loading or it's the last one.
        // Mutates the local columns array by splicing out the removed index. The DOM
        // elements bound via x-for will stay in place, and their x-effect="colIdx = index"
        // will automatically update the inner columnDesigner's colIdx to match its new position.
        async closeColumn(colIdx) {
            if (this.columns.length <= 1) return; // guard — can't close the last column
            // Read live Alpine state from the column DOM to decide whether to confirm
            const el = document.querySelector(`[data-col-idx="${colIdx}"]`);
            const colData = el?._x_dataStack?.[0];
            const hasWork = colData?.hasUnsubmittedText;
            const label = `Design ${colIdx + 1}`;
            if (hasWork && !confirm(`Close ${label}? Unsubmitted text will be lost.`)) return;
            const fd = new FormData();
            fd.append("session_id", this.sessionId);
            fd.append("column_id", colIdx);
            const res = await fetch("/session/remove-column", { method: "POST", body: fd });
            if (!res.ok) return;
            const data = await res.json();
            if (data.error) return;
            
            this.columns.splice(colIdx, 1);
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
                    return d?.hasUnsubmittedText;
                }).some(Boolean);
                if (hasWip && !confirm(`Lowering the max will close ${excess} column${excess > 1 ? 's' : ''} with unsubmitted text. Continue?`)) {
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
                    this.columns.pop();
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

        async reloadSession(themeDir, conceptDir) {
            this.browserLoading = true;
            try {
                const fd = new FormData();
                fd.append("session_id", this.sessionId);
                fd.append("column_id", this.$store.activeColIdx);
                fd.append("theme_dir", themeDir);
                fd.append("concept_dir", conceptDir);

                const res = await fetch("/session/reload", {
                    method: "POST",
                    body: fd,
                });
                const data = await res.json();

                if (data.error) {
                    alert("Error reloading session: " + data.error);
                    return;
                }

                // Dispatch event to the targeted column designer component to rehydrate its state
                window.dispatchEvent(
                    new CustomEvent("rehydrate-column", {
                        detail: {
                            columnIdx: this.$store.activeColIdx,
                            state: data,
                        },
                    })
                );

                this.showBrowser = false;
                this.browserMode = null;
            } catch (err) {
                console.error("Reload failed:", err);
                alert("Reload failed: " + err.message);
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
