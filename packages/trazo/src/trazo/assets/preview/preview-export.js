// Preview viewer: export downloads, the Export menu and the sections it offers.
// The parts of the viewer are classic scripts loaded in the order of
// trazo.preview.VIEWER_SCRIPTS; their top-level names are shared globals.
  if (!window.PreviewModel) throw new Error("preview-model.js did not load");  // see preview-page.js
  // ---------------------------------------------------------------- Figures & Tables
  async function downloadExport(name, kind, ids, format, extra = {}) {
    const query = new URLSearchParams({ doc: name, kind, format });
    for (const id of Array.isArray(ids) ? ids : [ids]) query.append("id", id);
    for (const [key, values] of Object.entries(extra)) for (const value of [].concat(values)) query.append(key, value);
    if (window.TRAZO_VIEW) return window.TRAZO_VIEW.download(`/__export?${query}`);  // a view cannot download
    const response = await fetch(`/__export?${query}`);
    if (!response.ok) { const data = await response.json(); throw new Error(data.error || response.status); }
    const blob = await response.blob(), url = URL.createObjectURL(blob);
    const link = h("a", { href: url, download: (response.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/)?.[1] || `${kind}.${format}` });
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  // ---------------------------------------------------------------- export menu
  // One Export button: the full manuscript (its full build), the section in view,
  // or a picker for any set of sections. Its controls never enter the manuscript.
  const exportBox = $(".pv-export", bar), exportToggle = $(".pv-export-toggle", exportBox);
  const exportMenu = $(".pv-export-menu", exportBox), exportPanel = $(".pv-export-panel", exportBox);
  // Word indexes, remembered per document and off at first. Each is offered
  // only when it would list something in the exported content: contents need
  // two headings (levels 1-3), the lists a figure or a table. The server
  // applies the same rule, so a stale choice never adds an empty index.
  const INDEX_KINDS = [
    { key: "toc", pill: "Contents", name: "contents", title: "Table of contents", min: 2, none: "Needs at least two headings" },
    { key: "lof", pill: "Figures", name: "figure list", title: "List of figures", min: 1, none: "No figures in this content" },
    { key: "lot", pill: "Tables", name: "table list", title: "List of tables", min: 1, none: "No tables in this content" },
  ];
  // With a separate title page the full build leaves the authors out (the
  // journal's copy); a copy for coauthors can keep them. Remembered with the
  // indexes; section exports never carry the title block.
  const AUTHORS_KIND = { key: "authors", pill: "Authors", name: "authors", title: "Authors and affiliations", min: 1 };
  const offersAuthors = !!window.TRAZO_CONFIG?.separate_title_page
    && !(window.TRAZO_CONFIG.submission_parts || []).includes(doc);
  const MENU_KINDS = offersAuthors ? [AUTHORS_KIND, ...INDEX_KINDS] : INDEX_KINDS;
  const storedIndexes = (App.readPrefs(prefs).exportIndexes || {})[doc] || {};
  const exportIndexes = Object.fromEntries(MENU_KINDS.map(({ key }) => [key, storedIndexes[key] === true]));
  const indexGroups = [];
  const wantedIndexes = (counts) => Object.fromEntries(INDEX_KINDS.map(({ key, min }) => [key, exportIndexes[key] && counts[key] >= min]));
  // What the full build adds: the indexes and, when offered, the authors.
  const wantedBuild = (counts) => ({ ...wantedIndexes(counts), ...(offersAuthors ? { authors: exportIndexes.authors } : {}) });
  const levelOf = (el) => Number(el.tagName.slice(1));
  // Headings, figures and tables of the whole document (title block and
  // bibliography aside).
  const documentCounts = () => ({
    toc: $$("section > :is(h1, h2, h3)", page).filter((el) => !el.closest("header, #refs")).length,
    lof: model.items.filter((it) => it.kind === "figure").length,
    lot: model.items.filter((it) => it.kind === "table").length,
    authors: 1,
  });
  // "contents and figure list"; "" when nothing will be added.
  const indexSummary = (counts) => {
    const wanted = wantedBuild(counts);
    const names = MENU_KINDS.filter((k) => wanted[k.key]).map((k) => k.name);
    return names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}` : names[0] || "";
  };
  // One pill per index: pressed adds it. An index with nothing to list is
  // shown dashed with the reason; it keeps the reader's choice for later.
  function indexPills(counts, where) {
    const group = h("div", { class: "pv-index-pills", role: "group", "aria-label": "Word indexes" });
    group.counts = counts;
    for (const kind of where === "menu" ? MENU_KINDS : INDEX_KINDS) {
      const pill = h("button", { type: "button", class: "pv-index-pill", "data-index": kind.key,
        role: where === "menu" ? "menuitemcheckbox" : null },
        h("span", { class: "pv-index-check", "aria-hidden": "true" }), h("span", { text: kind.pill }),
        h("span", { class: "pv-index-n" }));
      pill.addEventListener("click", (e) => {
        e.stopPropagation();
        if (pill.getAttribute("aria-disabled") === "true") return;
        exportIndexes[kind.key] = !exportIndexes[kind.key];
        const all = App.readPrefs(prefs).exportIndexes;
        const others = all && typeof all === "object" && !Array.isArray(all)
          ? Object.fromEntries(Object.entries(all).filter(([, v]) => v && typeof v === "object")) : {};
        savePrefs({ exportIndexes: { ...others, [doc]: { ...exportIndexes } } });
        renderIndexes();
      });
      group.append(pill);
    }
    indexGroups.push(group);
    return group;
  }
  function renderIndexes() {
    for (const group of indexGroups) {
      const counts = group.counts();
      for (const kind of MENU_KINDS) {
        const pill = $(`[data-index="${kind.key}"]`, group);
        if (!pill) continue;
        const n = counts[kind.key], ok = n >= kind.min, on = exportIndexes[kind.key] && ok;
        pill.setAttribute("aria-pressed", String(on));
        if (pill.getAttribute("role")) pill.setAttribute("aria-checked", String(on));
        pill.setAttribute("aria-disabled", String(exporting || !ok));
        pill.classList.toggle("off", !ok);
        pill.title = kind === AUTHORS_KIND ? `${kind.title} under the title; the separate title page keeps them either way`
          : ok ? `${kind.title}: Word fills it, with page numbers, when you update fields` : `${kind.title}: ${kind.none.toLowerCase()}`;
        $(".pv-index-n", pill).textContent = kind.key === "toc" || kind === AUTHORS_KIND || !ok ? "" : String(n);
      }
    }
    if (!docxPath) return;
    const summary = indexSummary(documentCounts());
    indexToggle.firstChild.textContent = summary ? `With ${summary}` : offersAuthors ? "No authors or indexes" : "No indexes";
    indexToggle.title = offersAuthors ? "Authors and Word indexes for the full build" : "Word indexes for the full build";
  }
  // The full build of this document. A line under it says which indexes it
  // adds and unfolds their pills. Section exports choose
  // theirs in the picker (Current section never adds any).
  const BUILDS = (window.TRAZO_CONFIG || {}).builds;
  const docxPath = BUILDS ? BUILDS[doc] || "" : "output/manuscript.docx";
  const fullLabel = `Full ${(DOCS[doc] || doc).toLowerCase()}`;
  $(".pv-mi-t", docxBtn).textContent = fullLabel;
  $(".pv-mi-s", docxBtn).textContent = docxPath ? `Builds ${docxPath}` : "No full build for this document";
  docxBtn.title = docxPath ? "Run the full build (may regenerate figures and tables first)" : "Set documents." + doc + ".docx and build in trazo.toml";
  docxBtn.disabled = !docxPath;
  const indexToggle = h("button", { type: "button", class: "pv-index-toggle", "aria-expanded": "false" },
    h("span"), h("span", { class: "pv-index-caret", "aria-hidden": "true" }));
  const menuPills = indexPills(documentCounts, "menu");
  menuPills.classList.add("pv-index-fold");
  menuPills.hidden = true;
  indexToggle.addEventListener("click", (e) => {
    e.stopPropagation();
    menuPills.hidden = !menuPills.hidden;
    indexToggle.setAttribute("aria-expanded", String(!menuPills.hidden));
  });
  if (docxPath) docxBtn.after(indexToggle, menuPills);
  // Focusable menu entries, in order; folded pills are skipped.
  const menuItems = () => $$(".pv-mi, .pv-index-toggle, .pv-index-pill", exportMenu)
    .filter((b) => !b.disabled && b.offsetParent !== null);
  const currentItem = $('[data-act="current"]', exportMenu);

  // Sections with an id, in document order. Each is ticked on its own: a
  // parent ticks its subsections too, but any of them can be unticked.
  const exportSections = [], headWords = new Map(outlineHeads.map((x) => [x.el, x]));
  for (const heading of $$("section > :is(h1, h2, h3, h4, h5, h6)", page)) {
    const el = heading.parentElement, id = heading.id || el.id;
    const refs = $(":scope > #refs", el);
    // Bibliography may share the final section; omit only a references-only body.
    if (!id || heading.closest("#refs, header") ||
      (refs && [...el.children].every((child) => child === heading || child === refs))) continue;
    const parent = exportSections.findLast((section) => section.el.contains(el)) || null;
    const words = headWords.get(heading);
    const section = { id, el, heading, parent, depth: parent ? parent.depth + 1 : 0, kids: [],
      text: heading.textContent.trim(), words: words ? words.words : 0, total: words ? words.total : 0 };
    if (parent) parent.kids.push(section);
    exportSections.push(section);
  }
  const branch = (section) => exportSections.filter((x) => x === section || section.el.contains(x.el));
  const selected = new Set(), folded = new Set();
  let includeRefs = prefs.exportRefs !== false, exporting = false;
  unsavedChecks.push(() => exporting);
  function stateOf(section) {
    const all = branch(section), n = all.filter((x) => selected.has(x)).length;
    return n === 0 ? "off" : n === all.length ? "on" : "mixed";
  }
  // The server keeps each block under the nearest listed heading above it:
  // `id` starts a chosen run and `exclude` cuts a subsection out of one.
  function exportQuery() {
    const ids = [], exclude = [];
    for (const section of exportSections) {
      const inherited = !!section.parent && selected.has(section.parent);
      if (selected.has(section) !== inherited) (inherited ? exclude : ids).push(section.id);
    }
    return { ids, exclude };
  }
  function currentSection() {
    const head = outlineHeads[activeHead];
    return head ? exportSections.findLast((s) => s.heading === head.el || s.el.contains(head.el)) || null : null;
  }
  async function exportSectionsNow(ids, exclude, indexes = {}) {
    exporting = true;
    renderExport();
    try {
      await downloadExport(doc, "section", ids, "docx", { exclude, refs: includeRefs ? "1" : "0",
        ...Object.fromEntries(Object.entries(indexes).map(([key, value]) => [key, value ? "1" : "0"])) });
      return null;
    } catch (e) { return e.message; } finally { exporting = false; renderExport(); }
  }
