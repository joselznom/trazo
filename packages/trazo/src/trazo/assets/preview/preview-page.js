// Preview viewer: builds the shell (top bar, index, document page, review
// sidebar, Figures & Tables and Submission views) around pandoc's body, and
// wires paragraph markers, review comments, the Markdown source editor, the
// diff, table text editing, builds and live reload. Document labels, figures
// and citations come from preview-model.js (shared by every document).
// The parts of the viewer are classic scripts loaded in the order of
// trazo.preview.VIEWER_SCRIPTS; their top-level names are shared globals.
  const Model = window.PreviewModel;
  if (!Model) {  // preview-model.js did not load (or this is a stale tab after an upgrade)
    const note = document.createElement("p");
    note.style.cssText = "margin:2em;font:16px system-ui,sans-serif";
    note.textContent = "Viewer scripts did not load; reload the page.";
    document.body.prepend(note);
    // Throw rather than return, so the viewer can be split into classic scripts.
    throw new Error("preview-model.js did not load");
  }
  const doc = window.PREVIEW_DOC;
  const DOCS = window.PREVIEW_TITLES || { manuscript: "Manuscript", supplementary: "Supplementary" };
  const docNames = Object.keys(DOCS);
  const params = new URLSearchParams(Trazo.search());
  const squash = Model.squash;
  const titleOf = Model.titleOf;

  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => [...(r || document).querySelectorAll(s)];
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  const fmt = (n) => n.toLocaleString("en");
  const reduceMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === false || v == null) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids) if (kid != null && kid !== false) el.append(kid);
    return el;
  }
  // Browser storage can be missing or refuse writes (private windows).
  const sget = (key) => { try { return sessionStorage.getItem(key); } catch (e) { return null; } };
  const sset = (key, value) => { try { sessionStorage.setItem(key, value); } catch (e) { /* ignore */ } };
  const sdel = (key) => { try { sessionStorage.removeItem(key); } catch (e) { /* ignore */ } };
  const App = window.Trazo;
  let prefs = App.readPrefs();
  const savePrefs = extra => { prefs = App.savePrefs(extra); };

  document.documentElement.classList.add("pv-app");

  // ---------------------------------------------------------------- page
  // Pandoc's body moves into the page; everything else is built around it.
  const page = h("div", { class: "pv-page", lang: document.documentElement.lang || false });
  page.append(...[...document.body.childNodes].filter((n) => !(n.nodeType === 1 && n.tagName === "SCRIPT")));
  // Labels, units, figures and citations of this document (see preview-model.js).
  const model = Model.analyse(page, doc);
  const { units, topSections, refsSection } = model;
  const analyses = { [doc]: model };  // the other documents are added by loadAll()
  // A table can stop short of the text width (Pandoc's inline width) or grow
  // past its inline width when its content needs the room: its note is as
  // wide as the visible table (at most its scroll box), so the edges line up.
  // The returned observer keeps it right when the page, text size or zoom change.
  const alignNote = (note, table, box) => {
    const fit = () => {  // computed widths: exact, and not scaled by the page zoom
      const width = (el) => parseFloat(getComputedStyle(el).width) || 0;
      const w = Math.min(width(table), width(box));
      note.style.width = w ? `${w}px` : "";
    };
    fit();
    const observer = new ResizeObserver(fit);
    observer.observe(table);
    observer.observe(box);
    return observer;
  };
  for (const table of $$("table", page)) {  // wide tables scroll sideways
    const wrap = h("div", { class: "pv-tscroll" });
    table.before(wrap);
    wrap.append(table);
    const note = wrap.nextElementSibling;
    if (note && note.matches("div.table-note")) alignNote(note, table, wrap);
  }

  // Paragraph markers (¶n) open the source; lists get a small pencil instead.
  // Captions and tables have Edit caption / Edit table. Labels were set by
  // Model.label().
  const addSrcMark = (el, host) => {
    if (!host) return;
    const mark = h("button", { type: "button", class: "pv-src-mark", text: "✎", title: "Open the Markdown source here" });
    mark.addEventListener("click", (e) => { e.preventDefault(); openSourceAt(el); });
    host.prepend(mark);
  };
  for (const entry of model.entries) {
    const { el } = entry;
    if (entry.kind === "list") {
      // Loose list items wrap their text in <p>: the pencil goes inside it,
      // or it would sit alone on the number's line.
      const first = el.firstElementChild;
      addSrcMark(el, first && first.tagName === "P" ? first : entry.host);
      continue;
    }
    if (entry.kind !== "para") continue;
    const text = `¶${entry.n}`;
    const mark = h("button", { type: "button", class: "pv-para", text,
      title: `${entry.label}: click to open the source here, ⌥-click to copy the label`,
      "aria-label": `${entry.label}: open the source` });
    mark.addEventListener("click", (e) => {
      if (!e.altKey) { openSourceAt(el); return; }
      try { navigator.clipboard.writeText(entry.label); } catch (err) { /* ignore */ }
      mark.classList.add("copied");
      toast(`Copied label “${entry.label}”`);
      setTimeout(() => mark.classList.remove("copied"), 900);
    });
    el.classList.add("pv-p");
    el.prepend(mark);
  }

  // "Edit diagram" under figures exported from figures/diagrams/<name>.json.
  const DIAGRAMS = window.PREVIEW_DIAGRAMS || { names: [] };
  const diagramHref = (name, from) => App.editorLink("/diagrams", name, from);
  const diagramTitle = (name) => `Open ${window.TRAZO_CONFIG.paths.diagrams}/${name}.json in the diagram editor`;
  function diagramButtons() {
    for (const link of $$(".pv-diagram", page)) link.remove();
    for (const img of $$("figure img", page)) {
      const name = Model.diagramOf(Model.srcOf(img));
      if (!name) continue;
      const from = Trazo.path() + Trazo.search() + "#" + img.closest("figure").id;
      img.after(h("div", { class: "pv-diagram" },
        h("a", { href: diagramHref(name, from), title: diagramTitle(name), text: "Edit diagram" })));
    }
  }
  diagramButtons();
