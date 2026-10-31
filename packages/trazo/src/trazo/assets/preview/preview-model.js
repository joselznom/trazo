// Document model of the preview, shared by the page and the Figures & Tables
// view. Everything here works on any element tree (the live page or another
// document parsed with DOMParser), so labels, figures and citations come out
// the same for every document.
(function () {
  const squash = (text) => text.replace(/\s+/g, " ").trim();
  const inside = (root, el, selector) => {
    const hit = el.closest(selector);
    return !!hit && root.contains(hit);
  };

  // Paragraph labels, numbered within the nearest heading. Labels are
  // unique per document: a repeated heading text gets " (2)", " (3)", ...
  // and list items are numbered separately ("Heading list 2"). Stored
  // review comments refer to these labels, so the scheme must not change.
  // Sets data-label on each labelled element; returns [{el, label, kind, n}]
  // with kind "para" (n = number), "list", "caption" (figcaption) or "table".
  function label(root) {
    const seenHeading = {}, seenLabel = {};
    const unique = (text) => {
      seenLabel[text] = (seenLabel[text] || 0) + 1;
      return seenLabel[text] > 1 ? `${text} (${seenLabel[text]})` : text;
    };
    const entries = [];
    let heading = "", n = 0, items = 0;
    for (const el of root.querySelectorAll("h1, h2, h3, h4, p, li, figcaption, table")) {
      if (inside(root, el, "header, #refs, .pk-front, .pk-keywords, .pk-form-badge")) continue;
      if (/^H\d$/.test(el.tagName)) {
        const text = el.textContent.trim();
        seenHeading[text] = (seenHeading[text] || 0) + 1;
        heading = seenHeading[text] > 1 ? `${text} (${seenHeading[text]})` : text;
        n = 0; items = 0;
        continue;
      }
      if (el.tagName === "FIGCAPTION" || el.tagName === "TABLE") {
        const caption = el.tagName === "TABLE" ? el.querySelector("caption") : el;
        const name = caption ? (caption.textContent.match(/^\s*(\S+\s+\S+?)\.?\s/) || [])[1] : "";
        el.dataset.label = unique(name ? `${name}${el.tagName === "TABLE" ? "" : " caption"}` : heading);
        entries.push({ el, label: el.dataset.label, kind: el.tagName === "TABLE" ? "table" : "caption", host: caption });
        continue;
      }
      if (inside(root, el, "figure, table")) continue;
      if (el.tagName === "LI") {
        if (!el.closest("li li")) {
          items += 1;
          el.dataset.label = unique(`${heading} list ${items}`);
          entries.push({ el, label: el.dataset.label, kind: "list", host: el });
        }
        continue;
      }
      if (el.closest("li") || !el.textContent.trim()) continue;
      n += 1;
      el.dataset.label = unique(`${heading} ¶${n}`);
      entries.push({ el, label: el.dataset.label, kind: "para", n });
    }
    return entries;
  }

  const titleOf = (s) => s.querySelector("h1, h2").textContent.trim();

  // Units are the top-level sections (the stored comments name them); a
  // document with a single top-level section (the supplement) is split at
  // its second level instead. The references section is no unit.
  function sections(root) {
    const refs = root.querySelector("#refs");
    const container = refs ? refs.closest("section.level1") : null;
    const refsSection = container && [...container.children].every((el) =>
      el === refs || /^H[1-6]$/.test(el.tagName)) ? container : null;
    const topSections = [...root.querySelectorAll(":scope > section.level1")]
      .filter((s) => s !== refsSection);
    let units = topSections;
    if (units.length === 1) {
      const inner = [...units[0].querySelectorAll(":scope > section.level2")];
      if (inner.length) units = inner;
    }
    return { refsSection, topSections, units };
  }

  // Headings h1..h6 in document order with their words: own (`words`), with
  // the sub-headings (`total`) and, for main text, what they add to it
  // (`main`; `counted` is false outside the main text). The server counts
  // them (trazo's counts.py, window.PREVIEW_COUNTS); without those counts,
  // or if the headings differ from them, `known` is false and all are 0.
  function outline(root, counts) {
    const heads = [...root.querySelectorAll("h1, h2, h3, h4, h5, h6")]
      .filter((el) => !inside(root, el, "header, .pk-front, .pk-keywords"))
      .map((el) => ({ el, level: Number(el.tagName[1]), text: el.textContent.trim(), words: 0, total: 0,
        counted: true, main: 0 }));
    const given = (counts && counts.heads) || [];
    heads.known = given.length === heads.length && given.every((x, i) => squash(x.text) === squash(heads[i].text));
    if (heads.known) heads.forEach((head, i) => { head.words = given[i].words; head.counted = given[i].counted; });
    heads.forEach((head, i) => {
      head.total = head.words;
      head.main = head.counted ? head.words : 0;
      for (let j = i + 1; j < heads.length && heads[j].level > head.level; j++) {
        head.total += heads[j].words;
        if (heads[j].counted) head.main += heads[j].words;
      }
    });
    return heads;
  }

  // Figures (<figure id="fig:...">) and tables (<table id="tbl:...">), as
  // the cross-reference pass writes them; the caption starts with "Figure 1." etc.
  // An image's own path: an MCP App view shows it as a data:
  // URL and keeps the path in data-trazo-src.
  const srcOf = (img) => img.dataset.trazoSrc || img.getAttribute("src") || "";
  function diagramOf(src) {
    let path;
    try { path = decodeURIComponent(src).replace(/^(?:\.\/|\/)/, "").split(/[?#]/)[0]; }
    catch (e) { return null; }
    const links = window.PREVIEW_DIAGRAMS || { names: [] };
    if (links.sources?.[path]) return links.sources[path];
    // Older generated pages supplied names only; keep their links working.
    const output = window.TRAZO_CONFIG?.paths.diagram_output || "figures/external";
    if (!path.startsWith(output + "/")) return null;
    const name = path.slice(output.length + 1).replace(/\.(?:png|svg|pdf)$/, "");
    return links.names.includes(name) ? name : null;
  }
  function items(root, doc) {
    const out = [];
    for (const el of root.querySelectorAll('figure[id^="fig:"], table[id^="tbl:"]')) {
      if (inside(root, el, "header, #refs, .pk-front, .pk-keywords") || (el.parentElement && el.parentElement.closest('figure[id^="fig:"]'))) continue;
      const figure = el.tagName === "FIGURE";
      const cap = figure ? el.querySelector(":scope > figcaption") : el.querySelector("caption");
      const content = cap ? cap.cloneNode(true) : root.ownerDocument.createElement("span");
      content.querySelectorAll("button, .pv-exports, .pv-tbl-edit").forEach((n) => n.remove());
      const text = squash(content.textContent);
      // "Figure 1." or, with the document's title prefix, "Supplementary Figure 1.".
      const words = 1 + ((window.PREVIEW_TITLE_PREFIXES || {})[doc] || "").split(/\s+/).filter(Boolean).length;
      const m = text.match(new RegExp(`^((?:\\S+\\s+){${words}}\\S+?)\\.?(?:\\s+|$)`));
      // Keep the caption's inline Markdown formatting in cards and the viewer.
      // Remove the generated number across text nodes without flattening it.
      let remaining = m ? (content.textContent.match(new RegExp(`^\\s*(?:\\S+\\s+){${words}}\\S+?\\.?(?:\\s+|$)`)) || [""])[0].length : 0;
      const walker = content.ownerDocument.createTreeWalker(content, NodeFilter.SHOW_TEXT);
      while (remaining && walker.nextNode()) {
        const node = walker.currentNode, count = Math.min(remaining, node.length);
        node.deleteData(0, count); remaining -= count;
      }
      // A table's note is the ::: table-note block right after it (or after
      // the preview's scroll wrapper).
      const after = figure ? null : (el.closest(".pv-tscroll") || el).nextElementSibling;
      const note = after && after.matches("div.table-note") ? after : null;
      const img = figure ? el.querySelector("img") : null;
      const composite = figure && el.classList.contains("subfigures");
      const src = img ? srcOf(img) : "";
      const num = m ? m[1] : el.id.replace(/^\w+:/, "");
      const prefix = (window.PREVIEW_NUMBER_PREFIXES || {})[doc] || "";
      const space = num.lastIndexOf(" "), token = num.slice(space + 1);
      // Older review comments may quote the number from before the document
      // gained its supplementary prefix. Keep that alias beside the label.
      const legacyNum = prefix && token.startsWith(prefix) && /^\d/.test(token.slice(prefix.length))
        ? num.slice(0, space + 1) + token.slice(prefix.length) : num;
      out.push({
        doc, id: el.id, el, kind: figure ? "figure" : "table",
        num, legacyNum,
        caption: m ? text.slice(m[0].length) : text,
        captionHtml: content.innerHTML,
        note: note ? [...note.children].map((p) => squash(p.textContent)).filter(Boolean).join(" ") : "",
        noteHtml: note ? note.innerHTML : "",
        src, composite, diagram: composite ? null : diagramOf(src), hasCaption: !!cap,
        cites: [],
      });
    }
    return out;
  }

  // Places that cite each figure or table: links `#<id>` in the text (the
  // preview build links cross-references), one per labelled
  // element. `at` is the label of the citing element, if it has one.
  function citations(root, list) {
    const byId = new Map(list.map((it) => [it.id, it]));
    for (const a of root.querySelectorAll('a[href^="#"]')) {
      let id = a.getAttribute("href").slice(1);
      try { id = decodeURIComponent(id); } catch (e) { /* keep as is */ }
      const item = byId.get(id);
      if (!item || item.el.contains(a) || inside(root, a, "#refs")) continue;
      cite(root, item, a, "");
    }
  }
  function cite(root, item, a, from) {
    const host = a.closest("[data-label]");
    const at = host && root.contains(host) ? host : null;
    const sect = a.closest("section");
    const head = sect && sect.querySelector("h1, h2, h3, h4");
    const where = at ? at.dataset.label : head ? head.textContent.trim() : "Document";
    if (item.cites.some((c) => c.el === (at || a))) return;
    const title = from ? ((window.PREVIEW_TITLES || {})[from] || from) + " › " : "";
    item.cites.push({ el: at || a, link: a, label: title + where, at: at ? at.dataset.label : "", from });
  }
  // References from one document to another link to "<doc>.html#<id>".
  // Run again whenever a document is reread.
  function crossCitations(analyses) {
    for (const a of Object.values(analyses)) for (const it of a.items) it.cites = it.cites.filter((c) => !c.from);
    for (const a of Object.values(analyses)) {
      for (const link of a.root.querySelectorAll('a[href*=".html#"]')) {
        const m = link.getAttribute("href").match(/^([^/?#]+)\.html#(.+)$/);
        const target = m && m[1] !== a.doc && analyses[m[1]];
        if (!target) continue;
        let id = m[2];
        try { id = decodeURIComponent(id); } catch (e) { /* keep as is */ }
        const item = target.items.find((it) => it.id === id);
        if (item) cite(a.root, item, link, a.doc);
      }
    }
  }

  // Everything the viewer needs to know about one document body.
  function analyse(root, doc) {
    const entries = label(root);
    const found = items(root, doc);
    citations(root, found);
    return { doc, root, entries, items: found, ...sections(root),
      labelled: [...root.querySelectorAll("[data-label]")] };
  }

  function parse(html, doc) {
    return analyse(new DOMParser().parseFromString(html, "text/html").body, doc);
  }

  window.PreviewModel = { squash, label, sections, titleOf, outline, items, citations, crossCitations, analyse, parse, diagramOf, srcOf };
})();
