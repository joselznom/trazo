// Preview viewer: bold, italic and underline while writing. Cmd+B, Cmd+I, Cmd+U
// or the bar for a selection toggle the style on the selected text, in the Markdown first
// (POST /__block-format, trazo.preview_block_format) and then on the page. See
// preview-writing.js.
// The parts of the viewer are classic scripts loaded in the order of
// trazo.preview.VIEWER_SCRIPTS; their top-level names are shared globals.
  if (!window.PreviewModel) throw new Error("preview-model.js did not load");  // see preview-page.js
  // ---------------------------------------------------------------- format
  // Each style of POST /__block-format and the tag Pandoc's HTML gives it.
  const FORMAT_TAG = { strong: "strong", em: "em", underline: "u" };
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
    // A double click takes the space after the word on some systems: a style stops at the word.
    const range = selection.getRangeAt(0).cloneRange();
    while (range.endContainer.nodeType === 3 && range.endOffset && /\s/.test(range.endContainer.data[range.endOffset - 1]))
      range.setEnd(range.endContainer, range.endOffset - 1);
    while (range.startContainer.nodeType === 3 && /\s/.test(range.startContainer.data[range.startOffset] || ""))
      range.setStart(range.startContainer, range.startOffset + 1);
    if (range.collapsed) return;
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
        paintStyle(range, FORMAT_TAG[style], payload.on);
        ed.restore = snapshotOf(ed.el);
        if (writing === ed) { selection.removeAllRanges(); selection.addRange(range); pressFormatButtons(); }
      },
    });
  }

  // Whether every letter of the range is inside `tag` within `root`.
  function rangeAllIn(range, root, tag) {
    const walk = document.createTreeWalker(range.commonAncestorContainer, NodeFilter.SHOW_TEXT);
    let any = false;
    for (let node = walk.currentNode.nodeType === 3 ? walk.currentNode : walk.nextNode(); node; node = walk.nextNode()) {
      if (!range.intersectsNode(node) || !node.data.trim()) continue;
      const start = node === range.startContainer ? range.startOffset : 0;
      const end = node === range.endContainer ? range.endOffset : node.data.length;
      if (!node.data.slice(start, end).trim()) continue;
      const styled = node.parentElement.closest(tag);
      if (!styled || !root.contains(styled)) return false;
      any = true;
    }
    return any;
  }
  // Pressed when the whole selection has the style, so a click takes it off.
  function pressFormatButtons() {
    const selection = getSelection();
    if (!writing || !selection.rangeCount) return;
    const range = selection.getRangeAt(0);
    for (const button of fab.querySelectorAll("[data-select]")) {
      const tag = FORMAT_TAG[button.dataset.select];
      if (tag) button.setAttribute("aria-pressed", String(rangeAllIn(range, writing.el, tag)));
    }
  }
  const formatButton = (style, label, title, hint) => {
    const button = h("button", { type: "button", class: "pv-fmt", "data-select": style, title: `${title}\n${hint}`,
      "aria-label": title, "aria-pressed": "false" });
    button.append(h(FORMAT_TAG[style], { text: label }));
    return button;
  };
  // The bar's buttons for a selection in the paragraph being written, after a
  // separator: what is left of it never reaches Word, these do.
  const formatButtons = () => (writing ? [h("span", { class: "pv-fab-sep", "aria-hidden": "true" }),
    formatButton("strong", "B", `Bold (${MOD}B)`, "Shows in Word"),
    formatButton("em", "I", `Italic (${MOD}I)`, "Shows in Word"),
    formatButton("underline", "U", `Underline (${MOD}U)`, "Shows in Word")] : []);

  document.addEventListener("keydown", (e) => {
    if (!(e.metaKey || e.ctrlKey) || e.shiftKey || e.altKey) return;
    const style = { b: "strong", i: "em", u: "underline" }[e.key.toLowerCase()];
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
