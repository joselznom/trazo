// Preview viewer: the @ menu while writing. An @ after a space opens a list of
// the paper's references, figures, tables and values, narrowed as the author
// types. Enter or a click inserts one as a locked chip, and Esc leaves a plain
// @. Only what exists is offered (trazo.preview_mentions). See preview-writing.js.
// The parts of the viewer are classic scripts loaded in the order of
// trazo.preview.VIEWER_SCRIPTS; their top-level names are shared globals.
  if (!window.PreviewModel) throw new Error("preview-model.js did not load");  // see preview-page.js
  // ---------------------------------------------------------------- mentions
  const MENTIONS_SHOWN = 8;
  const mentionMenu = h("div", { class: "pv-at-menu", role: "listbox", "aria-label": "Insert a reference", hidden: true });
  document.body.append(mentionMenu);
  let mention = null;        // {node, start, end, items, at} while the menu is open
  let mentionsLeft = null;   // the @ the author closed with Esc: no menu until it changes
  let referencesAsked = null;
  let newChips = 0;

  async function mentionItems() {
    referencesAsked = referencesAsked || postJson("/__references").then(({ ok, payload }) => (ok ? payload.references : [])).catch(() => []);
    const [references, values] = await Promise.all([referencesAsked, loadValues()]);
    return [
      ...references.map(({ key, text }) => ({ kind: "reference", key, label: key, text })),
      ...figItems().map((it) => ({ kind: "object", key: it.id, label: it.num, text: it.caption })),
      ...Object.entries(values).map(([key, value]) => ({ kind: "value", key, label: value.text, text: key })),
    ];
  }

  // The @ word the caret is at the end of.
  function mentionAtCaret() {
    const selection = getSelection(), node = selection.focusNode;
    if (!selection.isCollapsed || !node || node.nodeType !== 3) return null;
    const found = /(?:^|[\s([])@([^\s@]*)$/.exec(node.data.slice(0, selection.focusOffset));
    return found && { node, start: selection.focusOffset - found[1].length - 1, end: selection.focusOffset, query: found[1].toLowerCase() };
  }

  function closeMentions() {
    mention = null;
    mentionMenu.hidden = true;
  }

  function showMentions() {
    mentionMenu.replaceChildren(...mention.items.map((item, i) => {
      const row = h("button", { type: "button", role: "option", "aria-selected": String(i === mention.at) },
        h("b", { text: item.label }), h("span", { text: item.text }));
      row.addEventListener("mousedown", (e) => { e.preventDefault(); insertMention(item); });
      return row;
    }));
    mentionMenu.hidden = false;
    const range = document.createRange();
    range.setStart(mention.node, mention.start);
    const box = range.getClientRects()[0] || writing.el.getBoundingClientRect();
    mentionMenu.style.top = `${Math.max(8, Math.min(box.bottom + 4, innerHeight - mentionMenu.offsetHeight - 8))}px`;
    mentionMenu.style.left = `${Math.max(8, Math.min(box.left, innerWidth - mentionMenu.offsetWidth - 8))}px`;
  }

  async function updateMentions() {
    const found = writing && mentionAtCaret();
    if (!found || (mentionsLeft === found.node && found.query === "")) { closeMentions(); return; }
    mentionsLeft = null;
    const all = await mentionItems();
    const now = writing && mentionAtCaret();  // the author may have typed on
    if (!now || now.query !== found.query) return;
    const items = all.filter((item) => `${item.key} ${item.label} ${item.text}`.toLowerCase().includes(now.query)).slice(0, MENTIONS_SHOWN);
    if (!items.length) { closeMentions(); return; }
    mention = { ...now, items, at: 0 };
    showMentions();
  }

  function insertMention(item) {
    const { node, start, end } = mention;
    closeMentions();
    const range = document.createRange();
    range.setStart(node, start);
    range.setEnd(node, end);
    range.deleteContents();
    const chip = h("span", { class: "pv-atom", contenteditable: "false", text: item.label,
      "data-new": String(newChips++), "data-kind": item.kind, "data-key": item.key });
    range.insertNode(chip);
    writing.locked.push(chip);
    range.setStartAfter(chip);
    range.collapse(true);
    const selection = getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    fab.hidden = true;
  }

  page.addEventListener("input", updateMentions);
  page.addEventListener("focusout", closeMentions);
  // Before the keys of the paragraph: while the menu is open, these are the menu's.
  window.addEventListener("keydown", (e) => {
    if (!mention) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") mention.at = (mention.at + (e.key === "ArrowDown" ? 1 : mention.items.length - 1)) % mention.items.length;
    else if (e.key === "Enter" || e.key === "Tab") insertMention(mention.items[mention.at]);
    else if (e.key === "Escape") { mentionsLeft = mention.node; closeMentions(); } else return;
    e.preventDefault();
    e.stopImmediatePropagation();
    if (mention) showMentions();
  }, true);
