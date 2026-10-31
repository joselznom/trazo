// Preview viewer: comments: the sidebar list, undo, filters and going to a comment.
// The parts of the viewer are classic scripts loaded in the order of
// trazo.preview.VIEWER_SCRIPTS; their top-level names are shared globals.
  if (!window.PreviewModel) throw new Error("preview-model.js did not load");  // see preview-page.js
  // The next undo of this view: its label and history entry id.
  let undoLabel = null, undoId = null, showTrash = false;
  // The gallery edits every document, so Undo must use the same scope.
  const commentScope = () => view === "figures" ? null : doc;
  let commentLoadSeq = 0;
  // Inline edit in progress: {id, mode: "edit" | "reply"}.
  let editing = null, navId = null, hoverId = null;
  // Unsaved text of inline editors, keyed "id:mode"; survives redraws, tab
  // switches and external refreshes, and blocks live reload while non-empty.
  const drafts = {};
  const commentSaves = new Set();
  // The resolved comment shown in full (the others are one line each).
  let openDone = null;
  // Active (open and to check) or Resolved comments; kept across reloads.
  let cfilter = prefs.cfilter === "resolved" ? "resolved" : "active";
  const WHO = { agent: "Claude", author: "You" };
  const STATE_NAME = { open: "Open", review: "To check", done: "Resolved", deleted: "Deleted" };
  // Underlines are the author's reading marks: drawn in the text, never
  // listed or counted as comments, and left out of the agent's list.
  const isMark = (c) => c.kind === "underline";
  // Resolved and deleted comments are an archive. A resolved one is still
  // located (its card names the current place, and Show in text goes there)
  // but never painted, marked, picked by a click or visited by j / k.
  const archived = (c) => c.status === "deleted" || (c.status === "done" && !isMark(c));
  const locate = (a, c) => (c.status === "deleted" ? null : locateCached(a, c));
  // What the text of this page shows, set by draw(): located comments that
  // are not archived, and the underlines.
  let inText = [];

  function card(c, hit, entry) {
    const el = document.createElement("div");
    const live = c.status === "open" || c.status === "review";
    const lost = c.quote && !hit && live;
    const isEditing = editing && editing.id === c.id;
    const prefix = entry && entry.c.doc !== doc ? `${DOCS[c.doc] || c.doc} · ` : "";
    const place = hit?.el.dataset.label || c.para;
    el.dataset.id = c.id;
    if (c.status === "done" && openDone !== c.id && !isEditing) {
      el.className = "pv-card done compact";
      el.title = `${c.resolved ? `Resolved ${c.resolved}. ` : ""}Click to show the thread`;
      el.append(h("span", { class: "pv-ok", text: "✓", "aria-hidden": "true" }),
        h("span", { class: "pv-ctext", text: c.comment.split("\n")[0] }), h("span", { class: "pv-cwhere", text: prefix + place }));
      el.addEventListener("click", () => {
        openDone = navId = c.id;
        renderComments();
      });
      return el;
    }
    el.className = `pv-card ${c.status}${lost ? " lost" : ""}${navId === c.id ? " current" : ""}`;
    el.innerHTML =
      `<div class="pv-card-head"><span class="pv-dot" aria-hidden="true"></span><span class="pv-where"></span><span class="pv-state"></span></div>` +
      (c.quote ? `<blockquote></blockquote>` : "") +
      `<div class="pv-text"></div><div class="pv-thread"></div><div class="pv-actions"></div>`;
    $(".pv-where", el).textContent = `${prefix}${place}${lost ? " · text changed" : ""}` +
      (live && hit && hit.changed ? " · target rewritten" : "") +
      (live && hit && hit.moved ? ` · moved from ${c.section}` : "");
    $(".pv-state", el).textContent = STATE_NAME[c.status] || c.status;
    if (c.resolved && c.status === "done") $(".pv-state", el).title = `Resolved ${c.resolved}`;
    if (c.quote) $("blockquote", el).textContent = c.quote;
    const thread = $(".pv-thread", el);
    for (const r of c.replies || []) {
      const line = h("div", { class: `pv-reply ${r.by === "agent" ? "agent" : "author"}` },
        h("span", { class: "who", text: WHO[r.by] || r.by }), r.text);
      thread.append(line);
    }
    const text = $(".pv-text", el);
    const actions = $(".pv-actions", el);
    const button = (op, label, cls, title) => {
      actions.append(h("button", { type: "button", "data-op": op, class: cls || false, text: label, title: title || false }));
    };
    const spacer = () => actions.append(h("span", { class: "pv-sp" }));
    const trash = () => {
      const b = h("button", { type: "button", "data-op": "delete", class: "pv-del", "aria-label": "Delete comment",
        title: "Delete (Undo or ⌘Z brings it back)" });
      b.innerHTML = ICONS.trash;
      actions.append(b);
    };
    const key = `${c.id}:${editing ? editing.mode : ""}`;
    const field = (initial, placeholder) => {
      const area = document.createElement("textarea");
      area.rows = 3;
      area.value = key in drafts ? drafts[key] : initial;
      area.placeholder = placeholder || "";
      area.dataset.cid = c.id;
      area.addEventListener("input", () => {
        if (area.value === initial && !commentSaves.has(key)) delete drafts[key];
        else drafts[key] = area.value;
      });
      area.addEventListener("keydown", (e) => {
        if (e.key === "Escape") { delete drafts[key]; editing = null; renderComments(); }
        if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit(area);
      });
      if (editing.fresh) {
        editing.fresh = false;
        setTimeout(() => { area.focus(); area.setSelectionRange(area.value.length, area.value.length); }, 0);
      }
      return area;
    };
    async function submit(area) {
      if (commentSaves.has(key)) return;
      const edit = editing, raw = area.value;
      const value = area.value.trim();
      const mode = editing.mode;
      const body = mode === "edit" && value && value !== c.comment
        ? { op: "update", id: c.id, comment: value }
        : mode === "reply" && value
          ? { op: "reply", id: c.id, by: "author", text: value, status: "open" } : null;
      if (body) {
        drafts[key] = raw;
        commentSaves.add(key);
        const ok = await send(body, mode === "edit" ? "Comment edited" : "Reply sent; the comment is open for Claude again");
        commentSaves.delete(key);
        if (!ok) return;
      }
      // Text entered while the request was pending belongs to the next edit.
      if (!body || drafts[key] === raw) {
        delete drafts[key];
        if (editing === edit) editing = null;
      }
      renderComments();
    }
    text.textContent = c.comment;
    if (isEditing) {
      const reply = editing.mode === "reply";
      const area = field(reply ? "" : c.comment,
        reply ? "Reply to Claude; sending reopens the comment (⌘↵ to send, Esc to cancel)" : "⌘↵ to save, Esc to cancel");
      if (reply) thread.append(area);
      else { text.textContent = ""; text.append(area); }
      spacer();
      button("cancel-edit", "Cancel");
      button("save-edit", reply ? "Send" : "Save", "pri");
    } else if (c.status === "deleted") {
      spacer();
      button("restore", "Restore");
    } else if (c.status === "open") {
      button("edit", "Edit");
      spacer();
      button("resolve", "✓ Resolve", "line", "You handled it; it leaves Claude's list");
      trash();
    } else if (c.status === "review") {
      button("reply", "Reply", false, "Ask Claude for more; reopens the comment");
      spacer();
      button("resolve", "✓ Resolve", "pri", "Claude's change is fine");
      trash();
    } else {
      if (c.quote) button("show", "Show in text", false, "Scroll to the text it was about and highlight it for a moment");
      button("reopen", "Reopen", false, "Send it back to Claude");
      spacer();
      trash();
    }
    el.addEventListener("click", (e) => {
      const target = e.target.closest("[data-op]");
      const op = target && target.dataset.op;
      const area = $("textarea", el);
      if (op === "edit" || op === "reply") { editing = { id: c.id, mode: op, fresh: true }; renderComments(); return; }
      if (op === "cancel-edit") { delete drafts[key]; editing = null; renderComments(); return; }
      if (op === "save-edit") { submit(area); return; }
      if (op === "resolve") {
        if (navId === c.id) navId = null;  // it leaves the active list
        send({ op: "update", id: c.id, status: "done" }, "Comment resolved");
        return;
      }
      if (op === "show") { showResolved(c, hit); return; }
      if (op === "reopen") { openDone = null; send({ op: "update", id: c.id, status: "open" }, "Comment reopened for Claude"); return; }
      if (op === "delete") { send({ op: "delete", id: c.id }, "Comment deleted"); return; }
      if (op === "restore") { send({ op: "restore", id: c.id }, "Comment restored"); return; }
      if (e.target.closest("textarea, button")) return;
      navId = c.id;
      renderComments();
      if (!archived(c)) showCommentTarget(c, hit);
    });
    el.addEventListener("mouseenter", () => { hoverId = c.id; paintHighlights(); });
    el.addEventListener("mouseleave", () => { if (hoverId === c.id) { hoverId = null; paintHighlights(); } });
    return el;
  }

  // Go to the text a comment is about: in this page, or in another document.
  function showCommentTarget(c, hit) {
    if (!hit) { toast("The target text changed or was removed. Open Source to find it; the original quote is preserved."); return; }
    if (c.doc === doc) { goToEl(hit.el); return; }
    openDoc(c.doc, { at: hit.el.dataset.label });
  }

  // Scroll to the text of a resolved comment and highlight it for a moment
  // (the paragraph when the text changed; a note when it is gone). Without
  // the highlight API the paragraph flashes instead.
  let flashed = null, flashTimer = 0;
  function showResolved(c, hit) {
    if (!hit || c.doc !== doc) { showCommentTarget(c, hit); return; }
    setView("doc", true);
    requestAnimationFrame(() => {
      scrollToEl(hit.range || hit.el, true);
      closeDrawer();
      if (!hit.range || !(window.CSS && CSS.highlights)) { flash(hit.el); return; }
      flashed = hit.range;
      paintHighlights();
      clearTimeout(flashTimer);
      flashTimer = setTimeout(() => { flashed = null; paintHighlights(); }, 2000);
    });
  }

  const counts = (list) => {
    const k = { open: 0, review: 0, done: 0 };
    for (const x of list) if (!isMark(x.c) && x.c.status in k) k[x.c.status] += 1;
    return k;
  };
  // Document order (documents in tab order); comments without a target last.
  // A rewritten paragraph has no range: it sorts by its element.
  const spanOf = ({ range, el }) => {
    if (range) return range;
    const r = el.ownerDocument.createRange();
    r.selectNodeContents(el);
    return r;
  };
  const docOrder = (a, b) => !a.hit - !b.hit || (a.hit && b.hit
    ? (docIndex(a.c.doc) - docIndex(b.c.doc)
      || (a.c.doc === b.c.doc ? spanOf(a.hit).compareBoundaryPoints(Range.START_TO_START, spanOf(b.hit)) : 0)) : 0);
  // Two ranges of the page share at least one character.
  const overlaps = (a, b) => a.compareBoundaryPoints(Range.END_TO_START, b) < 0
    && a.compareBoundaryPoints(Range.START_TO_END, b) > 0;
  let nav = [];

  // Switch the list without redrawing; a folded Resolved list starts over.
  function useFilter(f) {
    if (f === cfilter) return;
    cfilter = f;
    openDone = null;
    savePrefs({ cfilter });
  }
  function setCommentFilter(f) {
    if (f === cfilter) return;
    useFilter(f);
    renderComments();
  }
  for (const b of $$("[data-filter]", right)) b.addEventListener("click", () => setCommentFilter(b.dataset.filter));

  function renderComments() {
    // Keep the caret in an inline editor across the redraw.
    const active = document.activeElement;
    const focus = active && active.tagName === "TEXTAREA" && panel.contains(active)
      ? { id: active.dataset.cid, at: active.selectionStart } : null;
    draw();
    if (focus) {
      const area = $(`textarea[data-cid="${focus.id}"]`, panel);
      if (area) { area.focus(); area.setSelectionRange(focus.at, focus.at); }
    }
  }

  // Comments of the Figures & Tables view: those anchored in a figure
  // caption, a table or a table note, in any document (their labels end in "caption" or
  // are the table's own label, see Model.label()).
  function figureComments() {
    const out = [];
    for (const c of comments) {
      const a = analyses[c.doc];
      if (!a || isMark(c)) continue;
      const hit = locate(a, c);
      const kind = (el) => (el.tagName === "FIGCAPTION" || el.tagName === "TABLE" || !!el.closest(".table-note"));
      const byLabel = a.entries.some((en) => en.label === c.para && (en.kind === "caption" || en.kind === "table"));
      if (hit ? kind(hit.el) : byLabel) out.push({ c, hit, unit: null, a });
    }
    return out;
  }

  // A comment whose text is gone has no range to paint: its paragraph gets a
  // bar in the margin, one strip per state it has (open, to check, underline).
  // Only redone when the set changes, so paintHighlights stays cheap.
  const LOST = [["open", "var(--pv-hl-open-edge)"], ["review", "var(--pv-hl-review-sel)"], ["underline", "var(--pv-warn-dot)"]];
  let marked = new Map();  // element -> its states
  function markLost(items) {
    const states = new Map();
    for (const x of items) {
      if (x.hit.range) continue;
      const set = states.get(x.hit.el) || new Set();
      states.set(x.hit.el, set.add(isMark(x.c) ? "underline" : x.c.status));
    }
    const next = new Map([...states].map(([el, set]) => [el, LOST.filter(([name]) => set.has(name))]));
    const names = (list) => list.map(([name]) => name).join(" ");
    const same = next.size === marked.size && [...next].every(([el, list]) => marked.has(el) && names(marked.get(el)) === names(list));
    if (same) return;
    for (const el of marked.keys()) {
      el.classList.remove("pv-lost");
      delete el.dataset.lost;
      el.style.removeProperty("--pv-lost");
      el.style.removeProperty("--pv-lost-in");
    }
    for (const [el, list] of next) {
      el.classList.add("pv-lost");
      el.dataset.lost = names(list);
      el.style.setProperty("--pv-lost", list.map(([, color], i) => `${-9 - 4 * i}px 0 0 -5px ${color}`).join(", "));
      el.style.setProperty("--pv-lost-in", list.map(([, color], i) => `inset ${4 * (i + 1)}px 0 0 ${color}`).join(", "));
    }
    marked = next;
  }
