// Preview viewer: the edits made to the sources since the author last
// reviewed them (trazo.preview_edit_review). The build marks them in the
// page: .pv-edit-ins (inserted), .pv-edit-del (deleted, shown as written) and empty
// .pv-blk spans at the start of a new or changed block. Here: the Edits
// control in the view bar (count, previous and next, show marks, accept or
// reject all), a small bar to accept or reject the selected edit, and the
// edits a review comment's card accepts or rejects (editsNear).
// The parts of the viewer are classic scripts loaded in the order of
// trazo.preview.VIEWER_SCRIPTS; their top-level names are shared globals.
  if (!window.PreviewModel) throw new Error("preview-model.js did not load");  // see preview-page.js
  // ---------------------------------------------------------------- edits
  let edits = [];      // [{file, id, line, old, new, seen}] in page order
  let editAt = -1;     // the selected edit
  let editAnchor = null;  // the mark its bar sits under
  const editKey = (e) => `${e.file}\n${e.id}`;
  // A block mark's element is the block it starts; a block may start several
  // edits' marks, so data-edit-block lists their ids.
  setUpBlocks((root) => { for (const mark of within(root, ".pv-blk")) {
    const block = mark.closest("p, li, h1, h2, h3, h4, h5, h6, dt, dd, blockquote") || mark.parentElement;
    block.classList.add(mark.classList.contains("pv-new") ? "pv-edit-new" : "pv-edit-changed");
    block.dataset.editBlock = `${block.dataset.editBlock || ""} ${mark.dataset.edit}`.trim();
  } });
  const blockIds = (el) => (el.dataset.editBlock || "").split(" ").filter(Boolean);
  const marksOf = (id) => [...$$(`.pv-edit-ins[data-edit="${CSS.escape(id)}"], .pv-edit-del[data-edit="${CSS.escape(id)}"]`, page),
    ...$$(`[data-edit-block~="${CSS.escape(id)}"]`, page)];

  const checkIcon = ICON('<path d="M3.5 8.5l3 3 6-7"/>', 1.8);
  const editsUi = h("div", { class: "pv-edits", role: "group", "aria-label": "Edits to review", hidden: true });
  editsUi.innerHTML =
    `<button type="button" class="pv-edits-toggle" aria-haspopup="menu" aria-expanded="false" aria-controls="pv-edits-menu">` +
    `Edits <span class="n"></span>${ICONS.down}</button>` +
    `<button type="button" class="pv-icon-btn sm" data-edit-step="-1" aria-label="Previous edit" title="Previous edit">${ICONS.up}</button>` +
    `<button type="button" class="pv-icon-btn sm" data-edit-step="1" aria-label="Next edit" title="Next edit">${ICONS.down}</button>` +
    `<div class="pv-edits-menu" id="pv-edits-menu" role="menu" aria-label="Edits" hidden>` +
    `<button type="button" role="menuitemcheckbox" data-edit-op="marks"><span class="ck">✓</span>Show marks</button>` +
    `<hr><button type="button" role="menuitem" data-edit-op="accept-all"><span class="ck"></span><span class="t"></span></button>` +
    `<button type="button" role="menuitem" class="danger" data-edit-op="reject-all"><span class="ck"></span><span class="t"></span></button></div>`;
  zoomBar.prepend(editsUi);
  const editsToggle = $(".pv-edits-toggle", editsUi), editsMenu = $(".pv-edits-menu", editsUi);
  const editBar = h("div", { class: "pv-edit-bar", role: "toolbar", "aria-label": "Selected edit", hidden: true });
  editBar.innerHTML = `<button type="button" class="ok" data-edit-op="accept" aria-label="Accept">${checkIcon}</button>` +
    `<button type="button" class="no" data-edit-op="reject" aria-label="Reject">${ICONS.close}</button>` +
    `<div class="pv-edit-was" hidden></div>`;
  document.body.append(editBar);

  let showMarks = prefs.editMarks !== false;
  function renderEditsUi() {
    const n = edits.length;
    editsUi.hidden = !n || view !== "doc";
    $(".n", editsUi).textContent = fmt(n);
    $('[data-edit-op="accept-all"] .t', editsMenu).textContent = `Accept all ${fmt(n)}`;
    $('[data-edit-op="reject-all"] .t', editsMenu).textContent = `Reject all ${fmt(n)}…`;
    const marks = $('[data-edit-op="marks"]', editsMenu);
    marks.setAttribute("aria-checked", String(showMarks));
    $(".ck", marks).textContent = showMarks ? "✓" : "";
    document.documentElement.classList.toggle("pv-edits-off", !showMarks);
    if (!n) hideEditBar();
  }
  function setMarks(on) {
    showMarks = on;
    savePrefs({ editMarks: on });
    renderEditsUi();
    if (!on) hideEditBar();
  }
  const closeEditsMenu = () => { editsMenu.hidden = true; editsToggle.setAttribute("aria-expanded", "false"); };

  async function loadEdits() {
    try {
      const { ok, payload } = await postJson(`/__edits?doc=${encodeURIComponent(doc)}`);
      edits = ok && Array.isArray(payload.edits) ? payload.edits : [];
    } catch (e) { edits = []; }
    renderEditsUi();
    if (commentsReady) renderComments();  // review cards offer Reject when edits are near
  }

  const changedAt = (seen) => !seen ? "" : seen.slice(0, 10) === new Date().toLocaleDateString("sv")
    ? `Changed ${seen.slice(11, 16)}` : `Changed ${seen.slice(0, 16)}`;
  function hideEditBar() {
    editBar.hidden = true;
    for (const el of $$(".pv-edit-cur", page)) el.classList.remove("pv-edit-cur");
    editAt = -1;
    editAnchor = null;
  }
  function placeEditBar() {
    const anchor = editAnchor;
    if (!anchor || !anchor.isConnected || !anchor.getClientRects().length) { editBar.hidden = true; return; }
    const rects = anchor.getClientRects(), r = rects[anchor.matches(".pv-edit-ins, .pv-edit-del") ? rects.length - 1 : 0];
    editBar.hidden = false;
    editBar.style.top = `${Math.max(8, Math.min(r.bottom + 6, window.innerHeight - editBar.offsetHeight - 8))}px`;
    editBar.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - editBar.offsetWidth - 8))}px`;
  }
  scroller.addEventListener("scroll", () => { if (!editBar.hidden) placeEditBar(); }, { passive: true });
  window.addEventListener("resize", () => { if (!editBar.hidden) placeEditBar(); });

  // Select an edit: mark it, scroll to it and show its bar. An edit with no
  // mark in the page (a table cell, a figure alone in its paragraph) opens
  // the Source tab at its line instead.
  function selectEdit(i, scroll) {
    hideEditBar();
    const e = edits[i];
    if (!e) return;
    const marks = marksOf(e.id);
    const clip = (text) => (text.trim().length > 400 ? text.trim().slice(0, 400) + "…" : text.trim());
    const was = $(".pv-edit-was", editBar);
    editAt = i;
    if (!marks.length) {
      // Its bar sits under the Edits control and says what it changes.
      toast(`This edit is not marked on the page: ${e.file}, line ${e.line}`);
      openSourceLine({ file: e.file, line: e.line, end_line: e.line + (e.new.match(/\n/g) || []).length });
      editAnchor = editsToggle;
      was.hidden = false;
      was.textContent = [e.old.trim() ? `Was: ${clip(e.old)}` : "", e.new.trim() ? `Now: ${clip(e.new)}` : ""].filter(Boolean).join("\n");
    } else {
      if (!showMarks) setMarks(true);
      editAnchor = marks.find((el) => el.getClientRects().length) || marks[0];
      for (const el of marks) el.classList.add("pv-edit-cur");
      // A changed block has no inline marks: the bar shows what it was.
      was.hidden = marks.some((el) => el.matches(".pv-edit-ins, .pv-edit-del")) || !e.old.trim();
      was.textContent = was.hidden ? "" : `Was: ${clip(e.old)}`;
    }
    const when = changedAt(e.seen);
    $('[data-edit-op="accept"]', editBar).dataset.tip = `Accept (A)${when ? " · " + when : ""}`;
    $('[data-edit-op="reject"]', editBar).dataset.tip = `Reject (R)${when ? " · " + when : ""}`;
    if (scroll && marks.length) scrollToEl(marks[0], true, reduceMotion());
    requestAnimationFrame(placeEditBar);
  }
  function stepEdit(dir) {
    if (!edits.length) return;
    const next = editAt < 0 ? (dir > 0 ? 0 : edits.length - 1) : (editAt + dir + edits.length) % edits.length;
    selectEdit(next, true);
  }

  // What the page shows at once after an accept or reject; the rebuilt page follows.
  function settle(e, op) {
    for (const el of marksOf(e.id)) {
      if (el.dataset.editBlock) {
        if (op === "reject" && el.querySelector(`.pv-blk.pv-new[data-edit="${CSS.escape(e.id)}"]`)) el.hidden = true;
        const rest = blockIds(el).filter((id) => id !== e.id);
        el.dataset.editBlock = rest.join(" ");
        if (!rest.length) {
          el.classList.remove("pv-edit-new", "pv-edit-changed", "pv-edit-cur");
          delete el.dataset.editBlock;
        }
      } else if (el.matches(op === "accept" ? ".pv-edit-del" : ".pv-edit-ins")) el.remove();
      else el.replaceWith(...el.childNodes);
    }
  }
  // Accept or reject `list`; resolves to the server's answer ({done,
  // missing, missed}), or null when it refused. Missed edits changed since
  // the page was built: they keep their marks until the rebuilt page.
  async function applyEdits(op, list, message) {
    if (!list.length) return { done: 0, missing: 0, missed: [] };
    const { ok, payload } = await postJson("/__edits", { op, edits: list.map(({ file, id }) => ({ file, id })) });
    if (!ok) { toast(`Could not ${op} the edits: ${payload.error || "the server refused"}`); return null; }
    hideEditBar();
    const missed = new Set((payload.missed || []).map(editKey));
    const done = list.filter((e) => !missed.has(editKey(e)));
    const keys = new Set(done.map(editKey));
    for (const e of done) settle(e, op);
    edits = edits.filter((e) => !keys.has(editKey(e)));
    renderEditsUi();
    // The list keeps matching the marks until the rebuilt page replaces both.
    if (missed.size) toast(`${plural(missed.size, "edit")} changed since the page was built and stayed as they are; the page reloads with them.`);
    else if (message) toast(message);
    if (payload.warning) toast(payload.warning);
    return payload;
  }

  // The edits a review comment is about: those in its paragraph and the new
  // paragraphs right after it.
  function editsNear(hit) {
    if (!hit || !hit.el || !page.contains(hit.el)) return [];
    const ids = new Set([...$$(".pv-edit-ins, .pv-edit-del", hit.el).map((el) => el.dataset.edit), ...blockIds(hit.el)]);
    for (let next = hit.el.nextElementSibling; next && next.classList.contains("pv-edit-new"); next = next.nextElementSibling)
      for (const id of blockIds(next)) ids.add(id);
    return edits.filter((e) => ids.has(e.id));
  }

  editsToggle.addEventListener("click", () => {
    const open = editsMenu.hidden;
    editsMenu.hidden = !open;
    editsToggle.setAttribute("aria-expanded", String(open));
  });
  editsUi.addEventListener("click", async (ev) => {
    const step = ev.target.closest("[data-edit-step]");
    if (step) { stepEdit(Number(step.dataset.editStep)); return; }
    const op = ev.target.closest("[data-edit-op]")?.dataset.editOp;
    if (!op) return;
    closeEditsMenu();
    if (op === "marks") setMarks(!showMarks);
    else if (op === "accept-all") applyEdits("accept", edits.slice(), `Accepted ${plural(edits.length, "edit")}`);
    else if (op === "reject-all" && confirm(`Reject all ${plural(edits.length, "edit")}? The text goes back to how it was when you last reviewed it. Undo save in the Source tab brings back a file's edits.`))
      applyEdits("reject", edits.slice(), `Rejected ${plural(edits.length, "edit")}`);
  });
  document.addEventListener("click", (ev) => { if (!editsUi.contains(ev.target)) closeEditsMenu(); });
  editBar.addEventListener("click", (ev) => {
    const op = ev.target.closest("[data-edit-op]")?.dataset.editOp, e = edits[editAt];
    if (op && e) applyEdits(op, [e], op === "accept" ? "Edit accepted" : "Edit rejected");
  });
  for (const button of $$("button", editBar)) {
    button.addEventListener("mouseenter", () => { tipFor = button; tip.textContent = button.dataset.tip || ""; placeTip(button); });
    button.addEventListener("mouseleave", hideTip);
  }
  // A click on a mark selects its edit (not when it ends a text selection).
  page.addEventListener("click", (ev) => {
    if (!showMarks || ev.target.closest("a, button") || !document.getSelection().isCollapsed) return;
    const mark = ev.target.closest(".pv-edit-ins, .pv-edit-del, [data-edit-block]");
    const id = mark && (mark.dataset.edit || blockIds(mark)[0]);
    const at = id ? edits.findIndex((e) => e.id === id) : -1;
    if (at >= 0) selectEdit(at, false);
    else if (!editBar.hidden && !editBar.contains(ev.target)) hideEditBar();
  });
  document.addEventListener("keydown", (ev) => {
    if (editBar.hidden || ev.metaKey || ev.ctrlKey || ev.altKey) return;
    if (ev.target.closest && ev.target.closest("textarea, input, select, [contenteditable]")) return;
    const op = { a: "accept", r: "reject" }[ev.key];
    if (op) { ev.preventDefault(); $(`[data-edit-op="${op}"]`, editBar).click(); }
    else if (ev.key === "Escape") hideEditBar();
  });
