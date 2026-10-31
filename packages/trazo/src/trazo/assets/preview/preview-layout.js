// Preview viewer: sidebar layout, scrolling, document zoom and text size.
// The parts of the viewer are classic scripts loaded in the order of
// trazo.preview.VIEWER_SCRIPTS; their top-level names are shared globals.
  if (!window.PreviewModel) throw new Error("preview-model.js did not load");  // see preview-page.js
  // ---------------------------------------------------------------- layout
  function applyLayout() {
    app.classList.toggle("pv-narrow", S.narrow);
    app.style.setProperty("--pv-lw", S.lw + "px");
    app.style.setProperty("--pv-rw", S.rw + "px");
    const lo = S.narrow ? S.drawer === "left" : S.left, ro = rightOpen();
    left.classList.toggle("open", lo);
    right.classList.toggle("open", ro);
    scrim.hidden = !(S.narrow && S.drawer);
    for (const b of $$("[data-side]", bar)) b.setAttribute("aria-pressed", String(b.dataset.side === "left" ? lo : ro));
  }
  function toggleSide(side) {
    if (S.narrow) S.drawer = S.drawer === side ? null : side;
    else { S[side] = !S[side]; savePrefs({ left: S.left, right: S.right }); }
    applyLayout();
    syncRight();
  }
  const closeDrawer = () => { if (S.narrow && S.drawer) { S.drawer = null; applyLayout(); } };
  for (const b of $$("[data-side]", bar)) b.addEventListener("click", () => toggleSide(b.dataset.side));
  scrim.addEventListener("click", closeDrawer);
  mqNarrow.addEventListener("change", (e) => { S.narrow = e.matches; S.drawer = null; applyLayout(); syncRight(); });

  function initResizer(handle, side) {
    const limit = () => {
      const other = side === "left" ? (S.right ? S.rw : 0) : (S.left ? S.lw : 0);
      return Math.max(200, app.clientWidth - other - 380);
    };
    handle.addEventListener("pointerdown", (e) => {
      if (S.narrow) return;
      e.preventDefault();
      handle.setPointerCapture(e.pointerId);
      handle.classList.add("drag");
      app.classList.add("resizing");
      const x0 = e.clientX, w0 = side === "left" ? S.lw : S.rw;
      const move = (ev) => {
        const dx = ev.clientX - x0;
        if (side === "left") S.lw = clamp(w0 + dx, 200, Math.min(460, limit()));
        else S.rw = clamp(w0 - dx, 280, Math.min(600, limit()));
        applyLayout();
      };
      const up = () => {
        handle.classList.remove("drag");
        app.classList.remove("resizing");
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", up);
        handle.removeEventListener("pointercancel", up);
        savePrefs({ lw: S.lw, rw: S.rw });
      };
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", up);
      handle.addEventListener("pointercancel", up);
    });
    handle.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      const d = (e.key === "ArrowRight" ? 1 : -1) * 16 * (e.shiftKey ? 3 : 1);
      if (side === "left") S.lw = clamp(S.lw + d, 200, 460); else S.rw = clamp(S.rw - d, 280, 600);
      applyLayout();
      savePrefs({ lw: S.lw, rw: S.rw });
      e.preventDefault();
    });
    handle.addEventListener("dblclick", () => {
      if (side === "left") S.lw = 290; else S.rw = 360;
      applyLayout();
      savePrefs({ lw: S.lw, rw: S.rw });
    });
  }
  initResizer($(".pv-resizer", left), "left");
  initResizer($(".pv-resizer", right), "right");

  App.mountTheme(bar, { themeButton: themeBtn, themeMenu: $(".pv-theme-menu", bar) });

  // ---------------------------------------------------------------- scrolling
  // Chrome's smooth scroll takes up to 1.5 s across a long paper, so a place
  // more than this many screens away is shown at once; callers flash it.
  const FAR_SCREENS = 3;
  function scrollToEl(el, center, instant) {
    const sr = scroller.getBoundingClientRect(), r = el.getBoundingClientRect();
    const top = Math.max(0, r.top - sr.top + scroller.scrollTop - (center ? Math.max(24, scroller.clientHeight / 2 - r.height / 2) : 24));
    const left = r.left < sr.left || r.right > sr.right
      ? scroller.scrollLeft + r.left - sr.left - Math.max(24, (scroller.clientWidth - r.width) / 2)
      : scroller.scrollLeft;
    const far = Math.abs(top - scroller.scrollTop) > FAR_SCREENS * scroller.clientHeight;
    scroller.scrollTo({ top, left: Math.max(0, left), behavior: instant || far || reduceMotion() ? "auto" : "smooth" });
  }
  function flash(el) {
    el.classList.remove("pv-flash");
    void el.offsetWidth;
    el.classList.add("pv-flash");
    setTimeout(() => el.classList.remove("pv-flash"), 1800);
  }

  // Scale the whole manuscript, leaving the viewer controls at their usual size.
  // Freeze the reading width while zooming; Fit width reflows to the panel.
  // Text size is independent of page scale and panel width.
  let zoom = typeof prefs.zoom === "number" && Number.isFinite(prefs.zoom) ? clamp(prefs.zoom, 0.5, 2) : "fit";
  let fontSize = typeof prefs.fontSize === "number" && Number.isFinite(prefs.fontSize) ? clamp(Math.round(prefs.fontSize), 10, 22) : 14;
  let pageScale = 1, pageWidth = 0;
  const zoomAnchors = $$("h1, h2, h3, h4, h5, h6, p, figure, table", page);
  const readingKey = "preview-anchor:" + Trazo.path();
  function saveReadingAnchor() {
    if (view !== "doc") return;
    const top = scroller.getBoundingClientRect().top;
    const anchor = scroller.scrollTop > 0 && zoomAnchors.find((el) =>
      (el.id || el.dataset.label) && el.getBoundingClientRect().bottom > top);
    if (anchor) sset(readingKey, JSON.stringify({ id: anchor.id, label: anchor.dataset.label,
      anchor: anchor.dataset.label ? contextOf(model, anchor) : null,
      offset: anchor.getBoundingClientRect().top - top }));
    else sdel(readingKey);
  }
  // Keep the spot of `el` under `point` (a pinch) in place across `change`.
  function keepPoint(el, point, change) {
    const box = el.getBoundingClientRect();
    const fx = (point.x - box.left) / box.width, fy = (point.y - box.top) / box.height;
    change();
    const after = el.getBoundingClientRect();
    scroller.scrollLeft += after.left + fx * after.width - point.x;
    scroller.scrollTop += after.top + fy * after.height - point.y;
  }
  function showScale(scale) {
    zoomReset.textContent = `${Math.round(scale * 100)}%`;
    zoomReset.setAttribute("aria-label", `Zoom: ${zoomReset.textContent}. Reset to 100%`);
    zoomOut.disabled = scale <= 0.5;
    zoomIn.disabled = scale >= 2;
  }
  // The scroller's first width, read with the page out of layout: with it in,
  // the reading lays out the whole paper at the CSS default width, and the
  // width set from it lays it out again. Out of layout the page shows no
  // scrollbar, which matches only where scrollbars overlay the content; a
  // classic scrollbar keeps the slower reading with the page in.
  function firstWidth() {
    page.hidden = true;
    const width = scroller.clientWidth;
    scroller.style.overflowY = "scroll";
    const overlay = scroller.clientWidth === width;
    scroller.style.overflowY = "";
    page.hidden = false;
    return overlay ? width : scroller.clientWidth;
  }
  function applyZoom(preserve = true, point = null) {
    if (view !== "doc") return;
    if (point) { keepPoint(page, point, () => applyZoom(false)); return; }
    const top = preserve ? scroller.getBoundingClientRect().top : 0;
    const anchor = preserve && scroller.scrollTop > 0
      ? zoomAnchors.find((el) => el.getBoundingClientRect().bottom > top) : null;
    const before = anchor && anchor.getBoundingClientRect();
    const fraction = before && before.top < top ? (top - before.top) / before.height : 0;
    const oldScale = pageScale;
    if (!pageWidth || zoom === "fit") {
      pageWidth = Math.max(200, Math.min(860, (pageWidth ? scroller.clientWidth : firstWidth()) - 48));
      docView.style.setProperty("--pv-page-width", pageWidth + "px");
    }
    docView.style.setProperty("--pv-page-font", fontSize + "px");
    fontValue.textContent = `${fontSize} px`;
    fontDown.disabled = fontSize <= 10;
    fontUp.disabled = fontSize >= 22;
    pageScale = zoom === "fit" ? 1 : zoom;
    docView.style.setProperty("--pv-zoom", pageScale);
    showScale(pageScale);
    zoomFit.setAttribute("aria-pressed", String(zoom === "fit"));
    if (before) {
      const after = anchor.getBoundingClientRect();
      scroller.scrollTop += after.top - before.top + fraction * (after.height - before.height);
    }
    scroller.scrollLeft = zoom === "fit" ? 0 : Math.max(0,
      (scroller.scrollLeft + scroller.clientWidth / 2) * pageScale / oldScale - scroller.clientWidth / 2);
    hideTip();
    spy();
  }
  function setZoom(value) {
    zoom = value;
    savePrefs({ zoom });
    applyZoom();
  }
  let figsZoom = typeof prefs.figsZoom === "number" && Number.isFinite(prefs.figsZoom) ? clamp(prefs.figsZoom, 0.5, 2) : 1;
  function applyFigsZoom(point = null) {
    if (view !== "figures") return;
    if (point) {
      const at = document.elementFromPoint(point.x, point.y);
      keepPoint((at && at.closest(".pv-fcard, .pv-fsec-head")) || figsView, point, () => applyFigsZoom());
      return;
    }
    figsView.style.setProperty("--pv-figs-width", scroller.clientWidth + "px");
    figsView.style.setProperty("--pv-figs-zoom", figsZoom);
    showScale(figsZoom);
  }
  function setFigsZoom(value) {
    figsZoom = value;
    savePrefs({ figsZoom });
    applyFigsZoom();
  }
  const viewScale = () => (view === "figures" ? figsZoom : pageScale);
  const stepZoom = (d) => {
    const value = clamp(Math.round(viewScale() * 100 + d) / 100, 0.5, 2);
    if (view === "figures") setFigsZoom(value); else setZoom(value);
  };
  zoomOut.addEventListener("click", () => stepZoom(-10));
  zoomIn.addEventListener("click", () => stepZoom(10));
  zoomReset.addEventListener("click", () => (view === "figures" ? setFigsZoom(1) : setZoom(1)));
  zoomFit.addEventListener("click", () => setZoom("fit"));
  // Trackpad pinch (and Ctrl + wheel) zooms the page or the cards instead of
  // the whole viewer: Chrome and Firefox send it as a wheel event with
  // ctrlKey, Safari as gesture events. The scale is kept unrounded during the
  // gesture and saved once it settles. The lightbox keeps the browser's zoom.
  let pinchScale = null, pinchBase = 1, pinchTimer = 0, gesturing = false;
  const pinchable = () => lbIndex() < 0 && view !== "submission";
  function pinchTo(scale, e) {
    pinchScale = clamp(scale, 0.5, 2);
    const value = Math.round(pinchScale * 100) / 100;
    const area = scroller.getBoundingClientRect();
    const inside = e.clientX >= area.left && e.clientX <= area.right && e.clientY >= area.top && e.clientY <= area.bottom;
    const point = inside ? { x: e.clientX, y: e.clientY } : { x: area.left + area.width / 2, y: area.top + area.height / 2 };
    if (view === "figures") { figsZoom = value; applyFigsZoom(point); } else { zoom = value; applyZoom(true, point); }
    clearTimeout(pinchTimer);
    pinchTimer = setTimeout(() => { pinchScale = null; savePrefs(view === "figures" ? { figsZoom } : { zoom }); }, 250);
  }
  window.addEventListener("wheel", (e) => {
    if (!e.ctrlKey || !pinchable()) return;
    e.preventDefault();
    if (gesturing) return;  // Safari: the gesture events already zoom
    // A trackpad sends many small deltas; a mouse notch (±100 px or 3 lines)
    // is capped at about 10 %, like the browser's own zoom steps.
    const dy = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? scroller.clientHeight : 1);
    pinchTo((pinchScale ?? viewScale()) * Math.exp(-clamp(dy, -10, 10) / 100), e);
  }, { passive: false });
  window.addEventListener("gesturestart", (e) => {
    if (!pinchable()) return;
    e.preventDefault();
    gesturing = true;
    pinchBase = viewScale();
  });
  window.addEventListener("gesturechange", (e) => {
    if (!gesturing) return;
    e.preventDefault();
    pinchTo(pinchBase * e.scale, e);
  });
  window.addEventListener("gestureend", (e) => { if (gesturing) e.preventDefault(); gesturing = false; });
  function setFontSize(value) {
    fontSize = clamp(value, 10, 22);
    savePrefs({ fontSize });
    applyZoom();
  }
  fontDown.addEventListener("click", () => setFontSize(fontSize - 1));
  fontUp.addEventListener("click", () => setFontSize(fontSize + 1));
  fontReset.addEventListener("click", () => setFontSize(14));
  document.addEventListener("click", (e) => { if (!typeMenu.contains(e.target)) typeMenu.open = false; });
  typeMenu.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    e.preventDefault(); e.stopPropagation();
    typeMenu.open = false;
    $("summary", typeMenu).focus();
  });
  new ResizeObserver(() => {
    if (view === "figures") applyFigsZoom();
    else if (zoom === "fit") applyZoom();
  }).observe(scroller);
  // Show a place in the page (leaving the Figures & Tables view first).
  function goToEl(el, flashEl) {
    setView("doc", true);
    requestAnimationFrame(() => { scrollToEl(el, true); flash(flashEl || el); closeDrawer(); });
  }
  // Open a place of another document (same page, new address).
  function openDoc(name, target) {
    const at = target && target.at ? `?at=${encodeURIComponent(target.at)}` : "";
    const hash = target && target.hash ? `#${encodeURIComponent(target.hash)}` : "";
    (window.TRAZO_VIEW || location).assign(`/${name}.html${at}${hash}`);
  }
