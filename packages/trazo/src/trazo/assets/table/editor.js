// Table editor (/tables#<label>): edits tables/specs/<label>.json through the
// preview server (trazo.table_editor). Every change is a list of
// `trazo table patch` operations sent with the spec's sha256; the server
// validates, saves, records history and renders the Markdown. The table is
// drawn from the server's render model, so each cell knows its leaf id and
// row. Numbers are never edited here, only how they are shown.
(function () {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const DOCS = window.PREVIEW_TITLES || { manuscript: "Manuscript", supplementary: "Supplementary" };
  const App = window.Trazo;
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
  let cfilter = "active";   // comments shown: Active (open, to check) or Resolved
  let showTrash = false;    // the deleted comments, under Resolved
  let openDone = null;      // the resolved comment shown in full (others are one line)
  let cedit = null;         // inline editor of a card: {id, mode: "edit" | "reply"}
  let cdrafts = {};         // unsaved text by "<id>:<mode>" ("new" for the composer)
  let composeFor = null;    // target of a new comment
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
    if (sel.kind === "group") return !!colAt(sel.path);
    return !!colAt(leafPath(sel.id));
  }

  // ---------------------------------------------------------------- saving
  function save(ops, opts) {
    opts = opts || {};
    queue = queue.then(async () => {
      if (blocked) return;
      busy += 1;
      setSave("Saving…");
      const body = opts.shared
        ? { label, base_sha256: st.shared.sha256, ops, hidden: showHidden }
        : { label, base_sha256: st.sha256, ops, hidden: showHidden };
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
      updateSample();
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
  const alignClass = (a) => (a === "right" ? "te-r" : a === "center" ? "te-c" : "");
  function renderTable() {
    const page = $("#te-page");
    if (!st) return;
    const scrollLeft = $(".te-table-scroll", page)?.scrollLeft || 0;
    const focused = page.contains(document.activeElement) ? document.activeElement : null;
    const focusKey = focused && (focused.matches(".te-cap") ? "caption"
      : focused.matches("th, td") ? `${focused.tagName}:${focused.dataset.id}:${focused.dataset.row || ""}` : null);
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
        onclick: (e) => { e.stopPropagation(); select(c.kind === "leaf" ? { kind: "column", id: c.id } : { kind: "group", id: c.id, path: c.id.split(".")[0] }); },
      });
      th.insertAdjacentHTML("beforeend", c.text_html);
      th.prepend(h("span", { class: "te-grip", draggable: "true", title: "Drag to reorder", "aria-hidden": "true", text: "⋮⋮" }));
      mark(th, marks.get(c.id));
      return th;
    }))));
    const tbody = h("tbody");
    let firstCell = true;
    for (const item of st.model.body) {
      if (item.block !== undefined) {
        const td = h("td", { colspan: ncols, class: "te-block", title: "Block row (table settings)",
          onclick: (e) => { e.stopPropagation(); select({ kind: "table" }); } });
        td.innerHTML = item.block;
        tbody.append(h("tr", null, td));
        continue;
      }
      const tr = h("tr");
      for (const c of item.cells) {
        const isCell = sel.kind === "cell" && sel.id === c.id && sel.row === item.row;
        const td = h("td", { rowspan: c.rowspan > 1 ? c.rowspan : null,
          tabindex: isCell || (firstCell && sel.kind !== "cell") ? "0" : "-1",
          class: [alignClass(c.align), c.hidden ? "te-hidden" : "", isCell ? "te-cellsel" : "",
            sel.kind === "column" && sel.id === c.id ? "te-colsel" : ""].join(" "),
          dataset: { id: c.id, row: String(item.row) },
          onclick: (e) => { e.stopPropagation(); select({ kind: "cell", id: c.id, row: item.row }); } });
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
    page.replaceChildren(cap, h("div", { class: "te-table-scroll" }, table), note, h("div", { class: "te-hint" },
      h("span", { text: "Click a header for the column, a cell for that value, the caption for the table." }),
      h("span", { text: "Use arrow keys between cells; Enter selects." }),
      h("span", null, "Drag ", h("kbd", { text: "⋮⋮" }), " to reorder"), h("span", null, h("kbd", { text: "C" }), " comment"),
      h("span", null, h("kbd", { text: "Esc" }), " clear")));
    initDrag(table);
    $(".te-table-scroll", page).scrollLeft = scrollLeft;
    if (focusKey) {
      const target = focusKey === "caption" ? cap : $$("th, td", table).find((el) =>
        `${el.tagName}:${el.dataset.id}:${el.dataset.row || ""}` === focusKey);
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
    const body = tab === "comments" ? commentsPanel() : tab === "issues" ? issuesPanel()
      : tab === "data" ? dataPanel() : propsPanel();
    panel.replaceChildren(...body);
    const field = caret && $(`[data-draft="${CSS.escape(caret.key)}"]`, panel);
    if (field) { field.focus({ preventScroll: true }); field.setSelectionRange(caret.at, caret.at); }
    panel.scrollTop = scroll;
    updateSample();
  }

  // Controls ------------------------------------------------------------
  function row(labelText, control, top) {
    const id = "te-f-" + Math.random().toString(36).slice(2, 8);
    const target = control.matches && control.matches("input, select, textarea") ? control : null;
    if (target) target.id = id;
    return h("div", { class: "te-row" + (top ? " top" : "") },
      target ? h("label", { for: id, text: labelText }) : h("span", { class: "te-lab", text: labelText }), control);
  }
  // A text field saved 600 ms after typing stops (or on blur); `path` is a
  // set op path ("shared:" for _shared.json); empty text sends `empty`.
  function textField(value, path, opts) {
    opts = opts || {};
    const el = h(opts.multiline ? "textarea" : "input", { class: "te-in" + (opts.mono ? " te-mono" : ""),
      "aria-label": opts.label, placeholder: opts.placeholder || null, dataset: { ids: (opts.ids || []).join(" ") }, spellcheck: opts.mono ? "false" : null });
    el.value = value || "";
    el.addEventListener("input", () => setPending(path, el.value === "" && "empty" in opts ? opts.empty : el.value));
    el.addEventListener("blur", () => flush());
    return el;
  }
  function switchCtl(on, text, onChange, name) {
    return h("button", { type: "button", class: "te-switch", role: "switch", "aria-checked": String(!!on), "aria-label": name || text,
      onclick: () => onChange(!on) }, h("i"), text);
  }
  function selectCtl(options, value, onChange, name) {
    const el = h("select", { class: "te-in", "aria-label": name });
    for (const [v, t] of options) el.append(h("option", { value: v, text: t, selected: v === value ? "" : null }));
    el.addEventListener("change", () => onChange(el.value));
    return el;
  }
  // A list reordered by drag (and the keyboard: Alt+↑/↓), e.g. row order.
  function orderList(items, onOrder, extra) {
    const ul = h("ul", { class: "te-order" });
    let from = null;
    items.forEach((it, i) => {
      const li = h("li", { draggable: "true", tabindex: "0", class: it.off ? "off" : "", title: "Drag (or Alt+↑/↓) to reorder",
        ondragstart: (e) => { from = i; e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", String(i)); },
        ondragover: (e) => { if (from !== null && from !== i) { e.preventDefault(); li.classList.add("drop"); } },
        ondragleave: () => li.classList.remove("drop"),
        ondrop: (e) => { e.preventDefault(); li.classList.remove("drop"); if (from !== null) move(from, i); from = null; },
        onkeydown: (e) => {
          if (!e.altKey || !["ArrowUp", "ArrowDown"].includes(e.key)) return;
          e.preventDefault();
          const to = e.key === "ArrowUp" ? i - 1 : i + 1;
          if (to >= 0 && to < items.length) move(i, to);
        } },
      h("span", { class: "te-h", "aria-hidden": "true", text: "⋮⋮" }), extra ? extra(it) : null, h("span", { text: it.text }),
      it.code !== undefined ? h("code", { text: it.code }) : null);
      ul.append(li);
    });
    function move(a, b) {
      const next = items.slice();
      const [it] = next.splice(a, 1);
      next.splice(b, 0, it);
      onOrder(next);
    }
    return ul;
  }

  // Properties ----------------------------------------------------------
  function ctx(kind, what, where) {
    return h("div", { class: "te-ctx" }, h("div", { class: "te-kind", text: kind }),
      h("div", { class: "te-what", text: what }), where ? h("div", { class: "te-where" }, where) : null);
  }
  function propsPanel() {
    if (sel.kind === "cell") return cellProps();
    if (sel.kind === "column") return columnProps();
    if (sel.kind === "group") return groupProps();
    return tableProps();
  }
  const fieldNames = () => Object.keys(st.data.fields);
  const fieldType = (f) => { const d = st.data.fields[f]; return d ? d.types.filter((t) => t !== "null").join("|") : "?"; };
  const mapped = (field, v) => {
    const m = { ...((st.shared.maps || {})[field] || {}), ...((spec.maps || {})[field] || {}) };
    return m[String(v)] !== undefined ? m[String(v)] : String(v);
  };
  function distinct(field) { return (st.data.fields[field] || { values: [] }).values; }

  function tableProps() {
    const rowsSpec = spec.rows || { keys: [] };
    const out = [ctx("Table", label, "Nothing selected: these settings apply to the whole table.")];
    const values = Object.entries(st.data.values || {});
    out.push(h("div", { class: "te-sec" }, h("h4", { text: "Caption" }),
      row("Title", textField(spec.title, "title", { label: "Title", ids: ["title", "spec"] })),
      row("Legend", textField(spec.legend, "legend", { label: "Legend", multiline: true, empty: null, ids: ["legend"] }), true),
      row("Note", textField(spec.note, "note", { label: "Note", multiline: true, empty: null, ids: ["note"],
        placeholder: "Below the table: abbreviations, symbols, sources. A blank line starts a paragraph." }), true),
      values.length ? row("Values", h("div", { class: "te-chips" }, values.map(([k, v]) =>
        h("span", { class: "te-chip", title: `Write {${k}} in the title, legend or note`, text: `{${k}} = ${typeof v === "object" ? show(v) : v}` })))) : null));
    // Rows
    const keys = rowsSpec.keys || [];
    let dragKey = null;
    const keyChips = keys.map((k, i) => h("span", { class: "te-kchip", draggable: "true", title: "Drag to reorder",
      ondragstart: () => { dragKey = i; }, ondragover: (e) => { if (dragKey !== null) e.preventDefault(); },
      ondrop: (e) => { e.preventDefault(); if (dragKey === null || dragKey === i) return;
        const next = keys.slice(); const [x] = next.splice(dragKey, 1); next.splice(i, 0, x); dragKey = null;
        save([{ op: "set", path: "rows.keys", value: next }]); } },
    k, h("span", { class: "te-muted", text: fieldType(k) }),
    keys.length > 1 ? h("button", { type: "button", "aria-label": `Remove row key ${k}`, text: "✕",
      onclick: () => save([{ op: "set", path: "rows.keys", value: keys.filter((x) => x !== k) },
        ...(rowsSpec.block === k ? [{ op: "set", path: "rows.block", value: null }] : [])]) }) : null));
    const candidates = fieldNames().filter((f) => !keys.includes(f) && !fieldType(f).includes("composite"));
    if (candidates.length) {
      keyChips.push(selectCtl([["", "+ key"], ...candidates.map((f) => [f, f])], "", (v) => {
        if (v) save([{ op: "set", path: "rows.keys", value: [...keys, v] }]);
      }, "Add a row key"));
      keyChips[keyChips.length - 1].className = "te-kchip add";
    }
    const rowsSec = h("div", { class: "te-sec" }, h("h4", { text: "Rows" }),
      row("One row per", h("div", { class: "te-chips", dataset: { ids: "rows" } }, keyChips)),
      row("Block rows", selectCtl([["", "none"], ...keys.map((k) => [k, k])], rowsSpec.block || "",
        (v) => save([{ op: "set", path: "rows.block", value: v || null }]), "Block rows")));
    for (const k of keys) {
      const all = distinct(k);
      if (!all.length) continue;
      if (all.length > 40) {
        rowsSec.append(row(`Order of ${k}`, h("span", { class: "te-muted te-small" }, `${all.length} values; set the order with `,
          h("code", { text: "trazo table patch" }))));
        continue;
      }
      const listed = (rowsSpec.order || {})[k] || [];
      const ordered = [...listed.filter((v) => all.includes(v)), ...all.filter((v) => !listed.includes(v))];
      rowsSec.append(row(`Order of ${k}`, orderList(ordered.map((v) => ({ value: v, text: plain(mapped(k, v)), code: v })),
        (next) => save([{ op: "set", path: `rows.order.${k}`, value: next.map((x) => x.value) }])), true));
    }
    for (const k of keys) {
      const all = distinct(k);
      if (all.length < 2 || all.length > 40) continue;
      const allowed = (rowsSpec.filter || {})[k];
      const on = (v) => !allowed || allowed.includes(v);
      rowsSec.append(row("Show", h("div", { class: "te-chips" }, all.map((v) => h("label", { class: "te-kchip" },
        h("input", { type: "checkbox", checked: on(v) ? "" : null, "aria-label": `Show ${k} ${v}`, onchange: (e) => {
          const next = all.filter((x) => (x === v ? e.target.checked : on(x)));
          save([{ op: "set", path: `rows.filter.${k}`, value: next.length === all.length ? null : next }]);
        } }), v)), h("span", { class: "te-muted te-small", text: `of ${k}` }))));
    }
    rowsSec.append(row("Missing", textField(spec.missing === undefined ? "–" : spec.missing, "missing",
      { label: "Missing value text", ids: ["missing"] })));
    out.push(rowsSec);
    // Emphasis rules
    const rules = spec.highlight || [];
    out.push(h("div", { class: "te-sec" }, h("h4", { text: "Emphasis" }),
      rules.map((rule, i) => ruleRow(rule, i, true)),
      h("div", null, h("button", { type: "button", class: "pv-btn sm", text: "+ Rule", disabled: !leafIds().length ? "" : null,
        onclick: () => { const first = numericLeaf(); if (first) saveRules([...rules, newRule(first)]); } })),
      (spec.emphasis || []).length ? h("p", { class: "te-muted te-small", text: `${spec.emphasis.length} cell(s) bold by hand (select a cell to change).` }) : null));
    return out;
  }
  function numericLeaf() {
    const body = st.model ? st.model.body.find((b) => b.cells) : null;
    const c = body ? body.cells.find((x) => !x.hidden && (typeof x.raw === "number" || (x.raw && typeof x.raw === "object"))) : null;
    return c ? c.id : (leafIds()[0] || {}).id;
  }
  const newRule = (column) => ({ column, best: "max", within: (spec.rows || {}).block ? "block" : "table" });
  const saveRules = (rules) => save([{ op: "set", path: "highlight", value: rules.length ? rules : null }]);
  function ruleRow(rule, i, withColumn) {
    const rules = spec.highlight || [];
    const change = (key, value) => { const next = clone(rules); next[i][key] = value; saveRules(next); };
    const keys = (spec.rows || {}).keys || [];
    const within = [["table", "the table"], ...((spec.rows || {}).block ? [["block", "each block"]] : []), ...keys.map((k) => [k, `each ${k}`])];
    return h("div", { class: "te-rule", dataset: { ids: `highlight.${i}` } }, "Bold the",
      selectCtl([["max", "highest"], ["min", "lowest"]], rule.best, (v) => change("best", v), "Highest or lowest"),
      withColumn ? selectCtl(leafIds().map((c) => [c.id, `${plain(c.text_html)} (${c.id})`]), rule.column, (v) => change("column", v), "Column") : null,
      "within", selectCtl(within, rule.within || "table", (v) => change("within", v), "Within"),
      h("span", { class: "te-grow" }),
      h("button", { type: "button", class: "te-icon", style: "width:24px;height:24px", "aria-label": "Remove this rule", text: "✕",
        onclick: () => saveRules(rules.filter((_, j) => j !== i)) }));
  }

  function columnProps() {
    const path = leafPath(sel.id);
    const at = colAt(path);
    if (!at) return [ctx("Column", sel.id, "Not in the spec any more.")];
    const col = at.col, parent = at.parent;
    const head = st.model.header.flat().find((c) => c.id === sel.id);
    const groupName = parent ? (parent.pivot ? `pivot ${parent.id}` : `group ${parent.id}`) : null;
    const base = `columns.${path}`;
    const out = [ctx("Column", (parent && parent.header ? parent.header + " › " : "") + plain(head ? head.text_html : col.header),
      h("span", null, h("code", { text: sel.id }), " · field ", h("code", { text: col.field }), groupName ? " · in " : "", groupName ? h("code", { text: groupName }) : null))];
    const fields = fieldNames().map((f) => [f, `${f} (${fieldType(f)})`]);
    out.push(h("div", { class: "te-sec" },
      row("Header", textField(col.header, `${base}.header`, { label: "Header", ids: [sel.id] })),
      parent && parent.pivot ? h("p", { class: "te-muted te-small", text: `One column per ${parent.pivot} value; this header is shared by all of them.` }) : null,
      row("Field", selectCtl(fields, col.field, (v) => save([{ op: "set", path: `${base}.field`, value: v }]), "Field"))));
    out.push(formatSection(col, base));
    const width = col.width || 0;
    const range = h("input", { type: "range", min: "0", max: "4", step: "0.25", value: String(width), "aria-label": "Width weight (0 is auto)",
      onchange: (e) => save([{ op: "set", path: `${base}.width`, value: Number(e.target.value) || null }]),
      oninput: (e) => { e.target.nextSibling.textContent = Number(e.target.value) ? e.target.value : "auto"; } });
    out.push(h("div", { class: "te-sec" }, h("h4", { text: "Layout" }),
      row("Align", h("div", { class: "te-seg", role: "group", "aria-label": "Align" }, ["left", "center", "right"].map((a) =>
        h("button", { type: "button", class: (col.align || "left") === a ? "on" : "", "aria-pressed": String((col.align || "left") === a),
          text: a[0].toUpperCase() + a.slice(1), onclick: () => save([{ op: "set", path: `${base}.align`, value: a === "left" ? null : a }]) })))),
      row("Width", h("div", { class: "te-chips" }, range, h("span", { class: "te-muted te-small", text: width ? String(width) : "auto" }))),
      row("Merge", switchCtl(col.merge, "Join repeated values", (v) => save([{ op: "set", path: `${base}.merge`, value: v || null }]), "Merge repeated values")),
      row("Hidden", switchCtl(col.hidden, "Keep in the spec, leave out of the table", (v) => save([{ op: "set", path: `${base}.hidden`, value: v || null }]), "Hidden"))));
    const rules = spec.highlight || [];
    const mine = rules.map((r, i) => [r, i]).filter(([r]) => r.column === sel.id);
    out.push(h("div", { class: "te-sec" }, h("h4", { text: "Emphasis" }), mine.map(([r, i]) => ruleRow(r, i, false)),
      mine.length ? null : h("button", { type: "button", class: "pv-btn sm", text: "+ Bold the best value", onclick: () => saveRules([...rules, newRule(sel.id)]) })));
    const sibs = at.siblings, i = sibs.indexOf(col);
    const acts = [
      h("button", { type: "button", class: "pv-btn sm", text: "← Move", disabled: i === 0 ? "" : null,
        onclick: () => save([{ op: "move", column: path, before: sibs[i - 1].id }]) }),
      h("button", { type: "button", class: "pv-btn sm", text: "Move →", disabled: i === sibs.length - 1 ? "" : null,
        onclick: () => save([{ op: "move", column: path, before: sibs[i + 2] ? sibs[i + 2].id : null }]) }),
    ];
    if (parent && !parent.pivot) acts.push(h("button", { type: "button", class: "pv-btn sm", text: "Ungroup", onclick: () => ungroup(parent) }));
    if (!parent && isLeaf(sibs[i + 1])) acts.push(h("button", { type: "button", class: "pv-btn sm", text: "Group with next", onclick: () => groupWithNext(col, sibs[i + 1]) }));
    acts.push(h("button", { type: "button", class: "pv-btn sm te-danger", text: "Delete column",
      onclick: () => removeColumn(path) }));
    out.push(h("div", { class: "te-sec te-acts" }, acts));
    return out;
  }
  function ungroup(group) {
    const ops = group.columns.map((c) => ({ op: "add", into: "columns", item: c, before: group.id }));
    ops.push({ op: "remove", column: group.id });
    save([...ops, ...remapColumnRules(group.columns.map((c) => [`${group.id}.${c.id}`, c.id]))]);
    sel = { kind: "table" };
  }
  function remapColumnRules(pairs) {
    const names = new Map(pairs), ops = [];
    for (const key of ["highlight", "emphasis"]) {
      const rules = spec[key] || [];
      if (rules.some((rule) => names.has(rule.column))) {
        const next = rules.filter((rule) => !names.has(rule.column) || names.get(rule.column) !== null)
          .map((rule) => names.has(rule.column) ? { ...rule, column: names.get(rule.column) } : rule);
        ops.push({ op: "set", path: key, value: next.length ? next : null });
      }
    }
    return ops;
  }
  function groupWithNext(a, b) {
    const id = uniqueId("group", spec.columns.map((c) => c.id));
    save([{ op: "add", into: "columns", before: a.id, item: { id, header: "Group", columns: [a, b] } },
      { op: "remove", column: a.id }, { op: "remove", column: b.id },
      ...remapColumnRules([[a.id, `${id}.${a.id}`], [b.id, `${id}.${b.id}`]])]);
    sel = { kind: "column", id: `${id}.${a.id}` };
  }
  function removeColumn(path) {
    const ids = [...new Set([...(spec.highlight || []), ...(spec.emphasis || [])].map((rule) => rule.column))];
    const removed = ids.filter((id) => leafPath(id) === path || leafPath(id).startsWith(`${path}.`));
    save([{ op: "remove", column: path }, ...remapColumnRules(removed.map((id) => [id, null]))]);
    sel = { kind: "table" };
  }

  // Format: a named format, its template, decimals, a live sample and
  // where else a shared format is used.
  let sampleEl = null;
  function formatSection(col, base) {
    const all = formats();
    const name = col.format || "";
    const opts = [["", "(none)"], ...Object.keys(all).map((n) => [n, isLocal(n) ? n : `${n} (shared)`]), ["__new__", "New format…"]];
    const sec = h("div", { class: "te-sec" }, h("h4", { text: "Format" }));
    sec.append(row("Named", selectCtl(opts, name, (v) => {
      if (v === "__new__") {
        const fresh = uniqueId(col.id, Object.keys(all));
        save([{ op: "set", path: `formats.${fresh}`, value: all[name] || defaultTemplate(col.field) },
          { op: "set", path: `${base}.format`, value: fresh }]);
      } else save([{ op: "set", path: `${base}.format`, value: v || null }]);
    }, "Named format")));
    if (name && all[name] !== undefined) {
      const shared = !isLocal(name);
      const tmpl = textField(all[name], shared ? `shared:formats.${name}` : `formats.${name}`,
        { label: "Format template", mono: true, ids: [sel.id] });
      sec.append(row("Template", tmpl));
      const m = all[name].match(/\.(\d+)f/);
      if (m) {
        const n = Number(m[1]);
        const step = (d) => {
          const value = all[name].replace(/\.(\d+)f/g, () => `.${Math.max(0, n + d)}f`);
          save([{ op: "set", path: `formats.${name}`, value }], { shared });
        };
        sec.append(row("Decimals", h("div", { class: "te-step", role: "group", "aria-label": "Decimals" },
          h("button", { type: "button", "aria-label": "Fewer decimals", text: "−", disabled: n === 0 ? "" : null, onclick: () => step(-1) }),
          h("span", { text: String(n) }),
          h("button", { type: "button", "aria-label": "More decimals", text: "+", onclick: () => step(1) }))));
      }
      sampleEl = h("div", { class: "te-sample", "aria-live": "polite" });
      sec.append(sampleEl);
      updateSample();
      if (shared) sec.append(sharedNote(name, col, base));
    } else sampleEl = null;
    return sec;
  }
  function defaultTemplate(field) {
    const d = st.data.fields[field];
    if (d && d.keys && d.keys.length) return d.keys.map((k) => `{${k}:.2f}`).join(" / ");
    return "{value:.2f}";
  }
  function updateSample() {
    if (!sampleEl || !sampleEl.isConnected || !st.model || sel.kind !== "column" && sel.kind !== "cell") return;
    let cell = null, raw;
    for (const item of st.model.body) {
      if (!item.cells) continue;
      const c = item.cells.find((x) => x.id === sel.id);
      if (c && (!cell || (sel.kind === "cell" && item.row === sel.row))) { cell = c; raw = c.raw; }
    }
    sampleEl.replaceChildren(h("code", { text: cell ? show(raw) : "(no cell)" }), h("span", { text: cell ? `→ ${plain(cell.text_html)}` : "" }));
  }
  function sharedNote(name, col, base) {
    const usage = (st.format_usage || {})[name] || { tables: [], prose: [] };
    const others = usage.tables.filter((t) => !t.local && t.label !== label);
    const here = (usage.tables.find((t) => t.label === label) || { leaves: [] }).leaves.filter((id) => id !== sel.id);
    const items = [
      ...here.map((id) => h("li", null, "this table: ", h("code", { text: id }))),
      ...others.map((t) => h("li", null, `${(numbers[t.label] || {}).num || t.label}: `, h("code", { text: t.leaves.join(", ") }))),
      ...usage.prose.map((p) => h("li", null, h("code", { text: `${p.file}:${p.line}` }), " ", h("code", { text: `{{v:${p.key}|${name}}}` }))),
    ];
    const copy = () => {
      const fresh = uniqueId(`${name}_${col.id}`, Object.keys(formats()));
      save([{ op: "set", path: `formats.${fresh}`, value: formats()[name] }, { op: "set", path: `${base}.format`, value: fresh }]);
    };
    return h("div", { class: "te-note" },
      h("div", null, "Shared format (", h("code", { text: st.shared.file }), "): editing the template changes it everywhere",
        items.length ? ", also in:" : "; nothing else uses it yet."),
      items.length ? h("ul", null, items) : null,
      h("button", { type: "button", class: "pv-btn sm", text: "Copy for this column", onclick: copy }));
  }

  function groupProps() {
    const at = colAt(sel.path);
    if (!at) return [ctx("Group", sel.id, "Not in the spec any more.")];
    const col = at.col;
    const out = [];
    const children = h("div", { class: "te-chips" }, col.columns.map((c) => h("button", { type: "button", class: "te-kchip",
      text: `${c.header} (${c.id})`, onclick: () => {
        const leaf = leafIds().find((x) => leafPath(x.id) === `${col.id}.${c.id}`);
        if (leaf) select({ kind: "column", id: leaf.id });
      } })));
    if (col.pivot) {
      out.push(ctx("Pivot", col.id, h("span", null, "one group per value of ", h("code", { text: col.pivot }))));
      const all = distinct(col.pivot);
      const chosen = col.values || all;
      const items = [...chosen, ...all.filter((v) => !chosen.includes(v))].map((v) => ({ value: v, text: plain(mapped(col.pivot, v)), code: v, off: !chosen.includes(v) }));
      const setValues = (vals) => save([{ op: "set", path: `columns.${col.id}.values`, value: vals }]);
      out.push(h("div", { class: "te-sec" },
        row("Pivot field", selectCtl(fieldNames().filter((f) => fieldType(f) === "string").map((f) => [f, f]), col.pivot,
          (v) => save([{ op: "set", path: `columns.${col.id}.pivot`, value: v }, { op: "set", path: `columns.${col.id}.values`, value: null }]), "Pivot field")),
        row("Values", orderList(items, (next) => setValues(next.filter((x) => !x.off).map((x) => x.value)),
          (it) => h("input", { type: "checkbox", checked: it.off ? null : "", "aria-label": `Show ${it.value}`, onclick: (e) => e.stopPropagation(),
            onchange: (e) => setValues(items.filter((x) => (x === it ? e.target.checked : !x.off)).map((x) => x.value)) })), true),
        row("Columns", children)));
    } else {
      out.push(ctx("Group", col.header, h("code", { text: col.id })));
      out.push(h("div", { class: "te-sec" },
        row("Header", textField(col.header, `columns.${col.id}.header`, { label: "Group header", ids: [col.id] })),
        row("Columns", children)));
    }
    const sibs = spec.columns, i = sibs.indexOf(col);
    out.push(h("div", { class: "te-sec te-acts" },
      h("button", { type: "button", class: "pv-btn sm", text: "← Move", disabled: i === 0 ? "" : null,
        onclick: () => save([{ op: "move", column: col.id, before: sibs[i - 1].id }]) }),
      h("button", { type: "button", class: "pv-btn sm", text: "Move →", disabled: i === sibs.length - 1 ? "" : null,
        onclick: () => save([{ op: "move", column: col.id, before: sibs[i + 2] ? sibs[i + 2].id : null }]) }),
      col.pivot ? null : h("button", { type: "button", class: "pv-btn sm", text: "Ungroup", onclick: () => ungroup(col) }),
      h("button", { type: "button", class: "pv-btn sm te-danger", text: col.pivot ? "Delete pivot" : "Delete group",
        onclick: () => removeColumn(col.id) })));
    return out;
  }

  function cellProps() {
    const item = st.model.body.find((b) => b.row === sel.row);
    const cell = item && item.cells.find((c) => c.id === sel.id);
    if (!cell) {  // the cell is joined to the one above, or gone
      const up = st.model.body.filter((b) => b.cells && b.row < sel.row).reverse().find((b) => b.cells.some((c) => c.id === sel.id));
      if (!up) return [ctx("Cell", sel.id, "Not in the table any more.")];
    }
    const key = item ? item.key : {};
    const emphasis = spec.emphasis || [];
    const at = emphasis.findIndex((e) => e.column === sel.id && stable(e.row) === stable(key));
    const toggle = (on) => {
      const next = on ? [...emphasis, { row: key, column: sel.id }] : emphasis.filter((_, i) => i !== at);
      save([{ op: "set", path: "emphasis", value: next.length ? next : null }]);
    };
    sampleEl = null;
    return [ctx("Cell", cell ? plain(cell.text_html) : "", h("span", null, "Row ", h("code", { text: Object.values(key).join(" · ") }), " · column ", h("code", { text: sel.id }))),
      h("div", { class: "te-sec" }, h("h4", { text: "Value from the data" }),
        h("pre", { class: "te-mono", style: "margin:0;padding:8px;background:var(--pv-hover);border-radius:6px;white-space:pre-wrap",
          text: cell ? JSON.stringify(cell.raw, null, 1) : "" }),
        h("p", { class: "te-muted te-small", style: "margin:8px 0 0" }, "Numbers come from ",
          h("code", { text: (st.data.file || "the data") }), ". To change them, change the generator or the data.")),
      h("div", { class: "te-sec" },
        row("Bold", switchCtl(at >= 0, "This cell only", toggle, "Bold this cell")),
        cell && cell.bold && at < 0 ? h("p", { class: "te-muted te-small", text: "Bold by a highlight rule." }) : null,
        h("div", { class: "te-acts", style: "margin-top:8px" },
          h("button", { type: "button", class: "pv-btn sm", text: "Comment on this cell", onclick: () => startComment() }),
          h("button", { type: "button", class: "pv-btn sm", text: "Select column", onclick: () => select({ kind: "column", id: sel.id }) })))];
  }

  // Comments ------------------------------------------------------------
  // Same states and panel as the preview: Active (open and to check) and
  // Resolved (one line each, the last resolved first); one action per
  // intent; each change shows a notice with Undo.
  const STATE_NAME = { open: "Open", review: "To check", done: "Resolved", deleted: "Deleted" };
  const TRASH = '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.4" '
    + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.5h5.8l.6-8.5"/></svg>';
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  function startComment() {
    composeFor = targetOf(sel);
    tab = "comments";
    cfilter = "active";
    renderTabs();
    renderPanel();
    const ta = $(".te-compose textarea");
    if (ta) ta.focus();
  }
  // Apply one comment change of this table. With `message`, a notice offers
  // Undo for it; Undo is refused after another change (e.g. the agent's).
  async function commentOp(body, message) {
    const table = label;
    const r = await App.review.change("table", { table, ...body });
    if (!r.ok) {
      flash(r.data.error || "Comment not saved");
      if (label === table) loadComments();  // e.g. a refused undo removed its entry
      return false;
    }
    if (label !== table) return true;
    comments = r.data;
    renderTable(); renderTabs(); renderPanel(); loadList();
    if (message) {
      // Undo exactly this change: the server refuses if another one came since.
      const mine = comments.undo_id;
      flash(message, comments.undo && body.op !== "undo" ? { label: "Undo", run: () => {
        if (label === table && comments.undo_id === mine) undoComment(mine);
        else flash("Not undone: the comments changed since");
      } } : null);
    }
    return true;
  }
  // The last comment change of this table (⌘Z here undoes table edits).
  const undoComment = (expect) => commentOp({ op: "undo", ...(expect ? { expect } : {}) }, "Undone");
  // A comment text field whose text survives redraws (see renderPanel).
  function draftField(key, initial, placeholder, name, submit, cancel) {
    const ta = h("textarea", { class: "te-in", placeholder, "aria-label": name, dataset: { draft: key } });
    ta.value = key in cdrafts ? cdrafts[key] : initial;
    ta.addEventListener("input", () => { cdrafts[key] = ta.value; });
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submit(); }
      if (e.key === "Escape" && cancel) { e.preventDefault(); e.stopPropagation(); cancel(); }
    });
    return ta;
  }
  function commentsPanel() {
    const all = comments.comments;
    const live = all.filter((c) => c.status === "open" || c.status === "review");
    const toCheck = live.filter((c) => c.status === "review").length, waiting = live.length - toCheck;
    const when = (c) => c.resolved || c.created || "";
    const done = all.filter((c) => c.status === "done").sort((a, b) => when(b).localeCompare(when(a)));
    const trash = all.filter((c) => c.status === "deleted");
    const seg = (key, text, n) => h("button", { type: "button", class: cfilter === key ? "on" : null, dataset: { filter: key },
      "aria-pressed": String(cfilter === key), onclick: () => { cfilter = key; openDone = null; renderPanel(); } },
    text, h("span", { class: "te-cn", text: String(n) }));
    const out = [h("div", { class: "te-cbar" }, h("div", { class: "te-seg", role: "group", "aria-label": "Show comments" },
      seg("active", "Active", live.length), seg("resolved", "Resolved", done.length)),
    comments.undo ? h("button", { type: "button", class: "pv-btn sm ghost te-cundo", text: "↶ Undo",
      title: `Undo the last comment change: ${comments.undo}`, onclick: () => undoComment(comments.undo_id) }) : null)];
    const empty = (title, text) => out.push(h("div", { class: "te-cempty" }, h("b", { text: title }), text || null));
    if (cfilter === "active") {
      if (live.length) {
        out.push(h("p", { class: "te-csum", "aria-live": "polite" }, toCheck ? h("b", { text: `${toCheck} to check` }) : null,
          toCheck && waiting ? " · " : null, waiting ? `${waiting} waiting for the agent` : null));
      } else if (done.length) empty("Nothing to handle", "Every comment is resolved.");
      else empty("No comments", "Select a header or cell and press C to add one.");
      for (const c of live) out.push(commentCard(c));
      const send = async () => {
        const raw = ta.value;
        if (!raw.trim()) return;
        const body = { op: "add", comment: raw };
        if (composeFor) body.target = composeFor;
        if (await commentOp(body, "Comment added") && cdrafts.new === raw) { delete cdrafts.new; renderPanel(); }
      };
      const ta = draftField("new", "", "Comment for the agent…", "New comment", send);
      out.push(h("div", { class: "te-compose" }, h("div", { class: "te-to" }, "New comment on ", h("code", { text: targetText(composeFor) })),
        ta, h("div", { style: "margin-top:6px" }, h("button", { type: "button", class: "pv-btn sm primary", text: "Comment", onclick: send }))));
    } else {
      if (!done.length) empty("No resolved comments");
      for (const c of done) out.push(commentCard(c));
      const foot = h("div", { class: "te-cfoot" });
      if (done.length) {
        foot.append(h("button", { type: "button", text: "Delete all resolved", title: "Move them to Deleted (Undo brings them back)",
          onclick: () => commentOp({ op: "delete", ids: done.map((c) => c.id) }, `${plural(done.length, "resolved comment")} deleted`) }));
      }
      if (trash.length) {
        foot.append(h("button", { type: "button", class: "te-trash", "aria-expanded": String(showTrash),
          text: `${showTrash ? "▾" : "▸"} Deleted (${trash.length})`, onclick: () => { showTrash = !showTrash; renderPanel(); } }));
      }
      if (foot.childNodes.length) out.push(foot);
      if (showTrash) for (const c of trash) out.push(commentCard(c));
    }
    return out;
  }
  function commentCard(c) {
    const mode = cedit && cedit.id === c.id ? cedit.mode : null;
    if (c.status === "done" && openDone !== c.id && !mode) {
      return h("div", { class: "te-card done compact", title: `${c.resolved ? `Resolved ${c.resolved}. ` : ""}Click to show the thread`,
        onclick: () => { openDone = c.id; renderPanel(); } },
      h("span", { class: "te-cok", "aria-hidden": "true", text: "✓" }), h("span", { class: "te-ctext", text: c.comment.split("\n")[0] }),
      h("span", { class: "te-cwhere", text: targetText(c.target) }));
    }
    const key = `${c.id}:${mode}`;
    const close = () => { delete cdrafts[key]; cedit = null; renderPanel(); };
    const open = (m) => {
      cedit = { id: c.id, mode: m };
      renderPanel();
      const ta = $(`[data-draft="${CSS.escape(`${c.id}:${m}`)}"]`);
      if (ta) { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); }
    };
    const submit = async () => {
      const raw = cdrafts[key] ?? (mode === "edit" ? c.comment : ""), value = raw.trim();
      const body = mode === "edit" ? (value && value !== c.comment ? { op: "update", id: c.id, comment: value } : null)
        : value ? { op: "reply", id: c.id, by: "author", text: value, status: "open" } : null;
      if (body && !await commentOp(body, mode === "edit" ? "Comment edited" : "Reply sent; the comment is open for the agent again")) return;
      // Text typed while the request was pending belongs to the next edit.
      if ((cdrafts[key] ?? raw) === raw && cedit && cedit.id === c.id && cedit.mode === mode) close();
    };
    const acts = h("div", { class: "te-cacts" });
    const btn = (text, onclick, cls, title) => acts.append(h("button", { type: "button", class: `pv-btn sm${cls ? " " + cls : ""}`, text, title, onclick }));
    const spacer = () => acts.append(h("span", { class: "te-grow" }));
    const del = () => acts.append(h("button", { type: "button", class: "te-icon te-del", "aria-label": "Delete comment",
      title: "Delete (Undo brings it back)", html: TRASH, onclick: () => commentOp({ op: "delete", id: c.id }, "Comment deleted") }));
    const resolve = () => commentOp({ op: "update", id: c.id, status: "done" }, "Comment resolved");
    let text = h("p", { text: c.comment }), reply = null;
    if (mode === "edit") {
      text = draftField(key, c.comment, "⌘↵ to save, Esc to cancel", "Edit comment", submit, close);
    } else if (mode === "reply") {
      reply = draftField(key, "", "Reply to the agent; sending reopens the comment (⌘↵ to send, Esc to cancel)", "Reply", submit, close);
    }
    if (mode) {
      spacer();
      btn("Cancel", close, "ghost");
      btn(mode === "edit" ? "Save" : "Send", submit, "primary");
    } else if (c.status === "deleted") {
      spacer();
      btn("Restore", () => commentOp({ op: "restore", id: c.id }, "Comment restored"));
    } else if (c.status === "open") {
      btn("Edit", () => open("edit"), "ghost");
      spacer();
      btn("✓ Resolve", resolve, "", "You handled it; it leaves the agent's list");
      del();
    } else if (c.status === "review") {
      btn("Reply", () => open("reply"), "ghost", "Ask the agent for more; reopens the comment");
      spacer();
      btn("✓ Resolve", resolve, "primary", "The agent's change is fine");
      del();
    } else {
      btn("Reopen", () => { openDone = null; commentOp({ op: "update", id: c.id, status: "open" }, "Comment reopened for the agent"); },
        "ghost", "Send it back to the agent");
      spacer();
      del();
    }
    return h("div", { class: `te-card ${c.status}` },
      h("div", { class: "te-meta" }, h("span", { class: `te-state ${c.status}`, text: STATE_NAME[c.status] || c.status,
        title: c.status === "done" && c.resolved ? `Resolved ${c.resolved}` : null }),
      h("button", { type: "button", class: "te-target", text: targetText(c.target), title: "Select the target",
        onclick: () => selectTarget(c.target) }), h("span", { class: "te-grow" }), h("span", { text: c.created || "" })),
      text,
      (c.replies || []).map((r) => h("div", { class: `te-reply ${r.by === "agent" ? "" : "author"}` },
        h("span", { class: "te-muted te-small", text: r.by === "agent" ? "Agent" : "You" }), h("br"), r.text)),
      reply, acts);
  }
  function selectTarget(t) {
    if (!t) { select({ kind: "table" }); return; }
    if (t.row) {
      const item = st.model && st.model.body.find((b) => b.cells && stable(b.key) === stable(t.row));
      if (item) { sel = { kind: "cell", id: t.column, row: item.row }; renderTable(); return; }
    }
    sel = leafIds().some((c) => c.id === t.column) ? { kind: "column", id: t.column } : { kind: "group", id: t.column, path: t.column.split(".")[0] };
    renderTable();
  }

  // Check and data ------------------------------------------------------
  function issuesPanel() {
    const issues = st.issues || [];
    if (!issues.length) {
      return [h("div", { class: "te-ok", text: "No issues: the data is current and the table renders (trazo table check)." })];
    }
    return issues.map((it) => h("button", { type: "button", class: `te-issue ${it.level}`, onclick: () => {
      if (it.id && leafIds().some((c) => c.id === it.id)) select({ kind: "column", id: it.id });
      else if (it.id && spec.columns.some((c) => c.id === it.id.split(".")[0] && (c.columns))) select({ kind: "group", id: it.id, path: it.id.split(".")[0] });
    } }, h("div", { class: "te-code", text: `${it.level} · ${it.code}${it.id ? " · " + it.id : ""}` }), it.message));
  }
  function dataPanel() {
    const d = st.data;
    const compact = (v) => (v && typeof v === "object" ? "{" + Object.entries(v).map(([k, x]) => `${k} ${typeof x === "number" ? +x.toFixed(4) : x}`).join(", ") + "}"
      : v === null ? "null" : String(v));
    const names = Object.keys(d.fields);
    return [h("div", { class: "te-sec" }, h("span", { class: "te-muted" }, h("code", { text: d.file || "" }), ` · ${d.count} records · ${Object.keys(d.values || {}).length} values`)),
      h("div", { class: "te-sec" }, h("h4", { text: "Fields" }), h("div", { class: "te-data-wrap", style: "margin:0" },
        h("table", { class: "te-data" }, h("tr", null, h("th", { text: "field" }), h("th", { text: "type" }), h("th", { text: "values" })),
          names.map((n) => h("tr", null, h("td", { text: n }), h("td", { text: d.fields[n].types.join(" | ") + (d.fields[n].keys.length ? ` {${d.fields[n].keys.join(", ")}}` : "") }),
            h("td", { class: "wrap", text: d.fields[n].values.slice(0, 12).join(", ") + (d.fields[n].values.length > 12 ? ", …" : "") })))))),
      Object.keys(d.values || {}).length ? h("div", { class: "te-sec" }, h("h4", { text: "Caption values" }),
        h("div", { class: "te-chips" }, Object.entries(d.values).map(([k, v]) => h("span", { class: "te-chip", text: `{${k}} = ${compact(v)}` })))) : null,
      h("div", { class: "te-sec" }, h("h4", { text: `Records${d.count > d.records.length ? ` (first ${d.records.length})` : ""}` })),
      h("div", { class: "te-data-wrap" }, h("table", { class: "te-data" }, h("tr", null, names.map((n) => h("th", { text: n }))),
        d.records.map((r) => h("tr", null, names.map((n) => h("td", { text: compact(r[n] === undefined ? null : r[n]) }))))))];
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
      startComment();
    } else if (["Enter", " "].includes(e.key) && !typing && e.target.matches("thead th, td[data-row], .te-cap")) {
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
      if (cells[next]) cells[next].focus();
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
    cedit = openDone = null;
    cdrafts = {};
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
