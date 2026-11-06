// Preview viewer: bold and italic while writing. Cmd+B, Cmd+I or the bar for a
// selection toggle the style on the selected text, in the Markdown first
// (POST /__block-format, trazo.preview_block_format) and then on the page. See
// preview-writing.js.
// The parts of the viewer are classic scripts loaded in the order of
// trazo.preview.VIEWER_SCRIPTS; their top-level names are shared globals.
  if (!window.PreviewModel) throw new Error("preview-model.js did not load");  // see preview-page.js
  // ---------------------------------------------------------------- format
  // The page's text in the range, as the Markdown now has it.
  function paintStyle(range, tag, on) {
    const contents = range.extractContents();
    for (const inner of contents.querySelectorAll(tag)) inner.replaceWith(...inner.childNodes);
    const nodes = [...contents.childNodes];
    const inside = range.startContainer.nodeType === 1 ? range.startContainer : range.startContainer.parentElement;
    const outer = inside.closest(tag);
    if (on) {
      const wrap = document.createElement(tag);
      wrap.append(contents);
      range.insertNode(wrap);
    } else if (outer) {
      // The text leaves `outer` and keeps the other styles it had inside it.
      let moved = contents;
      for (let node = inside; node !== outer; node = node.parentElement) {
        const copy = node.cloneNode(false);
        copy.append(moved);
        moved = copy;
      }
      const tail = range.cloneRange();
      tail.setEndAfter(outer);
      const rest = tail.extractContents(), parts = [...rest.childNodes];
      outer.after(moved, rest);
      for (const part of [outer, ...parts]) if (!part.textContent) part.remove();
    } else range.insertNode(contents);
    range.setStartBefore(nodes[0]);
    range.setEndAfter(nodes[nodes.length - 1]);
  }

  function toggleStyle(style) {
    const ed = writing, selection = getSelection();
    if (!ed || selection.isCollapsed || !ed.el.contains(selection.anchorNode) || !ed.el.contains(selection.focusNode)) return;
    const range = selection.getRangeAt(0);
    const head = range.cloneRange(), upTo = range.cloneRange();
    head.collapse(true);
    head.setStart(ed.el, 0);
    upTo.setStart(ed.el, 0);
    // In characters, as the server counts them: an emoji is two units of a JavaScript string.
    const from = [...textOf(head.cloneContents())].length, to = [...textOf(upTo.cloneContents())].length;
    saveBlock(ed);
    sendBlock(ed, () => ({ before: ed.before, style, from, to }), {
      path: "/__block-format",
      when: () => !failed.has(ed.el),  // the offsets are into text that did not save
      refused: (_, status, payload) => toast(`Not formatted: ${refusal(payload)}.`),
      done: (payload) => {
        ed.mark.dataset.h = payload.parts[0];
        if (range.collapsed) return;
        paintStyle(range, style, payload.on);
        ed.restore = snapshotOf(ed.el);
        if (writing === ed) { selection.removeAllRanges(); selection.addRange(range); }
      },
    });
  }

  const formatButton = (style, label, title) => {
    const button = h("button", { type: "button", "data-select": style, title, "aria-label": title });
    button.append(h(style, { text: label }));
    return button;
  };
  // The bar's buttons for a selection in the paragraph being written.
  const formatButtons = () => (writing ? [formatButton("strong", "B", "Bold (⌘B)"), formatButton("em", "I", "Italic (⌘I)")] : []);

  document.addEventListener("keydown", (e) => {
    if (!(e.metaKey || e.ctrlKey) || e.shiftKey || e.altKey) return;
    const style = { b: "strong", i: "em" }[e.key.toLowerCase()];
    if (!style) return;
    if (writing) {
      e.preventDefault();
      if (!writing.joining) toggleStyle(style);
      return;
    }
    // Text selected across paragraphs: the author means a style, not the build Cmd+B starts.
    const selection = getSelection();
    if (selection.isCollapsed || !page.contains(selection.anchorNode)) return;
    e.preventDefault();
    e.stopPropagation();
    toast("Select inside one paragraph to format text.");
  }, true);
