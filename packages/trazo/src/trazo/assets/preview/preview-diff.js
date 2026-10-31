// Preview viewer: the source diff, opening the editor at a paragraph and the Diff tab.
// The parts of the viewer are classic scripts loaded in the order of
// trazo.preview.VIEWER_SCRIPTS; their top-level names are shared globals.
  if (!window.PreviewModel) throw new Error("preview-model.js did not load");  // see preview-page.js
  // Line diff (LCS), with a word diff inside paired changed lines.
  function lcsDiff(a, b) {
    let s = 0;
    while (s < a.length && s < b.length && a[s] === b[s]) s++;
    let ea = a.length, eb = b.length;
    while (ea > s && eb > s && a[ea - 1] === b[eb - 1]) { ea--; eb--; }
    const A = a.slice(s, ea), B = b.slice(s, eb), n = A.length, m = B.length;
    const ops = a.slice(0, s).map((x) => ["=", x]);
    if (n * m > 4e6) {
      A.forEach((x) => ops.push(["-", x]));
      B.forEach((x) => ops.push(["+", x]));
    } else {
      const dp = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
      for (let i = n - 1; i >= 0; i--)
        for (let j = m - 1; j >= 0; j--)
          dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
      let i = 0, j = 0;
      while (i < n || j < m) {
        if (i < n && j < m && A[i] === B[j]) { ops.push(["=", A[i]]); i++; j++; }
        else if (j >= m || (i < n && dp[i + 1][j] >= dp[i][j + 1])) { ops.push(["-", A[i]]); i++; }
        else { ops.push(["+", B[j]]); j++; }
      }
    }
    for (let k = ea; k < a.length; k++) ops.push(["=", a[k]]);
    return ops;
  }

  function wordSpans(line, other, cls) {
    const row = document.createElement("div");
    row.className = "pv-diff-" + cls;
    row.append(cls === "del" ? "− " : "+ ");
    const tok = (t) => t.split(/(\s+)/);
    const ops = cls === "del" ? lcsDiff(tok(line), tok(other)) : lcsDiff(tok(other), tok(line));
    for (const [op, t] of ops) {
      if (op === "=") row.append(t);
      else if ((op === "-") === (cls === "del")) {
        const mark = document.createElement("mark");
        mark.textContent = t;
        row.append(mark);
      }
    }
    return row;
  }

  function renderDiff(disk, local) {
    const ops = lcsDiff(disk.split("\n"), local.split("\n"));
    diffEl.textContent = "";
    const head = document.createElement("div");
    head.className = "pv-diff-head";
    head.append("On disk (−) vs your edits (+). ");
    const back = document.createElement("button");
    back.type = "button";
    back.textContent = "Back to editor";
    back.addEventListener("click", () => { diffEl.hidden = true; editor2.show(true); });
    head.append(back);
    diffEl.append(head);
    const line = (cls, mark, t) => {
      const d = document.createElement("div");
      d.className = "pv-diff-" + cls;
      d.textContent = mark + t;
      return d;
    };
    const context = (t) => diffEl.append(line("eq", "  ", t));
    const pending = [];
    let k = 0;
    while (k < ops.length) {
      if (ops[k][0] === "=") { pending.push(ops[k][1]); k++; continue; }
      const dels = [], adds = [];
      while (k < ops.length && ops[k][0] !== "=") { (ops[k][0] === "-" ? dels : adds).push(ops[k][1]); k++; }
      const before = pending.splice(0);
      if (before.length > 2) diffEl.append(line("gap", "", "⋯"));
      before.slice(-2).forEach(context);
      for (let i = 0; i < Math.max(dels.length, adds.length); i++) {
        if (i < dels.length && i < adds.length) {
          diffEl.append(wordSpans(dels[i], adds[i], "del"), wordSpans(adds[i], dels[i], "add"));
        } else if (i < dels.length) diffEl.append(line("del", "− ", dels[i]));
        else diffEl.append(line("add", "+ ", adds[i]));
      }
      for (let q = 0; q < 2 && k < ops.length && ops[k][0] === "="; q++) { context(ops[k][1]); k++; }
    }
    if (!diffEl.querySelector(".pv-diff-del, .pv-diff-add")) diffEl.append(line("gap", "", "No differences."));
    diffEl.hidden = false;
    editor2.show(false);
  }

  async function viewDiff() {
    const file = cur, b = buffers.get(file), seq = sourceViewSeq;
    if (!b) return;
    await sourceOperation(file, async () => {
      const disk = (await fetchSource(file)).text.replace(/\r\n/g, "\n");
      if (cur === file && seq === sourceViewSeq) renderDiff(disk, b.text);
    });
  }

  async function overwrite() {
    const file = cur, b = buffers.get(file);
    if (!b || sourceBusy.has(file) || !confirm("Overwrite the version on disk with your text?")) return;
    await sourceOperation(file, async () => {
      b.sha = (await fetchSource(file)).sha256;
      b.conflict = null;
      if (cur === file) { diffEl.hidden = true; editor2.show(true); }
      await persistSource(file, b);
    });
  }

  srcBox.addEventListener("click", (e) => {
    const op = e.target.dataset && e.target.dataset.op;
    if (op === "save") saveSource();
    else if (op === "undo-save") undoSave();
    else if (op === "reload") reloadFromDisk();
    else if (op === "diff") viewDiff();
    else if (op === "overwrite") overwrite();
  });
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "s"
        && rightOpen() && tab === "source") {
      e.preventDefault();
      saveSource();
    }
  });

  // Open the editor at the source lines of a rendered paragraph.
  async function openSourceAt(el) {
    const f = fileOfEl(el);
    showSide("source");
    if (!f) { setMsg("No source file maps to this paragraph."); return; }
    followed = f.file;  // the section in view does not take the editor away
    if (!(await showFile(f.file))) return;
    const clone = el.cloneNode(true);
    clone.querySelectorAll(".pv-para, .pv-src-mark, .pv-tbl-edit, button, .pv-diagram").forEach((n) => n.remove());
    // Math: compare the TeX annotation with the source's $...$, not MathML glyphs.
    clone.querySelectorAll("math").forEach((m) => {
      const tex = m.querySelector("annotation");
      m.replaceWith(document.createTextNode(tex ? ` ${tex.textContent} ` : " "));
    });
    const b = buffers.get(f.file);
    if (!b) return;
    b.text = editor2.getValue();
    const seq = sourceViewSeq, sourceText = b.text;
    setMsg("Locating…");
    try {
      const { ok, payload } = await postJson("/__locate", {
        file: f.file, text: clone.textContent, source: b.text });
      if (cur !== f.file || seq !== sourceViewSeq || b.text !== sourceText) return;
      if (!ok) { setMsg(payload.error || "Could not locate"); return; }
      if (payload.file && payload.file !== f.file && payload.score >= 0.45) {
        // The paragraph comes from another file of the document.
        followed = payload.file;
        if (!(await showFile(payload.file))) return;
      }
      if (payload.score < 0.45) {
        editor2.setSelection(0, 0);
        editor2.setScroll(0);
        setMsg("No close match in the source; opened at the top.");
      } else {
        editor2.selectLines(payload.start_line, payload.end_line);
        editor2.scrollToLine(payload.start_line);
        setMsg(payload.include
          ? `Content comes from ${payload.include}; opened at its !include line ${payload.start_line}`
          : `Lines ${payload.start_line}–${payload.end_line}`);
      }
      editor2.focus();
    } catch (e) { setMsg("Could not reach the preview server: " + e.message); }
  }

  // Diff tab: the file of the section in view against git HEAD (word-level).
  const viewFile = () => spyFile || cur || (FILES[0] && FILES[0].file) || null;
  let diffSeq = 0;
  async function refreshDiff() {
    if (!sourceReady || !rightOpen() || tab !== "diff") return;
    const seq = ++diffSeq, file = viewFile();
    diffBox.textContent = "";
    const head = document.createElement("div");
    head.className = "pv-diff-head";
    const label = document.createElement("span");
    label.append((file || "no file") + " vs git HEAD");
    const again = document.createElement("button");
    again.type = "button";
    again.className = "pv-btn sm";
    again.textContent = "Refresh";
    again.addEventListener("click", refreshDiff);
    head.append(label, again);
    const body = document.createElement("div");
    body.className = "pv-diff-body";
    diffBox.append(head, body);
    const note = (t, cls) => { const d = document.createElement("div"); d.className = cls || "pv-diff-gap"; d.textContent = t; body.append(d); };
    if (!file) { note("No source file maps to this view."); return; }
    note("Loading…");
    try {
      const res = await fetch(`/__diff?file=${encodeURIComponent(file)}`, { cache: "no-store" });
      const payload = await res.json().catch(() => ({}));
      if (seq !== diffSeq) return;  // a newer request replaced this one
      body.textContent = "";
      if (!res.ok) { note(payload.error || `Error ${res.status}`, "pv-diff-error"); return; }
      if (payload.status === "new") { note("New file, not in git HEAD."); return; }
      if (payload.status === "clean") {
        note(payload.note ? `No changes since git HEAD (${payload.note}).` : "No changes since git HEAD.");
        return;
      }
      for (const hunk of payload.hunks) {
        const d = document.createElement("div");
        if (hunk.t === "gap") { d.className = "pv-diff-gap"; d.textContent = `⋯ ${hunk.n} unchanged paragraph${hunk.n > 1 ? "s" : ""}`; }
        else if (hunk.t === "chg") {
          d.className = "pv-diff-chg";
          for (const [op, text] of hunk.words) {
            if (op === "=") d.append(text);
            else { const m = document.createElement(op === "-" ? "del" : "ins"); m.textContent = text; d.append(m); }
          }
        } else { d.className = "pv-diff-" + hunk.t; d.textContent = hunk.text; }
        body.append(d);
      }
    } catch (e) { if (seq === diffSeq) { body.textContent = ""; note("Could not load the diff: " + e.message, "pv-diff-error"); } }
  }
