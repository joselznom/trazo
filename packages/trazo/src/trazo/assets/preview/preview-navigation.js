// Preview viewer: the index tree and the top bar's tabs.
// The parts of the viewer are classic scripts loaded in the order of
// trazo.preview.VIEWER_SCRIPTS; their top-level names are shared globals.
  if (!window.PreviewModel) throw new Error("preview-model.js did not load");  // see preview-page.js
  // ---------------------------------------------------------------- index
  // Counts as journals do: main-text words (without the abstract,
  // declarations, ...), figures, tables and references. The server makes
  // them (trazo's counts.py, with trazo.toml [counts] and the [limits]
  // of the first document); a page served without them shows none.
  const COUNTS = window.PREVIEW_COUNTS || null;
  const outlineHeads = Model.outline(page, COUNTS);
  const tally = COUNTS || {};
  const limits = (COUNTS && COUNTS.limits) || {};
  const hasLimit = (key) => limits[key] > 0;
  const missing = (key) => tally[key] == null;
  const overLimit = (key) => hasLimit(key) && !missing(key) && tally[key] > limits[key];
  // ok, near (90% of the limit or more), over or missing (a limit with
  // nothing to count); "" without a limit.
  const limitState = (key) => !hasLimit(key) ? "" : missing(key) ? "missing" : overLimit(key) ? "over"
    : tally[key] >= 0.9 * limits[key] ? "near" : "ok";
  const leftText = (key) => {  // the state in words, so it never rests on colour alone
    if (missing(key)) return "Not found: no Abstract heading or abstract: metadata";
    const d = limits[key] - tally[key];
    return d < 0 ? `${fmt(-d)} over the limit` : d === 0 ? "At the limit" : `${fmt(d)} left`;
  };
  const COUNT_ROWS = COUNTS?.total ? [["words", "Total", "words"]] : [["words", "Main text", "words"], ["abstract", "Abstract", "words"],
    ["figures", "Figures"], ["tables", "Tables"], ["references", "References"]];
  const countText = (key) => (missing(key) ? "—" : fmt(tally[key])) + (hasLimit(key) ? ` / ${fmt(limits[key])}` : "");
  const countsTitle = !COUNTS ? "" : [
    ...COUNT_ROWS.filter(([key]) => !missing(key) || hasLimit(key)).map(([key, name, unit]) =>
      `${name}: ${countText(key)}${unit ? ` ${unit}` : ""}${hasLimit(key) ? ` (${leftText(key).toLowerCase()})` : ""}`),
    COUNTS.left_out.length
      ? `Not in the main text: ${COUNTS.left_out.map(([text, n]) => `${text} (${fmt(n)})`).join(", ")}` : "",
  ].filter(Boolean).join("\n");
  // Words as meters (a bar only with a limit), figures, tables and references
  // as tiles. Without a limit a count is just the number. Built once: the
  // counts change only with a new page.
  const countsBox = (() => {
    if (!COUNTS) return null;
    const box = h("section", { class: "pv-counts", "aria-label": "Counts", title: countsTitle });
    for (const [key, name, unit] of COUNT_ROWS.slice(0, 2)) {
      if (missing(key) && !hasLimit(key)) continue;
      const meter = h("div", { class: `pv-meter pv-meter-${key} ${limitState(key)}`.trim(), "data-count": key },
        h("div", { class: "pv-meter-head" }, h("span", { class: "pv-meter-name", text: name }),
          h("span", { class: "pv-meter-val" }, h("b", { text: missing(key) ? "—" : fmt(tally[key]) }),
            hasLimit(key) ? ` / ${fmt(limits[key])} ${unit}` : ` ${unit}`)));
      if (hasLimit(key)) {
        if (!missing(key)) {
          meter.append(h("div", { class: "pv-meter-bar", role: "meter", "aria-label": name, "aria-valuemin": "0",
            "aria-valuemax": String(limits[key]), "aria-valuenow": String(tally[key]) },
            h("i", { style: `width: ${(Math.min(1, tally[key] / limits[key]) * 100).toFixed(1)}%` })));
        }
        meter.append(h("div", { class: "pv-meter-note", text: leftText(key) }));
      }
      box.append(meter);
    }
    if (COUNTS.total) return box;
    const tiles = h("div", { class: "pv-tiles" });
    for (const [key, name] of COUNT_ROWS.slice(2)) {
      tiles.append(h("div", { class: `pv-tile ${limitState(key)}`.trim(), "data-count": key,
        title: hasLimit(key) ? `${name}: ${countText(key)} (${leftText(key).toLowerCase()})` : `${name}: no limit set` },
        h("span", { class: "pv-tile-val" }, h("b", { text: fmt(tally[key]) }),
          hasLimit(key) ? h("span", { text: ` / ${fmt(limits[key])}` }) : null),
        h("span", { class: "pv-tile-name", text: name }),
        hasLimit(key) && limitState(key) !== "ok" ? h("span", { class: "pv-tile-note",
          text: overLimit(key) ? `${fmt(tally[key] - limits[key])} over` : leftText(key) }) : null));
    }
    box.append(tiles);
    if (doc === docNames[0] && !COUNT_ROWS.some(([key]) => hasLimit(key))) {
      box.append(h("p", { class: "pv-counts-hint", text: "No journal limits set. Add them under [limits] in trazo.toml." }));
    }
    return box;
  })();
  const wordsOf = () => (view === "figures" || !COUNTS ? "" : `${countText("words")} words`);
  let activeHead = -1, ixButtons = [], lastLocated = [];
  const collapsed = new Set();
  const badgeOf = (c) => c.status === "open" ? "open" : "review";

  // Comment counts per heading (own text plus sub-headings), live comments only.
  function headingCounts() {
    const count = outlineHeads.map(() => ({ open: 0, review: 0 }));
    const indexOfEl = (el) => {
      let at = -1;
      for (let i = 0; i < outlineHeads.length; i++) {
        if (outlineHeads[i].el.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) at = i; else break;
      }
      return at;
    };
    for (const { c, hit } of lastLocated) {
      if (c.kind === "underline" || (c.status !== "open" && c.status !== "review")) continue;
      let at = -1;
      if (hit) at = indexOfEl(hit.el);
      else {
        const u = units.find((x) => titleOf(x) === c.section);
        const head = u && u.querySelector("h1, h2");
        at = head ? outlineHeads.findIndex((x) => x.el === head) : -1;
      }
      if (at >= 0) count[at][badgeOf(c)] += 1;
    }
    return outlineHeads.map((head, i) => {
      const sum = { ...count[i] };
      for (let j = i + 1; j < outlineHeads.length && outlineHeads[j].level > head.level; j++) {
        sum.open += count[j].open;
        sum.review += count[j].review;
      }
      return sum;
    });
  }

  function renderIndex() {
    if (view === "submission") { renderSubmissionIndex(); return; }
    const keep = leftBody.scrollTop;
    const counts = headingCounts();
    const root = h("ul", { class: "pv-tree" });
    const stack = [{ level: 0, ul: root, li: null }];
    ixButtons = [];
    outlineHeads.forEach((head, i) => {
      while (stack[stack.length - 1].level >= head.level) stack.pop();
      const parent = stack[stack.length - 1];
      if (!parent.ul) { parent.ul = h("ul", { class: "pv-sub" }); parent.li.append(parent.ul); }
      const li = h("li", { class: `pv-node pv-lvl${head.level}`, "data-i": String(i) });
      const k = counts[i], n = k.open + k.review;
      // Counted sections show the words they add to the main text; the others
      // no number (the counts panel has the abstract's), nor any without counts.
      const shown = outlineHeads.known && head.counted, words = head.main;
      const ix = h("button", { type: "button", class: "pv-ix" + (i === activeHead ? " active" : ""), "data-i": String(i),
        title: `${head.text}${shown && words ? ` — ${fmt(words)} words` : ""}`
          + `${outlineHeads.known && !head.counted ? " (not in the main text)" : ""}`
          + `${n ? ` — ${plural(n, "comment")} to handle` : ""}` },
        h("span", { class: "pv-lbl", text: head.text }));
      if (n) ix.append(h("span", { class: "pv-bd" + (k.open ? "" : " review"), text: String(n),
        "aria-label": `${n} ${k.open ? "open comments" : "comments to check"}` }));
      ix.append(h("span", { class: "pv-wc", text: !shown ? "" : words ? fmt(words) : "—" }));
      if (i === activeHead) ix.setAttribute("aria-current", "location");
      ixButtons.push(ix);
      li.append(h("div", { class: "pv-row" }, h("span", { class: "pv-caret-sp" }), ix));
      parent.ul.append(li);
      stack.push({ level: head.level, ul: null, li });
    });
    for (const li of $$("li.pv-node", root)) {  // carets for entries with children
      const sub = $(":scope > ul.pv-sub", li);
      if (!sub) continue;
      const open = !collapsed.has(li.dataset.i);
      $(":scope > .pv-row > .pv-caret-sp", li).replaceWith(h("button", { type: "button", class: "pv-caret",
        "aria-expanded": String(open), "aria-label": `Toggle ${$(".pv-lbl", li).textContent}`, "data-caret": li.dataset.i }));
      sub.hidden = !open;
    }
    leftTitle.textContent = "Contents";
    leftSub.replaceChildren(...[DOCS[doc], countsBox].filter(Boolean));
    leftBody.replaceChildren(root);
    leftBody.scrollTop = keep;
  }

  function setActive(i) {
    if (i === activeHead) return;
    activeHead = i;
    ixButtons.forEach((b, j) => {
      b.classList.toggle("active", j === i);
      if (j === i) b.setAttribute("aria-current", "location"); else b.removeAttribute("aria-current");
    });
    const b = ixButtons[i];
    if (b) {
      for (let li = b.closest("li").parentElement.closest("li.pv-node"); li; li = li.parentElement.closest("li.pv-node")) {
        if (!collapsed.has(li.dataset.i)) continue;  // open the branches above it
        collapsed.delete(li.dataset.i);
        const caret = $(":scope > .pv-row > .pv-caret", li);
        if (caret) caret.setAttribute("aria-expanded", "true");
        $(":scope > ul.pv-sub", li).hidden = false;
      }
      const sr = leftBody.getBoundingClientRect(), br = b.getBoundingClientRect();
      if (br.top < sr.top + 8) leftBody.scrollTop -= sr.top + 8 - br.top;
      else if (br.bottom > sr.bottom - 8) leftBody.scrollTop += br.bottom - sr.bottom + 8;
    }
    const head = outlineHeads[i];
    if (head) onSpyHeading(head.el);
  }
  let spyTick = false;
  function spy() {
    if (view !== "doc" || !outlineHeads.length || !page.offsetParent) return;
    const top = scroller.getBoundingClientRect().top + 90;
    let cur = 0;
    for (let i = 0; i < outlineHeads.length; i++) {
      if (outlineHeads[i].el.getBoundingClientRect().top <= top) cur = i; else break;
    }
    if (scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 4) cur = outlineHeads.length - 1;
    setActive(cur);
  }
  scroller.addEventListener("scroll", () => {
    hideTip();
    fab.hidden = true;
    if (spyTick) return;
    spyTick = true;
    requestAnimationFrame(() => { spyTick = false; spy(); });
  }, { passive: true });
  leftBody.addEventListener("click", (e) => {
    const group = e.target.closest("[data-sub]");
    if (group) { showSubmissionCard(group.dataset.sub); return; }
    const caret = e.target.closest("[data-caret]");
    if (caret) {
      const id = caret.dataset.caret;
      if (collapsed.has(id)) collapsed.delete(id); else collapsed.add(id);
      renderIndex();
      return;
    }
    const ix = e.target.closest(".pv-ix");
    if (ix) {
      const head = outlineHeads[Number(ix.dataset.i)];
      setActive(Number(ix.dataset.i));
      scrollToEl(head.el);
      closeDrawer();
      return;
    }
    const f = e.target.closest("[data-f]");
    if (f) setFilter(f.dataset.f);
  });

  // ---------------------------------------------------------------- top bar
  function renderWords() {
    if (view === "submission") {
      wordsEl.classList.remove("over");
      wordsEl.title = "";
      wordsEl.textContent = submission ? todoText(submission) : "";
      return;
    }
    // Red only for the words it shows; the other limits are in the panel.
    wordsEl.classList.toggle("over", view !== "figures" && overLimit("words"));
    wordsEl.title = view === "figures" ? "" : countsTitle;
    if (view !== "figures") { wordsEl.textContent = wordsOf(); return; }
    const all = figItems();
    wordsEl.textContent = figsLoaded
      ? `${plural(all.filter((i) => i.kind === "figure").length, "figure")} · ${plural(all.filter((i) => i.kind === "table").length, "table")}` : "";
  }
  function renderTabs() {
    for (const a of $$("a[data-doc]", tabsTop)) {
      const on = view === "doc" && a.dataset.doc === doc;
      if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    }
    for (const [a, v] of [[figsTab, "figures"], [checklistItem, "submission"]]) {
      if (!a) continue;
      if (view === v) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    }
    if (!submissionTab) return;
    // Selected for the checklist and for a submission document's page.
    const on = view === "submission" || (view === "doc" && SUBMISSION_PARTS.includes(doc));
    submissionTab.classList.toggle("on", on);
    if (on) submissionTab.setAttribute("aria-current", "true"); else submissionTab.removeAttribute("aria-current");
  }
  tabsTop.addEventListener("click", (e) => {
    const a = e.target.closest("a");
    if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button) return;
    closeSubmissionMenu(submissionMenu && submissionMenu.contains(a));
    if (a === figsTab) { e.preventDefault(); setView("figures", true); }
    else if (a === checklistItem) { e.preventDefault(); setView("submission", true); }
    else if (a.dataset.doc === doc && view !== "doc") { e.preventDefault(); setView("doc", true); }
  });
  // The Submission menu works like the Export and theme menus: arrows move
  // between its items, Escape closes it and gives the focus back.
  const submissionItems = () => (submissionMenu ? $$("[role=menuitem]", submissionMenu) : []);
  function placeSubmissionMenu() {
    const rect = submissionTab.getBoundingClientRect();
    submissionMenu.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - submissionMenu.offsetWidth - 8))}px`;
    submissionMenu.style.top = `${rect.bottom + 6}px`;
  }
  function openSubmissionMenu() {
    submissionMenu.hidden = false;
    submissionTab.setAttribute("aria-expanded", "true");
    placeSubmissionMenu();
    const items = submissionItems();
    (items.find((a) => a.hasAttribute("aria-current")) || items[0]).focus();
  }
  function closeSubmissionMenu(focus) {
    if (!submissionMenu || submissionMenu.hidden) return;
    submissionMenu.hidden = true;
    submissionTab.setAttribute("aria-expanded", "false");
    if (focus) submissionTab.focus();
  }
  if (submissionTab) {
    submissionTab.addEventListener("click", () => (submissionMenu.hidden ? openSubmissionMenu() : closeSubmissionMenu()));
    submissionTab.addEventListener("keydown", (e) => { if (e.key === "ArrowDown") { e.preventDefault(); openSubmissionMenu(); } });
    submissionMenu.addEventListener("keydown", (e) => {
      const items = submissionItems(), i = items.indexOf(document.activeElement);
      const step = { ArrowDown: 1, ArrowUp: -1 }[e.key];
      if (step) { e.preventDefault(); items[(i + step + items.length) % items.length].focus(); }
      else if (e.key === "Home" || e.key === "End") { e.preventDefault(); items[e.key === "Home" ? 0 : items.length - 1].focus(); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeSubmissionMenu(true); }
      else if (e.key === "Tab") closeSubmissionMenu();
    });
    submissionMenu.addEventListener("focusout", (e) => {
      if (e.relatedTarget && !submissionMenu.contains(e.relatedTarget) && e.relatedTarget !== submissionTab) closeSubmissionMenu();
    });
    document.addEventListener("click", (e) => {
      if (!submissionMenu.contains(e.target) && !submissionTab.contains(e.target)) closeSubmissionMenu();
    });
    window.addEventListener("resize", () => { if (!submissionMenu.hidden) placeSubmissionMenu(); });
    tabsTop.addEventListener("scroll", () => closeSubmissionMenu(), { passive: true });  // narrow layout
  }
