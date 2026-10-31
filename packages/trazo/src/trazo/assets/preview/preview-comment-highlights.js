// Preview viewer: comments: highlights in the text, stepping through them, changes and clicks.
// The parts of the viewer are classic scripts loaded in the order of
// trazo.preview.VIEWER_SCRIPTS; their top-level names are shared globals.
  if (!window.PreviewModel) throw new Error("preview-model.js did not load");  // see preview-page.js
  // Highlights of this page: open and to-check comments in two colours,
  // stronger for the current or hovered card, and the underlines.
  // `flashed` is the text of a resolved comment shown for a moment.
  function paintHighlights() {
    if (!(window.CSS && CSS.highlights)) return;
    const ranged = inText.filter((x) => x.hit.range);
    const on = (x) => navId === x.c.id || hoverId === x.c.id;
    for (const [name, state, sel] of [["pv-open", "open", false], ["pv-review", "review", false],
      ["pv-open-sel", "open", true], ["pv-review-sel", "review", true]]) {
      const hl = new Highlight(...ranged.filter((x) => !isMark(x.c) && x.c.status === state && on(x) === sel).map((x) => x.hit.range));
      hl.priority = sel ? 1 : 0;
      CSS.highlights.set(name, hl);
    }
    CSS.highlights.set("pv-underline", new Highlight(...ranged.filter((x) => isMark(x.c)).map((x) => x.hit.range)));
    CSS.highlights.set("pv-done-flash", new Highlight(...(flashed && view === "doc" ? [flashed] : [])));
  }

  function draw() {
    const inFigs = view === "figures";
    hoverId = null;  // the hovered card is redrawn
    const items = inFigs ? figureComments() : comments.filter((c) => c.doc === doc).map((c) => {
      const hit = locate(model, c);
      const unit = hit ? unitOf(model, hit.el) : units.find((u) => titleOf(u) === c.section) || null;
      return { c, hit, unit, a: model };
    });
    if (!inFigs) lastLocated = items;
    inText = inFigs ? [] : items.filter((x) => x.hit && !archived(x.c));
    markLost(inText);
    paintHighlights();

    const notes = items.filter((x) => !isMark(x.c));
    const k = counts(notes);
    const live = k.open + k.review;
    const badge = $(".pv-badge", rtabs);
    badge.hidden = live === 0;
    badge.textContent = String(live);
    badge.classList.toggle("review", k.open === 0);
    badge.title = `${k.open} open, ${k.review} to check`;
    for (const a of $$("a[data-doc]", tabsTop)) {  // open + to check per document
      const kd = counts(comments.filter((c) => c.doc === a.dataset.doc).map((c) => ({ c })));
      let b = $(".pv-badge", a);
      if (!b) { b = h("span", { class: "pv-badge" }); a.append(b); }
      b.hidden = kd.open + kd.review === 0;
      b.textContent = String(kd.open + kd.review);
      b.classList.toggle("review", kd.open === 0);
      b.title = `${kd.open} open, ${kd.review} to check`;
    }

    // Active: open and to check, in document order (also the j / k order).
    // Resolved: the last resolved first. Deleted: the trash, under Resolved.
    nav = notes.filter((x) => x.c.status === "open" || x.c.status === "review").sort(docOrder);
    const when = (c) => c.resolved || c.created || "";
    const done = notes.filter((x) => x.c.status === "done").sort((a, b) => when(b.c).localeCompare(when(a.c)));
    const trash = notes.filter((x) => x.c.status === "deleted");
    const shown = cfilter === "active" ? nav : done.concat(showTrash ? trash : []);
    for (const key of Object.keys(drafts)) {
      const c = comments.find((x) => x.id === key.split(":")[0]);
      if (!c || c.status === "deleted") delete drafts[key];
    }
    // An inline editor only exists for a card that is on screen; a draft
    // for a card in another view comes back when that card is shown again.
    if (editing && !shown.some((x) => x.c.id === editing.id)) editing = null;
    if (!editing) {
      for (const key of Object.keys(drafts)) {
        const [id, mode] = key.split(":");
        if (shown.some((x) => x.c.id === id)) { editing = { id, mode, fresh: false }; break; }
      }
    }

    for (const b of $$("[data-filter]", right)) {
      b.setAttribute("aria-pressed", String(b.dataset.filter === cfilter));
      $(".n", b).textContent = String(b.dataset.filter === "active" ? live : k.done);
    }
    summaryEl.textContent = "";
    if (cfilter === "active" && live) {
      if (k.review) summaryEl.append(h("b", { text: `${k.review} to check` }));
      if (k.review && k.open) summaryEl.append(" · ");
      if (k.open) summaryEl.append(`${k.open} waiting for Claude`);
    }
    summaryEl.hidden = !summaryEl.textContent;
    $(".pv-stepper", right).hidden = cfilter !== "active";
    for (const b of $$("[data-step]", right)) b.disabled = nav.length === 0;

    panel.textContent = "";
    const empty = (title, text) => panel.append(h("div", { class: "pv-empty" }, h("b", { text: title }), text || null));
    if (inFigs && !figsLoaded) {
      empty("Loading…");
    } else if (cfilter === "active") {
      if (!nav.length && !done.length) empty("No comments", inFigs ? "Comments on figure captions and tables appear here." : "Select text in the document to add one.");
      else if (!nav.length) empty("Nothing to handle", "Every comment is resolved.");
      for (const x of nav) panel.append(card(x.c, x.hit, x));
    } else {
      if (!done.length) empty("No resolved comments");
      for (const x of done) panel.append(card(x.c, x.hit, x));
      const foot = h("div", { class: "pv-cfoot" });
      if (done.length) {
        const clear = h("button", { type: "button", text: "Delete all resolved",
          title: "Move them to Deleted (Undo or ⌘Z brings them back)" });
        clear.addEventListener("click", () => send({ op: "delete", ids: done.map((x) => x.c.id) },
          `${plural(done.length, "resolved comment")} deleted`));
        foot.append(clear);
      }
      if (trash.length) {
        const link = h("button", { type: "button", class: "pv-trash", "aria-expanded": String(showTrash),
          text: `${showTrash ? "▾" : "▸"} Deleted (${trash.length})` });
        link.addEventListener("click", () => { showTrash = !showTrash; renderComments(); });
        foot.append(link);
      }
      if (foot.childNodes.length) panel.append(foot);
      if (showTrash) for (const x of trash) panel.append(card(x.c, null, x));
    }
    if (!inFigs) renderIndex();
  }

  // Jump to the next/previous open or to-check comment in this view.
  function step(dir) {
    if (!nav.length) return;
    const at = nav.findIndex((x) => x.c.id === navId);
    const next = nav[at < 0 ? (dir > 0 ? 0 : nav.length - 1) : (at + dir + nav.length) % nav.length];
    navId = next.c.id;
    if (!rightOpen() || tab !== "comments") showSide("comments");
    useFilter("active");
    renderComments();
    showCommentTarget(next.c, next.hit);
    const cardEl = $(`.pv-card[data-id="${navId}"]`, panel);
    if (cardEl) cardEl.scrollIntoView({ block: "nearest" });
  }
  for (const b of $$("[data-step]", right)) b.addEventListener("click", () => step(Number(b.dataset.step)));

  // Apply one comment change. With `message`, a notice offers Undo for it;
  // Undo is refused after another change (e.g. Claude's) or a view switch.
  // Returns the new payload, or false.
  async function send(body, message) {
    const scope = commentScope();
    ++commentLoadSeq;  // an older GET must not replace this mutation's result
    let payload;
    try {
      const res = await App.review.change("document", { ...(scope ? { doc: scope } : {}), ...body });
      payload = res.data;
      if (!res.ok) {
        alert("Could not save the change: " + (payload.error || res.status));
        loadComments();  // e.g. a refused undo removed its entry: refresh the Undo label
        return false;
      }
      ++commentLoadSeq;  // discard reads started while the mutation was pending
      comments = payload.comments;
      if (scope === commentScope()) { undoLabel = payload.undo; undoId = payload.undo_id; }
      else loadComments();
    } catch (e) {
      alert("Could not reach the preview server: " + e.message);
      renderComments();
      return false;
    }
    renderComments();
    if (message) {
      // Undo exactly this change: the server refuses if another one came since.
      const mine = undoId;
      toast(message, undoLabel && body.op !== "undo" ? { label: "Undo", run: () => {
        if (undoId === mine && commentScope() === scope) send({ op: "undo", ...(mine ? { expect: mine } : {}) }, "Undone");
        else toast("Not undone: the comments changed since. ⌘Z undoes the latest change.");
      } } : null);
    }
    return payload;
  }
  let redrawPending = false;
  panel.addEventListener("focusout", () => {
    // Run a redraw that was skipped while a textarea had focus.
    setTimeout(() => {
      const a = document.activeElement;
      if (redrawPending && !(a && a.tagName === "TEXTAREA" && panel.contains(a))) {
        redrawPending = false;
        renderComments();
      }
    }, 0);
  });
  async function loadComments() {
    const scope = commentScope(), seq = ++commentLoadSeq;
    try {
      // The payload holds the comments of every document (`doc` only scopes Undo).
      const result = await App.review.load("document", scope);
      const payload = result.data;
      if (seq !== commentLoadSeq || scope !== commentScope()) return;
      if (!Array.isArray(payload.comments)) throw new Error(payload.error || "bad response");
      comments = payload.comments;
      undoLabel = payload.undo;
      undoId = payload.undo_id;
    } catch (e) { if (!commentsReady) comments = []; }
    commentsReady = true;
    // Drafts survive a redraw, but do not move the caret while typing.
    const active = document.activeElement;
    if (active && active.tagName === "TEXTAREA" && panel.contains(active)) redrawPending = true;
    else renderComments();
  }

  // The text position under a point of the page.
  function caretAt(x, y) {
    if (document.caretPositionFromPoint) {
      const p = document.caretPositionFromPoint(x, y);
      return p ? { node: p.offsetNode, offset: p.offset } : null;
    }
    const r = document.caretRangeFromPoint && document.caretRangeFromPoint(x, y);
    return r ? { node: r.startContainer, offset: r.startOffset } : null;
  }
  const rangeHas = (range, at) => range.startContainer.ownerDocument === document
    && range.comparePoint(at.node, at.offset) === 0;
  // The underlines of this page under a point; where there is none, those of
  // the paragraph whose text changed (they have no range, only a margin bar).
  const marksAt = (at) => {
    const marks = inText.filter((x) => isMark(x.c));
    const under = marks.filter((x) => x.hit.range && rangeHas(x.hit.range, at));
    return under.length ? under : marks.filter((x) => !x.hit.range && x.hit.el.contains(at.node));
  };

  // Clicking annotated text opens its thread, including links inside the
  // quote. Repeated clicks cycle threads that share the same text.
  scroller.addEventListener("click", (e) => {
    if (view !== "doc" || e.button !== 0 || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey
      || e.target.closest("button, .pv-tf, .pv-diagram")) return;
    const sel = window.getSelection();
    if (sel && !sel.isCollapsed) return;
    const at = caretAt(e.clientX, e.clientY);
    if (!at) return;
    const hits = inText.filter((x) => !isMark(x.c) && x.hit.range
      && rangeHas(x.hit.range, at) && [...x.hit.range.getClientRects()].some((r) =>
        e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom))
      .sort(docOrder);
    if (!hits.length) return;
    const hit = hits[(hits.findIndex((x) => x.c.id === navId) + 1) % hits.length];
    // Capture before the page's reference-link handler can scroll away.
    e.preventDefault();
    e.stopPropagation();
    navId = hit.c.id;
    useFilter("active");
    showSide("comments");
    renderComments();
    const cardEl = $(`.pv-card[data-id="${navId}"]`, panel);
    if (cardEl) cardEl.scrollIntoView({ block: "nearest" });
  }, true);

  document.addEventListener("keydown", (e) => {
    if (lbIndex() >= 0) {  // the lightbox has its own keys
      if (e.key === "Escape") closeLb();
      return;
    }
    const typing = e.target.closest && e.target.closest("textarea, input, select, [contenteditable]");
    // ⌘Z / Ctrl+Z undoes the last comment change when not typing.
    if (e.key === "z" && (e.metaKey || e.ctrlKey) && !e.shiftKey) {
      if (typing) return;
      if (undoLabel) { e.preventDefault(); send({ op: "undo" }, `Undone: ${undoLabel}`); }
      return;
    }
    // j / k (outside text fields) or ⌥↓ / ⌥↑: next / previous open comment.
    const plain = !e.metaKey && !e.ctrlKey && !e.altKey && !e.shiftKey;
    const alt = e.altKey && !e.metaKey && !e.ctrlKey && !e.shiftKey && !typing;
    const dir = alt && e.key === "ArrowDown" ? 1
      : alt && e.key === "ArrowUp" ? -1
      : plain && !typing && e.key === "j" ? 1
      : plain && !typing && e.key === "k" ? -1 : 0;
    if (dir) { e.preventDefault(); step(dir); }
    // [ and ] fold the index and the review sidebar.
    // Layouts that type brackets with Option (Mac) or AltGr (Windows/Linux) report altKey.
    if (!typing && !e.metaKey && (!e.ctrlKey || e.altKey) && (e.key === "[" || e.key === "]")) {
      e.preventDefault();
      toggleSide(e.key === "[" ? "left" : "right");
    }
    if (e.key === "Escape" && !typing) { hideTip(); closeDrawer(); }
  });
