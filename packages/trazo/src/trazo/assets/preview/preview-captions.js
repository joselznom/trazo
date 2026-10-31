// Preview viewer: caption titles and the shared caption editor.
// The parts of the viewer are classic scripts loaded in the order of
// trazo.preview.VIEWER_SCRIPTS; their top-level names are shared globals.
  if (!window.PreviewModel) throw new Error("preview-model.js did not load");  // see preview-page.js
  // ---------------------------------------------------------------- caption titles
  // A caption is "[Title.]{.caption-title} Legend": the preview shows the
  // title in bold next to its number, Word keeps its caption style.
  function splitCaption(md) {
    const text = md.trim();
    if (text[0] === "[") {
      for (let i = 0, depth = 0; i < text.length; i++) {
        if (text[i] === "\\") { i++; continue; }
        if (text[i] === "[") depth++;
        else if (text[i] === "]" && --depth === 0) {
          const tag = text.slice(i + 1).match(/^\{\.caption-title\}\s*/);
          if (tag) return { title: text.slice(1, i), legend: text.slice(i + 1 + tag[0].length) };
          break;
        }
      }
    }
    return { title: "", legend: text };
  }
  const joinCaption = (title, legend) => {
    const t = title.trim().replace(/\s+/g, " "), l = legend.trim();
    return t ? `[${t}]{.caption-title}${l ? " " + l : ""}` : l;
  };
  // Brackets in a title must pair up, or the title would end early.
  const balanced = (text) => {
    let depth = 0;
    for (let i = 0; i < text.length && depth >= 0; i++) {
      if (text[i] === "\\") i++;
      else if (text[i] === "[") depth++;
      else if (text[i] === "]") depth--;
    }
    return depth === 0;
  };

  // The first sentence of an untitled caption ("Study design. (A) ..."),
  // offered as its title: it ends at ". ", "? " or "! " before a capital or
  // an opening bracket, outside brackets, math and code, and leaves a legend.
  function firstSentence(text) {
    let square = 0, round = 0, math = false;
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (ch === "\\") i++;
      else if (ch === "`") {
        const run = text.slice(i).match(/^`+/)[0], end = text.indexOf(run, i + run.length);
        i = (end < 0 ? i : end) + run.length - 1;
      }
      else if (ch === "$") math = !math;
      else if (math) continue;
      else if (ch === "[") square++;
      else if (ch === "]") square = Math.max(0, square - 1);
      else if (ch === "(") round++;
      else if (ch === ")") round = Math.max(0, round - 1);
      else if (!square && !round && ".?!".includes(ch) && /^\s+[\p{Lu}([]/u.test(text.slice(i + 1, i + 4))) {
        const title = text.slice(0, i + 1);
        return balanced(title) ? { title, legend: text.slice(i + 1).trim() } : null;
      }
    }
    return null;
  }

  // ---------------------------------------------------------------- caption editors
  // Table and figure captions
  // share one frame: the source file and the unsaved state in the head, the
  // fields, Save / Cancel / Undo save, and a notice when the file changed on
  // disk. Saving closes the editor; one left open reopens after a live
  // reload. ⌘/Ctrl+Enter saves, Escape or Cancel closes.
  const unpaired = "The title has an unpaired [ or ].";
  const getJson = (path) => postJson(path);  // no body: GET

  // `ed` holds the editor's own parts:
  //   dirty(), problem()    unsaved edits; why they cannot be saved, or ""
  //   offer(), useOffer(s)  a first-sentence title to propose (captions)
  //   refresh(busy)         lock the fields while a request runs
  //   load(), send(op, sha) GET the payload; POST a save or undo
  //   accept(payload)       fill the fields; removed() forgets the editor
  //   saved, restored, discard  messages
  // Text at the caret of a rich field (chips stay intact); paragraphs of a
  // note go on lines of their own.
  function insertPlain(paragraphs) {
    const sel = window.getSelection();
    if (!sel.rangeCount) return;
    const range = sel.getRangeAt(0), node = document.createTextNode(paragraphs.join("\n"));
    range.deleteContents(); range.insertNode(node);
    range.setStartAfter(node); range.collapse(true);
    sel.removeAllRanges(); sel.addRange(range);
  }

  function editorFrame(heading, ed, { tools = "" } = {}) {
    const box = h("div", { class: "pv-tf pv-caption-editor" });
    box.innerHTML =
      `<div class="pv-tf-head"><b></b> <code></code><span class="pv-tf-state" role="status"></span></div>` +
      `<div class="pv-tf-main"></div>` +
      `<div class="pv-tf-bar"><button type="button" data-op="save" title="Save (⌘↵ / Ctrl+Enter)">Save</button>` +
      `<button type="button" data-op="close" title="Cancel (Esc)">Cancel</button>` +
      `<button type="button" data-op="undo" title="Restore the text before the last save, if the file is unchanged since">Undo save</button>` +
      tools + (ed.offer ? `<button type="button" data-op="offer" title="Make the legend's first sentence the title" hidden>Use first sentence as title</button>` : "") +
      `<span class="pv-tf-msg" role="status"></span></div>` +
      `<div class="pv-tf-conflict" hidden>The file changed on disk. <button type="button" data-op="reload">Reload it</button></div>`;
    $(".pv-tf-head b", box).textContent = heading;
    const state = { busy: false, sha: null, canUndo: false };
    const button = (op) => $(`[data-op="${op}"]`, box);
    const msgEl = $(".pv-tf-msg", box);
    const msg = (t) => { msgEl.textContent = t || ""; };
    let shownProblem = "";

    function refresh() {
      const dirty = ed.dirty(), problem = ed.problem();
      $(".pv-tf-state", box).textContent = dirty ? "● unsaved changes" : "";
      button("save").disabled = state.busy || !dirty || !!problem;
      button("undo").disabled = state.busy || !state.canUndo;
      button("close").disabled = button("reload").disabled = state.busy;
      if (ed.offer) { button("offer").hidden = !ed.offer(); button("offer").disabled = state.busy; }
      if (problem) msg(problem);
      else if (shownProblem && msgEl.textContent === shownProblem) msg("");
      shownProblem = problem;
      ed.refresh(state.busy);
    }

    function remove() { box.remove(); ed.removed(); }

    async function run(op) {
      if (state.busy || (op === "save" && (!ed.dirty() || ed.problem()))) return;
      if ((op === "undo" || op === "reload") && ed.dirty()
        && !confirm(op === "undo" ? "Discard your unsaved edits and undo the last save?" : "Discard your unsaved edits and reload the file?")) return;
      state.busy = true; refresh();
      try {
        const r = op === "load" || op === "reload" ? await ed.load() : await ed.send(op, state.sha);
        if (r.status === 409) { $(".pv-tf-conflict", box).hidden = false; msg(r.payload.error); }
        else if (!r.ok) msg(r.payload.error || `Could not ${op}.`);
        else if (op === "save") { state.busy = false; remove(); toast(ed.saved); return; }
        else {
          state.sha = r.payload.sha256; state.canUndo = r.payload.can_undo;
          $(".pv-tf-head code", box).textContent = r.payload.file;
          $(".pv-tf-conflict", box).hidden = true;
          ed.accept(r.payload);
          msg(op === "undo" ? ed.restored : "");
        }
      } catch (e) { msg(`Could not ${op}: ${e.message}`); }
      state.busy = false; refresh();
    }

    box.addEventListener("click", (e) => {
      const op = e.target.dataset && e.target.dataset.op;
      if (op === "close") { if (!state.busy && (!ed.dirty() || confirm(ed.discard))) remove(); }
      else if (op === "offer") {
        const s = ed.offer();
        if (s) { ed.useOffer({ title: s.title.replace(/\s+/g, " "), legend: s.legend }); refresh(); }
      } else if (op === "save" || op === "undo" || op === "reload") run(op);
    });
    box.addEventListener("input", refresh);
    box.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.preventDefault(); button("close").click(); return; }
      if (e.key !== "Enter") return;
      if (e.metaKey || e.ctrlKey) { e.preventDefault(); run("save"); }
      else if (e.target.isContentEditable) {
        // A rich field is one paragraph; a note's Enter starts the next one.
        e.preventDefault();
        if (e.target.dataset.paragraphs) { document.execCommand("insertLineBreak"); refresh(); }
      }
    });
    // Paste into a rich field as plain text: one line, or a note's paragraphs.
    box.addEventListener("paste", (e) => {
      const field = e.target.closest && e.target.closest("[contenteditable=true]");
      if (!field) return;
      e.preventDefault();
      const text = (e.clipboardData.getData("text/plain") || "").split(/\n\s*\n/).map((p) => p.replace(/\s+/g, " ").trim());
      insertPlain(field.dataset.paragraphs ? text.filter(Boolean) : [text.join(" ")]);
      refresh();
    });
    return { box, main: $(".pv-tf-main", box), state, refresh, run, unsaved: () => state.busy || ed.dirty() };
  }
