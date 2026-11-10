// Preview viewer: a new build of this document changes the page in place.
// Only the blocks that changed are replaced (Model.blocks: the elements
// between sections, compared as built), so the caret, the panels and the
// reading place stay. Anything else takes the reload it took before: other
// sections, what the viewer read at load (files, titles, diagrams, table
// specs), an open caption editor, or something on the page it does not know.
// While the author writes, the blocks they are at wait (heldBlocks) and the
// rest change.
// The parts of the viewer are classic scripts loaded in the order of
// trazo.preview.VIEWER_SCRIPTS; their top-level names are shared globals.
  if (!window.PreviewModel) throw new Error("preview-model.js did not load");  // see preview-page.js
  // ---------------------------------------------------------------- in-place update
  // The runs of `a` and `b` outside their longest common subsequence (pairs
  // that are `same`), as [aStart, aEnd, bStart, bEnd) from `at`.
  function unmatched(a, b, same, at) {
    const n = a.length, m = b.length;
    const common = Array.from({ length: n + 1 }, () => new Int32Array(m + 1));
    for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
      common[i][j] = same(a[i], b[j]) ? common[i + 1][j + 1] + 1 : Math.max(common[i + 1][j], common[i][j + 1]);
    const runs = [];
    let i = 0, j = 0, run = null;
    while (i < n || j < m) {
      if (i < n && j < m && same(a[i], b[j])) {
        if (run) { runs.push([at[0] + run[0], at[0] + i, at[1] + run[1], at[1] + j]); run = null; }
        i++; j++;
        continue;
      }
      if (!run) run = [i, j];
      if (i === n) j++; else if (j === m) i++; else if (common[i + 1][j] >= common[i][j + 1]) i++; else j++;
    }
    if (run) runs.push([at[0] + run[0], at[0] + n, at[1] + run[1], at[1] + m]);
    return runs;
  }

  // Where `old` and `fresh` (block keys) differ, block by block where it can
  // (byBlock), as [oldStart, oldEnd, newStart, newEnd) runs.
  function changedRuns(old, fresh) {
    let start = 0, oldEnd = old.length, newEnd = fresh.length;
    while (start < oldEnd && start < newEnd && old[start] === fresh[start]) start++;
    while (oldEnd > start && newEnd > start && old[oldEnd - 1] === fresh[newEnd - 1]) { oldEnd--; newEnd--; }
    return unmatched(old.slice(start, oldEnd), fresh.slice(start, newEnd), (x, y) => x === y, [start, start])
      .flatMap((run) => byBlock(run, old, fresh));
  }

  // A run split where its blocks are the same block of Markdown (the same
  // data-h: a number or a value changed in it), and one to one where the rest
  // has as many blocks on each side, so a block held for the author holds no other.
  const idOf = (key) => (key.match(/\bdata-h="([^"]+)"/) || [])[1] || null;
  function byBlock([oldStart, oldEnd, newStart, newEnd], old, fresh) {
    const gaps = unmatched(old.slice(oldStart, oldEnd).map(idOf), fresh.slice(newStart, newEnd).map(idOf),
      (x, y) => x !== null && x === y, [oldStart, newStart]);
    const out = [];
    let i = oldStart, j = newStart;
    for (const gap of [...gaps, [oldEnd, oldEnd, newEnd, newEnd]]) {
      for (; i < gap[0]; i++, j++) out.push([i, i + 1, j, j + 1]);  // the same block, changed
      const size = gap[1] - gap[0];
      if (size === gap[3] - gap[2]) for (let k = 0; k < size; k++) out.push([gap[0] + k, gap[0] + k + 1, gap[2] + k, gap[2] + k + 1]);
      else out.push(gap);
      i = gap[1]; j = gap[3];
    }
    return out;
  }

  // A table's block on the page is its scroll wrapper.
  const outerOf = (el) => (el.parentElement && el.parentElement.matches(".pv-tscroll") ? el.parentElement : el);
  const parentIndexes = (sections) => sections.map((s) => sections.indexOf(s.parentElement));
  // The scripts with what the viewer read at load (titles, files, diagrams, table
  // specs, form counts); an MCP App view also has the viewer's own inlined.
  const loadedConfig = (root) => [...root.querySelectorAll("script:not([src])")]
    .filter((s) => s.type === "application/json" || s.textContent.startsWith("window.PREVIEW_DOC = "))
    .map((s) => s.textContent).join("\n");
  // The served page's counts, the last statement of its first script (trazo.server.decorate_page).
  function countsIn(next) {
    const marker = "window.PREVIEW_COUNTS = ";
    const script = [...next.head.querySelectorAll("script:not([src])")].find((s) => s.textContent.includes(marker));
    if (!script) return null;
    const text = script.textContent, at = text.indexOf(marker) + marker.length;
    return JSON.parse(text.slice(at, text.lastIndexOf(";")));
  }

  // The changes from what the page shows to `next`, per group; null when they go beyond blocks.
  function planUpdate(next) {
    next.getElementById("pk-shell-ready")?.remove();  // after the viewer's scripts: never in the page
    const fresh = Model.blocks(next.body);
    if (loadedConfig(next.body) !== loadedConfig(document.body)) return null;
    // A changed image reads the same in its block (trazo.server.image_stamp).
    const images = (d) => d.querySelector('meta[name="trazo-images"]')?.content;
    if (images(next) !== images(document)) return null;
    if (fresh.sections.length !== shownBlocks.sections.length
        || parentIndexes(fresh.sections).join() !== parentIndexes(shownBlocks.sections).join()) return null;
    const groups = fresh.groups.map((els, g) => {
      const old = shownBlocks.groups[g], keys = els.map(Model.blockKey);
      return { container: g ? shownBlocks.sections[g - 1] : page, old, els, keys,
        runs: changedRuns(old.map((r) => r.key), keys) };
    });
    return { fresh, groups };
  }

  // The page's nodes a run replaces, between the blocks around it still on the
  // page (writing may have joined one away): the blocks it had and paragraphs
  // written on the page since (not built yet). A refused save's bar or box
  // belongs to its paragraph, which stays (heldBlocks). `unknown` when
  // something else is there (a caption editor); null when the blocks are not
  // where they were.
  function nodesOf(group, run, tracked) {
    const near = (i, step) => {
      for (; i >= 0 && i < group.old.length; i += step) if (group.old[i].el.isConnected) return outerOf(group.old[i].el);
      return null;
    };
    const before = near(run[0] - 1, -1), after = near(run[1], 1);
    if ((before && before.parentNode !== group.container) || (after && after.parentNode !== group.container)) return null;
    const nodes = [];  // with the white space and comments between them
    let node = before ? before.nextSibling : group.container.firstChild, unknown = false;
    for (; node && node !== after && !(node.nodeType === 1 && node.tagName === "SECTION"); node = node.nextSibling) {
      if (node.nodeType === 1 && node.matches(".pv-write-bar, .pv-write-box")) continue;
      if (node.nodeType === 1 && !tracked.has(node) && !node.matches(".pv-can-write, :has(.pv-src)")) unknown = true;
      nodes.push(node);
    }
    // Every block it had that is still on the page lies there.
    const gone = group.old.slice(run[0], run[1]).map((r) => outerOf(r.el)).filter((el) => el.isConnected);
    if (!gone.every((el) => nodes.includes(el))) return null;
    return { nodes, at: node, unknown };
  }

  // What waits while the author is at it: the paragraph with the caret and the
  // one it drafts from, refused saves, the blocks the writing history may put
  // back, and a selection.
  function heldBlocks() {
    const held = [writing?.el, writing?.draftOf?.el, ...failed.keys()];
    for (const step of [...undoSteps, ...redoSteps])
      held.push(step.target, step.draft?.el, ...(step.els || []), ...(step.afterBlocks || []), ...(step.results || []).map(([el]) => el));
    const selection = getSelection();
    return (nodes) => nodes.some((node) => node.nodeType === 1
      && (held.some((el) => el && node.contains(el)) || (!selection.isCollapsed && selection.containsNode(node, true))));
  }

  // "done", "partial" (blocks held, see heldBlocks), "later" (the author is
  // busy: the next poll tries again) or "reload".
  // A save or a step on its way, or a comment being written, changes the page itself.
  const busyWriting = () => saves > 0 || waiting || stylesSaving.size || writing?.joining || addingComment || !editor.hidden;
  // `wrote`: whether a write was answered since the status that asked for this build.
  async function updatePage(wrote = () => false) {
    if (tableEditors.size || captionEditors.size) return "reload";  // they reopen after it
    if (busyWriting() || wrote()) return "later";
    let next;
    try {
      const response = await fetch(`/${encodeURIComponent(doc)}.html`, { cache: "no-store" });
      if (!response.ok) return "reload";
      next = new DOMParser().parseFromString(await response.text(), "text/html");
    } catch (e) { return "reload"; }
    if (busyWriting() || wrote()) return "later";  // again: it may have started, or ended, meanwhile
    const plan = planUpdate(next);
    if (!plan) return "reload";
    const tracked = new Set(plan.groups.flatMap((group) => group.old.map((r) => outerOf(r.el))));
    const holds = heldBlocks(), work = [];
    let held = false;
    for (const group of plan.groups) for (const run of group.runs) {
      const where = nodesOf(group, run, tracked);
      if (!where) return "reload";
      if (holds(where.nodes)) { held = true; continue; }
      if (where.unknown) return "reload";
      work.push({ group, run, ...where });
    }
    try {
      applyUpdate(next, plan, work);
    } catch (e) {
      console.error(e);
      return "reload";
    }
    return held ? "partial" : "done";
  }

  function applyUpdate(next, plan, work) {
    // The paragraph being written, or the first block in view that stays, keeps its place on the screen.
    const box = scroller.getBoundingClientRect(), top = box.top;
    const replaced = work.flatMap((w) => w.nodes);
    const writingBox = writing && writing.el.getBoundingClientRect();
    const anchor = writingBox && writingBox.bottom > box.top && writingBox.top < box.bottom ? writing.el
      : scroller.scrollTop > 0 ? zoomAnchors.find((el) => el.isConnected
        && !replaced.some((node) => node.contains(el)) && el.getBoundingClientRect().bottom > top) : null;
    const anchorTop = anchor && anchor.getBoundingClientRect().top;
    scroller.style.overflowAnchor = "none";  // the browser's own anchoring would move it twice

    plan.fresh.sections.forEach((section, i) => {
      const live = shownBlocks.sections[i];
      for (const { name } of [...live.attributes]) if (!section.hasAttribute(name)) live.removeAttribute(name);
      for (const { name, value } of section.attributes) if (live.getAttribute(name) !== value) live.setAttribute(name, value);
    });
    const added = [];
    for (const { group, run, nodes, at } of work) {
      for (const node of nodes) node.remove();
      const els = group.els.slice(run[2], run[3]);
      for (const el of els) group.container.insertBefore(el, at);
      added.push(...els);
      historyReplaced(group.container, nodes, els, at);
    }
    // What the page now shows, as built; a held run keeps what it had, so the next update takes it.
    const applied = new Set(work.map((w) => w.run));
    shownBlocks = { sections: shownBlocks.sections, groups: plan.groups.map((group) => {
      const records = [];
      let i = 0, j = 0;
      for (const run of group.runs) {
        while (j < run[2]) { records.push(group.old[i]); i++; j++; }
        if (applied.has(run)) for (; j < run[3]; j++) records.push({ el: group.els[j], key: group.keys[j] });
        else { records.push(...group.old.slice(run[0], run[1])); j = run[3]; }
        i = run[1];
      }
      while (j < group.keys.length) { records.push(group.old[i]); i++; j++; }
      return records;
    }) };

    Object.assign(model, Model.analyse(page, doc));
    Model.crossCitations(analyses);  // what the other documents cite here, which analyse does not see
    for (const el of added) for (const setUp of blockSetups) setUp(el);
    setCounts(COUNTS && countsIn(next));  // also the headings
    for (const refresh of pageRefreshes) refresh();
    markParagraphs();
    diagramButtons();
    const stamp = next.querySelector('meta[name="trazo-page"]'), here = document.querySelector('meta[name="trazo-page"]');
    if (stamp && here) here.content = stamp.content;
    renderComments();
    renderWords();
    loadEdits();
    refreshCleanSources();

    if (anchor && anchor.isConnected) scroller.scrollTop += anchor.getBoundingClientRect().top - anchorTop;
    spy();
    requestAnimationFrame(() => { scroller.style.overflowAnchor = ""; });
  }
