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


// ── Alpine component ──────────────────────────────────────────────────────────
function designer() {
    const cfg = JSON.parse(document.getElementById('app-config').textContent);

    return {
        // ── Session ────────────────────────────────────────────────────────
        // Generate a UUID once per page load so the server can associate state
        // (PIL images in memory) with this browser tab.
        sessionId: crypto.randomUUID(),

        // ── Workflow state ─────────────────────────────────────────────────
        step: 1,               // controls which sections are visible
        theme: "Funny shark in space",
        concepts: [],
        selectedConcept: null,
        editedConcept: "",
        variants: [],          // [{url, origUrl, noBgUrl, ts}]
        prompts: [],
        selectedVariant: null,
        finalUrl: null,
        finalTs: 0,
        finalizedSize: "",  // resolution the final image was actually generated at
        _origFinalUrl: null,
        _noBgFinalUrl: null,
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
        finalSize: cfg.defaultFinalSize,

        // ── Preset management ──────────────────────────────────────────────
        presetNames: cfg.presetNames,
        activePreset: cfg.builtinName,
        newPresetName: "",
        presetStatus: "",
        conceptsTemplate: cfg.conceptsTemplate,
        variantsTemplate: cfg.variantsTemplate,
        styleTemplate: cfg.styleTemplate,

        // ── Load-from-browser state ───────────────────────────────────────
        loadedImageRes: null,   // {width, height} when variants came from the output browser; null otherwise

        // ── Drag state (Printify placement preview) ───────────────────────
        pDrag: null,            // null when idle; { startX, startY, startPX, startPY } while dragging

        // ── Output browser ────────────────────────────────────────────────
        showBrowser: false,
        browserThemes: [],
        browserFilter: "",
        browserLoading: false,
        manageMode: false,
        selectedFiles: {},    // url → size_bytes; object for Alpine reactivity
        storageStats: null,   // {totalBytes, themeCount}
        renamingDir: "",      // dir_name of the theme currently being renamed
        renameValue: "",      // current value of the rename input

        // ── Printify state ─────────────────────────────────────────────────
        cfg,                        // expose to template for printifyEnabled check
        showPrintify: false,
        printifyBusy: false,
        printifyStatus: "",
        printifyError: "",
        printifyDone: null,

        pShops: [],
        pShopId: cfg.printifyShopId || "",

        pAllBlueprints: [],         // full catalog (fetched once)
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

        // Print area dimensions — extracted from the variant placeholder data.
        // Used to calculate the correct y offset for top/center positioning.
        pPrintWidth: 0,
        pPrintHeight: 0,

        pXPx: 0,                    // left edge of design in print-area pixels (0 = flush left)
        pYPx: 0,                    // top edge of image in print-area pixels (0 = flush top)
        pTopAllowance: 10,          // px gap from print-area top for "Align to Top" preset; persists across opens
        pIsTopPreset: true,         // true when X/Y match the "Align to Top" formula
        pScale: 0.8,                // fraction of print area width the design occupies
        pContentTop: 0,             // fraction of image height above first visible pixel (for gap correction)

        pOverrideMinRes: false, // dev override: skip the resolution gate when testing

        pTitle: "",
        pDescription: "",
        pPrice: "29.99",

        // ── Lifecycle ──────────────────────────────────────────────────────
        init() {
            this.$nextTick(() => this.$refs.themeInput.focus());

            // Warn before unload if the user has typed a theme — reloading would
            // clear both the textarea and all server-side session state (images, concepts).
            window.addEventListener('beforeunload', (e) => {
                if (this.theme.trim()) {
                    e.preventDefault();
                    e.returnValue = '';
                }
            });

            // Bound drag handlers — stored so removeEventListener can reference them by identity.
            this._boundDragMove = this._onDragMove.bind(this);
            this._boundDragUp   = this._onDragUp.bind(this);

            // Keep the "Align to Top" preset in sync when scale or allowance changes.
            this.$watch('pScale', () => {
                if (this.pIsTopPreset && this.pPrintWidth) this.applyTopPreset();
            });
            this.$watch('pTopAllowance', () => {
                if (this.pIsTopPreset && this.pPrintWidth) this.applyTopPreset();
            });
            // pContentTop arrives async after modal open; re-calibrate preset if still active.
            this.$watch('pContentTop', () => {
                if (this.pIsTopPreset && this.pPrintWidth) this.applyTopPreset();
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

        // Derived from URL comparison — no separate flag needed.
        get finalBgRemoved() {
            return this._noBgFinalUrl !== null && this.finalUrl === this._noBgFinalUrl;
        },

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

        get pDesignPx() {
            return this.pScale * this.pPrintWidth;
        },

        get pNeedsUpscale() {
            return (this.cfg.sizePx[this.finalizedSize] ?? 0) < this.cfg.sizePx[this.cfg.printifyMinSize];
        },

        get pImageOutOfBounds() {
            if (!this.pPrintWidth || !this.pPrintHeight) return false;
            const designPx = this.pDesignPx;
            return this.pXPx < 0 || this.pXPx + designPx > this.pPrintWidth
                || this.pYPx < 0 || this.pYPx + designPx > this.pPrintHeight;
        },

        get selectedVariantCount() {
            // Count variants whose color AND size are both selected.
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
            // loadingStep is intentionally NOT cleared here — errors need it to know
            // which section to display in. Clear it only when the error is dismissed.
        },

        _onError(msg) {
            this._stopLoading();
            this.error = msg;
        },

        dismissError() {
            this.error = "";
            this.loadingStep = 0;
        },

        async loadToVariants(url, width, height, displayTheme) {
            // Only warn if the user has real work in progress — don't count the default theme text.
            const hasWork = this.concepts.length || this.variants.length;
            if (hasWork && !confirm("Load this image as a variant? Your current session will be cleared.")) return;

            const res = await fetch("/session/load-image", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ session_id: this.sessionId, image_url: url, display_theme: displayTheme }),
            });
            if (!res.ok) { alert(`Failed to load image (${res.status})`); return; }
            const data = await res.json();
            if (data.error) { alert(data.error); return; }

            // Reset workflow state to variant-only.
            this.concepts = [];
            this.selectedConcept = null;
            this.editedConcept = "";
            this.prompts = [];
            this.finalUrl = null;
            this._origFinalUrl = null;
            this._noBgFinalUrl = null;
            this.finalTs = 0;
            this.finalizedSize = "";
            this.error = "";
            this.theme = displayTheme;
            this.variants = [{ url, origUrl: url, noBgUrl: null, ts: Date.now() }];
            this.selectedVariant = 0;
            this.loadedImageRes = { width, height };

            this.step = 4;
            this.showBrowser = false;
            this.$nextTick(() => this.$refs.step4?.scrollIntoView({ behavior: "smooth", block: "start" }));
        },

        async openBrowser() {
            this.showBrowser = true;
            if (this.browserThemes.length > 0) return; // already loaded
            await this.reloadBrowser();
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
                    concepts: t.concepts.map(c => ({ ...c, expanded: false })),
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
            theme.concepts.forEach(c => c.variants.forEach(v => {
                files.push([v.url, v.size]);
                if (v.no_bg_url) files.push([v.no_bg_url, v.no_bg_size]);
            }));
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

        _bgFormData() {
            const fd = new FormData();
            fd.append("session_id", this.sessionId);
            fd.append("bg_color", this.bgColor);
            fd.append("bg_tolerance", this.bgTolerance);
            fd.append("edge_erode", this.edgeErode);
            fd.append("decontaminate", this.decontaminate);
            return fd;
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
            this.finalUrl = null;
            this.loadedImageRes = null;
            this.step = 1;

            const fd = new FormData();
            fd.append("session_id", this.sessionId);
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
            this.loadedImageRes = null;
            this.step = 2;  // step >= 2 satisfies the directMode badge condition; doGenerate() will advance to 3
            await this.doGenerate();
        },

        async doGenerate() {
            if (this.isLoading || !this.editedConcept.trim()) return;
            this.loadingStep = 3;
            this._startLoading("Building prompts...");

            this.variants = [];
            this.prompts = [];
            this.selectedVariant = null;
            this.finalUrl = null;
            this.loadedImageRes = null;
            this.step = Math.max(this.step, 3);

            const fd = new FormData();
            fd.append("session_id", this.sessionId);
            fd.append("concept", this.editedConcept);
            fd.append("original_concept", this.selectedConcept ?? this.editedConcept);
            fd.append("bg_color", this.bgColor);
            fd.append("num_variants", this.numVariants);
            fd.append("max_colors", this.maxColors);
            fd.append("variants_template", this.variantsTemplate);
            fd.append("style_template", this.styleTemplate);
            fd.append("aspect_ratio", this.aspectRatio);
            fd.append("variant_size", this.variantSize);

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
                    this.step = 4;
                    this._stopLoading();
                },
                error: (e) => { this._onError(e.message); },
            });
        },

        async doFinalize() {
            if (this.isLoading) return;
            const idx = this.selectedVariant ?? 0;
            // If already at step 5, keep the loading indicator anchored there so
            // the user sees feedback without scrolling back up to step 4.
            this.loadingStep = this.step >= 5 ? 5 : 4;
            this._startLoading(`Generating ${this.finalSize} design...`);

            const fd = this._bgFormData();
            fd.append("selected_idx", idx);
            fd.append("aspect_ratio", this.aspectRatio);
            fd.append("final_size", this.finalSize);

            await streamSSE("/finalize", fd, {
                status: (e) => { this.loadingMsg = e.message; },
                final: (e) => {
                    this.finalUrl = e.url;
                    this._origFinalUrl = e.url;
                    this._noBgFinalUrl = null;
                    this.finalTs = Date.now();
                    this.finalizedSize = this.finalSize;
                    this.step = 5;
                    this._stopLoading();
                },
                error: (e) => { this._onError(e.message); },
            });
        },

        async doFinalizeForPrintify() {
            if (this.printifyBusy) return;
            this.printifyBusy = true;
            this.printifyError = "";
            this.printifyStatus = `Re-finalizing at ${cfg.printifyMinSize}…`;

            const idx = this.selectedVariant ?? 0;
            const fd = this._bgFormData();
            fd.append("selected_idx", idx);
            fd.append("aspect_ratio", this.aspectRatio);
            fd.append("final_size", cfg.printifyMinSize);

            await streamSSE("/finalize", fd, {
                status: (e) => { this.printifyStatus = e.message; },
                final: (e) => {
                    this.finalUrl = e.url;
                    this._origFinalUrl = e.url;
                    this._noBgFinalUrl = null;
                    this.finalTs = Date.now();
                    this.finalizedSize = cfg.printifyMinSize;
                    this.step = 5;
                    this.printifyStatus = "";
                },
                error: (e) => { this.printifyError = e.message; },
            });

            this.printifyBusy = false;
        },

        async doRemoveVariantBg() {
            if (this.isLoading) return;
            const idx = this.selectedVariant ?? 0;
            const v = this.variants[idx];
            if (v?.noBgUrl) {
                const updated = [...this.variants];
                updated[idx] = { ...v, url: v.noBgUrl, ts: Date.now() };
                this.variants = updated;
                const fd = new FormData();
                fd.append("session_id", this.sessionId);
                fd.append("selected_idx", idx);
                fetch("/apply-cached-bg/variant", { method: "POST", body: fd })
                    .catch(() => this._onError("Failed to sync variant state."));
                return;
            }

            this.loadingStep = 4;
            this._startLoading("Removing background...");

            const fd = this._bgFormData();
            fd.append("selected_idx", idx);

            await streamSSE("/remove-bg/variant", fd, {
                status: (e) => { this.loadingMsg = e.message; },
                variant_updated: (e) => {
                    const updated = [...this.variants];
                    const prev = updated[e.index] ?? {};
                    updated[e.index] = {
                        ...prev,
                        url: e.url,
                        ts: Date.now(),
                        noBgUrl: e.bg_removed ? e.url : prev.noBgUrl,
                    };
                    this.variants = updated;
                    this._stopLoading();
                },
                error: (e) => { this._onError(e.message); },
            });
        },

        async doRestoreVariantBg() {
            if (this.isLoading) return;
            const idx = this.selectedVariant ?? 0;
            const v = this.variants[idx];
            // Instant UI swap — both URLs are already known after first removal.
            const updated = [...this.variants];
            updated[idx] = { ...v, url: v.origUrl, ts: Date.now() };
            this.variants = updated;
            // Sync server session in background so finalize uses the correct image.
            const fd = new FormData();
            fd.append("session_id", this.sessionId);
            fd.append("selected_idx", idx);
            fetch("/restore-bg/variant", { method: "POST", body: fd })
                .catch(() => this._onError("Failed to sync variant state."));
        },

        async doRestoreFinalBg() {
            if (this.isLoading) return;
            // Instant UI swap.
            this.finalUrl = this._origFinalUrl;
            this.finalTs = Date.now();
            // Sync server session in background.
            const fd = new FormData();
            fd.append("session_id", this.sessionId);
            fetch("/restore-bg/final", { method: "POST", body: fd })
                .catch(() => this._onError("Failed to sync final state."));
        },

        async doRemoveFinalBg() {
            if (this.isLoading) return;
            // Instant swap if we've already removed bg for this final image before.
            if (this._noBgFinalUrl) {
                this.finalUrl = this._noBgFinalUrl;
                this.finalTs = Date.now();
                const fd = new FormData();
                fd.append("session_id", this.sessionId);
                fetch("/apply-cached-bg/final", { method: "POST", body: fd })
                    .catch(() => this._onError("Failed to sync final state."));
                return;
            }
            this.loadingStep = 5;
            this._startLoading("Removing background...");

            await streamSSE("/remove-bg/final", this._bgFormData(), {
                status: (e) => { this.loadingMsg = e.message; },
                final_updated: (e) => {
                    this.finalUrl = e.url;
                    this.finalTs = Date.now();
                    if (e.bg_removed) this._noBgFinalUrl = e.url;
                    this._stopLoading();
                },
                error: (e) => { this._onError(e.message); },
            });
        },

        // ── Preset actions ─────────────────────────────────────────────────
        async loadPreset(name) {
            if (!name) return;
            const res = await fetch(`/presets/${encodeURIComponent(name)}`);
            const data = await res.json();
            if (!data.error) {
                this.conceptsTemplate = data.concepts_prompt;
                this.variantsTemplate = data.variants_prompt;
                this.styleTemplate = data.style_suffix;
            }
        },

        async savePreset() {
            const name = this.newPresetName.trim();
            if (!name) { this.presetStatus = "Enter a preset name."; return; }

            const fd = new FormData();
            fd.append("name", name);
            fd.append("concepts", this.conceptsTemplate);
            fd.append("variants", this.variantsTemplate);
            fd.append("style", this.styleTemplate);

            const res = await fetch("/presets", { method: "POST", body: fd });
            const data = await res.json();
            if (data.error) {
                this.presetStatus = data.error;
            } else {
                this.presetNames = data.names;
                this.activePreset = data.saved;
                this.newPresetName = "";
                this.presetStatus = `Saved "${name}".`;
            }
        },

        async deletePreset() {
            const name = this.activePreset;
            if (name === cfg.builtinName) { this.presetStatus = "Cannot delete the built-in preset."; return; }

            const res = await fetch(`/presets/${encodeURIComponent(name)}`, { method: "DELETE" });
            const data = await res.json();
            this.presetNames = data.names;
            this.activePreset = cfg.builtinName;
            await this.loadPreset(cfg.builtinName);
            this.presetStatus = `Deleted "${name}".`;
        },

        // ── Printify actions ───────────────────────────────────────────────

        async openPrintify() {
            // Reset publish result each time the modal opens.
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

            // Fetch content bounds from the final image alpha channel so we can
            // shift the Y position to align the subject's top with the print area top.
            // Non-critical — runs in parallel, falls back to 0 on any error.
            if (this.finalUrl) {
                fetch(`/analysis/final?session_id=${encodeURIComponent(this.sessionId)}`)
                    .then(r => r.json())
                    .then(data => { this.pContentTop = data.content_top ?? 0; })
                    .catch(() => {});
            }

            // Load shops (skip if shop ID already configured server-side).
            if (!cfg.printifyShopId && this.pShops.length === 0) {
                const res = await fetch("/printify/shops");
                const data = await res.json();
                if (data.error) { this.printifyError = data.error; return; }
                this.pShops = data;
                // Auto-select by configured name (case-insensitive), then fall back to
                // selecting the only shop if there's just one.
                const preferredName = (cfg.printifyShopName || "").toLowerCase();
                const match = preferredName && data.find(s => s.title.toLowerCase() === preferredName);
                if (match) this.pShopId = String(match.id);
                else if (data.length === 1) this.pShopId = String(data[0].id);
            }

            // Load blueprint catalog (fetched once; subsequent opens reuse cache).
            if (this.pAllBlueprints.length === 0) {
                this.printifyStatus = "Loading catalog…";
                const res = await fetch("/printify/blueprints");
                const data = await res.json();
                this.printifyStatus = "";
                if (data.error) { this.printifyError = data.error; return; }
                this.pAllBlueprints = data;
            }

            // Default search to show common shirt styles on open.
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
            // Sort by blueprint ID ascending — lower IDs are older, more established products,
            // which is the closest proxy to popularity the Printify API exposes.
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

            // Extract the front print area dimensions from the first variant that has them.
            // All variants for a blueprint+provider share the same print area dimensions.
            for (const v of data) {
                const front = (v.placeholders ?? []).find(p => p.position === "front");
                if (front?.width && front?.height) {
                    this.pPrintWidth = front.width;
                    this.pPrintHeight = front.height;
                    break;
                }
            }
            if (this.pPrintWidth) this.applyTopPreset();

            // Extract unique colors and sizes, preserving natural order from the API.
            const colors = [], sizes = [], seenC = new Set(), seenS = new Set();
            for (const v of data) {
                const c = v.options?.color ?? "";
                const s = v.options?.size ?? "";
                if (c && !seenC.has(c)) { seenC.add(c); colors.push(c); }
                if (s && !seenS.has(s)) { seenS.add(s); sizes.push(s); }
            }
            this.pColors = colors;
            this.pSizes = sizes;
            // Default: no colors selected; sizes default to S–2XL only.
            this.pSelectedColors = [];
            const defaultSizes = new Set(["S", "M", "L", "XL", "2XL", "XXL"]);
            this.pSelectedSizes = sizes.filter(s => defaultSizes.has(s.toUpperCase()));
        },

        startDrag(e) {
            const scale = 220 / this.pPrintWidth;  // preview px per print px
            const designPx = this.pDesignPx;
            // Cache all per-drag constants so _onDragMove (called on every mousemove)
            // doesn't recompute them hundreds of times per second.
            this.pDrag = {
                startX: e.clientX,
                startY: e.clientY,
                startPX: this.pXPx,
                startPY: this.pYPx,
                scale,
                designPx,
                snapThreshold: 8 / scale,  // 8 screen px → print px; feels consistent across sizes
                centerX: Math.round((this.pPrintWidth  - designPx) / 2),
                centerY: Math.round((this.pPrintHeight - designPx) / 2),
                minX: -(designPx - 1),
                maxX: this.pPrintWidth - 1,
                minY: -(designPx - 1),
                maxY: this.pPrintHeight - 1,
            };
            document.addEventListener('mousemove', this._boundDragMove);
            document.addEventListener('mouseup',   this._boundDragUp);
        },

        _onDragMove(e) {
            if (!this.pDrag) return;
            const { scale, snapThreshold, centerX, centerY, minX, maxX, minY, maxY } = this.pDrag;

            const rawX = this.pDrag.startPX + (e.clientX - this.pDrag.startX) / scale;
            const rawY = this.pDrag.startPY + (e.clientY - this.pDrag.startY) / scale;
            // Clamp so at least 1 print-pixel of the image stays inside the print area.
            let x = Math.round(Math.min(Math.max(rawX, minX), maxX));
            let y = Math.round(Math.min(Math.max(rawY, minY), maxY));

            // Snap to center when within threshold.
            if (Math.abs(rawX - centerX) <= snapThreshold) x = centerX;
            if (Math.abs(rawY - centerY) <= snapThreshold) y = centerY;

            this.pXPx = x;
            this.pYPx = y;
            this.pIsTopPreset = false;
        },

        _onDragUp() {
            this.pDrag = null;
            document.removeEventListener('mousemove', this._boundDragMove);
            document.removeEventListener('mouseup',   this._boundDragUp);
        },

        applyTopPreset() {
            if (!this.pPrintWidth) return;
            // Center horizontally; back out transparent padding so visible content
            // lands at pTopAllowance from the top of the print area.
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

            // Gather the variant IDs that match the selected color+size combinations.
            const variantIds = this.pAllVariants
                .filter(v =>
                    this.pSelectedColors.includes(v.options?.color ?? "") &&
                    this.pSelectedSizes.includes(v.options?.size ?? "")
                )
                .map(v => v.id);

            const priceCents = Math.round(parseFloat(this.pPrice) * 100);
            if (!priceCents || priceCents < 1) { this.printifyError = "Enter a valid price."; return; }

            // Convert pixel coords to Printify normalized center coordinates (0–1).
            // pXPx = left edge of design image; pYPx = top of visible content (transparent
            // fringe excluded). pContentTop corrects for transparent padding above the subject.
            //   design_x = (pXPx + designPx/2) / W
            //   design_y = (pYPx + designPx*(0.5 - pContentTop)) / H
            const scale = this.pScale;
            const W = this.pPrintWidth;
            const H = this.pPrintHeight;
            if (!W || !H) { this.printifyError = "Print dimensions not loaded."; return; }
            const designX = (this.pXPx + this.pDesignPx / 2) / W;
            const designY = (this.pYPx + this.pDesignPx / 2) / H;
            this.printifyBusy = true;

            const fd = new FormData();
            fd.append("session_id", this.sessionId);
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
            fd.append("final_url", this.finalUrl ?? "");
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
