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
  const NOT_TEXT = ".pv-src, .pv-para, .pv-src-mark, .pv-edit-del, .pv-write-bar, .header-section-number, button, ul, ol, dl, blockquote";
  const CHIP = 0xE000, NEW_CHIP = 0xE800, OTHER_CHIP = 0xF000, BREAK = "\uF8FF";
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

  // Each paragraph with the block it is: one with two is not one block, and takes no writing.
  const HEADINGS = "h1, h2, h3, h4, h5, h6";
  const blockMark = new Map();
  if (browserWrites) for (const mark of $$(".pv-src", page)) {
    const el = mark.closest("p, li");
    if (el && !el.closest("nav, table, figure")) blockMark.set(el, blockMark.has(el) ? null : mark);
  }
  // A heading's mark is an attribute on it, which its <section> gets too.
  if (browserWrites) for (const el of $$(`:is(${HEADINGS}).pv-head`, page)) blockMark.set(el, el);
  for (const [el, mark] of blockMark) if (mark) el.classList.add("pv-can-write");
  const isHeading = (el) => el.matches(HEADINGS);

  const chipChar = (chip) => String.fromCharCode(chip.dataset.new === undefined ? CHIP + Number(chip.dataset.i) : NEW_CHIP + Number(chip.dataset.new));

  // The paragraph's text as the server reads it, each chip one character.
  function textOf(el) {
    let others = 0;
    const walk = (node) => [...node.childNodes].map((child) => {
      if (child.nodeType === 3) return child.data;
      if (child.nodeType !== 1 || child.matches(NOT_TEXT)) return "";
      if (child.tagName === "BR") return " ";
      if (child.matches(".pv-atom")) return chipChar(child);
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
    for (const text of textNodes(el)) if (text.data.includes("\n"))
      text.data = text.data.replace(/\n/g, text.previousSibling?.nodeName === "BR" ? "" : " ");
  }

  function startWriting(el) {
    const mark = blockMark.get(el);
    if (!mark) return null;
    let ed = failed.get(el);
    if (ed) clearFailure(ed);
    else {
      dropLineBreaks(el);
      ed = { el, mark, before: textOf(el), restore: snapshotOf(el) };
    }
    ed.locked = [...el.querySelectorAll(`.pv-atom, ${OTHER_CHIPS}, ${NOT_TEXT}`)];
    for (const node of ed.locked) node.contentEditable = "false";
    el.classList.add("pv-writing");
    el.contentEditable = "plaintext-only";
    writing = ed;
    return ed;
  }

  function stopWriting() {
    const ed = writing;
    writing = null;
    ed.el.removeAttribute("contenteditable");
    ed.el.classList.remove("pv-writing");
    for (const node of ed.locked) node.removeAttribute("contenteditable");
    forgetPageText();
    return ed;
  }

  function clearFailure(ed) {
    failed.delete(ed.el);
    ed.el.classList.remove("pv-write-failed");
    if (ed.bar) ed.bar.remove();
    ed.bar = null;
  }

  const refusal = (payload) => payload.refused || payload.error || "the server refused";

  // One at a time and in order: a save may need the key the one before it got.
  // `group` are the paragraphs the block becomes, the first being the one sent.
  let saveQueue = Promise.resolve();
  function sendBlock(ed, body, { path = "/__block", group = [ed], block = group[0], refused = showFailure, done = null, when = null } = {}) {
    saves++;
    showSaveState("Saving…");
    saveQueue = saveQueue.then(async () => {
      // A paragraph a refused break folded back has nothing left to save.
      if (!ed.el.isConnected || (when && !when())) { saves--; return; }
      if (!block.el.isConnected) {  // a new paragraph after one that was folded back: its text stays, flagged
        saves--;
        refused(ed, 0, { error: "the paragraph before it was not saved" });
        return;
      }
      const { file, h: key } = block.mark.dataset;
      const sent = typeof body === "function" ? body() : body;
      let result;
      try {
        result = await postJson(path, { file, h: key, ...sent });
      } catch (e) {
        result = { ok: false, status: 0, payload: { error: "the preview server did not answer" } };
      }
      saves--;
      if (!result.ok || !result.payload.saved) {
        showSaveState("", false);
        refused(ed, result.status, result.payload);
      } else if (done) { showSaveState("Saved", true); done(result.payload); }
      else blockSaved(ed, result.payload, group, sent);
    });
  }

  function saveBlock(ed, extra) {
    const after = textOf(ed.el), anchor = ed.draftOf;
    if (anchor) {
      ed.draftOf = null;
      if (!after) { dropParagraph(ed); return; }
      ed.before = after;
      const parts = ed.above ? [ed, anchor] : [anchor, ed], mentions = mentionsIn(ed.el);
      sendBlock(ed, () => ({ before: anchor.before, after: parts.map((part) => part.before).join(BREAK), ...mentions, ...extra }), {
        group: parts,
        block: anchor,
        refused: (...failure) => { ed.draftOf = anchor; showFailure(...failure); },
      });
    } else if (after !== (ed.queued ?? ed.before) || failed.has(ed.el)) {
      // What a save goes from is what the one before it left, known only when it is sent.
      const mentions = mentionsIn(ed.el);
      ed.queued = after;
      sendBlock(ed, () => ({ before: ed.before, after, ...mentions, ...extra }));
    }
  }

  function dropParagraph(ed) {
    const item = ed.el.parentElement;
    blockMark.delete(ed.el);
    ed.el.remove();
    if (item && item.tagName === "LI" && !item.querySelector("p, ul, ol, dl")) item.remove();
  }

  // The chips the @ menu added to these paragraphs, for the save that takes them to the Markdown.
  function mentionsIn(...els) {
    const chips = els.flatMap((el) => $$("[data-new]:not([data-saved])", el));
    return chips.length ? { mentions: Object.fromEntries(chips.map((chip) => [chipChar(chip), { kind: chip.dataset.kind, key: chip.dataset.key }])) } : {};
  }

  // A chip's number only names it in its paragraph: the server goes by their order.
  // Two paragraphs joined need new names.
  function numberChips(el) {
    $$(".pv-atom", el).filter((chip) => chip.closest("p, li") === el && (chip.dataset.new === undefined || chip.dataset.saved !== undefined))
      .forEach((chip, i) => {
        delete chip.dataset.new;
        chip.dataset.i = String(i);
      });
  }

  function blockSaved(ed, payload, group, sent) {
    clearFailure(ed);
    closeMarkdown(ed);
    // Each paragraph now is what this save wrote, whatever was queued after it.
    const written = sent.after === undefined ? [] : sent.after.split(BREAK);
    group.forEach((part, i) => {
      if (payload.parts[i]) part.mark.dataset.h = payload.parts[i];
      if (written[i] !== undefined) part.before = written[i];
      for (const chip of $$("[data-new]", part.el)) if (chipChar(chip) in (sent.mentions || {})) chip.dataset.saved = "";
    });
    if (!payload.parts.length) dropParagraph(ed);
    showSaveState("Saved", true);
    // A heading without an id of its own gets one from its text: a link to the old one breaks.
    const anchor = ed.el.closest("section")?.id || ed.el.id;
    if (isHeading(ed.el) && ed.mark.dataset.auto && anchor && $$(`a[href="#${CSS.escape(anchor)}"]`, page).length)
      toast("A link in the text points to this heading by its old name: give the heading an id or update the link.");
  }

  // Outside the text, Cmd+Z undoes the latest change, a saved paragraph or a comment.
  async function undoLatest() {
    const { ok, payload } = await postJson(`/__undo?doc=${encodeURIComponent(doc)}`);
    if (ok && payload.kind === "comment" && undoLabel) send({ op: "undo" }, `Undone: ${undoLabel}`);
    if (!ok) { toast(`Not undone: ${refusal(payload)}.`); return; }
    if (payload.kind !== "file") return;
    const { ok: undone, payload: answer } = await postJson("/__source", { op: "undo", file: payload.file });
    toast(undone ? `Undone: the last save of ${payload.file}` : `Not undone: ${refusal(answer)}.`);
  }

  function discardWriting(ed) {
    clearFailure(ed);
    closeMarkdown(ed);
    if (ed.draftOf) dropParagraph(ed);
    else ed.restore();
    forgetPageText();
    showSaveState("", false);
  }

  function writeButton(text, run, disabled) {
    const button = h("button", { type: "button", text, disabled });
    button.addEventListener("click", run);
    return button;
  }

  function showFailure(ed, status, payload) {
    showSaveState("Not saved", true);
    ed.queued = undefined;  // what it tried is not saved: leaving the text tries again
    failed.set(ed.el, ed);
    ed.el.classList.add("pv-write-failed");
    if (ed.bar) ed.bar.remove();
    const changed = status === 409;
    const text = changed ? "This paragraph changed on disk, so your text is not saved."
      : `Not saved: ${refusal(payload)}.`;
    ed.bar = h("div", { class: "pv-write-bar", role: "alert", contenteditable: "false" }, h("span", { text }),
      ...(changed
        ? [writeButton("Keep mine", () => (ed.area ? sendBlock(ed, { keep: true, markdown: ed.area.value }) : saveBlock(ed, { keep: true }))),
          writeButton("Take the file's", () => discardWriting(ed)),
          writeButton("Open Markdown", () => openSourceAt(ed.el))]
        : [writeButton("Edit Markdown", () => editMarkdown(ed, payload.markdown), payload.markdown === undefined),
          writeButton("Discard", () => discardWriting(ed))]));
    placeBar(ed);
  }

  function placeBar(ed) {
    if (ed.box) ed.box.prepend(ed.bar);
    else if (ed.el.tagName === "LI") ed.el.append(ed.bar);
    else ed.el.after(ed.bar);
  }

  // Plan B: the block's Markdown in place of its text, there until it is saved.
  function editMarkdown(ed, markdown) {
    const area = ed.area = h("textarea", { class: "pv-write-md", "aria-label": "Markdown of this paragraph" });
    area.value = markdown;
    area.rows = Math.max(3, Math.ceil(markdown.length / 70));
    ed.box = h("div", { class: "pv-write-box" }, area, h("div", { class: "pv-actions" },
      writeButton("Cancel", () => closeMarkdown(ed)),
      writeButton("Save", () => { if (!saves) sendBlock(ed, { markdown: area.value }); })));
    ed.el.hidden = true;
    ed.el.after(ed.box);
    area.focus();
  }

  function closeMarkdown(ed) {
    if (!ed.box) return;
    ed.box.remove();
    ed.box = ed.area = null;
    ed.el.hidden = false;
    if (ed.bar) placeBar(ed);
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
    // Another window taking the focus leaves the paragraph the active element, and that is not leaving it.
    if (writing && !writing.joining && e.target === writing.el && document.activeElement !== e.target
        && !(e.relatedTarget && writing.el.contains(e.relatedTarget))) saveBlock(stopWriting());
  });
  page.addEventListener("input", () => { if (writing) fab.hidden = true; });
  document.addEventListener("keydown", (e) => {
    if (writing && !writing.joining && e.key === "Escape") {
      e.preventDefault();
      discardWriting(stopWriting());
      getSelection().removeAllRanges();
    }
  }, true);
