// Preview viewer: the lightbox.
// The parts of the viewer are classic scripts loaded in the order of
// trazo.preview.VIEWER_SCRIPTS; their top-level names are shared globals.
  if (!window.PreviewModel) throw new Error("preview-model.js did not load");  // see preview-page.js
  // Lightbox: the figure or table large, with previous / next, arrow keys,
  // Esc to close and focus kept inside.
  const lb = h("div", { class: "pv-lb", role: "dialog", "aria-modal": "true", "aria-label": "Preview", hidden: true });
  lb.innerHTML =
    `<div class="pv-lb-card"><div class="pv-lb-head"><span class="pv-lb-chip"></span><span class="ttl"></span>` +
    `<button type="button" class="pv-icon-btn" data-lb="prev" aria-label="Previous item" title="Previous (left arrow)">${ICONS.prev}</button>` +
    `<button type="button" class="pv-icon-btn" data-lb="next" aria-label="Next item" title="Next (right arrow)">${ICONS.next}</button>` +
    `<button type="button" class="pv-icon-btn" data-lb="close" aria-label="Close preview" title="Close (Esc)">${ICONS.close}</button></div>` +
    `<div class="pv-lb-body"></div><div class="pv-lb-foot"></div></div>`;
  document.body.append(lb);
  let lbAt = -1, lbOpener = null, lbItems = [], lbNoteFit = null;
  const lbIndex = () => (lb.hidden ? -1 : lbAt);
  function openLb(at, opener, items = figsShown) {
    const it = items[at];
    if (!it) return;
    lbItems = items;
    lbAt = at;
    if (opener) lbOpener = opener;
    $(".ttl", lb).textContent = kindOf(it) ? `${it.num} · ${kindOf(it)}` : it.num;
    $(".pv-lb-chip", lb).replaceChildren(chipEl(it.doc));
    const body = $(".pv-lb-body", lb);
    body.replaceChildren();
    let copy = null, box = null;
    lbNoteFit?.disconnect();
    if (it.kind === "figure") {
      const images = it.composite ? $$("img", it.el).map((img) => img.getAttribute("src")) : [it.src];
      for (const src of images) body.append(h("img", { src, alt: it.caption }));
    }
    else body.append(box = h("div", { class: "tscroll" }, copy = tableCopy(it)));
    const caption = captionView(it, "pv-lb-cap");
    caption.prepend(h("b", { text: `${it.num}. ` }));
    body.append(caption);
    const note = noteView(it, "pv-lb-note");
    if (note) body.append(note);
    const foot = $(".pv-lb-foot", lb);
    foot.replaceChildren(h("button", { type: "button", class: "pv-btn primary", "data-lb": "go", text: "Go to text" }),
      h("span", { class: "pv-hint", text: it.cites.length ? `Cited ${it.cites.length}×: ${it.cites.map((c) => c.label).join(", ")}` : "Not cited in the text" }));
    lb.hidden = false;
    if (note && copy) lbNoteFit = alignNote(note, copy, box);  // measured once it is shown
    $('[data-lb="close"]', lb).focus();
    for (const b of $$('[data-lb="prev"], [data-lb="next"]', lb)) b.disabled = lbItems.length < 2;
  }
  function closeLb() {
    if (lb.hidden) return;
    lb.hidden = true;
    if (lbOpener && document.contains(lbOpener)) lbOpener.focus({ preventScroll: true });
  }
  const stepLb = (d) => { if (lbAt >= 0 && lbItems.length) openLb((lbAt + d + lbItems.length) % lbItems.length, null, lbItems); };
  lb.addEventListener("click", (e) => {
    if (e.target === lb) { closeLb(); return; }
    const b = e.target.closest("[data-lb]");
    if (!b) return;
    if (b.dataset.lb === "close") closeLb();
    else if (b.dataset.lb === "prev") stepLb(-1);
    else if (b.dataset.lb === "next") stepLb(1);
    else if (b.dataset.lb === "go") { const it = lbItems[lbAt]; closeLb(); if (it) goToFigure(it); }
  });
  lb.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft") stepLb(-1);
    else if (e.key === "ArrowRight") stepLb(1);
    else if (e.key === "Escape") { e.preventDefault(); closeLb(); }
    else if (e.key === "Tab") {
      const f = $$("button:not(:disabled), a[href]", lb);
      if (!f.length) return;
      const i = f.indexOf(document.activeElement);
      if (e.shiftKey && i <= 0) { f[f.length - 1].focus(); e.preventDefault(); }
      else if (!e.shiftKey && (i === f.length - 1 || i < 0)) { f[0].focus(); e.preventDefault(); }
    }
  });

  const documentFigures = model.items.filter((it) => it.kind === "figure");
  for (const it of documentFigures) for (const image of $$("img", it.el)) {
    if (image.closest("a")) continue;
    image.tabIndex = 0;
    image.setAttribute("role", "button");
    image.setAttribute("aria-label", `Enlarge ${it.num}: ${it.caption}`);
    const enlarge = () => openLb(documentFigures.indexOf(it), image, documentFigures);
    image.addEventListener("click", enlarge);
    image.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); enlarge(); }
    });
  }
