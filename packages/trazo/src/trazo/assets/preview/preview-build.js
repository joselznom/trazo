// Preview viewer: value and citation tooltips, and the full build.
// The parts of the viewer are classic scripts loaded in the order of
// trazo.preview.VIEWER_SCRIPTS; their top-level names are shared globals.
  if (!window.PreviewModel) throw new Error("preview-model.js did not load");  // see preview-page.js
  // ---------------------------------------------------------------- tooltips
  // Substituted values and in-text citations (hover or focus).
  let valuesPromise = null;
  const loadValues = () => (valuesPromise = valuesPromise
    || fetch("/__values", { cache: "no-store" }).then((r) => r.json()).catch(() => ({})));
  $$(".pv-val", page).forEach((el) => { el.tabIndex = 0; });
  const TIP_TARGET = ".pv-val, .citation a[href^='#ref-']";
  async function showTip(target) {
    tipFor = target;
    tip.textContent = "";
    const line = (text, bold) => {
      const d = document.createElement("div");
      if (bold) d.style.fontWeight = "600";
      d.textContent = text;
      tip.append(d);
    };
    if (target.classList.contains("pv-val")) {
      const key = target.dataset.key;
      const info = (await loadValues())[key];
      if (tipFor !== target) return;
      tip.append(h("code", { text: `{{v:${key}}}` }));
      line(info ? `Value: ${info.text}` : "Unknown key");
      if (info && info.source) line(`Source: ${info.source}`);
    } else {
      const group = target.closest(".citation");
      for (const a of $$("a[href^='#ref-']", group)) {
        const entry = document.getElementById(a.getAttribute("href").slice(1));
        const body = entry && entry.querySelector(".csl-right-inline");
        line(`${a.textContent} ${body ? body.textContent.trim() : "(entry not found)"}`, a === target);
      }
    }
    placeTip(target);
  }
  document.addEventListener("mouseover", (e) => {
    const t = e.target.closest && e.target.closest(TIP_TARGET);
    if (t && t !== tipFor) showTip(t);
  });
  document.addEventListener("mouseout", (e) => {
    const t = e.target.closest && e.target.closest(TIP_TARGET);
    if (t && !(e.relatedTarget && t.contains(e.relatedTarget))) hideTip();
  });
  document.addEventListener("focusin", (e) => {
    const t = e.target.closest && e.target.closest(TIP_TARGET);
    if (t) showTip(t);
  });
  document.addEventListener("focusout", (e) => { if (e.target.closest && e.target.closest(TIP_TARGET)) hideTip(); });
  document.addEventListener("mousedown", hideTip);
  window.addEventListener("resize", hideTip);

  // Links inside the page (references, "Figure 1") scroll within the page.
  page.addEventListener("click", (e) => {
    const a = e.target.closest("a[href^='#']");
    if (!a || e.metaKey || e.ctrlKey || e.shiftKey) return;
    let id = a.getAttribute("href").slice(1);
    try { id = decodeURIComponent(id); } catch (err) { /* keep as is */ }
    const target = document.getElementById(id);
    if (!target || !page.contains(target)) return;
    e.preventDefault();
    scrollToEl(target, true);
    flash(target);
  });

  // ---------------------------------------------------------------- build
  // Build status: the dot (amber = warnings, red = failed), its tooltip and
  // the banner below the top bar.
  const dot = $(".pv-dot", buildBtn), dotLabel = $(".pv-bs-label", buildBtn);
  // The last status shows at once after a reload, until the first poll answers.
  const buildKey = "preview-build:" + doc;
  function showBuild(state, label, text) {
    dot.className = `pv-dot ${state}`;
    dotLabel.textContent = label;
    buildBtn.dataset.tip = text;
    buildBtn.setAttribute("aria-label", `Build status: ${label}`);
    sset(buildKey, JSON.stringify([state, label, text]));
  }
  try {
    const last = JSON.parse(sget(buildKey));
    if (Array.isArray(last) && last.length === 3) showBuild(...last.map(String));
  } catch (e) { /* no or unreadable last status */ }
  const buildTip = () => { tipFor = buildBtn; tip.textContent = buildBtn.dataset.tip || ""; placeTip(buildBtn); };
  buildBtn.addEventListener("mouseenter", buildTip);
  buildBtn.addEventListener("focus", buildTip);
  buildBtn.addEventListener("mouseleave", hideTip);
  buildBtn.addEventListener("blur", hideTip);
  buildBtn.addEventListener("click", buildTip);

  // Export > Full manuscript: runs its full build on the server; progress arrives via /__status.
  // The log text and the action link are separate nodes: the log is redrawn
  // as it grows, the link (cancel or close) stays clickable.
  const docxText = document.createElement("span");
  const docxAct = document.createElement("button");
  docxAct.type = "button";
  let docxAction = null;
  docxAct.addEventListener("click", () => { if (docxAction) docxAction(); });
  const docxMessage = (cls, text, act, action) => {
    docxBox.className = `pv-banner${cls ? " " + cls : ""}`;
    if (docxText.textContent !== text) docxText.textContent = text;
    docxAct.textContent = act;
    docxAct.hidden = !act;
    docxAction = action;
    if (text && !docxBox.firstChild) docxBox.append(docxText, docxAct);
    if (!text) docxBox.textContent = "";  // :empty hides the banner
  };
  // A dismissed (or auto-hidden) result stays hidden across live reloads.
  let docxDismissed = sget("preview-docx-dismissed") || "";
  const docxRun = (d) => `${d.started}|${d.finished}`;
  const dismissDocx = (d) => {
    docxDismissed = docxRun(d);
    sset("preview-docx-dismissed", docxDismissed);
    renderDocx(d);
  };
  const cancelDocx = async () => {
    try {
      await fetch("/__cancel-docx", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: "{}" });
    } catch (e) { /* the next poll shows the state */ }
  };
  let docxShown = null, docxHideTimer = null;
  docxBtn.addEventListener("click", async () => {
    if (docxBtn.classList.contains("busy")) return;
    try {
      const res = await fetch("/__build-docx", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ doc, ...wantedBuild(documentCounts()) }) });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) { docxMessage("error", payload.error || `Error ${res.status}`, "", null); return; }
      renderDocx(payload);
    } catch (e) {
      docxMessage("error", "Could not reach the preview server: " + e.message, "", null);
    }
  });
  function renderDocx(d) {
    if (!d) return;
    // Polled every 0.8 s: redraw only on change, so log text stays selectable.
    const sig = JSON.stringify([d, docxDismissed]);
    if (sig === docxShown) return;
    docxShown = sig;
    clearTimeout(docxHideTimer);
    const running = d.state === "running";
    docxBtn.classList.toggle("busy", running);
    docxBtn.disabled = running;
    $(".pv-mi-t", docxBtn).textContent = running ? `Building ${(DOCS[d.doc] || "document").toLowerCase()}…` : fullLabel;
    const note = $(".pv-mi-foot", app);
    note.textContent = running ? `Building since ${d.started}…` : d.state === "ok" ? `Last build ${d.finished}`
      : d.state === "error" ? `Last build failed ${d.finished}` : "";
    note.hidden = !note.textContent;
    const finished = d.state === "ok" || d.state === "error";
    if ((!running && !finished) || (finished && docxDismissed === docxRun(d))) {
      docxMessage("", "", "", null);
      return;
    }
    const head = running ? `Building DOCX (${d.shown || "full build"}) since ${d.started}…`
      : d.state === "ok" ? `DOCX built at ${d.finished} (exit 0).`
      : `DOCX build failed at ${d.finished} (exit ${d.exit_code}).`;
    const lines = running || d.state === "error" ? d.tail.slice(-(running ? 6 : 40)) : [];
    const text = [head, ...lines].join("\n");
    const cls = d.state === "error" ? "error" : "warn";
    if (running) docxMessage(cls, text, "  [cancel build]", cancelDocx);
    else {
      docxMessage(cls, text, "  [close]", () => dismissDocx(d));
      if (d.state === "ok") docxHideTimer = setTimeout(() => dismissDocx(d), 8000);
    }
  }

  // Everything the side panel needs is declared above; only now may a
  // restored tab (Source or Diff) render.
  sourceReady = true;
