// Preview viewer: the table and figure caption editors.
// The parts of the viewer are classic scripts loaded in the order of
// trazo.preview.VIEWER_SCRIPTS; their top-level names are shared globals.
  if (!window.PreviewModel) throw new Error("preview-model.js did not load");  // see preview-page.js
  // ---------------------------------------------------------------- table caption editor
  // Captions of every table kind, with generated values as locked chips; a
  // Markdown caption is edited as a title and a legend, like a figure's.
  // Structural changes open the table editor; captions stay in the preview.
  const TABLE_SPECS = window.PREVIEW_TABLE_SPECS || [];
  const tableHref = (label) => App.editorLink("/tables", label);
  const tableTitle = (label) => `Open tables/specs/${label}.json in the table editor`;
  let tableComments = {};  // label -> open table comments (GET /__tables)
  const tableEditors = new Map();  // "caption:" + label -> editor state
  unsavedChecks.push(() => [...tableEditors.values()].some((t) => t.frame.unsaved()));
  const tableOpenKey = (name) => "preview-tbl:/" + name + ".html";
  const escapeBraces = (t) => t.replace(/ /g, " ").replace(/[{}]/g, (m) => m + m);
  // A note's lines (newlines or <br>) are its paragraphs; other fields are one line.
  const serializeField = (node, template = node.dataset.template !== "false",
    breaks = node.dataset.paragraphs ? "\n\n" : "") => [...node.childNodes].map((n) =>
    n.nodeType === Node.TEXT_NODE ? (template ? escapeBraces(n.textContent) : n.textContent).replace(/\n+/g, breaks || "$&")
      : n.classList && n.classList.contains("pv-chip-v") ? n.dataset.value
      : n.nodeName === "BR" ? breaks : serializeField(n, template, breaks)).join("");
  const CAPTION_FIELDS = ["title", "legend", "note"];
  const fieldName = (key) => key === "title" ? "Title" : key === "legend" ? "Legend" : key === "note" ? "Note" : key.split(".")[1];
  // A note's paragraphs as the server writes them, to compare drafts.
  const notePlain = (text) => text.split(/\n\s*\n/).map((p) => p.replace(/\s+/g, " ").trim()).filter(Boolean).join("\n\n");
  const tableEditorKey = (label) => "caption:" + label;

  function chipFor(seg) {
    const chip = document.createElement("span");
    chip.className = "pv-chip-v" + (seg.row ? " row" : "");
    chip.contentEditable = "false";
    chip.dataset.value = seg.value;
    chip.textContent = seg.shown;
    chip.title = seg.row ? `${seg.value}: no value yet (build the tables)`
      : `${seg.value}: generated value (edit the data or script to change it)`;
    return chip;
  }

  function fillField(ed, field) {
    const text = (t) => document.createTextNode(ed.dataset.paragraphs ? t.replace(/\n\s*\n/g, "\n") : t);
    ed.replaceChildren(...field.segments.map((seg) => seg.text !== undefined ? text(seg.text) : chipFor(seg)));
  }

  function openTableEditor(table, label) {
    const key = tableEditorKey(label);
    if (tableEditors.has(key)) { goToEl(tableEditors.get(key).box); return; }
    const endpoint = "/__table-caption";
    const identity = { doc, id: "tbl:" + label };
    const fields = new Map();
    let markdown = false;
    const text = (k) => serializeField(fields.get(k).ed);
    // A Markdown caption is saved as one paragraph, so compare it as one.
    const joined = (from) => joinCaption(from("title"), from("legend"));
    const baseOf = (k) => fields.get(k).base;
    const st = { label, focus: null, range: null, fields };
    const frame = editorFrame("Table caption", {
      dirty: () => Object.keys(changes()).length > 0,
      problem: () => markdown && !balanced(text("title")) ? unpaired : "",
      offer: () => markdown && !text("title").trim() && firstSentence(text("legend")),
      useOffer: (s) => { fields.get("title").ed.textContent = s.title; fields.get("legend").ed.textContent = s.legend; },
      refresh,
      load: () => getJson(`${endpoint}?${new URLSearchParams(identity)}`),
      send: (op, sha) => postJson(endpoint, op === "undo" ? { op, ...identity } : { ...identity, base_sha256: sha, changes: changes() }),
      accept,
      removed: () => { tableEditors.delete(key); if (sget(tableOpenKey(doc)) === key) sdel(tableOpenKey(doc)); },
      saved: "Saved. The tables regenerate and the page reloads.",
      restored: "Restored. The tables regenerate and the page reloads.",
      discard: "Discard your unsaved edits to this table caption?",
    }, {
      tools: `<select data-op="insert" title="Insert a generated value at the cursor"><option value="">Insert value…</option></select>`,
    });
    const box = st.box = frame.box;
    st.frame = frame;
    (table.closest(".pv-tscroll") || table).before(box);
    tableEditors.set(key, st);
    sset(tableOpenKey(doc), key);
    const insert = $('[data-op="insert"]', box);

    function changes() {
      const out = {};
      if (markdown) {
        if (fields.size && joined(text) !== joined(baseOf)) out.caption = joined(text);
        if (fields.has("note") && notePlain(text("note")) !== notePlain(baseOf("note"))) out.note = notePlain(text("note"));
        return out;
      }
      for (const f of fields.values()) {
        const now = serializeField(f.ed);
        if (f.key === "note" ? notePlain(now) !== notePlain(f.base) : now !== f.base) out[f.key] = f.key === "note" ? notePlain(now) : now;
      }
      return out;
    }

    function refresh(busy) {
      // A response replaces the fields; do not accept edits it would erase.
      for (const f of fields.values()) f.ed.contentEditable = busy ? "false" : "true";
      const f = st.focus && fields.get(st.focus);
      insert.hidden = ![...fields.values()].some((x) => x.values.length);  // e.g. Markdown captions
      insert.disabled = busy || !f || !f.values.length;
      insert.replaceChildren(new Option(f && f.values.length ? `Insert value in ${fieldName(f.key)}…` : "Insert value…", ""),
        ...(f ? f.values.map((v, i) => new Option(v.row ? v.value : `${v.value} = ${v.shown}`, String(i))) : []));
    }

    function accept(payload) {
      fields.clear();
      frame.main.replaceChildren();
      let list = payload.fields;
      markdown = payload.kind === "markdown";
      if (markdown) {
        const parts = splitCaption(payload.fields[0].segments[0].text);
        parts.note = payload.fields[1].segments[0].text;
        list = Object.keys(parts).map((k) => ({ key: k, base: parts[k], segments: [{ text: parts[k] }], values: [] }));
      }
      for (const field of list) {
        if (!CAPTION_FIELDS.includes(field.key)) continue;
        const ed = h("div", { class: "pv-tf-text", contenteditable: "true", role: "textbox", "data-key": field.key,
          "data-template": String(payload.kind !== "markdown"), "aria-label": "Table " + fieldName(field.key).toLowerCase() });
        if (field.key === "note") Object.assign(ed.dataset, { paragraphs: "true",
          placeholder: "Below the table: abbreviations, symbols, sources. Each line is a paragraph." });
        fillField(ed, field);
        frame.main.append(h("div", { class: "pv-tf-row" }, h("span", { class: "pv-tf-name", text: fieldName(field.key) }), ed));
        fields.set(field.key, { key: field.key, ed, values: field.values, base: field.base ?? serializeField(ed) });
      }
    }

    box.addEventListener("focusin", (e) => {
      if (e.target.dataset && e.target.dataset.key) { st.focus = e.target.dataset.key; frame.refresh(); }
    });
    insert.addEventListener("change", () => {
      if (frame.state.busy || insert.value === "") return;
      const f = fields.get(st.focus), v = f && f.values[Number(insert.value)];
      insert.value = "";
      if (!v) return;
      const chip = chipFor(v);
      const range = st.range && f.ed.contains(st.range.startContainer) ? st.range : null;
      if (range) { range.deleteContents(); range.insertNode(chip); range.setStartAfter(chip); }
      else f.ed.append(chip);
      f.ed.focus();
      frame.refresh();
    });
    frame.run("load");
  }

  // Remember the caret in a table text field, for "Insert value".
  document.addEventListener("selectionchange", () => {
    const sel = window.getSelection();
    if (!sel.rangeCount || !tableEditors.size) return;
    const node = sel.anchorNode;
    const el = node && (node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement);
    const host = el && el.closest(".pv-tf-text");
    const st = host && [...tableEditors.values()].find((t) => t.box.contains(host));
    if (st) st.range = sel.getRangeAt(0).cloneRange();
  });

  for (const table of $$("table", page)) {
    const label = table.id.startsWith("tbl:") ? table.id.slice(4) : "";
    const host = table.querySelector("caption");
    if (!host) continue;
    if (!label) {
      const button = h("button", { type: "button", class: "pv-tbl-edit", text: "Edit source" });
      button.addEventListener("click", () => openSourceAt(table));
      host.append(button);
      continue;
    }
    const btn = h("button", { type: "button", class: "pv-tbl-edit", text: "Edit caption",
      title: "Edit the table caption in its original source" });
    btn.addEventListener("click", (e) => { e.preventDefault(); openTableEditor(table, label); });
    host.append(btn);
    if (TABLE_SPECS.includes(label)) host.append(h("a", { class: "pv-tbl-edit", href: tableHref(label),
      title: tableTitle(label), text: "Edit table" }));
  }
  // Reopen after live reload or a gallery action in another document.
  {
    const saved = sget(tableOpenKey(doc));
    const openLabel = saved && saved.startsWith("caption:") && saved.slice("caption:".length);
    const openTable = openLabel && document.getElementById("tbl:" + openLabel);
    if (openTable) openTableEditor(openTable, openLabel);
  }

  // ---------------------------------------------------------------- figure caption editor
  // A single-image figure's caption, as a Title and a Legend; the legend
  // keeps its line breaks. Saving rebuilds the document, which reloads.
  const captionEditors = new Map();
  unsavedChecks.push(() => [...captionEditors.values()].some((frame) => frame.unsaved()));
  const figureOpenKey = (name) => "preview-figure-caption:" + name;
  // `reopen` restores an editor after a reload without moving the reader.
  function editFigureCaption(it, reopen = false) {
    if (it.doc !== doc) { sset(figureOpenKey(it.doc), it.id); openDoc(it.doc, { hash: it.id }); return; }
    if (!reopen) setView("doc", true);
    if (it.composite) { openSourceAt(it.el); return; }
    if (captionEditors.has(it.id)) { goToEl(captionEditors.get(it.id).box); return; }
    const title = h("input", { type: "text", class: "pv-tf-text", "aria-label": "Figure title", placeholder: "Short title, e.g. Study workflow." });
    const area = h("textarea", { class: "pv-tf-text", rows: "3", "aria-label": "Figure legend", placeholder: "What the figure shows, abbreviations, statistics." });
    const value = () => joinCaption(title.value, area.value);
    let base = "";
    const frame = editorFrame("Figure caption", {
      dirty: () => value() !== base,
      problem: () => balanced(title.value) ? "" : unpaired,
      offer: () => !title.value.trim() && firstSentence(area.value),
      useOffer: (s) => { title.value = s.title; area.value = s.legend; },
      refresh: (busy) => { title.disabled = area.disabled = busy; },
      load: () => getJson(`/__figure-text?${new URLSearchParams({ doc, id: it.id })}`),
      send: (op, sha) => postJson("/__figure-text", { op, doc, id: it.id, caption: value(), base_sha256: sha }),
      accept: (payload) => {
        const parts = splitCaption(payload.caption);
        // A one-line field would join the words of a wrapped title.
        title.value = parts.title.replace(/\s+/g, " "); area.value = parts.legend;
        base = value();
      },
      removed: () => { captionEditors.delete(it.id); if (sget(figureOpenKey(doc)) === it.id) sdel(figureOpenKey(doc)); },
      saved: "Saved. The document rebuilds and the page reloads.",
      restored: "Restored. The document rebuilds and the page reloads.",
      discard: "Discard your unsaved edits to this caption?",
    });
    frame.main.append(h("label", { class: "pv-tf-row" }, h("span", { class: "pv-tf-name", text: "Title" }), title),
      h("label", { class: "pv-tf-row" }, h("span", { class: "pv-tf-name", text: "Legend" }), area));
    captionEditors.set(it.id, frame);
    sset(figureOpenKey(doc), it.id);
    it.el.after(frame.box);
    if (!reopen) goToEl(frame.box);
    frame.run("load");
  }
