// Preview viewer: Cmd+Z and Cmd+Shift+Z while writing on the page's text.
// Enter, Backspace at the start, bold and italic change the page by script and
// save at once (preview-paragraphs.js, preview-format.js), so the browser's own
// undo has nothing of theirs: each is a step here, in the order they were done.
// Cmd+Z takes the latest step back while the page reads what it left; typing
// after it goes first, by the browser. The file goes back with that save's own
// undo (POST /__source {op: undo, expect}, refused if a newer save came), then
// the page. Cmd+Shift+Z sends the same save again and puts back the page as
// Cmd+Z found it. Leaving the text ends the history: outside it, Cmd+Z undoes
// the latest save (undoLatest).
// The parts of the viewer are classic scripts loaded in the order of
// trazo.preview.VIEWER_SCRIPTS; their top-level names are shared globals.
  if (!window.PreviewModel) throw new Error("preview-model.js did not load");  // see preview-page.js
  // ---------------------------------------------------------------- history
  let undoSteps = [], redoSteps = [];
  let historyRun = 0;     // a step answered after the author left the text belongs to no history
  const MAX_STEPS = 100;  // each keeps the nodes of the blocks it changed

  const blockOf = (el) => { const item = itemOf(el); return item && item !== el ? item : el; };
  // A selection as positions, not a live Range: a step's own changes would move one.
  const pointsOf = (range) => ({ start: [range.startContainer, range.startOffset], end: [range.endContainer, range.endOffset] });
  const here = () => (getSelection().rangeCount ? pointsOf(getSelection().getRangeAt(0)) : null);
  function select(points) {
    if (!points) return;
    const range = document.createRange();
    range.setStart(...points.start);
    range.setEnd(...points.end);
    getSelection().removeAllRanges();
    getSelection().addRange(range);
  }

  // The blocks `els` (with one parent): their places among its children, their
  // nodes and text, and the data of block marks and chips.
  function capture(els) {
    const outers = els.map(blockOf), parent = outers[0].parentElement;
    const within = (selector) => outers.flatMap((outer) => [...(outer.matches(selector) ? [outer] : []), ...$$(selector, outer)]);
    return {
      parent,
      children: [...parent.childNodes],
      subtrees: outers.map(snapshotOf),
      data: within(".pv-src, .pv-head, .pv-atom").map((node) => [node, { ...node.dataset }]),
      blocks: within(`p, li, ${HEADINGS}`).filter((el) => blockMark.has(el)).map((el) => [el, blockMark.get(el)]),
    };
  }
  const reread = (state) => { state.data = state.data.map(([node]) => [node, { ...node.dataset }]); };
  const sameChildren = (parent, children) => parent.childNodes.length === children.length
    && children.every((node, i) => parent.childNodes[i] === node);

  function putBackState(state) {
    const { parent, children } = state, kept = new Set(children);
    for (const node of [...parent.childNodes]) if (!kept.has(node)) node.remove();
    // Only what moved is put back in its place: moving the rest would collapse their comments' ranges.
    let next = null;
    for (let i = children.length - 1; i >= 0; i--) {
      const node = children[i];
      if (node.parentNode !== parent || node.nextSibling !== next) parent.insertBefore(node, next);
      next = node;
    }
    for (const restore of state.subtrees) restore();
    for (const [node, data] of state.data) {
      for (const key of Object.keys(node.dataset)) if (!(key in data)) delete node.dataset[key];
      Object.assign(node.dataset, data);
    }
    for (const [el, mark] of state.blocks) blockMark.set(el, mark);
    forgetPageText();
  }

  // Before an action changes the page: the blocks it changes and where the caret is.
  // `onAnswer`: it changes the page only on its save's answer (a style, a join).
  function newStep(kind, ed, els, extra = {}) {
    return { kind, ...extra, run: historyRun, ed, els, target: ed.el, restore: ed.restore, at: here(), before: capture(els) };
  }

  // Once the action is on the page (a join's, once joined: its keys wait meanwhile).
  // results: [paragraph, text] it left; left: its parent's children then; after: the blocks
  // it left, the caret going to `afterTarget`; saved: the file's text for `target` once undone.
  function addStep(step, fields) {
    Object.assign(step, fields);
    undoSteps.push(step);
    if (undoSteps.length > MAX_STEPS) undoSteps.shift();
    redoSteps = [];
  }
  function dropStep(step) {
    step.dropped = true;
    undoSteps = undoSteps.filter((s) => s !== step);
  }
  // sendBlock's hooks: the request as sent (the keys are now those of the file it goes to), and its answer.
  function stepSent(step, request) {
    step.request = request;
    if (!step.onAnswer) { reread(step.before); return; }
    // The page it changes is the one now, after every save queued before it showed.
    step.before = capture(step.els);
    step.restore = step.ed.restore;
    step.at = step.range ? pointsOf(step.range) : null;  // a style's own live range follows those changes
    if (step.kind === "format") step.left = step.before.children;
  }
  function stepSaved(step, payload, sent) {
    if (payload.save_id == null) { dropStep(step); return; }  // a save that wrote nothing
    step.saveId = payload.save_id;
    step.file = payload.file;
    step.saved = sent.before;
  }

  function endHistory() {
    undoSteps = [];
    redoSteps = [];
    historyRun++;
  }

  const readsAsLeft = (step) => !failed.size && !!step.results && step.run === historyRun
    && step.results.every(([el, text]) => el.isConnected && textOf(el) === text)
    && sameChildren(step.before.parent, step.left)
    && (step.kind !== "draft" || (writing === step.draft && !!step.draft.draftOf));

  // Writing in `el` again, the caret at `points`; `before` is what the file has for it.
  function resume(el, before, restore, points) {
    const ed = startWriting(el);
    if (!ed) return null;
    ed.before = before;
    if (restore) ed.restore = restore;
    el.focus({ preventScroll: true });
    select(points);
    return ed;
  }

  function putBack(step) {
    step.after = capture(step.afterBlocks);
    if (writing) stopWriting();
    if (step.kind === "draft") {
      // Only the empty paragraph goes: its neighbour keeps what saves gave it since.
      dropParagraph(step.draft);
      resume(step.target, step.saved(), step.restore, null);
      caretAtEdge(step.target, !step.above);
    } else {
      putBackState(step.before);
      resume(step.target, step.saved, step.restore, step.at);
      if (!step.at) caretAtEdge(step.target, false);  // a join: back to where Backspace was pressed
    }
    step.undone = textOf(step.target);
    redoSteps.push(step);
  }

  // One request of the history, after the saves before it; keys wait for its answer.
  function queueStep(path, body, label, answered) {
    const ed = writing;
    holdKeys(ed, true);
    saves++;
    showSaveState(label);
    saveQueue = saveQueue.then(async () => {
      const result = typeof body === "function" ? body() : body;
      const answer = result && await postQueued(path, result);
      saves--;
      if (writing === ed) holdKeys(ed, false);
      answered(answer);
    });
  }

  // The latest step, if the page reads what it left; else the browser's undo (typing) goes first.
  function undoStep() {
    const step = undoSteps.at(-1);
    if (!step || !readsAsLeft(step)) return false;
    undoSteps.pop();
    step.afterAt = here();
    if (step.kind === "draft") { putBack(step); return true; }
    // A step still saving is undone once its answer came: a failed one has nothing to undo.
    queueStep("/__source", () => !step.dropped && { op: "undo", file: step.file, expect: step.saveId }, "Undoing…", (answer) => {
      if (!answer) { showSaveState("", false); return; }
      if (!answer.ok) {
        showSaveState("", false);
        toast(`Not undone: ${refusal(answer.payload)}.`);
        endHistory();
        return;
      }
      showSaveState("Undone", true);
      putBack(step);
    });
    return true;
  }

  // The step Cmd+Z took back: the same save again, and the page Cmd+Z found.
  function redoStep() {
    const step = redoSteps.at(-1);
    if (!step || writing?.el !== step.target || textOf(step.target) !== step.undone
        || !sameChildren(step.before.parent, step.before.children)) return false;
    redoSteps.pop();
    const again = (before) => {
      stopWriting();
      putBackState(step.after);
      const ed = resume(step.afterTarget, before ?? textOf(step.afterTarget), null, step.afterAt);
      undoSteps.push(step);
      return ed;
    };
    if (step.kind === "draft") {
      const anchor = writing;
      const draft = again("");
      if (draft) { draft.draftOf = anchor; draft.above = step.above; step.draft = draft; }
      return true;
    }
    queueStep(step.request.path, step.request.body, "Saving…", (answer) => {
      if (!answer.ok || !answer.payload.saved) {
        showSaveState("", false);
        toast(`Not redone: ${refusal(answer.payload)}.`);
        endHistory();
        return;
      }
      showSaveState("Saved", true);
      step.saveId = answer.payload.save_id;
      again();
    });
    return true;
  }

  page.addEventListener("input", (e) => { if (writing && !(e.inputType || "").startsWith("history")) redoSteps = []; });
  document.addEventListener("keydown", (e) => {
    if (!writing || writing.joining || e.altKey || !(e.metaKey || e.ctrlKey) || e.key.toLowerCase() !== "z") return;
    if (e.shiftKey ? redoStep() : undoStep()) { e.preventDefault(); e.stopPropagation(); }
  }, true);
