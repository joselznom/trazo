// Preview viewer: Cmd+Z and Cmd+Shift+Z while writing on the page's text.
// Enter, Backspace at the start, bold and italic change the page by script and
// save at once (preview-paragraphs.js, preview-format.js), so the browser's own
// undo has nothing of theirs: each is a step here. Cmd+Z takes the latest step
// back while the page reads what the step left; typing after it goes first, by
// the browser. The file goes back with that save's own undo (POST /__source
// {op: undo, expect}, refused if a newer save came), then the page.
// Cmd+Shift+Z does the step again from where Cmd+Z left it. Leaving the text
// ends the history: outside it, Cmd+Z undoes the latest save (undoLatest).
// The parts of the viewer are classic scripts loaded in the order of
// trazo.preview.VIEWER_SCRIPTS; their top-level names are shared globals.
  if (!window.PreviewModel) throw new Error("preview-model.js did not load");  // see preview-page.js
  // ---------------------------------------------------------------- history
  let undoSteps = [], redoSteps = [];
  let historyRun = 0;     // a step answered after the author left the text belongs to no history
  let replaying = false;  // a step Cmd+Shift+Z makes again keeps the steps still to redo

  const blockOf = (el) => { const item = itemOf(el); return item && item !== el ? item : el; };
  // A selection as positions, not a live Range: a step's own changes would move one.
  const pointsOf = (range) => ({ start: [range.startContainer, range.startOffset], end: [range.endContainer, range.endOffset] });
  function select(points) {
    const range = document.createRange();
    range.setStart(...points.start);
    range.setEnd(...points.end);
    getSelection().removeAllRanges();
    getSelection().addRange(range);
  }

  // What a step can change in `outers` (blocks with one parent): their places
  // among its children, their nodes and text, and the data of block marks and chips.
  function capture(outers) {
    const parent = outers[0].parentElement;
    const within = (selector) => outers.flatMap((outer) => [...(outer.matches(selector) ? [outer] : []), ...$$(selector, outer)]);
    return {
      parent,
      children: [...parent.childNodes],
      subtrees: outers.map(snapshotOf),
      data: within(".pv-src, .pv-head, .pv-atom").map((node) => [node, { ...node.dataset }]),
      blocks: [...blockMark].filter(([el]) => outers.some((outer) => outer.contains(el))),
    };
  }

  function putBackState(state) {
    const { parent, children } = state;
    for (const node of [...parent.childNodes]) if (!children.includes(node)) node.remove();
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

  // Called by an action before it changes the page; `outers` are the blocks it changes.
  function newStep(kind, ed, outers, extra = {}) {
    const selection = getSelection();
    return { kind, ...extra, run: historyRun, keepRedo: replaying, target: ed.el, restore: ed.restore,
      at: selection.rangeCount ? pointsOf(selection.getRangeAt(0)) : null, before: capture(outers.map(blockOf)) };
  }

  // Called once the action is on the page and, but for a new empty paragraph, in the file.
  // `results`: [paragraph, text] it left; `saved`: the file's text for the target once undone.
  function addStep(step, { payload, ...rest }) {
    if (step.run !== historyRun || (payload && payload.save_id === undefined)) return;  // a save that wrote nothing
    undoSteps.push({ ...step, ...rest, file: payload?.file, saveId: payload?.save_id });
    if (!step.keepRedo) redoSteps = [];
  }

  function endHistory() {
    undoSteps = [];
    redoSteps = [];
    historyRun++;
  }

  const readsAsLeft = (step) => !failed.size && step.results.every(([el, text]) => el.isConnected && textOf(el) === text)
    && (step.kind !== "draft" || (writing === step.draft && step.draft.draftOf));

  function putBack(step) {
    if (writing) stopWriting();
    putBackState(step.before);
    const ed = startWriting(step.target);
    if (!ed) return;
    ed.before = typeof step.saved === "function" ? step.saved() : step.saved;
    ed.restore = step.restore;
    step.target.focus({ preventScroll: true });
    if (step.at) select(step.at);
    step.undone = textOf(step.target);
    redoSteps.push(step);
  }

  // The latest step, if the page reads what it left; else the browser's undo (typing) goes first.
  function undoStep() {
    const step = undoSteps.at(-1);
    if (!step || !readsAsLeft(step)) return false;
    undoSteps.pop();
    if (!step.saveId) { putBack(step); return true; }
    const ed = writing;
    ed.joining = true;  // keys wait: what is typed before the answer would be lost
    ed.el.contentEditable = "false";
    saves++;
    showSaveState("Undoing…");
    saveQueue = saveQueue.then(async () => {
      let result;
      try {
        result = await postJson("/__source", { op: "undo", file: step.file, expect: step.saveId });
      } catch (e) {
        result = { ok: false, payload: { error: "the preview server did not answer" } };
      }
      saves--;
      if (writing === ed) {
        ed.joining = false;
        ed.el.contentEditable = "plaintext-only";
        ed.el.focus({ preventScroll: true });
      }
      if (!result.ok) {
        showSaveState("", false);
        toast(`Not undone: ${refusal(result.payload)}.`);
        endHistory();
        return;
      }
      showSaveState("Undone", true);
      putBack(step);
    });
    return true;
  }

  // The step Cmd+Z took back, made again from the same place.
  function redoStep() {
    const step = redoSteps.at(-1);
    if (!step || writing?.el !== step.target || textOf(step.target) !== step.undone) return false;
    redoSteps.pop();
    if (step.at) select(step.at);
    replaying = true;
    try {
      if (step.kind === "format") toggleStyle(step.style);
      else if (step.kind === "join") joinBack();
      else breakParagraph();
    } finally { replaying = false; }
    return true;
  }

  page.addEventListener("input", (e) => { if (writing && !(e.inputType || "").startsWith("history")) redoSteps = []; });
  document.addEventListener("keydown", (e) => {
    if (!writing || writing.joining || e.altKey || !(e.metaKey || e.ctrlKey) || e.key.toLowerCase() !== "z") return;
    if (e.shiftKey ? redoStep() : undoStep()) { e.preventDefault(); e.stopPropagation(); }
  }, true);
