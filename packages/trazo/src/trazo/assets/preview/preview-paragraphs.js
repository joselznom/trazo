// Preview viewer: writing across paragraphs. Enter breaks the paragraph at the
// caret (at its end it opens a new one, saved once it holds text), Backspace at
// the start joins it to the one before, the arrows at its edges go to the next
// one, and a paste of several paragraphs makes as many. See preview-writing.js.
// The parts of the viewer are classic scripts loaded in the order of
// trazo.preview.VIEWER_SCRIPTS; their top-level names are shared globals.
  if (!window.PreviewModel) throw new Error("preview-model.js did not load");  // see preview-page.js
  // ---------------------------------------------------------------- paragraphs
  const SUBLIST = ":scope > ul, :scope > ol, :scope > dl";

  // The paragraph from the caret to its end, or to its start.
  function restOf(ed, forward) {
    const range = getSelection().getRangeAt(0).cloneRange();
    if (forward) range.setEnd(ed.el, ed.el.childNodes.length);
    else range.setStart(ed.el, 0);
    return range;
  }
  const atEdge = (ed, forward) => textOf(restOf(ed, forward).cloneContents()) === "";

  // Whether the caret is on the paragraph's first or last line: a move by a line stays on it.
  function onLastLine(ed, forward) {
    const selection = getSelection(), was = selection.getRangeAt(0).cloneRange();
    const top = () => {
      const range = selection.getRangeAt(0);
      return (range.getClientRects()[0] || ed.el.getBoundingClientRect()).top;
    };
    const from = top();
    selection.modify("move", forward ? "forward" : "backward", "line");
    const moved = Math.abs(top() - from);
    selection.removeAllRanges();
    selection.addRange(was);
    return moved < parseFloat(getComputedStyle(ed.el).fontSize) * 0.6;
  }

  function caretAtEdge(el, atEnd) {
    const texts = textNodes(el).filter((text) => text.parentElement.closest("p, li") === el && !text.parentElement.closest(".pv-atom"));
    const text = texts[atEnd ? texts.length - 1 : 0];
    if (text) getSelection().collapse(text, atEnd ? text.data.length : 0);
    else getSelection().collapse(el, el.childNodes.length);
  }

  function writeInto(el, atEnd) {
    if (!startWriting(el)) return;
    el.focus({ preventScroll: true });
    caretAtEdge(el, atEnd);
    (getSelection().focusNode.parentElement || el).scrollIntoView({ block: "nearest" });
  }

  // Moves a paragraph's text to the end of `el`, and the paragraph goes.
  function takeText(el, ed, ...before) {
    el.append(...before, ...[...ed.el.childNodes].filter((node) => !(node.nodeType === 1 && node.matches(".pv-src, .pv-para, .pv-src-mark"))));
    dropParagraph(ed);
    numberChips(el);
  }

  function breakParagraph() {
    const ed = writing, selection = getSelection();
    if (ed.el.querySelector(SUBLIST)) return;  // the new item would take the sublist from this one
    if (!selection.isCollapsed) selection.deleteFromDocument();
    if (atEdge(ed, false)) return;
    if (ed.draftOf) saveBlock(ed);
    const mark = h("span", { class: "pv-src", "data-file": ed.mark.dataset.file, "data-h": "" });
    const el = h(ed.el.tagName.toLowerCase(), { class: ed.el.classList.contains("pv-p") ? "pv-p pv-can-write" : "pv-can-write" },
      mark, restOf(ed, true).extractContents());
    ed.el.after(el);
    blockMark.set(el, mark);
    const left = textOf(ed.el), right = textOf(el);
    const first = stopWriting();
    numberChips(el);
    const second = startWriting(el);
    el.focus({ preventScroll: true });
    selection.collapse(el, 1);
    if (!right) {
      saveBlock(first);
      second.draftOf = first;
      return;
    }
    sendBlock(first, { before: first.before, after: left + BREAK + right }, {
      group: [first, second],
      refused: (...failure) => {  // the text goes back in one paragraph, where the failure shows
        if (writing === second) stopWriting();
        takeText(first.el, second);
        showFailure(...failure);
      },
    });
  }

  function joinTo(ed, previous, payload) {
    const at = previous.childNodes.length;
    blockMark.get(previous).dataset.h = payload.parts[0];
    stopWriting();
    takeText(previous, ed, " ");
    startWriting(previous);
    previous.focus({ preventScroll: true });
    getSelection().collapse(previous, at + 1);
  }

  function joinBack() {
    const ed = writing, previous = ed.el.previousElementSibling;
    const mark = previous && blockMark.get(previous);
    if (!mark || previous.tagName !== ed.el.tagName || mark.dataset.file !== ed.mark.dataset.file) return;
    if (ed.draftOf && !textOf(ed.el)) {
      stopWriting();
      dropParagraph(ed);
      writeInto(previous, true);
      return;
    }
    if (ed.draftOf) saveBlock(ed);
    const after = textOf(ed.el);
    // What is typed before the answer would be lost with this paragraph.
    ed.joining = true;
    ed.el.contentEditable = "false";
    sendBlock(ed, () => ({ before: ed.before, after, previous: textOf(previous), into: mark.dataset.h }), {
      done: (payload) => joinTo(ed, previous, payload),
      refused: (_, status, payload) => {
        ed.joining = false;
        ed.el.contentEditable = "plaintext-only";
        ed.el.focus({ preventScroll: true });
        toast(`Not joined: ${payload.refused || payload.error || "the server refused"}.`);
      },
    });
  }

  function goTo(forward) {
    const all = $$(".pv-can-write", page), next = all[all.indexOf(writing.el) + (forward ? 1 : -1)];
    if (!next) return false;
    saveBlock(stopWriting());
    writeInto(next, !forward);
    return true;
  }

  page.addEventListener("paste", (e) => {
    if (!writing) return;
    e.preventDefault();
    const pieces = e.clipboardData.getData("text/plain").split(/\n\s*\n/).map(squash).filter(Boolean);
    pieces.forEach((piece, i) => {
      if (i) breakParagraph();
      document.execCommand("insertText", false, piece);
    });
  });
  document.addEventListener("keydown", (e) => {
    if (!writing) {
      const selection = getSelection();
      if ((e.key === "Delete" || e.key === "Backspace") && !selection.isCollapsed && page.contains(selection.anchorNode)
          && !(e.target.closest && e.target.closest("textarea, input, select, [contenteditable]")))
        toast("Select inside one paragraph to delete text.");
      return;
    }
    if (writing.joining) { e.preventDefault(); return; }
    if (e.isComposing) return;
    const plain = !e.metaKey && !e.ctrlKey && !e.altKey && !e.shiftKey;
    if (e.key === "Enter") {
      e.preventDefault();
      if (e.metaKey || e.ctrlKey) writing.el.blur();
      else breakParagraph();
    } else if (e.key === "Backspace" && plain && getSelection().isCollapsed && atEdge(writing, false)) {
      e.preventDefault();
      joinBack();
    } else if (plain && e.key.startsWith("Arrow") && getSelection().isCollapsed) {
      const forward = e.key === "ArrowDown" || e.key === "ArrowRight";
      const sideways = e.key === "ArrowLeft" || e.key === "ArrowRight";
      if ((sideways ? atEdge(writing, forward) : onLastLine(writing, forward)) && goTo(forward)) e.preventDefault();
    }
  }, true);
