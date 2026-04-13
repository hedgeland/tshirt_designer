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

        // ── Preset management ──────────────────────────────────────────────
        presetNames: cfg.presetNames,
        activePreset: cfg.builtinName,
        newPresetName: "",
        presetStatus: "",
        conceptsTemplate: cfg.conceptsTemplate,
        variantsTemplate: cfg.variantsTemplate,
        styleTemplate: cfg.styleTemplate,

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

        pPosition: "top",           // "top" or "center"
        pScale: 0.8,                // fraction of print area width the design occupies
        pContentTop: 0,             // fraction of image height above first visible pixel (for gap correction)

        pTitle: "",
        pDescription: "",
        pPrice: "29.99",

        // ── Lifecycle ──────────────────────────────────────────────────────
        init() {
            // Warn before unload if the user has typed a theme — reloading would
            // clear both the textarea and all server-side session state (images, concepts).
            window.addEventListener('beforeunload', (e) => {
                if (this.theme.trim()) {
                    e.preventDefault();
                    e.returnValue = '';
                }
            });
        },

        // ── Computed ───────────────────────────────────────────────────────
        get generateBtnLabel() {
            const n = this.numVariants;
            return `Generate ${n} ${n === 1 ? "Variant" : "Variants"}`;
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
            this.step = 2;  // show Step 2 so the direct mode badge is visible
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
                    this.variants = e.urls.map((url) => ({ url, ts }));
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
            this.loadingStep = 4;
            this._startLoading("Generating 4K design...");

            const fd = this._bgFormData();
            fd.append("selected_idx", idx);

            await streamSSE("/finalize", fd, {
                status: (e) => { this.loadingMsg = e.message; },
                final: (e) => {
                    this.finalUrl = e.url;
                    this.finalTs = Date.now();
                    this.step = 5;
                    this._stopLoading();
                },
                error: (e) => { this._onError(e.message); },
            });
        },

        async doRemoveVariantBg() {
            if (this.isLoading) return;
            const idx = this.selectedVariant ?? 0;
            this.loadingStep = 4;
            this._startLoading("Removing background...");

            const fd = this._bgFormData();
            fd.append("selected_idx", idx);

            await streamSSE("/remove-bg/variant", fd, {
                status: (e) => { this.loadingMsg = e.message; },
                variant_updated: (e) => {
                    // Bump timestamp so the browser re-fetches the updated image
                    const updated = [...this.variants];
                    updated[e.index] = { url: e.url, ts: Date.now() };
                    this.variants = updated;
                    this._stopLoading();
                },
                error: (e) => { this._onError(e.message); },
            });
        },

        async doRemoveFinalBg() {
            if (this.isLoading) return;
            this.loadingStep = 5;
            this._startLoading("Removing background...");

            await streamSSE("/remove-bg/final", this._bgFormData(), {
                status: (e) => { this.loadingMsg = e.message; },
                final_updated: (e) => {
                    this.finalUrl = e.url;
                    this.finalTs = Date.now();
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
                if (data.length === 1) this.pShopId = String(data[0].id);
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
            if (!q) {
                this.pFilteredBlueprints = this.pAllBlueprints.slice(0, 50);
                return;
            }
            const terms = q.split(/\s+/);
            this.pFilteredBlueprints = this.pAllBlueprints.filter(bp =>
                terms.every(t => bp.title.toLowerCase().includes(t) || (bp.brand || "").toLowerCase().includes(t))
            ).slice(0, 50);
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

            // Calculate y so the design sits at the requested vertical position.
            // x/y in Printify are the CENTER of the image as fractions of the print area.
            // scale is the fraction of the print area WIDTH that the square design occupies.
            // For a square design the displayed height = scale * printWidth pixels.
            //
            // Without transparency: y_center = (scale * W) / (2 * H) anchors image top at y=0.
            // With transparency: pContentTop is the fraction of the image height above the first
            // visible pixel. Shifting up by that amount anchors the SUBJECT top at y=0 instead,
            // eliminating the visible gap from empty space at the top of the image.
            //   y_center = scale * W * (0.5 - contentTop) / H
            const scale = this.pScale;
            const contentTop = this.pContentTop;
            let designY;
            if (this.pPosition === "top" && this.pPrintWidth && this.pPrintHeight) {
                designY = scale * this.pPrintWidth * (0.5 - contentTop) / this.pPrintHeight;
                designY = Math.max(0.01, designY); // don't push the image fully off the top
            } else {
                designY = 0.5;
            }

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
            fd.append("design_x", 0.5);
            fd.append("design_y", designY.toFixed(4));
            fd.append("design_scale", scale);

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
