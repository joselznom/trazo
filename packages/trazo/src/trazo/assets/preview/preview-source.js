// Preview viewer: the right sidebar's tabs and the Source tab's editor.
// The parts of the viewer are classic scripts loaded in the order of
// trazo.preview.VIEWER_SCRIPTS; their top-level names are shared globals.
  if (!window.PreviewModel) throw new Error("preview-model.js did not load");  // see preview-page.js
  // ---------------------------------------------------------------- right tabs
  function setTab(t) {
    tab = t;
    savePrefs({ tab });
    for (const b of $$("[role=tab]", rtabs)) {
      const on = b.dataset.tab === t;
      b.setAttribute("aria-selected", String(on));
      b.tabIndex = on ? 0 : -1;
    }
    for (const p of paneEls) p.hidden = p.dataset.pane !== t;
    syncRight();
  }
  function showSide(t) {
    if (S.narrow) S.drawer = "right"; else S.right = true;
    applyLayout();
    if (!S.narrow) savePrefs({ right: true });
    setTab(t);
  }
  rtabs.addEventListener("click", (e) => { const b = e.target.closest("[role=tab]"); if (b) setTab(b.dataset.tab); });
  rtabs.addEventListener("keydown", (e) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    const list = $$("[role=tab]", rtabs), at = list.indexOf(document.activeElement);
    if (at < 0) return;
    const to = e.key === "Home" ? 0 : e.key === "End" ? list.length - 1 : (at + (e.key === "ArrowRight" ? 1 : -1) + list.length) % list.length;
    list[to].focus();
    setTab(list[to].dataset.tab);
    e.preventDefault();
  });
  // The Source and Diff tabs load when they are shown.
  function syncRight() {
    if (!sourceReady) return;
    syncSource();
    refreshDiff();
  }

  // ---------------------------------------------------------------- unsaved work
  // Anything that would be lost on reload registers here.
  const unsavedChecks = [() => !editor.hidden && $("textarea", editor).value.trim() !== "",
                         () => Object.keys(drafts).length > 0 || commentSaves.size > 0];
  const hasUnsaved = () => unsavedChecks.some((f) => f());

  // ---------------------------------------------------------------- source editor
  // Markdown source editor: edits the raw .md file behind the section in
  // view, in the sidebar's "Source" tab.
  const FILES = window.PREVIEW_FILES || [];
  const norm = (t) => t.toLowerCase().replace(/\s+/g, " ").trim();
  const titled = FILES.filter((f) => f.title);
  const fileOfSection = (sec) => titled.find((f) => norm(f.title) === norm(titleOf(sec)))
    || titled[topSections.indexOf(sec)] || null;
  const fileOfEl = (el) => {
    const top = topSections.find((s) => s.contains(el));
    return top ? fileOfSection(top) : null;
  };
  // The file of the section the reader is looking at (scroll-spy).
  let spyFile = null, followed = null, started = false;
  function onSpyHeading(el) {
    const f = fileOfEl(el);
    if (f && f.file !== spyFile) { spyFile = f.file; if (started) syncRight(); }
  }

  // A tiny editor interface; a CodeMirror wrapper can implement the same.
  function createTextEditor(parent) {
    const ta = document.createElement("textarea");
    ta.className = "pv-src-text";
    ta.setAttribute("aria-label", "Markdown source");
    ta.spellcheck = false;
    parent.append(ta);
    const offsetOfLine = (n) => {
      const v = ta.value;
      let at = 0;
      for (let i = 1; i < n; i++) {
        const k = v.indexOf("\n", at);
        if (k < 0) return v.length;
        at = k + 1;
      }
      return at;
    };
    return {
      getValue: () => ta.value,
      setValue: (v) => { ta.value = v; },
      focus: () => ta.focus(),
      getSelection: () => [ta.selectionStart, ta.selectionEnd],
      setSelection: (a, b) => ta.setSelectionRange(a, b),
      selectLines(first, last) {
        const a = offsetOfLine(first);
        ta.setSelectionRange(a, Math.max(a, Math.min(ta.value.length, offsetOfLine(last + 1) - 1)));
      },
      // Approximate: soft-wrapped lines have no fixed height.
      scrollToLine(n) {
        const share = offsetOfLine(n) / Math.max(1, ta.value.length);
        ta.scrollTop = Math.max(0, ta.scrollHeight * share - ta.clientHeight / 3);
      },
      getScroll: () => ta.scrollTop,
      setScroll: (y) => { ta.scrollTop = y; },
      onChange: (fn) => ta.addEventListener("input", () => fn(ta.value)),
      show: (visible) => { ta.hidden = !visible; },
      setReadOnly: (readOnly) => { ta.readOnly = readOnly; },
    };
  }

  srcBox.innerHTML =
    `<div class="pv-src-head"><select class="pv-src-file" aria-label="Source file" title="Source file (follows the section in view; pick another one if you like)"></select>` +
    `<span class="pv-src-state" role="status"></span></div>` +
    `<div class="pv-src-conflict" hidden><span>The file changed on disk.</span> ` +
    `<button type="button" data-op="reload">Reload from disk</button> <button type="button" data-op="diff">View diff</button> <button type="button" data-op="overwrite">Overwrite</button></div>` +
    `<div class="pv-src-body"></div>` +
    `<div class="pv-src-bar"><button type="button" class="pv-btn primary sm" data-op="save" title="Save (⌘S / Ctrl+S)">Save</button>` +
    `<button type="button" class="pv-btn sm" data-op="undo-save" title="Restore the content before the last save, if the file is unchanged since">Undo save</button>` +
    `<span class="pv-src-msg" role="status"></span></div>`;
  const srcSelect = $(".pv-src-file", srcBox);
  const srcState = $(".pv-src-state", srcBox);
  const srcMsg = $(".pv-src-msg", srcBox);
  const srcConflict = $(".pv-src-conflict", srcBox);
  const srcBody = $(".pv-src-body", srcBox);
  const saveBtn = $('[data-op="save"]', srcBox);
  const undoBtn = $('[data-op="undo-save"]', srcBox);
  const editor2 = createTextEditor(srcBody);
  const diffEl = document.createElement("div");
  diffEl.className = "pv-src-diff";
  diffEl.hidden = true;
  srcBody.append(diffEl);
  for (const f of FILES) {
    const o = document.createElement("option");
    o.value = f.file;
    o.textContent = f.file.split("/").pop() + (f.title ? " · " + f.title : "");
    srcSelect.append(o);
  }

  // One buffer per file, so edits survive switching sections.
  const buffers = new Map(), loads = new Map(), sourceBusy = new Set();
  let sourceViewSeq = 0;
  let cur = null, restore = null;
  try { restore = JSON.parse(sget("preview-src:" + Trazo.path())); } catch (e) { restore = null; }
  const isDirty = (b) => b.text !== b.base;
  const setMsg = (t) => { srcMsg.textContent = t || ""; };
  const toDisk = (b) => (b.eol === "\r\n" ? b.text.replace(/\n/g, "\r\n") : b.text);
  const fromState = (st) => {
    const text = st.text.replace(/\r\n/g, "\n");
    return { text, base: text, eol: st.text.includes("\r\n") ? "\r\n" : "\n",
      sha: st.sha256, canUndo: st.can_undo, conflict: null, sel: [0, 0], scroll: 0 };
  };
  unsavedChecks.push(() => sourceBusy.size > 0 || [...buffers.values()].some(isDirty));
  const fileMsg = (file, text) => { if (cur === file) setMsg(text); };

  async function sourceOperation(file, action) {
    if (!file || sourceBusy.has(file)) return;
    sourceBusy.add(file);
    updateSourceUi();
    try { await action(); }
    catch (e) { fileMsg(file, "Could not complete the operation: " + e.message); }
    finally { sourceBusy.delete(file); updateSourceUi(); }
  }

  function acceptSource(file, state) {
    const b = fromState(state);
    buffers.set(file, b);
    if (cur === file) {
      editor2.setValue(b.text);
      diffEl.hidden = true;
      editor2.show(true);
    }
  }

  async function fetchSource(file) {
    const res = await fetch(`/__source?file=${encodeURIComponent(file)}`, { cache: "no-store" });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(payload.error || res.status);
    return payload;
  }
  async function postJson(path, body) {
    const result = await App.api(path, body);
    return { ...result, payload: result.data };
  }

  function updateSourceUi() {
    const b = cur && buffers.get(cur);
    const busy = sourceBusy.has(cur);
    editor2.setReadOnly(!b || busy);
    srcState.textContent = !b ? "" : isDirty(b) ? "● unsaved changes" : "saved";
    srcState.classList.toggle("dirty", !!b && isDirty(b));
    saveBtn.disabled = !b || busy || !isDirty(b);
    undoBtn.disabled = !b || busy || !b.canUndo;
    srcConflict.hidden = !(b && b.conflict);
    $(".dirty", rtabs).hidden = ![...buffers.values()].some(isDirty);
  }

  async function showFile(file) {
    ++sourceViewSeq;
    if (cur && buffers.has(cur)) {
      const b = buffers.get(cur);
      b.text = editor2.getValue();
      b.sel = editor2.getSelection();
      b.scroll = editor2.getScroll();
    }
    cur = file;
    srcSelect.value = file;
    diffEl.hidden = true;
    editor2.show(true);
    updateSourceUi();
    if (!buffers.has(file)) {
      editor2.setValue("");
      setMsg("Loading…");
      try {
        if (!loads.has(file)) loads.set(file, fetchSource(file));
        const state = await loads.get(file);
        loads.delete(file);
        if (!buffers.has(file)) {  // a concurrent call may have stored it
          const b = fromState(state);
          if (restore && restore.file === file) { b.sel = restore.sel; b.scroll = restore.scroll; restore = null; }
          buffers.set(file, b);
        }
        fileMsg(file, "");
      } catch (e) { loads.delete(file); fileMsg(file, "Could not load: " + e.message); updateSourceUi(); return false; }
      if (cur !== file) return false;  // the user moved on meanwhile
    }
    const b = buffers.get(file);
    editor2.setValue(b.text);
    editor2.setSelection(b.sel[0], b.sel[1]);
    editor2.setScroll(b.scroll);
    updateSourceUi();
    return true;
  }

  // The Source tab follows the section in view while it has no unsaved edits
  // (an explicit pick in the file list stays until the reader moves to
  // another file's section).
  function syncSource() {
    if (!sourceReady || !rightOpen() || tab !== "source" || !FILES.length) return;
    const b = cur && buffers.get(cur);
    if (!cur) {
      followed = spyFile;
      showFile(restore ? restore.file : spyFile || FILES[0].file);
    } else if (spyFile && spyFile !== cur && spyFile !== followed && !(b && isDirty(b))) {
      followed = spyFile;
      showFile(spyFile);
    }
  }
  srcSelect.addEventListener("change", () => showFile(srcSelect.value));
  editor2.onChange((v) => {
    const b = buffers.get(cur);
    if (b) { b.text = v; updateSourceUi(); }
  });

  async function persistSource(file, b) {
    if (b.conflict) { fileMsg(file, "Resolve the conflict first: reload, view diff or overwrite."); return; }
    if (!isDirty(b)) return;
    const sent = b.text;
    fileMsg(file, "Saving…");
    const { status, ok, payload } = await postJson("/__source", { file, text: toDisk(b), base_sha256: b.sha });
    if (status === 409) { b.conflict = { sha: payload.sha256 }; fileMsg(file, ""); }
    else if (!ok) fileMsg(file, "Save failed: " + (payload.error || status));
    else {
      b.base = sent; b.sha = payload.sha256; b.canUndo = payload.can_undo;
      fileMsg(file, "Saved. The preview rebuilds and reloads.");
      refreshDiff();
    }
  }

  async function saveSource() {
    const file = cur, b = buffers.get(file);
    if (b) await sourceOperation(file, () => persistSource(file, b));
  }

  async function reloadFromDisk() {
    const file = cur, old = buffers.get(file);
    if (!old || sourceBusy.has(file)) return;
    if (old && isDirty(old) && !confirm("Discard your unsaved edits and reload from disk?")) return;
    await sourceOperation(file, async () => {
      acceptSource(file, await fetchSource(file));
      fileMsg(file, "Reloaded from disk.");
    });
  }

  async function undoSave() {
    const file = cur, b = buffers.get(file);
    if (!b || !b.canUndo || sourceBusy.has(file)) return;
    if (isDirty(b) && !confirm("Discard your unsaved edits and undo the last save?")) return;
    await sourceOperation(file, async () => {
      const { ok, payload } = await postJson("/__source", { op: "undo", file });
      if (!ok) { b.canUndo = false; fileMsg(file, payload.error || "Could not undo"); }
      else {
        acceptSource(file, payload);
        fileMsg(file, "Last save undone.");
      }
    });
  }
