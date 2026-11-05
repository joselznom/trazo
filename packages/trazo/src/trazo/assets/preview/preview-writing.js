// Preview viewer: writing on the page's text. A click in a paragraph or list
// item puts the caret there, with no mode to enter; leaving it saves the words
// that changed to its Markdown (POST /__block, trazo.preview_block_save).
// Citations, values, math, code, note calls and images are locked chips. A
// save that fails leaves the text and flags the paragraph until the author
// picks what stays. The build marks what can be written in (.pv-src, .pv-atom).
// The parts of the viewer are classic scripts loaded in the order of
// trazo.preview.VIEWER_SCRIPTS; their top-level names are shared globals.
  if (!window.PreviewModel) throw new Error("preview-model.js did not load");  // see preview-page.js
  // ---------------------------------------------------------------- writing
  // Chips the build did not wrap, and what is on the page but not in the block.
  const OTHER_CHIPS = ".citation, .pv-val, span.math, math, code, .footnote-ref, a[href^='#'], img, svg";
  const NOT_TEXT = ".pv-src, .pv-para, .pv-src-mark, .pv-edit-del, .pv-write-bar, button, ul, ol, dl, blockquote";
  const CHIP = 0xE000, OTHER_CHIP = 0xF000;
  const browserWrites = (() => {
    const probe = document.createElement("div");
    try { probe.contentEditable = "plaintext-only"; } catch (e) { return false; }
    return probe.contentEditable === "plaintext-only";
  })();
  let writing = null;           // the paragraph with the caret
  let saves = 0;                // saves on their way
  const failed = new Map();     // paragraph -> what was written in it, until the author decides
  unsavedChecks.push(() => saves > 0 || failed.size > 0 || (!!writing && textOf(writing.el) !== writing.before));

  const saveState = h("span", { class: "pv-save-state", role: "status" });
  buildBtn.after(saveState);
  let saveStateTimer = null;
  function showSaveState(text, lasts) {
    clearTimeout(saveStateTimer);
    saveState.textContent = text;
    if (lasts) saveStateTimer = setTimeout(() => { saveState.textContent = ""; }, 2000);
  }

  const markOf = (el) => $$(".pv-src", el).find((mark) => mark.closest("p, li") === el);
  if (browserWrites) for (const mark of $$(".pv-src", page)) {
    const el = mark.closest("p, li");
    const own = el && !el.closest("nav, table, figure") && $$(".pv-src", el).filter((m) => m.closest("p, li") === el);
    if (own && own.length === 1) el.classList.add("pv-can-write");
  }

  // The paragraph's text as the server reads it, each chip one character.
  function textOf(el) {
    let others = 0;
    const walk = (node) => [...node.childNodes].map((child) => {
      if (child.nodeType === 3) return child.data;
      if (child.nodeType !== 1 || child.matches(NOT_TEXT)) return "";
      if (child.tagName === "BR") return " ";
      if (child.matches(".pv-atom")) return String.fromCharCode(CHIP + Number(child.dataset.i));
      if (child.matches(OTHER_CHIPS)) return String.fromCharCode(OTHER_CHIP + others++);
      return walk(child);
    }).join("");
    return squash(walk(el));
  }

  // What Esc puts back: the same nodes, with their listeners and text.
  function snapshotOf(el) {
    const kept = [];
    const walk = (node) => {
      kept.push([node, node.nodeType === 3 ? node.data : [...node.childNodes]]);
      node.childNodes.forEach(walk);
    };
    walk(el);
    return () => {
      for (const [node, was] of kept) {
        if (node.nodeType === 3) node.data = was;
        else node.replaceChildren(...was);
      }
    };
  }

  // plaintext-only shows line breaks as written, and a <br> already is one.
  function dropLineBreaks(el) {
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) if (walker.currentNode.data.includes("\n")) {
      walker.currentNode.data = walker.currentNode.data.replace(/\n/g, walker.currentNode.previousSibling?.nodeName === "BR" ? "" : " ");
    }
  }

  function startWriting(el) {
    const mark = markOf(el);
    if (!mark) return false;
    let ed = failed.get(el);
    if (ed) clearFailure(ed);
    else {
      dropLineBreaks(el);
      ed = { el, mark, before: textOf(el), restore: snapshotOf(el), source: null };
      // Kept for "Keep mine", which needs the Markdown this text came from.
      postJson(`/__block?file=${encodeURIComponent(mark.dataset.file)}&h=${encodeURIComponent(mark.dataset.h)}`)
        .then(({ ok, payload }) => { if (ok) ed.source = payload.markdown; })
        .catch(() => {});
    }
    ed.locked = [...el.querySelectorAll(`.pv-atom, ${OTHER_CHIPS}, ${NOT_TEXT}`)];
    for (const node of ed.locked) node.contentEditable = "false";
    el.classList.add("pv-writing");
    el.contentEditable = "plaintext-only";
    writing = ed;
    return true;
  }

  function stopWriting(ed) {
    if (writing === ed) writing = null;
    ed.el.removeAttribute("contenteditable");
    ed.el.classList.remove("pv-writing");
    for (const node of ed.locked) node.removeAttribute("contenteditable");
    forgetText(ed.el);
  }

  function clearFailure(ed) {
    failed.delete(ed.el);
    ed.el.classList.remove("pv-write-failed");
    if (ed.bar) ed.bar.remove();
    ed.bar = null;
  }

  async function sendBlock(ed, body) {
    saves++;
    showSaveState("Saving…");
    let result;
    try {
      result = await postJson("/__block", { file: ed.mark.dataset.file, h: ed.mark.dataset.h, ...body });
    } catch (e) {
      result = { ok: false, status: 0, payload: { error: "the preview server did not answer" } };
    }
    saves--;
    return result;
  }

  async function saveBlock(ed, extra) {
    const after = textOf(ed.el);
    if (after === ed.before && !failed.has(ed.el)) return;
    const { ok, status, payload } = await sendBlock(ed, { before: ed.before, after, ...extra });
    if (ok && payload.saved) blockSaved(ed, payload);
    else showFailure(ed, status, payload);
  }

  function blockSaved(ed, payload) {
    clearFailure(ed);
    ed.mark.dataset.h = payload.h;
    // Until the page reloads, the chips left are numbered as the Markdown is.
    $$(".pv-atom", ed.el).forEach((chip, i) => { chip.dataset.i = String(i); });
    showSaveState("Saved", true);
  }

  function discardWriting(ed) {
    clearFailure(ed);
    ed.restore();
    forgetText(ed.el);
    showSaveState("", false);
  }

  function showFailure(ed, status, payload) {
    showSaveState("Not saved", true);
    failed.set(ed.el, ed);
    ed.el.classList.add("pv-write-failed");
    if (ed.bar) ed.bar.remove();
    const button = (text, run, disabled) => {
      const b = h("button", { type: "button", text, disabled });
      b.addEventListener("click", run);
      return b;
    };
    const changed = status === 409;
    const text = changed ? "This paragraph changed on disk, so your text is not saved."
      : `Not saved: ${payload.refused || payload.error || "the server refused"}.`;
    ed.bar = h("div", { class: "pv-write-bar", role: "alert", contenteditable: "false" }, h("span", { text }),
      ...(changed
        ? [button("Keep mine", () => saveBlock(ed, { mine: true, source: ed.source }), !ed.source),
          button("Take the file's", () => discardWriting(ed)),
          button("Open Markdown", () => openSourceAt(ed.el))]
        : [button("Edit Markdown", () => editMarkdown(ed, payload.markdown), payload.markdown === undefined),
          button("Discard", () => discardWriting(ed))]));
    if (ed.el.tagName === "LI") ed.el.append(ed.bar);
    else ed.el.after(ed.bar);
  }

  // Plan B: the block's Markdown in place of its text.
  function editMarkdown(ed, markdown) {
    const area = h("textarea", { class: "pv-write-md", "aria-label": "Markdown of this paragraph" });
    area.value = markdown;
    area.rows = Math.max(3, Math.ceil(markdown.length / 70));
    const close = () => { box.remove(); ed.el.hidden = false; };
    const box = h("div", { class: "pv-write-box" }, area);
    const saveButton = h("button", { type: "button", text: "Save" }), cancel = h("button", { type: "button", text: "Cancel" });
    box.append(h("div", { class: "pv-actions" }, cancel, saveButton));
    cancel.addEventListener("click", close);
    saveButton.addEventListener("click", async () => {
      const { ok, status, payload } = await sendBlock(ed, { markdown: area.value });
      close();
      if (ok && payload.saved) blockSaved(ed, payload);
      else showFailure(ed, status, payload);
    });
    ed.el.hidden = true;
    ed.el.after(box);
    area.focus();
  }

  function leaveWriting() {
    const ed = writing;
    if (!ed) return;
    stopWriting(ed);
    saveBlock(ed);
  }

  document.addEventListener("mouseup", (e) => {
    if (!browserWrites || view !== "doc" || e.button !== 0 || writing || !e.target.closest) return;
    const el = e.target.closest(".pv-can-write");
    if (!el || e.target.closest(`.pv-atom, a, button, .pv-write-bar, ${OTHER_CHIPS}`)) return;
    if (showMarks && e.target.closest(".pv-edit-ins, .pv-edit-del")) return;  // these open Accept and Reject
    const selection = getSelection();
    const within = !selection.isCollapsed && el.contains(selection.anchorNode) && el.contains(selection.focusNode);
    if (!selection.isCollapsed && !within) return;  // across paragraphs: a selection to comment on
    const kept = within ? selection.getRangeAt(0).cloneRange() : null;
    const at = kept ? null : caretAt(e.clientX, e.clientY);
    if (!startWriting(el)) return;
    el.focus({ preventScroll: true });
    if (kept) { selection.removeAllRanges(); selection.addRange(kept); }
    else if (at) selection.collapse(at.node, at.offset);
  }, true);
  page.addEventListener("focusout", (e) => {
    // Another window taking the focus is not leaving the paragraph.
    if (writing && e.target === writing.el && document.hasFocus()
        && !(e.relatedTarget && writing.el.contains(e.relatedTarget))) leaveWriting();
  });
  page.addEventListener("input", () => { if (writing) fab.hidden = true; });
  document.addEventListener("keydown", (e) => {
    if (!writing) return;
    if (e.key === "Escape") {
      e.preventDefault();
      const ed = writing;
      stopWriting(ed);
      discardWriting(ed);
      getSelection().removeAllRanges();
    } else if (e.key === "Enter") {
      e.preventDefault();
      writing.el.blur();
    }
  }, true);
