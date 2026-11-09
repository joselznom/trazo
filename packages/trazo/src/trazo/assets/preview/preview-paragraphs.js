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

  // The list item a paragraph opens: the <li> itself, or in a loose list the <li> of its first <p>.
  function itemOf(el) {
    if (el.tagName === "LI") return el;
    const item = el.parentElement;
    return item.tagName === "LI" && item.querySelector(":scope > p") === el ? item : null;
  }

  // A new paragraph next to this one, or in a list a new item.
  function newParagraph(ed, contents, above) {
    const mark = h("span", { class: "pv-src", "data-file": ed.mark.dataset.file, "data-h": "" });
    const el = h(ed.el.tagName.toLowerCase(), { class: ed.el.classList.contains("pv-p") ? "pv-p pv-can-write" : "pv-can-write" }, mark, contents);
    blockMark.set(el, mark);
    const item = itemOf(ed.el), loose = item && item !== ed.el;
    const outer = loose ? h("li", null, el) : el;
    if (above) (loose ? item : ed.el).before(outer);
    else {
      // The item's other paragraphs follow the line that starts the new item.
      if (loose) for (let next = ed.el.nextSibling; next; next = ed.el.nextSibling) outer.append(next);
      (loose ? item : ed.el).after(outer);
    }
    return el;
  }

  // Moves a paragraph's text to the end of `el`, and the paragraph goes.
  function takeText(el, ed, ...before) {
    el.append(...before, ...[...ed.el.childNodes].filter((node) => !(node.nodeType === 1 && node.matches(".pv-src, .pv-para, .pv-src-mark"))));
    // The item's other paragraphs go with its text, or the item would stay behind.
    const item = itemOf(ed.el);
    if (item && item !== ed.el && item !== el.parentElement) {
      for (let next = ed.el.nextSibling; next; next = ed.el.nextSibling) el.parentElement.append(next);
    }
    dropParagraph(ed);
  }

  function breakParagraph() {
    const ed = writing, selection = getSelection(), item = itemOf(ed.el);
    if (isHeading(ed.el)) { goTo(true, true); return ""; }  // a heading stays one line: Enter goes on to the text
    if (item && item.querySelector(SUBLIST)) return;  // the new item would take the sublist from this one
    const step = newStep("break", ed, [ed.el]);
    if (!selection.isCollapsed) selection.deleteFromDocument();
    if (!textOf(ed.el)) return;  // the file has no empty paragraphs
    if (ed.draftOf && !atEdge(ed, false)) saveBlock(ed);
    const above = atEdge(ed, false);  // a paragraph cannot start empty: this opens one before it
    const el = newParagraph(ed, above ? "" : restOf(ed, true).extractContents(), above);
    // The space at the break goes, as it does in the saved text.
    const start = textNodes(el)[0], end = textNodes(ed.el).pop();
    const spaceAfter = start && /^\s/.test(start.data) ? " " : "";
    const gap = spaceAfter || (end && /\s$/.test(end.data) ? " " : "");
    if (start) start.data = start.data.trimStart();
    if (end) end.data = end.data.trimEnd();
    const left = textOf(ed.el), right = textOf(el), mentions = mentionsIn(ed.el, el);
    const first = stopWriting();
    const second = startWriting(el);
    el.focus({ preventScroll: true });
    selection.collapse(el, 1);
    const afterwards = { left: [...step.before.parent.childNodes], afterBlocks: [first.el, el], afterTarget: el };
    if (!right) {
      saveBlock(first);
      second.draftOf = first;
      second.above = above;
      // The file has no empty paragraphs: taking this one back is the page's alone. It changed
      // no keys or chips, so it keeps those the saves before it give them (data: none).
      addStep(step, { ...afterwards, kind: "draft", draft: second, above, results: [[el, ""]], saved: () => first.queued ?? first.before,
        before: { ...step.before, data: [] } });
      return spaceAfter;
    }
    addStep(step, { ...afterwards, results: [[first.el, left], [el, right]] });
    first.brokenInto = second;
    second.gap = gap;
    sendBlock(first, () => ({ before: first.before, after: left + BREAK + right, ...mentions }), {
      group: [first, second],
      refused: (...failure) => {  // the text goes back in one paragraph, where the failure shows
        for (let part = second; part; part = part.brokenInto) {
          if (writing === part) stopWriting();
          takeText(first.el, part, part.gap);
        }
        showFailure(...failure);
      },
      step,
    });
    return spaceAfter;
  }

  function joinTo(ed, previous, payload) {
    const at = previous.childNodes.length;
    blockMark.get(previous).dataset.h = payload.parts[0];
    stopWriting();
    for (const chip of $$("[data-new]", ed.el)) chip.dataset.saved = "";  // the join took them to the Markdown
    takeText(previous, ed, " ");
    numberChips(previous);
    startWriting(previous);
    previous.focus({ preventScroll: true });
    getSelection().collapse(previous, at + 1);
  }

  function joinBack() {
    const ed = writing, item = itemOf(ed.el), loose = item && item !== ed.el;
    if (isHeading(ed.el)) return;
    const sibling = (loose ? item : ed.el).previousElementSibling;
    const previous = loose ? sibling && $$(":scope > p", sibling).pop() : sibling;
    const mark = previous && blockMark.get(previous);
    if (!mark || previous.tagName !== ed.el.tagName || mark.dataset.file !== ed.mark.dataset.file) return;
    if (!textOf(ed.el)) {  // nothing to join: it goes
      saveBlock(stopWriting());
      writeInto(previous, true);
      return;
    }
    if (ed.draftOf) saveBlock(ed);
    const step = newStep("join", ed, [previous, ed.el]);
    addStep(step, { afterBlocks: [previous], afterTarget: previous });  // undoable once joined
    holdKeys(ed, true);
    sendBlock(ed, () => ({ before: ed.before, after: textOf(ed.el), previous: textOf(previous), into: mark.dataset.h, ...mentionsIn(ed.el) }), {
      done: (payload) => {
        joinTo(ed, previous, payload);
        Object.assign(step, { results: [[previous, textOf(previous)]], left: [...step.before.parent.childNodes] });
      },
      step,
      refused: (_, status, payload) => {
        holdKeys(ed, false);
        toast(`Not joined: ${refusal(payload)}.`);
      },
    });
  }

  function goTo(forward, toText) {
    const all = $$(".pv-can-write", page), from = all.indexOf(writing.el);
    const next = toText ? all.slice(from + 1).find((el) => !isHeading(el)) : all[from + (forward ? 1 : -1)];
    if (!next) return false;
    saveBlock(stopWriting());
    writeInto(next, !forward);
    return true;
  }

  page.addEventListener("paste", (e) => {
    if (!writing) return;
    e.preventDefault();
    const text = e.clipboardData.getData("text/plain");
    const pieces = (isHeading(writing.el) ? [text] : text.split(/\r?\n[ \t]*\r?\n\s*/))
      .map((piece) => piece.replace(/\s+/g, " ")).filter((piece) => piece.trim());
    const paste = () => {
      let gap = "";  // the space before the text that follows, kept after the last piece
      pieces.forEach((piece, i) => {
        if (i) gap = breakParagraph() || gap;
        document.execCommand("insertText", false, i === pieces.length - 1 ? piece + gap : piece);
      });
    };
    if (pieces.length < 2 || !afterStyle(paste)) paste();
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
      else if (!afterStyle(breakParagraph)) breakParagraph();
    } else if (e.key === "Backspace" && plain && getSelection().isCollapsed && atEdge(writing, false)) {
      e.preventDefault();
      if (!afterStyle(joinBack)) joinBack();
    } else if (plain && e.key.startsWith("Arrow") && getSelection().isCollapsed) {
      const forward = e.key === "ArrowDown" || e.key === "ArrowRight";
      const sideways = e.key === "ArrowLeft" || e.key === "ArrowRight";
      if ((sideways ? atEdge(writing, forward) : onLastLine(writing, forward)) && goTo(forward)) e.preventDefault();
    }
  }, true);
