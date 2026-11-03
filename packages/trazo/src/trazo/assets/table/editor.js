// Table editor (/tables#<label>): edits tables/specs/<label>.json through the
// preview server (trazo.table_editor). Every change is a list of
// `trazo table patch` operations sent with the spec's sha256; the server
// validates, saves, records history and renders the Markdown. The table is
// drawn from the server's render model, so each cell knows its leaf id and
// row. Numbers are never edited here, only how they are shown. The panels,
// comments and editing on the table are in the other editor-*.js files.
(function () {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const DOCS = window.PREVIEW_TITLES || { manuscript: "Manuscript", supplementary: "Supplementary" };
  const App = window.Trazo;
  const T = window.TableEditor;
  const SAVE_DELAY = 600, PREVIEW_DELAY = 200, POLL = 2000;

  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "text") el.textContent = v;
      else if (k === "html") el.innerHTML = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "dataset") Object.assign(el.dataset, v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) el.append(c);
    return el;
  }
  const clone = (v) => JSON.parse(JSON.stringify(v));
  const show = (v) => JSON.stringify(v);
  const stable = (o) => JSON.stringify(Object.keys(o || {}).sort().map((k) => [k, o[k]]));
  const plain = (html) => { const d = document.createElement("div"); d.innerHTML = html; return d.textContent; };

  const api = App.api;

  // ---------------------------------------------------------------- state
  let label = "";
  let st = null;            // GET /__table payload
  let spec = null;          // st.spec (the saved spec)
  let list = [];            // GET /__tables
  let listLoaded = false;   // poll() retries a failed first load
  let numbers = {};         // label -> {doc, num}
  let comments = { comments: [], undo: null };
  let sel = { kind: "table" };
  let tab = "props";
  let showHidden = false;
  let composeFor = null;    // target of a new comment
  let advanced = !!App.readPrefs().tableAdvanced;  // technical settings shown
  let editing = false;      // a header or label is being edited on the table
  const pending = new Map();  // path -> value ("shared:" prefix: _shared.json)
  let saveTimer = null, previewTimer = null, queue = Promise.resolve();
  let busy = 0, blocked = false, lastStatus = null, lastCatalog = null;
  let stateVersion = 0;     // discard polls started before a load or save completed
  let editVersion = 0;      // also protect typing and queued edits from old GETs/previews

  App.mountTheme($(".te-top"), { themeButton: $("#te-theme") });

  // ---------------------------------------------------------------- messages
  function setSave(text, error) {
    const el = $("#te-save");
    el.textContent = text;
    el.classList.toggle("err", !!error);
    $("#te-ftext").textContent = text.startsWith("Saved") ? "Saved automatically · ⌘Z undoes" : text;
    $("#te-fdot").className = "te-dot" + (error ? " err" : busy || pending.size ? " warn" : "");
  }
  let flashTimer = null;
  // A short notice; with `action` ({label, run}) it stays longer and offers
  // a button (Undo after a comment change). Hovering keeps it open.
  function flash(text, action) {
    let el = $(".te-flash");
    if (!el) {
      el = h("div", { class: "te-flash", role: "status" });
      el.addEventListener("mouseenter", () => clearTimeout(flashTimer));
      el.addEventListener("mouseleave", () => { flashTimer = setTimeout(() => el.remove(), 2600); });
      document.body.append(el);
    }
    el.textContent = text;
    if (action) el.append(h("button", { type: "button", text: action.label,
      onclick: () => { clearTimeout(flashTimer); el.remove(); action.run(); } }));
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => el.remove(), action ? 6000 : 2600);
  }
  function banner(content, kind, actions) {
    const el = $("#te-banner");
    el.replaceChildren();
    if (!content) { el.hidden = true; return; }
    el.className = "te-banner" + (kind === "err" ? " err" : "");
    el.append(h("div", { class: "te-grow" }, content), ...(actions || []),
      h("button", { type: "button", class: "te-icon", "aria-label": "Dismiss", text: "✕", onclick: () => banner(null) }));
    el.hidden = false;
  }

  // ---------------------------------------------------------------- spec helpers
  // Spec path of a leaf of the model: <id>, <group>.<leaf>, or <pivot>.<leaf>
  // for <pivot>.<value>.<leaf>.
  function leafPath(id) {
    const parts = id.split(".");
    if (parts.length === 1) return id;
    const top = spec.columns.find((c) => c.id === parts[0]);
    return top && top.pivot ? `${parts[0]}.${parts[parts.length - 1]}` : id;
  }
  // The spec column at a dotted path, with its sibling list.
  function colAt(path) {
    const parts = path.split(".");
    let siblings = spec.columns, col = null, parent = null;
    for (const [i, part] of parts.entries()) {
      col = (siblings || []).find((c) => c.id === part);
      if (!col) return null;
      if (i < parts.length - 1) { parent = col; siblings = col.columns; }
    }
    return { col, parent, siblings: parent ? parent.columns : spec.columns };
  }
  const isLeaf = (c) => c && !c.columns && !c.pivot;
  function leafIds() {
    const ids = [];
    for (const row of (st.model ? st.model.header : [])) for (const c of row) if (c.kind === "leaf") ids.push(c);
    return ids;
  }
  function formats() { return { ...(st.shared.formats || {}), ...(spec.formats || {}) }; }
  const isLocal = (name) => !!(spec.formats && name in spec.formats);
  function uniqueId(base, taken) {
    const clean = (base || "col").replace(/[^A-Za-z0-9_-]/g, "_").replace(/^[^A-Za-z0-9]+/, "") || "col";
    let id = clean, n = 2;
    while (taken.includes(id)) id = `${clean}_${n++}`;
    return id;
  }
  const fieldNames = () => Object.keys(st.data.fields);
  const fieldType = (f) => { const d = st.data.fields[f]; return d ? d.types.filter((t) => t !== "null").join("|") : "?"; };
  function defaultTemplate(field) {
    const d = st.data.fields[field];
    return d && d.keys && d.keys.length ? d.keys.map((k) => `{${k}:.2f}`).join(" / ") : "{value:.2f}";
  }
  const tableName = (l) => (numbers[l] || {}).num || l;
  // Other tables that show a shared label and do not rename it themselves.
  const labelOthers = (l) => ((st.map_usage || {})[l.field] || []).filter((t) => !t.own.includes(l.value)).map((t) => tableName(t.label));
  function selectedCell() {
    if (sel.kind !== "cell" || !st.model) return null;
    const item = st.model.body.find((b) => b.row === sel.row);
    const cell = item && item.cells.find((c) => c.id === sel.id);
    return cell ? { item, cell } : null;
  }
  function hiddenCount() {
    let n = 0;
    for (const c of spec.columns) for (const leaf of (c.columns || [c])) if (leaf.hidden) n += 1;
    return n;
  }
  // Client copy of the server's set op, for live previews only.
  function setPath(target, path, value) {
    let parts = path.split(".");
    if (parts[0] === "columns" && parts.length > 1) {
      let list = target.columns, at = null, i = 1;
      while (i < parts.length && Array.isArray(list) && list.some((c) => c.id === parts[i])) {
        at = list.find((c) => c.id === parts[i]); list = at.columns; i += 1;
      }
      if (!at) return;
      target = at; parts = parts.slice(i);
    }
    for (const part of parts.slice(0, -1)) {
      if (typeof target[part] !== "object" || target[part] === null) target[part] = {};
      target = target[part];
    }
    const last = parts[parts.length - 1];
    if (value === null) delete target[last]; else target[last] = value;
  }

  // ---------------------------------------------------------------- loading
  async function loadNumbers() {
    const r = await api("/__catalog");
    if (!r.ok) return;
    numbers = {};
    for (const item of r.data.objects) {
      if (item.kind !== "table") continue;
      const key = item.id.slice(4);
      const requestedDoc = App.returnUrl && App.returnUrl.split(/[?#]/)[0] === `/${item.doc}.html`;
      if (!numbers[key] || requestedDoc) numbers[key] = { doc: item.doc, num: (item.caption.match(/^(\S+\s+\S+?)\.?(?:\s|$)/) || [])[1] || "Table" };
    }
    renderTop();
  }
  async function loadList() {
    const r = await api("/__tables");
    if (r.ok) { list = r.data; listLoaded = true; }
    renderList();
  }
  async function loadTable(quiet) {
    if (!label) return;
    const requested = label;
    const version = stateVersion, edits = editVersion;
    const r = await api(`/__table?label=${encodeURIComponent(label)}${showHidden ? "&hidden=1" : ""}`);
    if (label !== requested || version !== stateVersion || edits !== editVersion || pending.size || busy) return;
    if (!r.ok) {
      clearTable(r.data.error || "Cannot load this table.");
      setSave("Table unavailable", true);
      return;
    }
    accept(r.data);
    if (!quiet) setSave(`Saved · ${st.file}`);
    return true;
  }
  async function loadComments() {
    if (!label || !st) return;
    const requested = label;
    const r = await App.review.load("table", label);
    if (label !== requested || !st) return;
    if (r.ok) comments = r.data;
    renderTable();
    renderTabs();
    if (tab === "comments") renderPanel();
  }
  // Take a new server state and redraw (keeping a text field that has focus).
  function accept(data) {
    st = data;
    spec = data.spec;
    stateVersion += 1;
    if (!stillThere()) sel = { kind: "table" };
    renderAll();
  }
  function stillThere() {
    if (sel.kind === "table" || !spec || !st.model) return true;
    if (sel.kind === "block") {  // its label may have a new text
      const item = st.model.body.find((b) => b.label && b.label.field === sel.label.field && b.label.value === sel.label.value);
      if (item) sel = { kind: "block", label: item.label };
      return !!item;
    }
    if (sel.kind === "group") return !!colAt(sel.path);
    return !!colAt(leafPath(sel.id));
  }

  // ---------------------------------------------------------------- saving
  // `ops` may be a function: an edit that rewrites a whole map or list reads
  // the state left by the edits queued before it.
  function save(ops, opts) {
    opts = opts || {};
    queue = queue.then(async () => {
      if (blocked) return;
      busy += 1;
      setSave("Saving…");
      const list = typeof ops === "function" ? ops() : ops;
      const body = opts.shared
        ? { label, base_sha256: st.shared.sha256, ops: list, hidden: showHidden }
        : { label, base_sha256: st.sha256, ops: list, hidden: showHidden };
      let r;
      try {
        r = await api(opts.shared ? "/__table/shared" : "/__table", body);
      } finally {
        busy -= 1;
      }
      if (r.ok) {
        clearFieldErrors();
        if (!$("#te-banner").classList.contains("err")) banner(null);
        accept(r.data);
        setSave(`Saved · ${opts.shared ? st.shared.file : st.file}`);
        loadList();
      } else if (r.status === 409) {
        blocked = true;
        setSave("Not saved: changed elsewhere", true);
        banner("This table changed on disk since it was loaded (another edit or an agent). Your last edits are not saved.", "err",
          [h("button", { type: "button", class: "pv-btn sm", text: "Reload (lose my edits)", onclick: reloadAfterConflict })]);
      } else if (r.status === 422) {
        setSave("Not saved: fix the errors", true);
        showIssues(r.data.issues || [], r.data.error);
        renderTable();
      } else {
        setSave(`Error: ${r.data.error || r.status}`, true);
        banner(r.data.error || `HTTP ${r.status}`, "err");
      }
    }).catch((error) => {
      setSave("Not saved: network or server error", true);
      banner(`Could not save this edit: ${error.message}. Edit the field again to retry.`, "err");
    });
    return queue;
  }
  async function reloadAfterConflict() {
    pending.clear();
    blocked = false;
    banner(null);
    await loadTable();
  }
  function setPending(path, value) {
    editVersion += 1;
    pending.set(path, value);
    setSave("Editing…");
    clearTimeout(saveTimer);
    saveTimer = setTimeout(flush, SAVE_DELAY);
    clearTimeout(previewTimer);
    previewTimer = setTimeout(livePreview, PREVIEW_DELAY);
  }
  function flush() {
    clearTimeout(saveTimer);
    if (!pending.size || blocked) return queue;
    const ops = [], shared = [];
    for (const [path, value] of pending) {
      if (path.startsWith("shared:")) shared.push({ op: "set", path: path.slice(7), value });
      else ops.push({ op: "set", path, value });
    }
    pending.clear();
    if (ops.length) save(ops);
    if (shared.length) save(shared, { shared: true });
    return queue;
  }
  async function livePreview() {
    if (!pending.size || !st) return;
    const draft = clone(spec);
    const version = stateVersion, edits = editVersion, requested = label;
    for (const [path, value] of pending) {
      if (path.startsWith("shared:formats.")) {  // preview a shared format as a local one
        draft.formats = draft.formats || {};
        draft.formats[path.slice(15)] = value;
      } else if (!path.startsWith("shared:")) setPath(draft, path, value);
    }
    const r = await api("/__table/preview", { label, spec: draft, hidden: showHidden });
    if (r.ok && r.data.model && pending.size && label === requested
        && version === stateVersion && edits === editVersion) {
      st = { ...st, model: r.data.model };
      renderTable();
      P.updateSample();
    }
  }

  // ---------------------------------------------------------------- issues
  function clearFieldErrors() {
    $$(".te-fielderr").forEach((e) => e.remove());
    $$(".te-in.bad").forEach((e) => e.classList.remove("bad"));
  }
  function showIssues(issues, message) {
    clearFieldErrors();
    const rest = [];
    for (const it of issues) {
      const field = it.id && $$("[data-ids]").find((el) => el.dataset.ids.split(" ").includes(it.id));
      if (field) {
        field.classList.add("bad");
        const row = field.closest(".te-row") || field.parentElement;
        row.append(h("div", { class: "te-fielderr", text: it.message }));
      } else rest.push(it);
    }
    if (rest.length || !issues.length) {
      banner(h("div", null, h("b", { text: message || "Not saved" }),
        rest.length ? h("ul", null, rest.map((it) => h("li", { text: it.message }))) : null), "err");
    }
  }

  // ---------------------------------------------------------------- top bar and list
  function renderTop() {
    const n = numbers[label];
    $("#te-num").textContent = n ? n.num : "Table";
    $("#te-label").textContent = label;
    document.title = `${n ? n.num + " · " : ""}${label} · Table editor`;
    const doc = n ? n.doc : Object.keys(DOCS)[0];
    $("#te-back").href = App.returnUrl || `/${doc}.html`;
    $("#te-open").href = `/${doc}.html#tbl:${label}`;
    $("#te-open").hidden = !n || !st;
    $("#te-add").disabled = !st;
    $("#te-kindb").hidden = !st;
    const issues = st ? st.issues : [];
    const errors = issues.filter((i) => i.level === "error").length, warns = issues.length - errors;
    const pill = $("#te-check"), dot = "te-dot" + (!st ? " off" : errors ? " err" : warns ? " warn" : "");
    pill.disabled = !st;
    $(".te-dot", pill).className = dot;
    if (!st) pill.setAttribute("aria-label", "Check: unavailable"); else pill.removeAttribute("aria-label");
    $("span:last-child", pill).textContent = !st ? "Check: —" : errors ? `Check: ${errors} error${errors > 1 ? "s" : ""}`
      : warns ? `Check: ${warns} warning${warns > 1 ? "s" : ""}` : "Check: ok";
    $("#te-idot").className = dot;
    const undo = $("#te-undo");
    undo.disabled = !(st && st.undo);
    undo.title = st && st.undo ? `Undo: ${st.undo} (⌘Z)` : "Nothing to undo";
    const hidden = st ? hiddenCount() : 0;
    const hb = $("#te-hidden");
    hb.textContent = `${showHidden ? "Hide" : "Show"} hidden (${hidden})`;
    hb.setAttribute("aria-pressed", String(showHidden));
    hb.disabled = !st || (!hidden && !showHidden);
  }
  function renderList() {
    const q = $("#te-filter").value.trim().toLowerCase();
    const groups = new Map([...Object.keys(DOCS).map((d) => [d, []]), ["", []]]);
    for (const t of list) {
      const n = numbers[t.label];
      const text = `${n ? n.num : ""} ${t.label} ${t.title}`.toLowerCase();
      if (q && !text.includes(q)) continue;
      groups.get(n && groups.has(n.doc) ? n.doc : "").push(t);
    }
    const out = [];
    for (const [doc, items] of groups) {
      if (!items.length) continue;
      items.sort((a, b) => ((numbers[a.label] || {}).num || "~" + a.label)
        .localeCompare((numbers[b.label] || {}).num || "~" + b.label, undefined, { numeric: true }));
      out.push(h("div", { class: "te-head", style: "padding-top:4px", text: doc ? DOCS[doc] : "Not in a document" }));
      out.push(h("ul", { class: "te-tl" }, items.map((t) => {
        const n = numbers[t.label];
        const status = { ok: "", stale: "stale", error: "error" }[t.status];
        return h("li", {
          class: t.label === label ? "on" : "", tabindex: "0", role: "link", title: `tables/specs/${t.label}.json (${t.status})`,
          onclick: () => openTable(t), onkeydown: (e) => { if (e.key === "Enter") openTable(t); },
        }, h("span", { class: "te-n", text: n ? n.num : "—" }), h("span", { class: "te-lbl", text: t.label }),
        h("span", { class: "te-end" },
          t.open_comments ? h("span", { class: "te-badge", title: "Open comments", text: String(t.open_comments) }) : null,
          h("span", { class: `te-dot ${status}`, title: t.status === "stale" ? "Stale: run scripts/trazo build --prepare=inputs" : t.status })),
        h("span", { class: "te-t", text: t.title || "" }));
      })));
    }
    if (!out.length) out.push(h("p", { class: "te-muted", style: "padding:0 14px", text: "No tables." }));
    $("#te-list").replaceChildren(...out);
  }
  function openTable(t) {
    setDrawer(false);
    if (t.label !== label) location.hash = t.label;
  }

  // ---------------------------------------------------------------- the table
  function commentMarks() {
    const marks = new Map();
    for (const c of comments.comments) {
      if (c.status !== "open" && c.status !== "review") continue;
      const t = c.target;
      const key = !t ? "" : t.row ? `${t.column}|${stable(t.row)}` : t.column;
      const m = marks.get(key) || { open: 0, review: 0 };
      m[c.status] += 1;
      marks.set(key, m);
    }
    return marks;
  }
  function mark(el, m) {
    if (!m) return;
    if (m.open) { el.classList.add("te-cmt"); el.append(h("span", { class: "te-badge te-pin", title: "Open comments", text: String(m.open) })); }
    else el.classList.add("te-review");
  }
  // A double click edits in place. The first click redraws the table, so the
  // browser sends no dblclick: the second click, on the new cell, says it.
  const secondClick = (e, el) => e.detail === 2 && I.startEdit(el);
  const alignClass = (a) => (a === "right" ? "te-r" : a === "center" ? "te-c" : "");
  function renderTable() {
    const page = $("#te-page");
    if (!st || editing) return;
    const scrollLeft = $(".te-table-scroll", page)?.scrollLeft || 0;
    const focused = page.contains(document.activeElement) ? document.activeElement : null;
    const focusKey = focused && (focused.matches(".te-cap") ? "caption"
      : focused.matches("th, td") ? `${focused.tagName}:${focused.dataset.id}:${focused.dataset.row || ""}:${focused.dataset.block || ""}` : null);
    if (!st.model) {
      page.replaceChildren(h("p", { class: "te-cap", text: spec.title || label }),
        h("div", { class: "te-empty", text: "The table cannot be drawn: see the Check tab." }));
      return;
    }
    const marks = commentMarks();
    const n = numbers[label];
    const cap = h("p", { class: "te-cap" + (sel.kind === "table" ? " sel" : ""), tabindex: "0",
      title: "Table settings", onclick: (e) => { e.stopPropagation(); select({ kind: "table" }); } },
    h("b", { text: `${n ? n.num : "Table"}: ` }));
    cap.insertAdjacentHTML("beforeend", st.model.caption_html);
    const ncols = st.model.header.reduce((s, row) => s + row.filter((c) => c.kind === "leaf").length, 0);
    const thead = h("thead", null, st.model.header.map((row) => h("tr", null, row.map((c) => {
      const isSel = (sel.kind === "column" && sel.id === c.id) || (sel.kind === "group" && sel.id === c.id);
      const th = h("th", {
        colspan: c.colspan > 1 ? c.colspan : null, rowspan: c.rowspan > 1 ? c.rowspan : null, scope: "col",
        class: [c.kind !== "leaf" ? "te-grp" : alignClass(c.align), c.rowspan > 1 ? "te-span2" : "",
          c.hidden ? "te-hidden" : "", isSel ? "te-colsel" : ""].join(" "),
        dataset: { id: c.id, kind: c.kind }, tabindex: "0",
        onclick: (e) => { e.stopPropagation(); if (!secondClick(e, th)) select(c.kind === "leaf" ? { kind: "column", id: c.id } : { kind: "group", id: c.id, path: c.id.split(".")[0] }); },
      });
      th.insertAdjacentHTML("beforeend", c.text_html);
      th.prepend(h("span", { class: "te-grip", draggable: "true", title: "Drag to reorder", "aria-hidden": "true", text: "⋮⋮" }));
      mark(th, marks.get(c.id));
      return th;
    }))));
    const tbody = h("tbody");
    let firstCell = true, blocks = 0;
    for (const item of st.model.body) {
      if (item.block !== undefined) {
        const on = sel.kind === "block" && stable(sel.label) === stable(item.label);
        const td = h("td", { colspan: ncols, class: "te-block" + (on ? " te-cellsel" : ""), tabindex: "0",
          title: item.label ? "Double-click to rename" : "Block row", dataset: { block: String(blocks++) },
          onclick: (e) => { e.stopPropagation(); if (!secondClick(e, td)) select(item.label ? { kind: "block", label: item.label } : { kind: "table" }); } });
        td.innerHTML = item.block;
        tbody.append(h("tr", null, td));
        continue;
      }
      const tr = h("tr");
      for (const c of item.cells) {
        const isCell = sel.kind === "cell" && sel.id === c.id && sel.row === item.row;
        const td = h("td", { rowspan: c.rowspan > 1 ? c.rowspan : null,
          tabindex: isCell || (firstCell && sel.kind !== "cell") ? "0" : "-1",
          class: [alignClass(c.align), c.hidden ? "te-hidden" : "", isCell ? "te-cellsel" : "", c.label ? "te-label" : "te-num",
            sel.kind === "column" && sel.id === c.id ? "te-colsel" : ""].join(" "),
          dataset: { id: c.id, row: String(item.row) }, title: c.label ? "Double-click to rename" : null,
          onclick: (e) => { e.stopPropagation(); if (!secondClick(e, td)) select({ kind: "cell", id: c.id, row: item.row }); } });
        td.innerHTML = c.text_html;
        firstCell = false;
        mark(td, marks.get(`${c.id}|${stable(item.key)}`));
        tr.append(td);
      }
      tbody.append(tr);
    }
    const table = h("table", { class: "te-t", "aria-label": `${label} table` }, thead, tbody);
    // The note reads under the table, as in the preview and Word.
    let note = null;
    if (st.model.note_html) {
      note = h("div", { class: "te-note", title: "Table settings", onclick: (e) => { e.stopPropagation(); select({ kind: "table" }); } });
      note.innerHTML = st.model.note_html;
    }
    page.replaceChildren(cap, h("div", { class: "te-table-scroll" }, table), ...(note ? [note] : []), h("div", { class: "te-hint" },
      h("span", { text: "Numbers come from your data. Double-click a header or a label to rename it." }),
      h("span", null, h("kbd", { text: "Enter" }), " rename"), h("span", null, h("kbd", { text: "Tab" }), " next"),
      h("span", null, "Drag ", h("kbd", { text: "⋮⋮" }), " to reorder"), h("span", null, h("kbd", { text: "C" }), " comment"),
      h("span", null, h("kbd", { text: "Esc" }), " clear")));
    initDrag(table);
    $(".te-table-scroll", page).scrollLeft = scrollLeft;
    if (focusKey) {
      const target = focusKey === "caption" ? cap : $$("th, td", table).find((el) =>
        `${el.tagName}:${el.dataset.id}:${el.dataset.row || ""}:${el.dataset.block || ""}` === focusKey);
      if (target) target.focus({ preventScroll: true });
    }
  }
  $("#te-page").addEventListener("focusin", (e) => {
    if (!e.target.matches("td[data-row]")) return;
    $$("tbody td[data-row]").forEach((td) => { td.tabIndex = td === e.target ? 0 : -1; });
  });
  $("#te-page").addEventListener("click", (e) => { if (e.target.id === "te-page" || e.target.classList.contains("te-hint")) select({ kind: "table" }); });

  // Header drag: a `move` op within the same parent.
  let drag = null;
  const parentOf = (path) => (path.includes(".") ? path.split(".")[0] : "");
  function headerPath(th) { return th.dataset.kind === "leaf" ? leafPath(th.dataset.id) : th.dataset.id.split(".")[0]; }
  function initDrag(table) {
    for (const th of $$("thead th", table)) {
      const grip = $(".te-grip", th);
      grip.addEventListener("dragstart", (e) => {
        drag = headerPath(th);
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", drag);
      });
      grip.addEventListener("dragend", () => { drag = null; $$(".te-drop").forEach((x) => x.classList.remove("te-drop")); });
      th.addEventListener("dragover", (e) => {
        if (!drag) return;
        const target = headerPath(th);
        if (target === drag || parentOf(target) !== parentOf(drag)) return;
        e.preventDefault();
        th.classList.add("te-drop");
      });
      th.addEventListener("dragleave", () => th.classList.remove("te-drop"));
      th.addEventListener("drop", (e) => {
        e.preventDefault();
        th.classList.remove("te-drop");
        const target = headerPath(th);
        if (!drag || target === drag) return;
        save([{ op: "move", column: drag, before: target.split(".").pop() }]);
        drag = null;
      });
    }
  }

  // ---------------------------------------------------------------- selection
  function select(next) {
    flush();
    sel = next;
    if (tab !== "comments") tab = "props";
    composeFor = targetOf(sel);
    renderTable();
    renderTabs();
    renderPanel();
    I.renderBar();
  }
  function targetOf(s) {
    if (s.kind === "column") return { column: s.id };
    if (s.kind === "group") return { column: s.id };
    if (s.kind === "cell") {
      const item = st.model.body.find((b) => b.row === s.row);
      return { column: s.id, row: item ? item.key : {} };
    }
    return null;
  }
  const targetText = (t) => (!t ? "the whole table" : t.column + (t.row ? " · " + Object.values(t.row).join(" · ") : ""));

  // ---------------------------------------------------------------- right panel
  function renderTabs() {
    for (const b of $$(".te-tabs button")) {
      b.classList.toggle("on", b.dataset.tab === tab);
      b.setAttribute("aria-selected", String(b.dataset.tab === tab));
    }
    const open = comments.comments.filter((c) => c.status === "open").length;
    const review = comments.comments.filter((c) => c.status === "review").length;
    const badge = $("#te-cbadge");
    badge.hidden = !(open + review);
    badge.textContent = String(open + review);
    badge.title = `${open} open, ${review} to check`;
  }
  $$(".te-tabs button").forEach((b) => b.addEventListener("click", () => { flush(); tab = b.dataset.tab; renderTabs(); renderPanel(); }));
  $("#te-check").addEventListener("click", () => { tab = "issues"; renderTabs(); renderPanel(); });

  function renderAll() {
    renderTop();
    renderList();
    renderTable();
    renderTabs();
    I.renderBar();
    const focused = document.activeElement;
    if (focused && $("#te-panel").contains(focused) && /^(INPUT|TEXTAREA)$/.test(focused.tagName)
        && focused.type !== "checkbox" && focused.type !== "range") return;  // keep the caret
    renderPanel();
  }
  function renderPanel() {
    const panel = $("#te-panel");
    if (!st) { panel.replaceChildren(); return; }
    const scroll = panel.scrollTop;
    const active = document.activeElement;
    const caret = active && panel.contains(active) && active.dataset.draft
      ? { key: active.dataset.draft, at: active.selectionStart } : null;
    if (tab === "data" && !advanced) tab = "props";
    const body = tab === "comments" ? Cm.commentsPanel() : tab === "issues" ? K.issuesPanel()
      : tab === "data" ? K.dataPanel() : P.propsPanel();
    panel.replaceChildren(...body);
    const field = caret && $(`[data-draft="${CSS.escape(caret.key)}"]`, panel);
    if (field) { field.focus({ preventScroll: true }); field.setSelectionRange(caret.at, caret.at); }
    panel.scrollTop = scroll;
    P.updateSample();
  }

  // ---------------------------------------------------------------- toolbar
  const menu = $("#te-add-menu");
  function closeMenu() { menu.hidden = true; $("#te-add").setAttribute("aria-expanded", "false"); }
  $("#te-add").addEventListener("click", (e) => {
    e.stopPropagation();
    if (!menu.hidden) { closeMenu(); return; }
    if (!st) return;
    const keys = (spec.rows || {}).keys || [];
    const valueField = fieldNames().find((f) => /float|composite|int/.test(fieldType(f)) && !keys.includes(f)) || fieldNames()[0];
    menu.replaceChildren(h("h6", { text: "Field from the data" }),
      ...fieldNames().map((f) => h("button", { type: "button", role: "menuitem", onclick: () => addColumn(f) },
        h("span", { text: f }), h("span", { class: "te-muted te-small", text: fieldType(f) }))),
      h("h6", { text: "Group" }),
      h("button", { type: "button", role: "menuitem", onclick: () => addGroup(valueField) }, h("span", { text: "New group (spanning header)" })),
      h("h6", { text: "Pivot on a text field" }),
      ...fieldNames().filter((f) => fieldType(f) === "string" && !keys.includes(f)).map((f) =>
        h("button", { type: "button", role: "menuitem", onclick: () => addPivot(f, valueField) }, h("span", { text: `One column group per ${f}` }))));
    menu.hidden = false;
    $("#te-add").setAttribute("aria-expanded", "true");
    const first = $("button", menu);
    if (first) first.focus();
  });
  document.addEventListener("click", (e) => { if (!menu.hidden && !menu.contains(e.target)) closeMenu(); });
  function leafFor(field, id) {
    const item = { id, field, header: field };
    const ops = [];
    const type = fieldType(field);
    if (/composite|float/.test(type)) {
      const all = formats();
      const fresh = uniqueId(id, Object.keys(all));
      ops.push({ op: "set", path: `formats.${fresh}`, value: defaultTemplate(field) });
      item.format = fresh;
    }
    if (/int|float|composite/.test(type) && !type.includes("string")) item.align = "right";
    return { item, ops };
  }
  function insertBefore() {  // after the selected top-level column, else at the end
    if (sel.kind !== "column" && sel.kind !== "group") return null;
    const top = (sel.kind === "group" ? sel.path : leafPath(sel.id)).split(".")[0];
    const i = spec.columns.findIndex((c) => c.id === top);
    return i >= 0 && spec.columns[i + 1] ? spec.columns[i + 1].id : null;
  }
  function addColumn(field) {
    closeMenu();
    tab = "props";
    const id = uniqueId(field, spec.columns.map((c) => c.id));
    const { item, ops } = leafFor(field, id);
    save([...ops, { op: "add", into: "columns", item, before: insertBefore() }]);
    sel = { kind: "column", id };
  }
  function addGroup(field) {
    closeMenu();
    tab = "props";
    const id = uniqueId("group", spec.columns.map((c) => c.id));
    const { item, ops } = leafFor(field, uniqueId(field, []));
    save([...ops, { op: "add", into: "columns", item: { id, header: "Group", columns: [item] }, before: insertBefore() }]);
    sel = { kind: "group", id, path: id };
  }
  function addPivot(pivot, field) {
    closeMenu();
    tab = "props";
    const id = uniqueId(pivot, spec.columns.map((c) => c.id));
    const { item, ops } = leafFor(field, "value");
    item.header = "Value";
    save([...ops, { op: "add", into: "columns", item: { id, pivot, columns: [item] }, before: insertBefore() }]);
    sel = { kind: "group", id, path: id };
  }
  $("#te-hidden").addEventListener("click", async () => { showHidden = !showHidden; await loadTable(true); });
  for (const [id, fit] of [["#te-w-page", false], ["#te-w-fit", true]]) {
    $(id).addEventListener("click", () => {
      $("#te-work").classList.toggle("fit", fit);
      $("#te-w-page").classList.toggle("on", !fit); $("#te-w-page").setAttribute("aria-pressed", String(!fit));
      $("#te-w-fit").classList.toggle("on", fit); $("#te-w-fit").setAttribute("aria-pressed", String(fit));
    });
  }
  $("#te-filter").addEventListener("input", renderList);
  function setDrawer(open) {
    $("#te-main").classList.toggle("drawer", open);
    $("#te-drawer").setAttribute("aria-expanded", String(open));
    if (open) $("#te-filter").focus();
    else if ($(".te-left").contains(document.activeElement)) $("#te-drawer").focus();
  }
  $("#te-drawer").addEventListener("click", () => setDrawer(!$("#te-main").classList.contains("drawer")));
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".te-left, #te-drawer") && $("#te-main").classList.contains("drawer")) setDrawer(false);
  });

  // ---------------------------------------------------------------- undo and keys
  async function undo() {
    if (!st || !st.undo) { flash("Nothing to undo"); return; }
    clearTimeout(saveTimer); clearTimeout(previewTimer);
    pending.clear();
    await queue;
    const r = await api("/__table/undo", { label, hidden: showHidden });
    if (r.ok) {
      blocked = false;
      banner(null);
      accept(r.data);
      flash(r.data.undone.replace(/^undone: /, "Undone: ").replace(/; next undo: .*$/, ""));
      setSave(`Saved · ${st.file}`);
      loadList();
    } else {
      flash(r.data.error || "Undo failed");
      loadTable(true);
    }
  }
  $("#te-undo").addEventListener("click", undo);
  document.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable;
    if ((e.metaKey || e.ctrlKey) && !e.shiftKey && e.key.toLowerCase() === "z" && !typing) {
      e.preventDefault();
      undo();
    } else if (e.key === "Escape") {
      if (!menu.hidden) { closeMenu(); return; }
      if ($("#te-main").classList.contains("drawer")) { setDrawer(false); return; }
      if (typing) e.target.blur();
      select({ kind: "table" });
    } else if ((e.key === "c" || e.key === "C") && !typing && !e.metaKey && !e.ctrlKey && !e.altKey && st) {
      e.preventDefault();
      Cm.startComment();
    } else if (["Enter", "F2"].includes(e.key) && !typing && e.target.matches("thead th, td[data-row], td.te-block")
        && I.target(e.target)) {
      e.preventDefault();  // a header or label: edit it in place
      if (e.key === "F2" || e.target.matches(".te-colsel, .te-cellsel")) I.startEdit(e.target);
      else e.target.click();
    } else if (["Enter", " "].includes(e.key) && !typing && e.target.matches("thead th, td[data-row], td.te-block, .te-cap")) {
      e.preventDefault();
      e.target.click();
    } else if (!typing && e.target.matches("td[data-row]") &&
        ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(e.key)) {
      e.preventDefault();
      const cell = e.target;
      const vertical = ["ArrowUp", "ArrowDown"].includes(e.key);
      const cells = vertical ? $$("tbody td[data-row]").filter((td) => td.dataset.id === cell.dataset.id)
        : $$("td[data-row]", cell.parentElement);
      const index = cells.indexOf(cell);
      const next = e.key === "Home" ? 0 : e.key === "End" ? cells.length - 1
        : index + (["ArrowLeft", "ArrowUp"].includes(e.key) ? -1 : 1);
      if (cells[next]) {
        cells[next].focus();
        select({ kind: "cell", id: cells[next].dataset.id, row: Number(cells[next].dataset.row) });
      }
    }
  });
  // Closing the tab: send pending text edits with keepalive so they are not lost.
  window.addEventListener("pagehide", () => {
    if (!pending.size || blocked || !st) return;
    clearTimeout(saveTimer);
    const ops = [], shared = [];
    for (const [path, value] of pending) {
      if (path.startsWith("shared:")) shared.push({ op: "set", path: path.slice(7), value });
      else ops.push({ op: "set", path, value });
    }
    pending.clear();
    const send = (url, body) => fetch(url, { method: "POST", keepalive: true,
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(() => null);
    if (ops.length) send("/__table", { label, base_sha256: st.sha256, ops });
    if (shared.length) send("/__table/shared", { label, base_sha256: st.shared.sha256, ops: shared });
  });

  // ---------------------------------------------------------------- polling
  async function poll(workspace, offline) {
    const version = stateVersion;
    const r = !offline && workspace?.tables ? { ok: true, data: workspace.tables } : null;
    if (!r || !r.ok || version !== stateVersion) return;
    const now = r.data;
    if (workspace.catalog !== lastCatalog) {
      lastCatalog = workspace.catalog;
      loadNumbers().catch(() => null);
    }
    const prev = lastStatus;
    lastStatus = now;
    if (!st) {  // nothing loaded (the server did not answer, or no such table yet): retry
      await loadList().catch(() => null);
      await route().catch(() => null);
      return;
    }
    const tablesChanged = prev && Object.keys(now.tables).join() !== Object.keys(prev.tables).join();
    if (tablesChanged || !listLoaded) loadList().catch(() => null);
    const changed = now.tables[label] !== st.sha256 || now.shared !== st.shared.sha256
      || (now.data[spec.data] || null) !== st.data.sha256;
    if (changed && !busy && !pending.size && !blocked) {
      const applied = await loadTable(true);
      if (applied) {
        flash("Updated by another change");
        if (st) setSave(`Saved · ${st.file}`);
      }
      loadList();
    }
    if (prev && now.comments !== prev.comments) { loadComments(); loadList(); }
  }

  // ---------------------------------------------------------------- modules
  // The other editor-*.js files read and change this editor's state through E.
  const E = {
    h, $, $$, clone, show, stable, plain, App,
    get st() { return st; }, get spec() { return spec; }, get label() { return label; }, get numbers() { return numbers; },
    get sel() { return sel; }, set sel(v) { sel = v; }, get tab() { return tab; }, set tab(v) { tab = v; },
    get comments() { return comments; }, set comments(v) { comments = v; },
    get composeFor() { return composeFor; }, set composeFor(v) { composeFor = v; },
    get advanced() { return advanced; }, get editing() { return editing; }, set editing(v) { editing = v; },
    save, setPending, flush, flash, select, renderTable, renderTabs, renderPanel, loadList, loadComments,
    leafPath, colAt, isLeaf, leafIds, formats, isLocal, uniqueId, targetOf, targetText, selectedCell,
    fieldNames, fieldType, defaultTemplate, tableName, labelOthers,
    startComment: (text) => Cm.startComment(text), ask: (kind) => I.ask(kind), rename: (...a) => I.rename(...a),
    markRow: (t, m) => I.markRow(t, m), alignCtl: (c, b) => I.alignCtl(c, b), boldCtl: (at) => I.boldCtl(at),
  };
  const K = T.controls(E), P = T.panel(E, K), Cm = T.comments(E), I = T.inline(E, K);
  function setAdvanced(on) {
    advanced = on;
    App.savePrefs({ tableAdvanced: on });
    document.documentElement.classList.toggle("te-advanced", on);
    $("#te-advanced").setAttribute("aria-checked", String(on));
    renderTop();
    renderPanel();
  }
  $("#te-advanced").addEventListener("click", () => setAdvanced(!advanced));
  setAdvanced(advanced);

  // ---------------------------------------------------------------- start
  function clearTable(message) {
    st = null;
    spec = null;
    comments = { comments: [], undo: null };
    composeFor = null;
    closeMenu();
    renderAll();
    $("#te-panel").replaceChildren();
    $("#te-page").replaceChildren(h("div", { class: "te-empty", text: message }));
  }
  async function route() {
    const next = decodeURIComponent(location.hash.slice(1));
    await flush();
    if (next === label && st) return;
    label = next;
    sel = { kind: "table" };
    composeFor = null;
    Cm.reset();
    blocked = false;
    banner(null);
    pending.clear();
    clearTable("Loading…");
    setSave("Loading…");
    if (!label) {
      const first = list.find((t) => t.kind === "spec");
      if (first) { App.replaceHash(first.label); return; }
      $("#te-page").replaceChildren(h("div", { class: "te-empty", text: "No table specs in this project (tables/specs/<label>.json)." }));
      setSave("No table selected");
      renderTop();
      return;
    }
    await loadTable();
    await loadComments();
    // The first table arrives with the page itself; only a switch fades.
    if (routed) App.reveal($("#te-work"));
    routed = true;
  }
  let routed = false;
  window.addEventListener("hashchange", route);
  // A failed first load must not stop polling: poll() retries it.
  (async () => {
    await Promise.all([loadNumbers(), loadList().catch(() => null)]);
    renderList();
    await route().catch(() => null);
    App.watch(poll);
  })();
})();
