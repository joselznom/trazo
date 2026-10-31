// Preview viewer: the Figures & Tables view: cards, thumbnails, captions and notes.
// The parts of the viewer are classic scripts loaded in the order of
// trazo.preview.VIEWER_SCRIPTS; their top-level names are shared globals.
  if (!window.PreviewModel) throw new Error("preview-model.js did not load");  // see preview-page.js
  // The view shows the figures and tables of every document. The current
  // document comes from the live page, the others are fetched as pages and
  // read with the same model, so labels and numbers match.
  let versions = {}, figsLoaded = false, figsSeq = 0;
  let resolveStatus;
  const statusReady = new Promise((resolve) => { resolveStatus = resolve; });
  const fetched = {};  // name -> {v, p}
  let catalogState = { diagrams: [] }, catalogStamp = null, reviewCounts = {};
  function loadDoc(name) {
    if (name === doc) return Promise.resolve(model);
    const v = versions[name], hit = fetched[name];
    if (hit && hit.v === v) return hit.p;
    const p = fetch(`/${name}.html`, { cache: "no-store" })
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
      .then((text) => Model.parse(text, name));
    fetched[name] = { v, p };
    p.then((a) => { analyses[name] = a; }, () => { delete fetched[name]; delete analyses[name]; });
    return p;
  }
  async function loadAll() {
    await statusReady;
    await Promise.all([...docNames.map((name) => loadDoc(name).catch(() => null)),
      App.api("/__catalog").then(result => {
        if (!result.ok) return;
        catalogState = result.data;
        Object.assign(DIAGRAMS, result.data.links);
        window.PREVIEW_DIAGRAMS = DIAGRAMS;
        for (const a of Object.values(analyses)) for (const item of a.items) item.diagram = item.composite ? null : Model.diagramOf(item.src);
        diagramButtons();
      }).catch(() => null),
      App.api("/__review").then(result => {
        if (!result.ok) return;
        reviewCounts = {};
        for (const c of result.data.comments) if (c.status === "open" && c.kind !== "underline") {
          const key = `${c.scope}:${c.name}`;
          reviewCounts[key] = (reviewCounts[key] || 0) + 1;
        }
        tableComments = Object.fromEntries(Object.entries(reviewCounts).filter(([key]) => key.startsWith("table:")).map(([key, count]) => [key.slice(6), count]));
      }).catch(() => null)]);
    Model.crossCitations(analyses);
    figsLoaded = true;
    showFigsCount(figItems().length);
    // Rebuilt documents can move or remove quoted text without changing
    // comments.json. Relocate comments while preserving any inline draft.
    if (view === "figures") renderComments();
  }
  const figItems = () => docNames.flatMap((name) => (analyses[name] ? analyses[name].items : []));
  // The Figures & Tables count needs every document; until they are fetched
  // the tab shows the last count seen in this tab.
  function showFigsCount(n) {
    const badge = $(".pv-badge", figsTab);
    badge.hidden = n === 0;
    badge.textContent = String(n);
    sset("preview-figs-count", String(n));
  }
  const lastFigsCount = Number(sget("preview-figs-count"));
  if (sget("preview-figs-count") !== null && Number.isInteger(lastFigsCount)) showFigsCount(lastFigsCount);
  const filter = { kind: "all", doc: "all", uncited: false };
  let figsShown = [];

  function setFilter(spec) {
    const [group, value] = spec.split(":");
    if (group === "kind") filter.kind = value;
    else if (group === "doc") filter.doc = value;
    else filter.uncited = !filter.uncited;
    renderFigs();
  }

  // Thumbnails: the image, or a scaled-down copy of the table (without ids
  // and viewer additions, so nothing in the page is duplicated).
  function tableCopy(it) {
    const copy = it.el.cloneNode(true);
    for (const el of [copy, ...copy.querySelectorAll("*")]) {
      el.removeAttribute("id");
      el.removeAttribute("data-label");
    }
    copy.querySelectorAll("caption, button").forEach((n) => n.remove());
    return copy;
  }
  // The label already says Figure or Table; add a word only when it tells more.
  const isDiagram = (it) => !!it.diagram;
  const kindOf = (it) => (isDiagram(it) ? "Diagram" : "");

  function renderFilters(all) {
    const nF = all.filter((i) => i.kind === "figure").length, nT = all.length - nF;
    const uncited = all.filter((i) => !i.cites.length).length;
    leftTitle.textContent = "Filters";
    leftSub.textContent = `${plural(nF, "figure")} · ${plural(nT, "table")}`;
    const group = (title, rows) => {
      const g = h("div", { class: "pv-fgroup" }, h("h3", { text: title }));
      for (const [spec, on, text, sw, n] of rows) {
        g.append(h("button", { type: "button", class: "pv-fbtn", "data-f": spec, "aria-pressed": String(on) },
          h("span", { class: `pv-sw ${sw}` }), text, h("span", { class: "n", text: String(n) })));
      }
      return g;
    };
    leftBody.replaceChildren(
      group("Type", [["kind:all", filter.kind === "all", "All", "", all.length], ["kind:figure", filter.kind === "figure", "Figures", "", nF],
        ["kind:table", filter.kind === "table", "Tables", "", nT]]),
      group("Document", [["doc:all", filter.doc === "all", "All documents", "", all.length],
        ...docNames.map((name) => [`doc:${name}`, filter.doc === name, DOCS[name], chips[name].cls,
          all.filter((i) => i.doc === name).length])]),
      group("Check", [["uncited", filter.uncited, "Not cited", "warn", uncited]]));
  }

  async function renderFigs() {
    const seq = ++figsSeq;
    if (!figsLoaded) {
      figsView.replaceChildren(h("p", { class: "pv-fempty", text: "Loading figures and tables…" }));
      leftTitle.textContent = "Filters";
      leftSub.textContent = "";
      leftBody.replaceChildren();
      await loadAll();
      if (seq !== figsSeq || view !== "figures") return;
      renderWords();
    }
    const all = figItems();
    renderFilters(all);
    const nF = all.filter((i) => i.kind === "figure").length;
    const uncited = all.filter((i) => !i.cites.length);
    const keepOpen = new Set($$(".pv-fcard", figsView).filter((c) => !$(".pv-cited", c).hidden).map((c) => c.dataset.key));
    const list = all.filter((it) => (filter.kind === "all" || it.kind === filter.kind)
      && (filter.doc === "all" || it.doc === filter.doc) && (!filter.uncited || !it.cites.length));
    figsShown = [];
    const sum = h("div", { class: "sum", text: `${plural(nF, "figure")} · ${plural(all.length - nF, "table")} · ` });
    sum.append(uncited.length ? h("b", { text: `${uncited.length} not cited` }) : "0 not cited");
    const seg = (name, key, opts) => {
      const d = h("div", { class: "pv-seg", role: "group", "aria-label": name });
      for (const [value, text] of opts) {
        d.append(h("button", { type: "button", "data-f": `${key}:${value}`, text,
          "aria-pressed": String(key === "kind" ? filter.kind === value : filter.doc === value) }));
      }
      return d;
    };
    figsView.replaceChildren(
      h("div", { class: "pv-figs-head" }, h("h2", { text: "Figures & Tables" }),
        h("div", { class: "pv-figs-meta" }, sum,
          h("a", { class: "pv-btn sm", href: "/diagrams", title: "Open the diagram editor", text: "All diagrams" }))),
      h("div", { class: "pv-inline-filters" },
        seg("Type", "kind", [["all", "All"], ["figure", "Figures"], ["table", "Tables"]]),
        seg("Document", "doc", [["all", "All"], ...docNames.map((n) => [n, chips[n].text])]),
        h("div", { class: "pv-seg" }, h("button", { type: "button", "data-f": "uncited", text: "Not cited", "aria-pressed": String(filter.uncited) }))));
    if (!list.length) figsView.append(h("p", { class: "pv-fempty", text: all.length ? "Nothing matches these filters." : "No figures or tables in the documents." }));
    for (const name of docNames) {
      const items = list.filter((i) => i.doc === name).sort((a, b) => (a.kind === b.kind ? 0 : a.kind === "figure" ? -1 : 1));
      if (!items.length) continue;
      const { cls } = chips[name];
      const sec = h("section", { class: `pv-fsec ${cls}`, "aria-label": DOCS[name] },
        h("div", { class: "pv-fsec-head" }, chipEl(name), h("h3", { text: DOCS[name] }),
          h("span", { class: "sub-t", text: plural(items.length, "item") })));
      const grid = h("div", { class: "pv-fgrid" });
      for (const it of items) {
        figsShown.push(it);
        grid.append(figCard(it, cls, keepOpen));
      }
      sec.append(grid);
      figsView.append(sec);
    }
    const used = new Set(all.map(it => it.diagram).filter(Boolean));
    const outside = catalogState.diagrams.filter(item => !used.has(item.name));
    if (outside.length && filter.kind !== "table" && filter.doc === "all") {
      const section = h("section", { class: "pv-fsec pk-drafts" }, h("h3", { text: "Diagrams outside documents" }));
      for (const item of outside) section.append(h("div", { class: "pk-resource" },
        h("b", { text: item.name }), h("span", { class: "pv-chip", text: item.export_state }),
        h("a", { class: "pv-btn sm", href: diagramHref(item.name), text: "Edit diagram" })));
      figsView.append(section);
    }
  }

  // Removes a caption's leading [Title.]{.caption-title} and returns it. The
  // number before it was already taken out, which can leave empty nodes.
  function takeTitle(caption) {
    const blank = (n) => !n.textContent.trim() && !(n.nodeType === 1 && n.querySelector("img, svg, math"));
    const first = [...caption.childNodes].find((n) => !blank(n));
    if (!first || first.nodeType !== 1 || !first.classList.contains("caption-title")) return null;
    while (caption.firstChild !== first) caption.firstChild.remove();
    first.remove();
    while (caption.firstChild && blank(caption.firstChild)) caption.firstChild.remove();
    if (caption.firstChild && caption.firstChild.nodeType === 3) caption.firstChild.textContent = caption.firstChild.textContent.trimStart();
    return first;
  }
  // Caption or note HTML of an item, its links kept to the item's document.
  function captionView(it, className, tag = "p", html = it.captionHtml) {
    const caption = h(tag, { class: className });
    caption.innerHTML = html;
    for (const link of $$("a[href^='#']", caption)) {
      const fragment = link.getAttribute("href");
      let id = fragment.slice(1);
      try { id = decodeURIComponent(id); } catch (e) { /* keep as is */ }
      // Keep native modified clicks pointed at the caption's own document.
      link.setAttribute("href", `/${it.doc}.html${fragment}`);
      link.addEventListener("click", (e) => {
        if (e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey
            || (link.target && link.target !== "_self")) return;
        const target = it.doc === doc && document.getElementById(id);
        if (it.doc === doc && (!target || !page.contains(target))) return;
        e.preventDefault();
        closeLb();
        if (target) goToEl(target); else openDoc(it.doc, { hash: id });
      });
    }
    return caption;
  }

  // A table's note under its caption (its paragraphs read as one line).
  function noteView(it, className) {
    if (!it.note) return null;
    const note = captionView(it, className, "div", it.noteHtml);
    note.title = it.note;
    return note;
  }

  function figCard(it, cls, keepOpen) {
    const key = `${it.doc}|${it.id}`;
    const art = h("div", { class: "pv-art" + (it.kind === "table" ? " t" : "") });
    if (it.kind === "figure") {
      const images = it.composite ? $$("img", it.el).map((img) => img.getAttribute("src")) : [it.src];
      art.classList.toggle("multiple", images.length > 1);
      for (const src of images) art.append(h("img", { src, alt: "", loading: "lazy" }));
    }
    else art.append(h("div", { class: "pv-mini", "aria-hidden": "true" }, tableCopy(it)));
    const thumb = h("button", { type: "button", class: `pv-thumb ${it.kind}`, "data-a": "zoom", "aria-label": `Preview ${it.num}` },
      art, h("span", { class: "pv-zoom", "aria-hidden": "true", text: "Preview" }));
    const row = h("div", { class: "pv-frow" }, kindOf(it) ? h("span", { class: "pv-fkind", text: kindOf(it) }) : null);
    if (!it.cites.length) row.append(h("span", { class: "pv-chip warn", title: `This ${it.kind} is never referenced in the text`, text: "⚠ Not cited" }));
    if (isDiagram(it)) {
      const diagram = catalogState.diagrams.find(item => item.name === it.diagram);
      if (diagram) row.append(h("span", { class: "pv-chip" + (diagram.export_state === "current" ? "" : " warn"),
        text: diagram.export_state === "current" ? "Export current" : diagram.export_state === "draft" ? "Draft" : "Export pending" }));
      const count = reviewCounts[`diagram:${it.diagram}`];
      if (count) row.append(h("span", { class: "pv-chip", text: plural(count, "open comment"), title: "Comments in the diagram editor" }));
    }
    const open = keepOpen.has(key) && it.cites.length > 0;
    const listId = "pv-cited-" + key.replace(/[^\w-]/g, "_");
    const acts = h("div", { class: "pv-facts" }, h("button", { type: "button", class: "pv-btn sm", "data-a": "go", text: "Go to text" }));
    const tlabel = it.kind === "table" ? it.id.slice(4) : "";
    if (TABLE_SPECS.includes(tlabel)) {
      acts.append(h("a", { class: "pv-btn sm", href: tableHref(tlabel), title: tableTitle(tlabel), text: "Edit table" }));
      if (tableComments[tlabel]) row.append(h("span", { class: "pv-chip", title: "Open comments in the table editor", text: plural(tableComments[tlabel], "open comment") }));
    }
    if (it.kind === "table" && it.hasCaption) {
      acts.append(h("button", { type: "button", class: "pv-btn sm", "data-a": "edit-caption", text: "Edit caption" }));
    }
    if (it.kind === "figure") {
      const button = h("button", { type: "button", class: "pv-btn sm", text: it.composite ? "Edit source" : "Edit caption" });
      button.addEventListener("click", () => editFigureCaption(it)); acts.append(button);
    }
    acts.append(exportButtons(it.doc, it.kind, it.id, it.src, it.el.querySelectorAll("img").length === 1));
    if (isDiagram(it)) acts.append(h("a", { class: "pv-btn sm", href: diagramHref(it.diagram, Trazo.path() + Trazo.search() + "#" + it.id), title: diagramTitle(it.diagram), text: "Edit diagram" }));
    acts.append(h("button", { type: "button", class: "pv-btn sm ghost", "data-a": "cited", "aria-expanded": String(open), "aria-controls": listId,
      disabled: !it.cites.length, text: `Cited in (${it.cites.length}) ${open ? "▴" : "▾"}` }));
    const ul = h("ul", { class: "pv-cited", id: listId, hidden: !open });
    it.cites.forEach((c, i) => ul.append(h("li", null, h("button", { type: "button", "data-a": "goto-cite", "data-i": String(i) },
      h("span", { class: "pv-where", text: c.label }), h("span", { "aria-hidden": "true", text: "→" })))));
    const caption = captionView(it, "pv-fcap");
    caption.title = it.caption;
    // The title reads with the number, so figures are easy to tell apart;
    // the legend stays below.
    const title = takeTitle(caption);
    const head = h("p", { class: "pv-fhead" }, chipEl(it.doc), h("strong", { class: "pv-flabel", text: it.num }),
      title ? h("span", { class: "pv-ftitle" }, ...title.childNodes) : null);
    const body = h("div", { class: "pv-fbody" }, head, row.childNodes.length ? row : null,
      caption.textContent.trim() ? caption : null, noteView(it, "pv-fnote"), acts, ul);
    if (!it.cites.length) body.append(h("p", { class: "pv-nocite", text: "Not referenced anywhere in the text." }));
    return h("article", { class: `pv-fcard ${cls}`, "data-key": key }, thumb, body);
  }

  const itemOfCard = (card) => figItems().find((it) => `${it.doc}|${it.id}` === card.dataset.key);
  function goToFigure(it) {
    if (it.doc === doc) goToEl(it.el);
    else openDoc(it.doc, { hash: it.id });
  }
  function goToCite(it, c) {
    const where = c.from || it.doc;  // a citation from another document is there
    if (where === doc) goToEl(c.el, c.el);
    else openDoc(where, c.at ? { at: c.at } : c.from ? null : { hash: it.id });
  }
  // Table caption editing happens in the page of the table's document.
  function editTableCaption(it) {
    const label = it.id.slice(4);
    if (it.doc === doc) {
      setView("doc", true);
      openTableEditor(it.el, label);
      const st = tableEditors.get(tableEditorKey(label));
      goToEl(st ? st.box : it.el);
    } else {
      sset(tableOpenKey(it.doc), tableEditorKey(label));
      openDoc(it.doc, { hash: it.id });
    }
  }
  figsView.addEventListener("click", (e) => {
    const f = e.target.closest("[data-f]");
    if (f) { setFilter(f.dataset.f); return; }
    const btn = e.target.closest("[data-a]");
    const card = btn && btn.closest(".pv-fcard");
    if (!card) return;
    const it = itemOfCard(card), a = btn.dataset.a;
    if (!it) return;
    if (a === "zoom") openLb(figsShown.indexOf(it), btn);
    else if (a === "go") goToFigure(it);
    else if (a === "edit-caption") editTableCaption(it);
    else if (a === "cited") {
      const list = $(".pv-cited", card), open = btn.getAttribute("aria-expanded") !== "true";
      btn.setAttribute("aria-expanded", String(open));
      list.hidden = !open;
      btn.textContent = `Cited in (${it.cites.length}) ${open ? "▴" : "▾"}`;
    } else if (a === "goto-cite") goToCite(it, it.cites[Number(btn.dataset.i)]);
  });
