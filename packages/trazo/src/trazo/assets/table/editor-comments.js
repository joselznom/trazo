// Table editor: the Comments tab. Same states and panel as the preview:
// Active (open and to check) and Resolved (one line each, the last resolved
// first); one action per intent; each change but a deletion (see app.js)
// shows a notice with Undo.
// editor.js calls TableEditor.comments(E) with its shared state and helpers.
(function () {
  "use strict";
  const T = (window.TableEditor = window.TableEditor || {});
  T.comments = function (E) {
    const { h, $, App } = E;
    const STATE_NAME = { open: "Open", review: "To check", done: "Resolved" };
    const deletion = App.commentDeletion(() => E.renderPanel(), (body, message) => commentOp(body, message));
    const TRASH = '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.4" '
      + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.5h5.8l.6-8.5"/></svg>';
    let cfilter = "active";   // comments shown: Active (open, to check) or Resolved
    let openDone = null;      // the resolved comment shown in full (others are one line)
    let cedit = null;         // inline editor of a card: {id, mode: "edit" | "reply"}
    let cdrafts = {};         // unsaved text by "<id>:<mode>" ("new" for the composer)

    function reset() { cedit = openDone = null; cdrafts = {}; }
    // Open the composer on the selection; `text` starts a request to the agent.
    function startComment(text) {
      E.composeFor = E.targetOf(E.sel);
      E.tab = "comments";
      cfilter = "active";
      if (text && !(cdrafts.new || "").trim()) cdrafts.new = text;
      E.renderTabs();
      E.renderPanel();
      const ta = $(".te-compose textarea");
      if (ta) { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); }
    }
    // Apply one comment change of this table. With `message`, a notice offers
    // Undo for it; Undo is refused after another change (e.g. the agent's).
    async function commentOp(body, message) {
      const table = E.label;
      const r = await App.review.change("table", { table, ...body });
      if (!r.ok) {
        E.flash(r.data.error || "Comment not saved");
        if (E.label === table) E.loadComments();  // e.g. a refused undo removed its entry
        return false;
      }
      if (E.label !== table) return true;
      E.comments = r.data;
      E.renderTable(); E.renderTabs(); E.renderPanel(); E.loadList();
      if (message) {
        // Undo exactly this change: the server refuses if another one came since.
        const mine = E.comments.undo_id;
        E.flash(message, E.comments.undo && !["undo", "purge"].includes(body.op) ? { label: "Undo", run: () => {
          if (E.label === table && E.comments.undo_id === mine) undoComment(mine);
          else E.flash("Not undone: the comments changed since");
        } } : null);
      }
      return true;
    }
    // The last comment change of this table (⌘Z here undoes table edits).
    const undoComment = (expect) => commentOp({ op: "undo", ...(expect ? { expect } : {}) }, "Undone");
    // A comment text field whose text survives redraws (see renderPanel).
    function draftField(key, initial, placeholder, name, submit, cancel) {
      const ta = h("textarea", { class: "te-in", placeholder, "aria-label": name, dataset: { draft: key } });
      ta.value = key in cdrafts ? cdrafts[key] : initial;
      ta.addEventListener("input", () => { cdrafts[key] = ta.value; });
      ta.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submit(); }
        if (e.key === "Escape" && cancel) { e.preventDefault(); e.stopPropagation(); cancel(); }
      });
      return ta;
    }
    function commentsPanel() {
      const all = E.comments.comments;
      const live = all.filter((c) => c.status === "open" || c.status === "review");
      const toCheck = live.filter((c) => c.status === "review").length, waiting = live.length - toCheck;
      const when = (c) => c.resolved || c.created || "";
      const done = all.filter((c) => c.status === "done").sort((a, b) => when(b).localeCompare(when(a)));
      const ids = (list) => list.map((c) => c.id);
      deletion.sync(ids(done), ids(cfilter === "active" ? live : done));
      const seg = (key, text, n) => h("button", { type: "button", class: cfilter === key ? "on" : null, dataset: { filter: key },
        "aria-pressed": String(cfilter === key), onclick: () => { cfilter = key; openDone = null; E.renderPanel(); } },
      text, h("span", { class: "te-cn", text: String(n) }));
      const out = [h("div", { class: "te-cbar" }, h("div", { class: "te-seg", role: "group", "aria-label": "Show comments" },
        seg("active", "Active", live.length), seg("resolved", "Resolved", done.length)),
      E.comments.undo ? h("button", { type: "button", class: "pv-btn sm ghost te-cundo", text: "↶ Undo",
        title: `Undo the last comment change: ${E.comments.undo}`, onclick: () => undoComment(E.comments.undo_id) }) : null)];
      const empty = (title, text) => out.push(h("div", { class: "te-cempty" }, h("b", { text: title }), text || null));
      if (cfilter === "active") {
        if (live.length) {
          out.push(h("p", { class: "te-csum", "aria-live": "polite" }, toCheck ? h("b", { text: `${toCheck} to check` }) : null,
            toCheck && waiting ? " · " : null, waiting ? `${waiting} waiting for the agent` : null));
        } else if (done.length) empty("Nothing to handle", "Every comment is resolved.");
        else empty("No comments", "Select a header or cell and press C to add one.");
        for (const c of live) out.push(commentCard(c));
        const send = async () => {
          const raw = ta.value;
          if (!raw.trim()) return;
          const body = { op: "add", comment: raw };
          if (E.composeFor) body.target = E.composeFor;
          if (await commentOp(body, "Comment added") && cdrafts.new === raw) { delete cdrafts.new; E.renderPanel(); }
        };
        const ta = draftField("new", "", "Comment for the agent…", "New comment", send);
        out.push(h("div", { class: "te-compose" }, h("div", { class: "te-to" }, "New comment on ", h("code", { text: E.targetText(E.composeFor) })),
          ta, h("div", { style: "margin-top:6px" }, h("button", { type: "button", class: "pv-btn sm primary", text: "Comment", onclick: send }))));
      } else {
        if (!done.length) empty("No resolved comments");
        for (const c of done) out.push(commentCard(c));
        if (done.length) out.push(deletion.foot(ids(done), "te-cfoot"));
      }
      return out;
    }
    function commentCard(c) {
      const mode = cedit && cedit.id === c.id ? cedit.mode : null;
      if (c.status === "done" && openDone !== c.id && !mode) {
        return h("div", { class: `te-card done compact pk-row${deletion.ticked(c.id) ? " selected" : ""}`,
          title: `${c.resolved ? `Resolved ${c.resolved}. ` : ""}Click to show the thread`,
          onclick: () => { openDone = c.id; E.renderPanel(); } },
        deletion.tick(c.id), h("span", { class: "te-cok", "aria-hidden": "true", text: "✓" }),
        h("span", { class: "te-ctext", text: c.comment.split("\n")[0] }), h("span", { class: "te-cwhere", text: E.targetText(c.target) }),
        deletion.trash(c.id, "te-icon te-del", TRASH), deletion.box(c.id));
      }
      const key = `${c.id}:${mode}`;
      const close = () => { delete cdrafts[key]; cedit = null; E.renderPanel(); };
      const open = (m) => {
        cedit = { id: c.id, mode: m };
        E.renderPanel();
        const ta = $(`[data-draft="${CSS.escape(`${c.id}:${m}`)}"]`);
        if (ta) { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); }
      };
      const submit = async () => {
        const raw = cdrafts[key] ?? (mode === "edit" ? c.comment : ""), value = raw.trim();
        const body = mode === "edit" ? (value && value !== c.comment ? { op: "update", id: c.id, comment: value } : null)
          : value ? { op: "reply", id: c.id, by: "author", text: value, status: "open" } : null;
        if (body && !await commentOp(body, mode === "edit" ? "Comment edited" : "Reply sent; the comment is open for the agent again")) return;
        // Text typed while the request was pending belongs to the next edit.
        if ((cdrafts[key] ?? raw) === raw && cedit && cedit.id === c.id && cedit.mode === mode) close();
      };
      const acts = h("div", { class: "te-cacts" });
      const btn = (text, onclick, cls, title) => acts.append(h("button", { type: "button", class: `pv-btn sm${cls ? " " + cls : ""}`, text, title, onclick }));
      const spacer = () => acts.append(h("span", { class: "te-grow" }));
      const del = () => acts.append(deletion.trash(c.id, "te-icon te-del", TRASH));
      const resolve = () => commentOp({ op: "update", id: c.id, status: "done" }, "Comment resolved");
      let text = h("p", { text: c.comment }), reply = null;
      if (mode === "edit") {
        text = draftField(key, c.comment, "⌘↵ to save, Esc to cancel", "Edit comment", submit, close);
      } else if (mode === "reply") {
        reply = draftField(key, "", "Reply to the agent; sending reopens the comment (⌘↵ to send, Esc to cancel)", "Reply", submit, close);
      }
      if (mode) {
        spacer();
        btn("Cancel", close, "ghost");
        btn(mode === "edit" ? "Save" : "Send", submit, "primary");
      } else if (c.status === "open") {
        btn("Edit", () => open("edit"), "ghost");
        spacer();
        btn("✓ Resolve", resolve, "", "You handled it; it leaves the agent's list");
        del();
      } else if (c.status === "review") {
        btn("Reply", () => open("reply"), "ghost", "Ask the agent for more; reopens the comment");
        spacer();
        btn("✓ Resolve", resolve, "primary", "The agent's change is fine");
        del();
      } else {
        btn("Reopen", () => { openDone = null; commentOp({ op: "update", id: c.id, status: "open" }, "Comment reopened for the agent"); },
          "ghost", "Send it back to the agent");
        spacer();
        del();
      }
      const done = c.status === "done";
      return h("div", { class: `te-card pk-row ${c.status}${done && deletion.ticked(c.id) ? " selected" : ""}` },
        h("div", { class: "te-meta" }, done ? deletion.tick(c.id) : null, h("span", { class: `te-state ${c.status}`, text: STATE_NAME[c.status] || c.status,
          title: c.status === "done" && c.resolved ? `Resolved ${c.resolved}` : null }),
        h("button", { type: "button", class: "te-target", text: E.targetText(c.target), title: "Select the target",
          onclick: () => selectTarget(c.target) }), h("span", { class: "te-grow" }), h("span", { text: c.created || "" })),
        text,
        (c.replies || []).map((r) => h("div", { class: `te-reply ${r.by === "agent" ? "" : "author"}` },
          h("span", { class: "te-muted te-small", text: r.by === "agent" ? "Agent" : "You" }), h("br"), r.text)),
        reply, acts, mode ? null : deletion.box(c.id));
    }
    function selectTarget(t) {
      if (!t) { E.select({ kind: "table" }); return; }
      if (t.row) {
        const item = E.st.model && E.st.model.body.find((b) => b.cells && E.stable(b.key) === E.stable(t.row));
        if (item) { E.sel = { kind: "cell", id: t.column, row: item.row }; E.renderTable(); return; }
      }
      E.sel = E.leafIds().some((c) => c.id === t.column) ? { kind: "column", id: t.column } : { kind: "group", id: t.column, path: t.column.split(".")[0] };
      E.renderTable();
    }
    return { commentsPanel, startComment, reset };
  };
})();
