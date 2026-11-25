// Preview viewer: the banner over the page while edits are pending (how
// many, Next, Reject all and Accept all) and their batches: the edits the
// preview first saw in builds less than two minutes apart, as an agent saves
// several files in a row. The banner's batches link and the Edits menu list
// them, each to accept or reject. All of them means the edits this page shows.
// The parts of the viewer are classic scripts loaded in the order of
// trazo.preview.VIEWER_SCRIPTS; their top-level names are shared globals.
  if (!window.PreviewModel) throw new Error("preview-model.js did not load");  // see preview-page.js
  // ---------------------------------------------------------------- edit batches
  const BATCH_GAP = 2 * 60 * 1000;
  let batches = [];
  const editBanner = h("div", { class: "pv-edit-banner", role: "region", "aria-label": "Changes to review", hidden: true });
  editBanner.innerHTML =
    `<div class="msg"><b></b> <button type="button" class="pv-batches-link" aria-haspopup="dialog" aria-expanded="false" aria-controls="pv-batches-pop"></button>` +
    `<small>Click a mark to accept or reject it alone.</small></div>` +
    `<button type="button" class="pv-btn sm ghost" data-banner-op="next">Next ↓</button>` +
    `<button type="button" class="pv-btn sm no" data-banner-op="reject">Reject all…</button>` +
    `<button type="button" class="pv-btn sm primary" data-banner-op="accept">✓ Accept all</button>` +
    `<div class="pv-batches-pop" id="pv-batches-pop" role="dialog" aria-label="Batches" hidden></div>`;
  banners.append(editBanner);
  const batchesLink = $(".pv-batches-link", editBanner), batchesPop = $(".pv-batches-pop", editBanner);
  const menuBatches = $(".pv-edits-batches", editsMenu);

  const seenAt = (e) => (e.seen ? new Date(e.seen.replace(" ", "T")).getTime() : Date.now());
  function editBatches() {
    const out = [];
    for (const [at, e] of edits.map((e) => [seenAt(e), e]).sort((a, b) => a[0] - b[0])) {
      const last = out.at(-1);
      if (last && at - last.end < BATCH_GAP) { last.edits.push(e); last.end = at; }
      else out.push({ seen: e.seen, end: at, edits: [e] });
    }
    const order = new Map(edits.map((e, i) => [e, i]));
    for (const batch of out) batch.edits.sort((a, b) => order.get(a) - order.get(b));
    return out.reverse();
  }
  function sectionsOf(list) {
    const names = [];
    for (const e of list) {
      const mark = marksOf(e.id)[0];
      let section = mark && mark.closest("section");
      for (let up = section; up; up = up.parentElement.closest("section")) section = up;
      const name = section ? ($("h1, h2, h3, h4, h5, h6", section)?.textContent || "").trim() : "";
      if (name && !names.includes(name)) names.push(name);
    }
    return names.join(", ");
  }
  function batchRows() {
    return [h("div", { class: "pv-batches-cap", text: "Batches" }), ...batches.map((batch, i) => {
      const label = `${plural(batch.edits.length, "change")} ${batch.seen ? `at ${seenTime(batch.seen)}` : "just now"}`;
      return h("div", { class: "pv-batch", role: "group", "aria-label": label },
        h("div", { class: "t" }, label, h("small", { text: sectionsOf(batch.edits) })),
        h("button", { type: "button", class: "no", "data-batch-op": "reject", "data-batch": i, "aria-label": `Reject ${label}…`, title: "Reject…", text: "✕" }),
        h("button", { type: "button", class: "ok", "data-batch-op": "accept", "data-batch": i, "aria-label": `Accept ${label}`, text: "✓ Accept" }));
    })];
  }

  function renderEditBanner() {
    const n = edits.length;
    batches = n ? editBatches() : [];
    editBanner.hidden = !n || view !== "doc";
    $(".msg b", editBanner).textContent = `${fmt(n)} change${n === 1 ? "" : "s"} to review`;
    batchesLink.hidden = batches.length < 2;
    labelBatches();
    menuBatches.hidden = batches.length < 2;
    menuBatches.replaceChildren(...(batches.length < 2 ? [] : [h("hr"), ...batchRows()]));
    if (batches.length < 2 || editBanner.hidden) closeBatches();
    else if (!batchesPop.hidden) batchesPop.replaceChildren(...batchRows());
  }
  function labelBatches() {
    batchesLink.setAttribute("aria-expanded", String(!batchesPop.hidden));
    batchesLink.textContent = `in ${batches.length} batches ${batchesPop.hidden ? "▾" : "▴"}`;
  }
  function closeBatches() {
    if (batchesPop.hidden) return;
    batchesPop.hidden = true;
    labelBatches();
  }
  function openBatches() {
    batchesPop.replaceChildren(...batchRows());
    batchesPop.hidden = false;
    batchesPop.style.left = `${batchesLink.offsetLeft}px`;
    labelBatches();
  }

  function batchClick(ev) {
    const button = ev.target.closest("[data-batch-op]"), batch = button && batches[Number(button.dataset.batch)];
    if (!batch) return;
    closeBatches();
    closeEditsMenu();
    if (button.dataset.batchOp === "accept") acceptEdits(batch.edits.slice());
    else rejectEdits(batch.edits.slice());
  }
  batchesPop.addEventListener("click", batchClick);
  menuBatches.addEventListener("click", batchClick);
  batchesLink.addEventListener("click", () => (batchesPop.hidden ? openBatches() : closeBatches()));
  editBanner.addEventListener("click", (ev) => {
    const op = ev.target.closest("[data-banner-op]")?.dataset.bannerOp;
    if (op === "next") stepEdit(1);
    else if (op === "reject") rejectEdits(edits.slice());
    else if (op === "accept") acceptEdits(edits.slice());
  });
  document.addEventListener("click", (ev) => { if (!batchesPop.contains(ev.target) && ev.target !== batchesLink) closeBatches(); });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") closeBatches(); });
