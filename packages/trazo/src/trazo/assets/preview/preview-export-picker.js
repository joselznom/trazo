// Preview viewer: the section export picker and the menus' placement.
// The parts of the viewer are classic scripts loaded in the order of
// trazo.preview.VIEWER_SCRIPTS; their top-level names are shared globals.
  if (!window.PreviewModel) throw new Error("preview-model.js did not load");  // see preview-page.js
  // The picker: header (filter, quick picks), tree, footer (summary, export).
  const exportFilter = h("input", { type: "search", class: "pv-export-filter", placeholder: "Filter sections",
    autocomplete: "off", "aria-label": "Filter sections" });
  const presets = { all: "Whole document", current: "Current section", last: "Last export" };
  const presetBtns = Object.entries(presets).map(([key, text]) =>
    h("button", { type: "button", class: "pv-export-chip", "data-preset": key, text }));
  const exportClose = h("button", { type: "button", class: "pv-icon-btn sm", "aria-label": "Close", title: "Close (Esc)" });
  exportClose.innerHTML = ICONS.close;
  exportPanel.append(h("div", { class: "pv-export-head" },
    h("div", { class: "pv-export-title" }, h("h2", { id: "pv-export-title", text: "Export sections" }),
      h("span", { class: "pv-export-doc", text: DOCS[doc] }), exportClose),
    h("label", { class: "pv-export-search" }, h("span", { class: "pv-export-search-ic", "aria-hidden": "true" }), exportFilter),
    h("div", { class: "pv-export-presets", role: "group", "aria-label": "Quick selections" }, ...presetBtns)));
  const exportTree = h("ul", { class: "pv-export-tree", role: "tree", "aria-label": "Sections", "aria-multiselectable": "true" });
  const exportEmpty = h("p", { class: "pv-hint pv-export-none", text: "No section matches the filter.", hidden: true });
  const exportSum = h("div", { class: "pv-export-sum" }), exportPills = h("div", { class: "pv-export-pills" });
  const exportStatus = h("p", { class: "pv-export-status", role: "status" });
  const refsBox = h("input", { type: "checkbox", checked: includeRefs });
  const exportClear = h("button", { type: "button", class: "pv-btn ghost", text: "Clear" });
  const exportSubmit = h("button", { type: "button", class: "pv-btn primary pv-export-submit", title: `Export (${MOD}↵)` });
  exportPanel.append(exportTree, exportEmpty, h("div", { class: "pv-export-foot" }, exportSum, exportPills, exportStatus,
    h("div", { class: "pv-export-indexes" }, h("span", { class: "pv-export-indexes-t", text: "Word indexes" }),
      indexPills(() => selectionCounts(), "picker")),
    h("div", { class: "pv-export-row" }, h("label", { class: "pv-export-opt" }, refsBox, " Include references"),
      h("span", { class: "pv-spacer" }), exportClear, exportSubmit)));
  for (const section of exportSections) {
    const caret = h("span", { class: section.kids.length ? "pv-caret" : "pv-caret-sp", "aria-hidden": "true" });
    section.row = h("li", { class: "pv-xnode", role: "treeitem", tabindex: "-1", "data-id": section.id,
      "aria-level": String(section.depth + 1) }, caret, h("span", { class: "pv-xcheck", "aria-hidden": "true" }),
      h("span", { class: "pv-xlabel", text: section.text }),
      h("span", { class: "pv-xwords", text: section.total ? fmt(section.total) : "" }));
    section.row.style.paddingLeft = `${4 + section.depth * 16}px`;
    section.caret = caret;
    exportTree.append(section.row);
  }
  const findSection = (id) => exportSections.find((s) => s.id === id);
  // What the picked sections export: a heading or object belongs to the
  // innermost section around it, so an unticked subsection leaves it out.
  const ownerOf = (el) => exportSections.findLast((s) => s.el.contains(el));
  const selectionCounts = () => ({
    toc: [...selected].filter((s) => levelOf(s.heading) <= 3).length,
    lof: model.items.filter((it) => it.kind === "figure" && selected.has(ownerOf(it.el))).length,
    lot: model.items.filter((it) => it.kind === "table" && selected.has(ownerOf(it.el))).length,
  });
  const lastExport = () => ((prefs.lastExport || {})[doc] || []).map(findSection).filter(Boolean);
  const presetSet = (key) => {
    const here = currentSection();
    return key === "all" ? exportSections : key === "current" ? (here ? branch(here) : []) : lastExport();
  };
  function renderExport() {
    const query = exportFilter.value.trim().toLowerCase(), here = currentSection();
    currentItem.disabled = exporting || !here;
    const shown = new Set();
    for (const section of exportSections) {
      if (query ? !section.text.toLowerCase().includes(query) : false) continue;
      for (let s = section; s; s = s.parent) shown.add(s);
    }
    for (const section of exportSections) {
      const state = stateOf(section), row = section.row;
      let visible = shown.has(section);
      if (!query) for (let p = section.parent; p && visible; p = p.parent) if (folded.has(p)) visible = false;
      row.hidden = !visible;
      row.setAttribute("aria-checked", state === "on" ? "true" : state === "mixed" ? "mixed" : "false");
      row.classList.toggle("on", state === "on");
      row.classList.toggle("here", section === here);
      if (section.kids.length) {
        const open = !!query || !folded.has(section);
        row.setAttribute("aria-expanded", String(open));
        section.caret.setAttribute("aria-expanded", String(open));
      }
    }
    exportEmpty.hidden = !exportSections.length || [...shown].length > 0;
    const { ids } = exportQuery(), starts = ids.map(findSection);
    const words = [...selected].reduce((n, s) => n + s.words, 0);
    exportSum.replaceChildren(...(starts.length
      ? [h("strong", { text: plural(starts.length, "section") }), h("span", { text: `${fmt(words)} words` })]
      : [h("span", { text: "Nothing selected" })]));
    const pill = (section) => {
      const remove = h("button", { type: "button", "aria-label": `Remove ${section.text}`, title: "Remove", disabled: exporting });
      remove.innerHTML = ICONS.close;
      remove.addEventListener("click", () => { for (const s of branch(section)) selected.delete(s); renderExport(); });
      return h("span", { class: "pv-export-pill" },
        h("span", { text: section.text + (stateOf(section) === "mixed" ? " (part)" : "") }), remove);
    };
    exportPills.replaceChildren(...(starts.length > 4
      ? [...starts.slice(0, 3).map(pill), h("span", { class: "pv-export-pill more", text: `+${starts.length - 3} more` })]
      : starts.map(pill)));
    exportPills.hidden = !starts.length;
    for (const button of presetBtns) {
      const want = presetSet(button.dataset.preset);
      button.disabled = exporting || !want.length;
      button.classList.toggle("on", !!want.length && want.length === selected.size && want.every((s) => selected.has(s)));
    }
    exportClear.disabled = exporting || !selected.size;
    refsBox.disabled = exporting;
    renderIndexes();
    exportSubmit.disabled = exporting || !starts.length;
    exportSubmit.textContent = exporting ? "Exporting…" : starts.length ? `Export ${plural(starts.length, "section")}` : "Export DOCX";
  }
  function toggleSection(section) {
    const on = stateOf(section) !== "on";
    for (const s of branch(section)) if (on) selected.add(s); else selected.delete(s);
    exportStatus.textContent = "";
    renderExport();
  }
  function setOpen(section, open) {
    if (open) folded.delete(section); else folded.add(section);
    renderExport();
  }
  exportTree.addEventListener("click", (e) => {
    const row = e.target.closest(".pv-xnode");
    if (!row || exporting) return;
    const section = findSection(row.dataset.id);
    if (e.target.closest(".pv-caret")) setOpen(section, folded.has(section));
    else toggleSection(section);
    row.focus();
  });
  exportTree.addEventListener("keydown", (e) => {
    const rows = $$(".pv-xnode:not([hidden])", exportTree), i = rows.indexOf(document.activeElement);
    if (i < 0) return;
    const section = findSection(rows[i].dataset.id), go = (row) => { if (row) { e.preventDefault(); row.focus(); } };
    if (e.key === "ArrowDown") go(rows[i + 1]);
    else if (e.key === "ArrowUp") { if (i) go(rows[i - 1]); else { e.preventDefault(); exportFilter.focus(); } }
    else if (e.key === "Home") go(rows[0]);
    else if (e.key === "End") go(rows[rows.length - 1]);
    else if ((e.key === " " || e.key === "Enter") && !e.metaKey && !e.ctrlKey) {
      e.preventDefault(); if (!exporting) toggleSection(section);
    } else if (e.key === "ArrowRight" && section.kids.length) {
      e.preventDefault();
      if (folded.has(section)) setOpen(section, true); else go(section.kids[0].row);
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      if (section.kids.length && !folded.has(section) && !exportFilter.value) setOpen(section, false);
      else if (section.parent) section.parent.row.focus();
    }
  });
  exportFilter.addEventListener("input", renderExport);
  exportFilter.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); const row = $(".pv-xnode:not([hidden])", exportTree); if (row) row.focus(); }
  });
  for (const button of presetBtns) button.addEventListener("click", () => {
    selected.clear();
    for (const s of presetSet(button.dataset.preset)) selected.add(s);
    exportStatus.textContent = "";
    renderExport();
  });
  exportClear.addEventListener("click", () => { selected.clear(); exportStatus.textContent = ""; renderExport(); exportFilter.focus(); });
  refsBox.addEventListener("change", () => { includeRefs = refsBox.checked; savePrefs({ exportRefs: includeRefs }); });
  exportClose.addEventListener("click", () => closeExport(true));
  exportSubmit.addEventListener("click", async () => {
    const { ids, exclude } = exportQuery();
    if (!ids.length || exporting) return;
    const count = ids.length;
    const selection = [...selected].map((s) => s.id);
    exportStatus.textContent = "";
    const error = await exportSectionsNow(ids, exclude, wantedIndexes(selectionCounts()));
    if (error) { exportStatus.textContent = `Export failed: ${error}. The selection is kept.`; return; }
    savePrefs({ lastExport: { ...(App.readPrefs(prefs).lastExport || {}), [doc]: selection } });
    closeExport(true);
    toast(`Exported ${plural(count, "section")} as one DOCX.`);
  });

  // Menu and picker placement, opening and closing.
  function placeExport(el) {
    const rect = exportToggle.getBoundingClientRect();
    el.style.left = `${Math.max(8, Math.min(rect.right - el.offsetWidth, window.innerWidth - el.offsetWidth - 8))}px`;
    el.style.top = `${rect.bottom + 6}px`;
    el.style.maxHeight = `${Math.max(160, window.innerHeight - rect.bottom - 14)}px`;
  }
  function closeExport(focus = false) {
    exportMenu.hidden = exportPanel.hidden = true;
    exportToggle.setAttribute("aria-expanded", "false");
    if (focus) exportToggle.focus();
  }
  function openExportMenu() {
    const here = currentSection();
    currentItem.disabled = !here || exporting;
    $(".pv-mi-s", currentItem).textContent = here
      ? `${here.text}${here.total ? ` · ${fmt(here.total)} words` : ""}` : "Scroll to a section first";
    exportPanel.hidden = true;
    exportMenu.hidden = false;
    exportToggle.setAttribute("aria-expanded", "true");
    placeExport(exportMenu);
    renderIndexes();
    const first = menuItems()[0];
    if (first) first.focus();
  }
  function openExportPanel() {
    exportMenu.hidden = true;
    // Show the branch in view; a short document starts fully open.
    folded.clear();
    const here = currentSection();
    if (exportSections.length > 24) for (const s of exportSections) if (s.kids.length && !(here && s.el.contains(here.el))) folded.add(s);
    exportFilter.value = "";
    renderExport();
    exportPanel.hidden = false;
    exportToggle.setAttribute("aria-expanded", "true");
    placeExport(exportPanel);
    exportFilter.focus();
  }
  exportToggle.addEventListener("click", () => {
    if (exportMenu.hidden && exportPanel.hidden) openExportMenu(); else closeExport();
  });
  exportToggle.addEventListener("keydown", (e) => { if (e.key === "ArrowDown") { e.preventDefault(); openExportMenu(); } });
  exportMenu.addEventListener("keydown", (e) => {
    const items = menuItems(), i = items.indexOf(document.activeElement);
    const step = { ArrowDown: 1, ArrowUp: -1 }[e.key];
    if (step) { e.preventDefault(); items[(i + step + items.length) % items.length].focus(); }
    else if (e.key === "Home" || e.key === "End") { e.preventDefault(); items[e.key === "Home" ? 0 : items.length - 1].focus(); }
  });
  $('[data-act="choose"]', exportMenu).addEventListener("click", openExportPanel);
  currentItem.addEventListener("click", async () => {
    const here = currentSection();
    if (!here || exporting) return;
    closeExport(true);
    const error = await exportSectionsNow([here.id], []);
    toast(error ? `Export failed: ${error}` : `Exported ${here.text} as DOCX.`);
  });
  exportBox.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && !exportPanel.hidden) { e.preventDefault(); exportSubmit.click(); }
  });
  exportBox.addEventListener("focusout", (e) => { if (e.relatedTarget && !exportBox.contains(e.relatedTarget)) closeExport(); });
  document.addEventListener("click", (e) => { if (!exportBox.contains(e.target)) closeExport(); });
  docxBtn.addEventListener("click", () => closeExport(true));
  window.addEventListener("resize", () => {
    if (!exportMenu.hidden) placeExport(exportMenu);
    if (!exportPanel.hidden) placeExport(exportPanel);
  });
  // ⌘B builds the manuscript and ⌘E opens the picker (Ctrl elsewhere).
  // Escape closes the menu or picker first, wherever focus went.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !(exportMenu.hidden && exportPanel.hidden)) {
      e.preventDefault(); e.stopPropagation(); closeExport(true);
    }
  }, true);
  document.addEventListener("keydown", (e) => {
    if (!(e.metaKey || e.ctrlKey) || e.shiftKey || e.altKey || lbIndex() >= 0) return;
    if (e.target.closest && e.target.closest("textarea, input, select, [contenteditable]")) return;
    const key = e.key.toLowerCase();
    if (key === "e") { e.preventDefault(); openExportPanel(); }
    else if (key === "b") { e.preventDefault(); docxBtn.click(); }
  });

  function exportButtons(name, kind, id, source, singleImage) {
    const formats = kind === "table" ? ["docx", "md"] : ["docx", "png", "pdf", "svg"];
    const row = h("span", { class: "pv-exports", "aria-label": "Export " + kind }, h("span", { text: "Export:" }));
    for (const format of formats) {
      const button = h("button", { type: "button", text: format === "md" ? "Markdown" : format.toUpperCase() });
      if (format === "svg") {
        button.disabled = true;
        button.title = singleImage ? "SVG requires a vector source" : "SVG requires a single-image figure";
        if (singleImage) fetch(`/__export-formats?src=${encodeURIComponent(source || "")}`)
          .then((r) => r.ok ? r.json() : null).then((data) => {
            if (data && data.formats.includes("svg")) { button.disabled = false; button.title = "Export the vector source with its caption"; }
          }).catch(() => {});
      }
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          await downloadExport(name, kind, id, format);
        } catch (e) { toast("Export failed: " + e.message); }
        finally { button.disabled = false; }
      });
      row.append(button);
    }
    return row;
  }
  // Exports live on the Figures & Tables cards, not in the page.
  for (const it of model.items) {
    const cap = it.kind === "figure" && it.el.querySelector(":scope > figcaption");
    if (!cap) continue;
    const button = h("button", { type: "button", class: "pv-tbl-edit", text: it.composite ? "Edit source" : "Edit caption" });
    button.addEventListener("click", () => editFigureCaption(it)); cap.append(button);
  }
  // Reopen after live reload or a gallery action in another document.
  {
    const id = sget(figureOpenKey(doc)), item = id && model.items.find((it) => it.id === id && !it.composite);
    if (item) editFigureCaption(item, true); else if (id) sdel(figureOpenKey(doc));
  }
