// Preview viewer: writing a new comment or underline on selected text.
// The parts of the viewer are classic scripts loaded in the order of
// trazo.preview.VIEWER_SCRIPTS; their top-level names are shared globals.
  if (!window.PreviewModel) throw new Error("preview-model.js did not load");  // see preview-page.js
  // ---------------------------------------------------------------- new comments
  // Selected text offers Comment (then a small editor) and Underline, a
  // reading mark; over an underline it offers Remove underline instead, as
  // does a click on underlined text.
  const fab = h("div", { class: "pv-fab", hidden: true, role: "toolbar", "aria-label": "Selected text" });
  document.body.append(fab);
  const fabButton = (select, icon, label, title) => {
    const b = h("button", { type: "button", "data-select": select, title });
    b.innerHTML = `${ICONS[icon]}<span>${label}</span>`;
    return b;
  };
  function showFab(box, buttons) {
    fab.textContent = "";
    fab.append(...buttons);
    fab.hidden = false;
    fab.style.top = `${Math.max(8, Math.min(box.bottom + 6, window.innerHeight - fab.offsetHeight - 8))}px`;
    fab.style.left = `${Math.max(8, Math.min(box.left, window.innerWidth - fab.offsetWidth - 8))}px`;
  }
  const unmarkButton = () => fabButton("unmark", "underline", "Remove underline", "Remove this reading mark");
  const editor = h("div", { class: "pv-editor", hidden: true, role: "dialog", "aria-label": "Add comment" });
  editor.innerHTML = `<button type="button" class="pv-editor-move" aria-label="Move comment editor" title="Drag to move, or focus and use arrow keys (Shift for larger steps)"><span class="pv-where"></span><span aria-hidden="true">⠿</span></button><blockquote></blockquote>` +
    `<textarea rows="4" placeholder="What should Claude change? (⌘↵ to save, Esc to cancel)"></textarea>` +
    `<div class="pv-actions"><button type="button" data-op="cancel">Cancel</button><button type="button" data-op="save">Comment</button></div>`;
  document.body.append(editor);
  let pending = null, pendingMarks = [];
  const moveEditor = $(".pv-editor-move", editor);
  let editorDrag = null;
  function placeEditor(left, top) {
    editor.style.left = `${clamp(left, 8, Math.max(8, innerWidth - editor.offsetWidth - 8))}px`;
    editor.style.top = `${clamp(top, 8, Math.max(8, innerHeight - editor.offsetHeight - 8))}px`;
  }
  const fitEditor = () => {
    if (!editor.hidden) { const box = editor.getBoundingClientRect(); placeEditor(box.left, box.top); }
  };
  moveEditor.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const box = editor.getBoundingClientRect();
    editorDrag = { pointer: e.pointerId, x: e.clientX - box.left, y: e.clientY - box.top };
    moveEditor.setPointerCapture(e.pointerId);
    editor.classList.add("dragging");
  });
  moveEditor.addEventListener("pointermove", (e) => {
    if (editorDrag && editorDrag.pointer === e.pointerId) placeEditor(e.clientX - editorDrag.x, e.clientY - editorDrag.y);
  });
  const endEditorDrag = () => { editorDrag = null; editor.classList.remove("dragging"); };
  moveEditor.addEventListener("lostpointercapture", endEditorDrag);
  moveEditor.addEventListener("pointerup", endEditorDrag);
  moveEditor.addEventListener("pointercancel", endEditorDrag);
  moveEditor.addEventListener("keydown", (e) => {
    const delta = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key];
    if (!delta || e.metaKey || e.ctrlKey || e.altKey) return;
    e.preventDefault();
    const box = editor.getBoundingClientRect(), step = e.shiftKey ? 40 : 10;
    placeEditor(box.left + delta[0] * step, box.top + delta[1] * step);
  });
  window.addEventListener("resize", fitEditor);
  new ResizeObserver(fitEditor).observe(editor);

  document.addEventListener("mouseup", (e) => {
    if (e.button !== 0 || e.target.closest(".pv-topbar, .pv-side, .pv-editor, .pv-fab, .pv-lb")) return;
    const x = e.clientX, y = e.clientY;
    // Links and controls in the text (¶, Edit caption…) keep their own click.
    const onText = page.contains(e.target) && !e.target.closest("a, button, .pv-tf, .pv-diagram");
    setTimeout(() => {
      if (!editor.hidden) return;  // finish or cancel the comment being written first
      const sel = window.getSelection();
      pendingMarks = [];
      if (view !== "doc") { fab.hidden = true; return; }
      if (!sel || sel.isCollapsed) {  // a click: on an underline, offer to remove it
        const at = onText ? caretAt(x, y) : null;
        pendingMarks = at ? marksAt(at) : [];
        if (!pendingMarks.length) { fab.hidden = true; return; }
        const range = pendingMarks[0].hit.range;
        const line = range && ([...range.getClientRects()].find((r) => y >= r.top && y <= r.bottom) || range.getBoundingClientRect());
        showFab({ left: x, bottom: line ? line.bottom : y }, [unmarkButton()]);
        return;
      }
      const range = sel.getRangeAt(0);
      const quote = squash(sel.toString().replace(/¶\d+|✎/g, ""));
      const anchor = quote ? anchorOf(range.startContainer) : null;
      if (!anchor || !page.contains(anchor)) { fab.hidden = true; return; }
      const unit = unitOf(model, anchor);
      pending = { doc, quote, para: anchor.dataset.label, section: unit ? titleOf(unit) : "",
        anchor: { ...contextOf(model, anchor), ...surroundOf(anchor, range, quote) } };
      pendingMarks = inText.filter((x) => isMark(x.c) && x.hit.range && overlaps(x.hit.range, range));
      showFab(range.getBoundingClientRect(), [fabButton("comment", "comment", "Comment", "Comment for Claude"),
        pendingMarks.length ? unmarkButton()
          : fabButton("underline", "underline", "Underline", "A reading mark for you: not a comment, and Claude does not see it")]);
    }, 0);
  });
  fab.addEventListener("mousedown", (e) => e.preventDefault());
  fab.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-select]");
    const action = button && button.dataset.select;
    if (!action) return;
    fab.hidden = true;
    if (action === "unmark") {
      const ids = pendingMarks.map((x) => x.c.id);
      pendingMarks = [];
      if (ids.length && await send(ids.length === 1 ? { op: "delete", id: ids[0] } : { op: "delete", ids }, "Underline removed")) {
        pending = null; window.getSelection().removeAllRanges();
      }
      return;
    }
    if (!pending) return;
    if (action === "underline") {
      const anchor = pending;
      if (await send({ op: "add", ...anchor, kind: "underline", comment: "Underline" }, "Underlined")) {
        pending = null; window.getSelection().removeAllRanges();
      }
      return;
    }
    $(".pv-where", editor).textContent = pending.para;
    $("blockquote", editor).textContent = pending.quote;
    editor.hidden = false;
    const top = parseFloat(fab.style.top), left = parseFloat(fab.style.left);
    placeEditor(left, top + editor.offsetHeight + 8 > innerHeight ? top - editor.offsetHeight - 36 : top);
    const area = $("textarea", editor);
    area.value = "";
    area.focus();
  });
  const closeEditor = () => { endEditorDrag(); editor.hidden = true; pending = null; };
  let addingComment = false;
  unsavedChecks.push(() => addingComment);
  const saveEditor = async () => {
    if (addingComment) return;
    const area = $("textarea", editor), raw = area.value, anchor = pending;
    const text = raw.trim();
    if (!text || !anchor) return;
    addingComment = true;
    const payload = await send({ op: "add", ...anchor, comment: text }, "Comment added");
    addingComment = false;
    if (payload && pending === anchor && area.value === raw) {
      closeEditor();
      window.getSelection().removeAllRanges();
      // Show the new comment (added last) as the current one.
      const added = payload.comments[payload.comments.length - 1];
      if (added) {
        navId = added.id;
        useFilter("active");
        renderComments();
        const cardEl = $(`.pv-card[data-id="${navId}"]`, panel);
        if (cardEl) cardEl.scrollIntoView({ block: "nearest" });
      }
    }
  };
  editor.addEventListener("click", (e) => {
    if (e.target.dataset.op === "cancel") closeEditor();
    if (e.target.dataset.op === "save") saveEditor();
  });
  $("textarea", editor).addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeEditor();
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) saveEditor();
  });
