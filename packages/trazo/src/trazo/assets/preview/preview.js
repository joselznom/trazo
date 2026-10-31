// Preview viewer: switching views, starting the viewer and live reload.
// The parts of the viewer are classic scripts loaded in the order of
// trazo.preview.VIEWER_SCRIPTS; their top-level names are shared globals.
  if (!window.PreviewModel) throw new Error("preview-model.js did not load");  // see preview-page.js
  // ---------------------------------------------------------------- views
  function applyView() {
    typeMenu.open = false;
    docView.hidden = view !== "doc";
    figsView.hidden = view !== "figures";
    submissionView.hidden = view !== "submission";
    zoomBar.hidden = view === "submission";
    zoomFit.hidden = typeMenu.hidden = view !== "doc";
    if (view === "doc") applyZoom(false); else applyFigsZoom();
    renderTabs();
    renderWords();
    if (view === "figures") { renderFigs(); renderComments(); }
    else if (view === "submission") { renderSubmission(); renderComments(); openSubmission(); }
    else { renderComments(); spy(); }
  }
  function setView(v, push) {
    if (v === view) return;
    saveReadingAnchor();
    pos[view] = scroller.scrollTop;
    view = v;
    undoLabel = undoId = null;
    if (push) history.pushState({ view: v }, "", v === "doc" ? Trazo.path() : `?view=${v}`);
    fab.hidden = true;
    applyView();
    App.reveal(v === "doc" ? docView : v === "figures" ? figsView : submissionView);
    loadComments();
    scroller.scrollTop = pos[v] || 0;
    closeDrawer();
  }
  window.addEventListener("popstate", () => {
    setView(viewFromUrl(), false);
  });

  // ---------------------------------------------------------------- start
  // Land on a place asked for by the address: #<id> (a figure, table or
  // section), ?at=<label> (a paragraph; links between documents) or the old
  // ?s=<section id>.
  function landOn() {
    if (view !== "doc") return false;
    const at = params.get("at");
    let id = location.hash.slice(1);
    try { id = decodeURIComponent(id); } catch (e) { /* keep as is */ }
    let target = null;
    if (at) target = model.labelled.find((el) => el.dataset.label === at) || null;
    else if (id) target = document.getElementById(id);
    else if (params.get("s")) {
      const section = document.getElementById(params.get("s"));
      target = section && (section.querySelector("h1, h2") || section);
    }
    if (!target || !page.contains(target)) return false;
    history.replaceState(null, "", Trazo.path());
    scrollToEl(target, !!(at || id));
    flash(target);
    return true;
  }
  const savedPos = (v) => { const n = Number(sget(scrollKey(v))); return Number.isFinite(n) ? n : 0; };
  pos.doc = savedPos("doc");
  pos.figures = savedPos("figures");
  pos.submission = savedPos("submission");
  applyLayout();
  applyView();
  // A row of the Submission view on another document's page asked for its
  // place (and source line) here; see goToItem().
  let handoff = null;
  try { handoff = JSON.parse(sget("preview-open-source:" + Trazo.path())); } catch (e) { handoff = null; }
  sdel("preview-open-source:" + Trazo.path());
  const handoffEl = handoff && view === "doc" ? landOnPlace(handoff) : null;
  // Restore the scroll position after a live reload (unless the address or
  // a Submission row asks for a place); the section in view then decides
  // which file the Source and Diff tabs start with.
  if (!landOn() && !handoffEl) {
    let saved = null;
    try { saved = JSON.parse(sget(readingKey)); } catch (e) { /* pixel fallback */ }
    const canRestore = view === "doc" && saved && Number.isFinite(saved.offset);
    let anchor = canRestore
      && ((saved.id && document.getElementById(saved.id))
        || (saved.anchor?.text ? locateIn(model, { quote: saved.anchor.text, anchor: saved.anchor, para: saved.label })?.el
          : model.labelled.find((el) => el.dataset.label === saved.label)));
    // At a document boundary, the edge and one unchanged neighbour can
    // identify a rewritten paragraph even when its old words are gone.
    if (!anchor && canRestore && saved.anchor) {
      const old = saved.anchor;
      const candidate = !old.before ? model.labelled[0] : !old.after ? model.labelled.at(-1) : null;
      const current = candidate && contextOf(model, candidate);
      if (current && current.before === old.before && current.after === old.after) anchor = candidate;
    }
    const restorePosition = () => {
      scroller.scrollTop = pos[view];
      if (anchor && page.contains(anchor)) {
        const rect = anchor.getBoundingClientRect();
        // Keep the paragraph visible if it was shortened.
        const offset = Math.max(saved.offset, Math.min(0, 24 * pageScale - rect.height));
        scroller.scrollTop += rect.top - scroller.getBoundingClientRect().top - offset;
      }
    };
    restorePosition();
    // Images and fonts can change height after the first layout. Follow the
    // same paragraph, unless the reader has interacted since loading began.
    let moved = false;
    const events = new AbortController();
    for (const type of ["wheel", "touchstart", "pointerdown", "keydown"])
      document.addEventListener(type, () => { moved = true; }, { once: true, passive: true, signal: events.signal });
    window.addEventListener("load", () => {
      document.fonts.ready.then(() => requestAnimationFrame(() => {
        if (!moved) { restorePosition(); spy(); }
        events.abort();
      }));
    }, { once: true });
  }
  spy();
  setTab(tab);
  started = true;
  loadComments();
  if (view !== "submission") loadSubmission();  // applyView() opened the view
  if (handoff && handoff.edit && handoff.file) openSourceLine({ ...handoff, doc }, handoffEl);
  window.addEventListener("focus", () => loadSubmission());  // e.g. after `trazo build --submission`
  if (view === "figures") {
    const want = pos.figures;  // the cards appear after the documents are fetched
    loadAll().then(() => requestAnimationFrame(() => { scroller.scrollTop = want; }));
  }
  // Prefetch the other documents for the count and the Figures & Tables view.
  setTimeout(() => { loadAll().then(() => { if (view === "figures") renderFigs(); renderWords(); }); }, 400);

  window.addEventListener("beforeunload", (e) => {
    if (hasUnsaved()) { e.preventDefault(); e.returnValue = ""; }
  });
  window.addEventListener("beforeunload", () => {
    saveReadingAnchor();
    pos[view] = scroller.scrollTop;
    sset(scrollKey("doc"), String(pos.doc));
    sset(scrollKey("figures"), String(pos.figures));
    sset(scrollKey("submission"), String(pos.submission));
    if (cur && tab === "source") {
      const b = buffers.get(cur);
      sset("preview-src:" + Trazo.path(), JSON.stringify({
        file: cur, sel: editor2.getSelection(), scroll: editor2.getScroll(), dirty: !!b && isDirty(b) }));
    }
  });

  // ---------------------------------------------------------------- live reload
  let loaded = null;
  async function poll(all, offline) {
    if (polling) return;  // one request at a time
    polling = true;
    try {
      if (offline) throw offline;
      const status = all.documents[doc];
      const before = versions;
      versions = Object.fromEntries(docNames.map((n) => [n, all.documents[n] ? all.documents[n].version : 0]));
      if (loaded === null) loaded = { ...versions };
      resolveStatus();
      // Do not reload while a comment is being written or edited. The server
      // only bumps the version on a successful build, so a failed build shows
      // the banner without reloading. The Figures & Tables view shows every
      // document, so it reloads for any of them.
      const busy = hasUnsaved();
      const catalogVersion = `${all.catalog}:${all.review}`;
      if (!busy && catalogVersion && catalogStamp !== catalogVersion) {
        catalogStamp = catalogVersion;
        loadAll().then(() => { if (view === "figures" && !hasUnsaved()) renderFigs(); });
      }
      const stale = (view !== "doc" ? docNames : [doc]).some((n) => versions[n] !== loaded[n]);
      // An update rewrites the paper and then restarts this server: a reload served
      // meanwhile can meet the restart and leave a page with no scripts. The update
      // reloads the page itself, against the new server (preview-updates.js).
      if (stale && !busy && !updateBusy && await App.reload(() => !hasUnsaved())) return;
      hold.hidden = !(busy && stale);
      if (view === "doc") for (const n of docNames) if (n !== doc) loaded[n] = versions[n];  // not shown here
      // Refresh copies fetched at another version, or before the first
      // status answered (a failed first poll leaves their version unknown).
      const outdated = (n) => n !== doc && (fetched[n] ? fetched[n].v !== versions[n]
        : before[n] !== undefined && before[n] !== versions[n]);
      if (docNames.some(outdated)) {
        loadAll().then(() => { if (view === "figures") renderFigs(); renderWords(); });  // refreshed copies
      }
      // The submission report covers every document and the Word files.
      const finished = all.docx && all.docx.state !== "running" ? all.docx.finished || "" : "";
      if (docNames.some((n) => before[n] !== undefined && before[n] !== versions[n])
          || (docxFinished !== null && finished !== docxFinished)) loadSubmission();
      docxFinished = finished;
      checkSubmissionNow();
      renderDocx(all.docx);
      if (commentStamp !== null && all.comments !== commentStamp) loadComments();
      commentStamp = all.comments;
      const state = !status.ok ? "error" : status.warnings.length ? "warn" : "ok";
      const text = status.ok ? status.warnings.join("\n") : status.error;
      const label = state === "ok" ? "Build OK" : state === "warn" ? plural(status.warnings.length, "warning") : "Build failed";
      showBuild(state, label, `Built ${status.built}${text ? "\n" + text.split("\n").slice(0, 6).join("\n") : ""}`);
      const message = state === "ok" ? "" :
        (state === "error" ? "Build failed (showing last good version):\n" : "Pandoc warnings:\n") + text;
      // Only touch the DOM on change, so the message can be selected and copied.
      if (banner.textContent !== message) banner.textContent = message;
      banner.className = `pv-banner ${state}`;
    } catch (e) {
      showBuild("off", "Offline", "The preview server does not answer.");
      resolveStatus();
    }
    polling = false;
    // A hidden tab polls slowly; it catches up as soon as it is shown again.

  }
  let polling = false, commentStamp = null, docxFinished = null;
  App.watch(poll);
