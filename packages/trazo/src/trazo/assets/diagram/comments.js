/* Diagram comments. Same states and panel as the manuscript preview: open
 * (the author wrote it), review (the agent replied with `trazo diagram
 * resolve`; "To check"), done (only the author resolves it). Active lists
 * open and to-check comments in pin order, Resolved the last resolved first.
 * Each change shows a notice with Undo. The server stores them in
 * notes/review/diagram_comments.json. A comment is pinned to an element id,
 * a point in millimetres, both, or the whole diagram.
 */
(function () {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const ed = window.DiagramEditor;
  const S = ed.state;
  const $ = (sel) => document.querySelector(sel);
  const ic = (name, size = 14) => Icons.node(name, size);

  let comments = [];
  let undoLabel = null, undoId = null;  // the next comment undo of this diagram
  let stamp = null;
  let pin = {};
  let draft = "";
  let showTrash = false;
  let focusId = null;
  let editing = null;
  let replying = null;
  let replyDraft = "";
  let filter = "active"; // Active (open, to check) or Resolved
  let editDrafts = {};
  let activeDiagram = null;
  const drafts = new Map();
  const pending = new Set();

  function syncDiagram() {
    if (activeDiagram === S.name) return;
    if (activeDiagram) drafts.set(activeDiagram, { pin, draft, editing, replying, replyDraft, editDrafts });
    activeDiagram = S.name;
    const saved = drafts.get(activeDiagram) || {};
    pin = saved.pin || {};
    draft = saved.draft || "";
    editing = saved.editing || null;
    replying = saved.replying || null;
    replyDraft = saved.replyDraft || "";
    editDrafts = saved.editDrafts || {};
    focusId = null;
    comments = [];
    undoLabel = undoId = null;
    load();
  }

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function where(item) {
    const bits = [];
    if (item.target) bits.push(item.target);
    if (item.point) bits.push(`${item.point[0]}, ${item.point[1]} mm`);
    return bits.join(" · ") || "whole diagram";
  }

  function when(iso) {
    const t = Date.parse(iso || "");
    if (!Number.isFinite(t)) return "";
    const sec = Math.max(0, (Date.now() - t) / 1000);
    if (sec < 60) return "now";
    if (sec < 3600) return `${Math.round(sec / 60)} min`;
    if (sec < 86400) return `${Math.round(sec / 3600)} h`;
    return `${Math.round(sec / 86400)} d`;
  }

  /* Apply one comment change. With `message`, a notice offers Undo for it;
   * Undo is refused after another change (e.g. the agent's). */
  async function send(body, message) {
    /* Scope the reply to this diagram; the server returns every comment otherwise. */
    if (!body.diagram && S.name) body.diagram = S.name;
    const diagram = body.diagram;
    const key = `${diagram}:${body.op}:${body.id || ""}`;
    if (pending.has(key)) return false;
    pending.add(key);
    let res;
    let data = {};
    try {
      res = await Trazo.review.change("diagram", body);
      data = res.data;
    } catch (err) {
      ed.toast("Could not reach the diagram server", "error");
      return false;
    } finally {
      pending.delete(key);
    }
    if (!res.ok) {
      ed.toast(data.error || `HTTP ${res.status}`, "error");
      return false;
    }
    if (S.name !== diagram) return true;
    comments = (data.comments || []).filter((item) => item.diagram === diagram);
    undoLabel = data.undo || null;
    undoId = data.undo_id || null;
    draw();
    if (S.diagram) redrawPins();
    if (message) {
      /* Undo exactly this change: the server refuses if another one came since. */
      const mine = undoId;
      ed.toast(message, "check", undoLabel && body.op !== "undo" ? { label: "Undo", run: () => {
        if (S.name === diagram && undoId === mine) undoComment(mine);
        else ed.toast("Not undone: the comments changed since", "info");
      } } : null);
    }
    return true;
  }

  /* The last comment change of this diagram (⌘Z here undoes diagram edits). */
  const undoComment = (expect) => send({ op: "undo", diagram: S.name, ...(expect ? { expect } : {}) }, "Undone");

  function redrawPins() {
    /* The overlay is rebuilt by the editor; ask it to redraw. */
    ed.select(S.sel.slice());
  }

  async function load() {
    const diagram = S.name;
    if (!S.name) {
      comments = [];
      draw();
      return;
    }
    try {
      const res = await Trazo.review.load("diagram", diagram);
      const data = res.data;
      if (S.name !== diagram || !res.ok || !Array.isArray(data.comments)) return;
      comments = data.comments;
      undoLabel = data.undo || null;
      undoId = data.undo_id || null;
    } catch (err) {
      return;
    }
    draw();
    if (S.diagram) redrawPins();
  }

  S.onStatus = (data) => {
    if (!data || data.comments === stamp) return;
    const first = stamp === null;
    stamp = data.comments;
    if (!first) load(); // the first poll only records the stamp; the page load already fetched them
  };

  function openTab() {
    ed.showTab("comments");
  }

  const isLive = (c) => c.status === "open" || c.status === "review";
  const resolvedAt = (c) => c.resolved || c.created || "";
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

  function draw() {
    const root = $("#comments");
    if (!root) return;
    /* Active: open and to check, in pin order. Resolved: the last resolved
     * first. Deleted: the trash, under Resolved. */
    const live = comments.filter(isLive);
    const review = live.filter((c) => c.status === "review").length;
    const done = comments.filter((c) => c.status === "done").sort((a, b) => resolvedAt(b).localeCompare(resolvedAt(a)));
    const trash = comments.filter((c) => c.status === "deleted");
    ed.setTabBadge("comments", live.length, review ? "accent" : "");
    const scroll = root.scrollTop;
    if (!S.name) {
      root.replaceChildren(el("div", { class: "empty" }, ic("comment", 28), "Open a diagram to comment on it."));
      return;
    }
    const pinned = pin.target || pin.point;
    const area = el("textarea", { class: "input", rows: "3", placeholder: "Ask the agent for a change…",
      oninput: (e) => { draft = e.target.value; } });
    area.value = draft;
    area.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) add();
      if (e.key === "Escape") area.blur();
    });
    const composer = el("div", { class: "composer" },
      el("div", { class: "pin-to" }, ic(pinned ? "target" : "file"), "On ", el("b", {}, where(pin)),
        pinned ? el("button", { type: "button", class: "btn small ghost", onclick: () => { pin = {}; draw(); } }, "Whole diagram") : null),
      area,
      el("div", { class: "foot" }, el("span", { class: "note", style: "margin:0" }, "⌘↵ to add · C to pin on the canvas"),
        el("button", { type: "button", class: "btn small primary", onclick: add }, "Comment")));
    const tab = (key, label, n) => el("button", { type: "button", class: filter === key ? "on" : null, "data-filter": key,
      "aria-pressed": String(filter === key), onclick: () => { filter = key; draw(); } }, label, el("span", { class: "n" }, String(n)));
    const filters = el("div", { class: "cfilter" }, el("div", { class: "seg", role: "group", "aria-label": "Show comments" },
      tab("active", "Active", live.length), tab("resolved", "Resolved", done.length)),
    undoLabel ? el("button", { type: "button", class: "btn small ghost", title: `Undo the last comment change: ${undoLabel}`,
      onclick: () => undoComment(undoId) }, ic("undo"), "Undo") : null);
    const open = live.length - review;
    const summary = filter === "active" && live.length ? el("div", { class: "csum", "aria-live": "polite" },
      review ? el("b", {}, `${review} to check`) : null, review && open ? " · " : null,
      open ? `${open} waiting for the agent` : null) : null;
    root.replaceChildren(...[composer, filters, summary].filter(Boolean));
    const empty = (icon, title, text) => root.append(el("div", { class: "empty" }, ic(icon, 26), el("b", {}, title), text || ""));
    if (filter === "active") {
      if (!live.length && !done.length) empty("comment", "No comments", "Press C and click the canvas to pin one.");
      else if (!live.length) empty("check", "Nothing to handle", "Every comment is resolved.");
      for (const item of live) root.append(card(item));
    } else {
      if (!done.length) empty("check", "No resolved comments");
      for (const item of done) root.append(card(item));
      const foot = el("div", { class: "cfoot" });
      if (done.length) {
        foot.append(el("button", { type: "button", title: "Move them to Deleted (Undo brings them back)",
          onclick: () => send({ op: "delete", ids: done.map((c) => c.id) }, `${plural(done.length, "resolved comment")} deleted`) },
        "Delete all resolved"));
      }
      if (trash.length) {
        foot.append(el("button", { type: "button", class: "trash-toggle", "aria-expanded": String(showTrash),
          onclick: () => { showTrash = !showTrash; draw(); } }, ic(showTrash ? "chevronDown" : "chevron"), ic("trash"), `Deleted (${trash.length})`));
      }
      if (foot.childNodes.length) root.append(foot);
      if (showTrash) for (const item of trash) root.append(card(item));
    }
    root.scrollTop = scroll;
    if (focusId) {
      const cardEl = root.querySelector(`.ccard[data-id="${CSS.escape(focusId)}"]`);
      if (cardEl) cardEl.scrollIntoView({ block: "nearest" });
    }
    const replyArea = root.querySelector(".reply-box textarea");
    if (replyArea) replyArea.focus();
  }

  const STATE_LABEL = { open: "Open", review: "To check", done: "Resolved", deleted: "Deleted" };

  function card(item) {
    if (item.status === "done" && focusId !== item.id && editing !== item.id) {
      /* A resolved comment is one line; a click shows the thread. */
      const line = el("div", { class: "ccard done compact",
        title: `${item.resolved ? `Resolved ${item.resolved}. ` : ""}Click to show the thread`,
        onclick: () => { focusId = item.id; showTarget(item); } },
      el("span", { class: "ok", "aria-hidden": "true" }, "✓"), el("span", { class: "body" }, item.comment.split("\n")[0]),
      el("span", { class: "where" }, where(item)));
      line.dataset.id = item.id;
      return line;
    }
    const node = el("div", { class: `ccard ${item.status}${focusId === item.id ? " on" : ""}` });
    node.dataset.id = item.id;
    const state = el("span", { class: `state ${item.status}`, title: item.status === "done" && item.resolved ? `Resolved ${item.resolved}` : null },
      STATE_LABEL[item.status] || item.status);
    node.append(el("div", { class: "meta" }, ic(item.target ? "target" : item.point ? "dot" : "file", 13),
      el("span", { class: "where" }, where(item)), item.created ? el("span", {}, `· ${when(item.created)}`) : null, state));
    if (editing === item.id) {
      const area = el("textarea", { class: "input", "aria-label": "Edit comment", placeholder: "⌘↵ to save, Esc to cancel",
        oninput: (e) => { editDrafts[item.id] = e.target.value; } });
      area.value = editDrafts[item.id] ?? item.comment;
      const cancel = () => { editing = null; delete editDrafts[item.id]; draw(); };
      const save = async () => {
        const value = area.value;
        const diagram = S.name;
        if (!value.trim() || value.trim() === item.comment) { cancel(); return; }
        const ok = await send({ op: "update", id: item.id, comment: value }, "Comment edited");
        if (ok && S.name === diagram && editing === item.id && (editDrafts[item.id] ?? item.comment) === value) {
          editing = null;
          delete editDrafts[item.id];
          draw();
        } else if (ok && S.name !== diagram) {
          const saved = drafts.get(diagram);
          if (saved && saved.editing === item.id && (saved.editDrafts[item.id] ?? item.comment) === value) {
            saved.editing = null;
            delete saved.editDrafts[item.id];
          }
        }
      };
      area.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) save();
        if (e.key === "Escape") cancel();
      });
      node.append(area, el("div", { class: "actions" }, el("span", { class: "sp" }),
        el("button", { type: "button", class: "btn small ghost", onclick: cancel }, "Cancel"),
        el("button", { type: "button", class: "btn small primary", onclick: save }, "Save")));
    } else {
      node.append(el("div", { class: "body" }, item.comment));
    }
    const replies = item.replies || [];
    if (replies.length) {
      node.append(el("div", { class: "thread" }, replies.map((reply) =>
        el("div", { class: `reply ${reply.by}` }, el("span", { class: "who" }, reply.by === "agent" ? "Agent" : "You"), reply.text))));
    }
    if (replying === item.id) {
      const area = el("textarea", { class: "input", rows: "2",
        placeholder: "Reply to the agent; sending reopens the comment (⌘↵ to send, Esc to cancel)",
        oninput: (e) => { replyDraft = e.target.value; } });
      area.value = replyDraft;
      const submit = () => sendReply(item);
      area.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
        if (e.key === "Escape") { replying = null; replyDraft = ""; draw(); }
      });
      node.append(el("div", { class: "reply-box" }, area, el("div", { class: "foot" },
        el("button", { type: "button", class: "btn small ghost", onclick: () => { replying = null; replyDraft = ""; draw(); } }, "Cancel"),
        el("button", { type: "button", class: "btn small primary", onclick: submit }, "Send"))));
    }
    /* One action per intent: Edit while open, Reply once the agent answered,
     * ✓ Resolve for both, Reopen when resolved; delete is an icon. */
    if (editing !== item.id && replying !== item.id) {
      const actions = el("div", { class: "actions" });
      const btn = (label, fn, cls, title) => actions.append(el("button", { type: "button", class: `btn small ${cls}`, title, onclick: fn }, label));
      const spacer = () => actions.append(el("span", { class: "sp" }));
      const trash = () => actions.append(el("button", { type: "button", class: "icon-btn small del", "aria-label": "Delete comment",
        title: "Delete (Undo brings it back)", onclick: () => send({ op: "delete", id: item.id }, "Comment deleted") }, ic("trash", 15)));
      const resolve = () => send({ op: "update", id: item.id, status: "done" }, "Comment resolved");
      if (item.status === "deleted") {
        spacer();
        btn("Restore", () => send({ op: "restore", id: item.id }, "Comment restored"), "");
      } else if (item.status === "open") {
        btn("Edit", () => { editing = item.id; draw(); }, "ghost");
        spacer();
        btn("✓ Resolve", resolve, "", "You handled it; it leaves the agent's list");
        trash();
      } else if (item.status === "review") {
        btn("Reply", () => { replying = item.id; replyDraft = ""; draw(); }, "ghost", "Ask the agent for more; reopens the comment");
        spacer();
        btn("✓ Resolve", resolve, "primary", "The agent's change is fine");
        trash();
      } else {
        btn("Reopen", () => send({ op: "update", id: item.id, status: "open" }, "Comment reopened for the agent"), "ghost",
          "Send it back to the agent");
        spacer();
        trash();
      }
      node.append(actions);
    }
    node.addEventListener("click", (e) => {
      if (e.target.closest("a, textarea, button")) return;
      focusId = focusId === item.id && item.status === "done" ? null : item.id;
      showTarget(item);
    });
    return node;
  }

  /* Select the comment's target on the canvas (that redraws the panel). */
  function showTarget(item) {
    if (item.target && S.diagram && findTarget(item.target)) {
      ed.select([item.target]);
    } else {
      draw();
      redrawPins();
    }
  }

  function findTarget(id) {
    return S.boxes && S.boxes[id];
  }

  async function sendReply(item) {
    const diagram = S.name, original = replyDraft;
    const text = replyDraft.trim();
    if (!text) return;
    const body = { op: "reply", id: item.id, by: "author", text, status: "open" };
    const ok = await send(body, "Reply sent; the comment is open for the agent again");
    if (ok && S.name === diagram && replying === item.id && replyDraft === original) {
      replying = null;
      replyDraft = "";
      draw();
    } else if (ok && S.name !== diagram) {
      const saved = drafts.get(diagram);
      if (saved && saved.replying === item.id && saved.replyDraft === original) {
        saved.replying = null;
        saved.replyDraft = "";
      }
    }
  }

  async function add() {
    const diagram = S.name, original = draft;
    const text = draft.trim();
    if (!S.name || !text) return;
    const body = { op: "add", diagram: S.name, comment: text };
    if (pin.target) body.target = pin.target;
    if (pin.point) body.point = pin.point;
    const ok = await send(body, "Comment added");
    if (ok && S.name === diagram) {
      filter = "active";
      if (draft === original) {
        draft = "";
        pin = {};
      }
      draw();
    } else if (ok && S.name !== diagram) {
      const saved = drafts.get(diagram);
      if (saved && saved.draft === original) { saved.draft = ""; saved.pin = {}; }
    }
  }

  /* Pins on the overlay: numbered speech-bubble markers. */
  S.drawComments = function drawPins() {
    const overlay = $("#overlay");
    if (!overlay || !S.view) return;
    const z = S.view.zoom;
    const r = 9 / z;
    let n = 0;
    for (const item of comments) {
      if (item.status === "deleted" || item.status === "done") continue;
      n += 1;
      let x = null;
      let y = null;
      if (item.point) {
        x = item.point[0];
        y = item.point[1];
      } else if (item.target && S.boxes[item.target] && S.boxes[item.target].box) {
        const box = S.boxes[item.target].box;
        x = box[0] + box[2];
        y = box[1];
      }
      if (x === null) continue;
      const color = item.status === "review" ? "#e4572e" : "#3166e8";
      const g = document.createElementNS(SVG_NS, "g");
      g.setAttribute("transform", `translate(${x} ${y})`);
      const bubble = document.createElementNS(SVG_NS, "path");
      /* A round marker with its tip at the pinned point (bottom-left). */
      bubble.setAttribute("d", `M0 0 L0 ${-r} A${r} ${r} 0 1 1 ${r} 0 Z`);
      bubble.setAttribute("fill", color);
      bubble.setAttribute("stroke", "#fff");
      bubble.setAttribute("stroke-width", "1.5");
      bubble.setAttribute("vector-effect", "non-scaling-stroke");
      bubble.setAttribute("pointer-events", "all");
      bubble.setAttribute("data-role", "comment");
      bubble.setAttribute("data-comment", item.id);
      bubble.style.cursor = "pointer";
      if (focusId === item.id) bubble.setAttribute("stroke-width", "3");
      const label = document.createElementNS(SVG_NS, "text");
      label.setAttribute("x", String(r * 0.95));
      label.setAttribute("y", String(-r * 0.62));
      label.setAttribute("font-size", String(10 / z));
      label.setAttribute("font-weight", "700");
      label.setAttribute("font-family", "-apple-system, system-ui, sans-serif");
      label.setAttribute("fill", "#fff");
      label.setAttribute("text-anchor", "middle");
      label.setAttribute("pointer-events", "none");
      label.textContent = String(n);
      const title = document.createElementNS(SVG_NS, "title");
      title.textContent = item.comment;
      bubble.append(title);
      g.append(bubble, label);
      overlay.appendChild(g);
    }
  };

  window.DiagramComments = {
    pin(spec) {
      pin = spec || {};
      openTab();
      draw();
      const area = $("#comments") && $("#comments").querySelector(".composer textarea");
      if (area) area.focus();
    },
    focus(id) {
      focusId = id;
      const item = comments.find((c) => c.id === id);
      if (item && item.status !== "deleted") filter = item.status === "done" ? "resolved" : "active";
      openTab();
      draw();
      if (item && item.target && findTarget(item.target)) ed.select([item.target]);
    },
  };

  window.addEventListener("diagramchange", syncDiagram);
  window.addEventListener("beforeunload", (e) => {
    const dirty = (d) => d.draft.trim() || d.replyDraft.trim() || Object.keys(d.editDrafts).length;
    if (pending.size || dirty({ draft, replyDraft, editDrafts }) || [...drafts.entries()].some(([name, d]) => name !== activeDiagram && dirty(d))) {
      e.preventDefault();
      e.returnValue = "";
    }
  });
  setTimeout(syncDiagram, 500);
})();
