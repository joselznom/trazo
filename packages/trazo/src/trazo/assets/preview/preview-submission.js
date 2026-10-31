// Preview viewer: the Submission view.
// The parts of the viewer are classic scripts loaded in the order of
// trazo.preview.VIEWER_SCRIPTS; their top-level names are shared globals.
  if (!window.PreviewModel) throw new Error("preview-model.js did not load");  // see preview-page.js
  // ---------------------------------------------------------------- submission
  // The Submission view of an article (?view=submission): the checks of
  // trazo's submission_check as cards, one row per item (a row opens
  // where it is fixed: a section with its source line in the Source tab, the
  // title block or keywords made from the metadata, the Figures & Tables
  // view or a document), then the journal form's fields as plain text with
  // Copy buttons. Other views fetch the report without the fields, for the
  // tab's badge (the number of errors). The last report is kept for the tab,
  // so a reload shows it at once.
  const SUB_ICONS = { error: "✕", warn: "!", ok: "✓", info: "i" };
  const SUB_STATES = { error: "To fix", warn: "To check", ok: "OK", info: "Note" };
  function showSubmissionBadge() {
    if (!submissionTab) return;
    const badge = $(".pv-badge", submissionTab), n = submission ? submission.errors : 0;
    badge.hidden = n === 0;
    badge.textContent = String(n);
    submissionTab.title = submission ? `Submission: ${todoText(submission).toLowerCase()}` : "";
  }
  showSubmissionBadge();
  // `fresh` (seconds): a report whose check started at most that long ago,
  // made again in the background if the kept one is older: a safety net for
  // a change the server's cache missed (trazo.preview_submission). The
  // newest check wins, whatever order the answers come in. Returns whether
  // the report changed (undefined: no report).
  async function loadSubmission(fresh) {
    if (!submissionTab) return undefined;
    const seq = ++submissionSeq, full = view === "submission";
    const query = new URLSearchParams();
    if (!full) query.set("fields", "0");
    // The count is of full checks: the view's button and its opening wait for those only.
    const counted = fresh !== undefined && full;
    if (fresh !== undefined) { query.set("fresh", fresh); submissionAsked = Date.now(); }
    if (counted) { submissionChecking++; showChecking(); }
    let result;
    try { result = await App.api(`/__submission${query.size ? "?" + query : ""}`); }
    catch (e) { result = { ok: false, data: { error: e.message } }; }
    if (counted) { submissionChecking--; showChecking(); }
    if (!result.ok) {
      if (seq !== submissionSeq) return undefined;  // a newer request is on its way
      submissionError = (result.data && result.data.error) || `HTTP ${result.status}`;
      renderSubmission();
      return undefined;
    }
    if (submission && result.data.checked < (submission.checked || 0)) return false;  // older than the one shown
    // Without the fields, keep the last ones until this view asks again.
    const next = full ? result.data : { ...result.data, fields: submission ? submission.fields : null };
    const content = (report) => JSON.stringify({ ...report, checked: 0 });
    const changed = !!submissionError || content(next) !== content(submission);
    submission = next;
    submissionError = "";
    sset("preview-submission", JSON.stringify(next));
    showSubmissionBadge();
    if (!changed) return false;  // keep focus and selection in the cards
    renderSubmission();
    renderWords();
    return true;
  }
  // The view opened: the kept report at once, then a fresh check unless the
  // kept one was made since (after a build it is new) or one is on its way.
  // The server skips fresh checks while the Word build runs.
  async function openSubmission() {
    const opened = Date.now() / 1000;
    await loadSubmission();
    if (view === "submission" && submission && (submission.checked || 0) < opened && !submissionChecking) loadSubmission(0);
  }
  // Once a minute while the page is visible (no report or request of the
  // last minute); the live reload's poll calls it.
  function checkSubmissionNow() {
    const last = Math.max(((submission && submission.checked) || 0) * 1000, submissionAsked);
    if (document.hidden || submissionChecking || Date.now() - last < 60000) return;
    loadSubmission(60);  // another tab's check of the last minute will do
  }
  function showChecking() {
    const button = $(".pv-subm-again", submissionView);
    if (!button) return;
    button.textContent = submissionChecking ? "Checking…" : "Check again";
    button.setAttribute("aria-disabled", String(!!submissionChecking));
  }

  function renderSubmissionIndex() {
    leftTitle.textContent = "Submission";
    leftSub.textContent = submission ? todoText(submission) : "";
    if (!submission) { leftBody.replaceChildren(); return; }
    const checks = h("div", { class: "pv-fgroup" }, h("h3", { text: "Checks" }));
    for (const group of submission.groups) {
      const k = tallyOf(group.items);
      checks.append(h("button", { type: "button", class: "pv-fbtn", "data-sub": group.id, title: todoText(k) },
        h("span", { class: `pv-sw ${k.errors ? "error" : k.warnings ? "warn" : "ok"}` }), group.label,
        h("span", { class: "n", text: String(k.errors + k.warnings) })));
    }
    const fields = submission.fields;
    leftBody.replaceChildren(checks, h("div", { class: "pv-fgroup" }, h("h3", { text: "Form" }),
      h("button", { type: "button", class: "pv-fbtn", "data-sub": "form" }, h("span", { class: "pv-sw" }),
        "For the submission form", h("span", { class: "n", text: fields ? String(fields.length) : "" }))));
  }
  function showSubmissionCard(id) {
    const card = document.getElementById(`pv-subm-${id}`);
    if (!card) return;
    card.scrollIntoView({ block: "start", behavior: reduceMotion() ? "auto" : "smooth" });
    card.focus({ preventScroll: true });
    closeDrawer();
  }

  // Where an item is shown in this page: a block made from the metadata, a
  // section (by id, else by heading within its file's part of the page) or
  // the part of the page its source file makes.
  const BLOCKS = { front: [".pk-front", "#title-block-header"], keywords: [".pk-keywords"] };
  const loose = (t) => String(t || "").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
  function placeOf(item) {
    if (item.block) return (BLOCKS[item.block] || []).map((sel) => $(sel, page)).find(Boolean) || null;
    const byId = item.hash && document.getElementById(item.hash);
    if (byId && page.contains(byId)) return (byId.matches("section") && $(":scope > :is(h1, h2, h3, h4, h5, h6)", byId)) || byId;
    const top = item.file && topSections.find((s) => fileOfSection(s)?.file === item.file);
    if (!top) return null;
    const heads = $$(":is(h1, h2, h3, h4, h5, h6)", top), want = loose(item.section);
    return (want && (heads.find((x) => loose(x.textContent) === want) || heads.find((x) => loose(x.textContent).endsWith(want))))
      || heads[0] || top;
  }
  // Scroll to an item's place and highlight it, as a link to a section does
  // (at once: a page that is starting drops a smooth scroll).
  function landOnPlace(item) {
    const el = placeOf(item);
    if (el) { scrollToEl(el, false, true); flash(el); }
    return el;
  }
  // Open an item's place: in the document view, with its source line in the
  // Source tab when it has one. Another document's page does it on load.
  function goToItem(item) {
    if (item.doc !== doc) {
      const { file, line, end_line, edit, hash, section, block } = item;
      sset(`preview-open-source:/${item.doc}.html`, JSON.stringify({ file, line, end_line, edit, hash, section, block }));
      (window.TRAZO_VIEW || location).assign(`/${encodeURIComponent(item.doc)}.html`);
      return;
    }
    closeDrawer();
    setView("doc", true);
    const el = placeOf(item);
    // A frame after the view switch, or Chrome drops the smooth scroll.
    if (el) requestAnimationFrame(() => requestAnimationFrame(() => { scrollToEl(el); flash(el); }));
    if (item.edit && item.file) openSourceLine(item, el);
  }
  // Where a row leads (null: nowhere, a setting of trazo.toml); `run`
  // stays in this page when it can.
  function subTarget(item) {
    if (item.url) return /^https?:\/\//i.test(item.url) ? { href: item.url, title: "Open in a new tab", external: true } : null;
    if (!item.doc || !DOCS[item.doc]) return null;
    const url = `/${encodeURIComponent(item.doc)}.html`, here = item.doc === doc, name = DOCS[item.doc];
    if (item.view === "figures") {
      return { href: `${url}?view=figures`, title: "Open the Figures & Tables view",
        run: () => (here ? setView("figures", true) : (window.TRAZO_VIEW || location).assign(`${url}?view=figures`)) };
    }
    const source = item.edit && item.file;
    if (source || item.block || item.hash || item.section) {
      return { href: url + (item.hash ? `#${encodeURIComponent(item.hash)}` : ""), run: () => goToItem(item),
        title: source ? `Show in ${name}, with ${item.file}, line ${item.line}, in the Source tab` : `Show in ${name}` };
    }
    return { href: url, title: `Open ${name}`, run: () => (here ? setView("doc", true) : (window.TRAZO_VIEW || location).assign(url)) };
  }
  // Open a source file of this document at a line in the Source tab. `el`,
  // the place shown with it, keeps the editor on the file while it is in view.
  async function openSourceLine(item, el) {
    showSide("source");
    followed = (el && fileOfEl(el)?.file) || item.file;  // the section in view does not take the editor away
    if (!(await showFile(item.file))) return;
    editor2.selectLines(item.line, item.end_line || item.line);
    editor2.scrollToLine(item.line);
    setMsg(`Line ${item.line}`);
    editor2.focus();
  }

  // The whole row is one link (no other control inside it).
  function subRow(item) {
    const target = subTarget(item);
    const kids = [h("span", { class: "pv-subm-icon", role: "img", "aria-label": SUB_STATES[item.status], title: SUB_STATES[item.status],
      text: SUB_ICONS[item.status] }),
    h("span", { class: "pv-subm-main" },
      h("span", { class: "pv-subm-line" },
        h("span", { class: "pv-subm-label" + (item.line && item.label.startsWith(item.file) ? " file" : ""), text: item.label }),
        item.value ? h("span", { class: "pv-subm-value", text: item.value }) : null),
      item.detail ? h("span", { class: "pv-subm-detail", text: item.detail }) : null)];
    if (!target) return h("div", { class: `pv-subm-row ${item.status}` }, ...kids);
    const row = h("a", { class: `pv-subm-row ${item.status}`, href: target.href, title: target.title,
      target: target.external ? "_blank" : null, rel: target.external ? "noopener noreferrer" : null },
    ...kids, h("span", { class: "pv-subm-go", "aria-hidden": "true", text: target.external ? "↗" : "›" }));
    if (target.run) {
      row.addEventListener("click", (e) => {
        if (e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        e.preventDefault();
        target.run();
      });
    }
    return row;
  }
  function subCard(group) {
    const k = tallyOf(group.items);
    const card = h("section", { class: "pv-subm-card", id: `pv-subm-${group.id}`, tabindex: "-1", "aria-labelledby": `pv-subm-${group.id}-h` },
      h("h3", { id: `pv-subm-${group.id}-h` }, h("span", { text: group.label }),
        h("span", { class: `n${k.errors ? " error" : k.warnings ? " warn" : ""}`, text: todoText(k) })));
    for (const item of group.items) card.append(subRow(item));
    return card;
  }
  async function copyField(field, button, box) {
    try {
      await navigator.clipboard.writeText(field.text);
    } catch (e) {  // no clipboard access: select the text for the copy shortcut
      const range = document.createRange();
      range.selectNodeContents(box);
      getSelection().removeAllRanges();
      getSelection().addRange(range);
      toast(`Press ${MOD}C to copy`);
      return;
    }
    button.textContent = "Copied";
    button.classList.add("copied");
    clearTimeout(button.copiedTimer);
    button.copiedTimer = setTimeout(() => { button.textContent = "Copy"; button.classList.remove("copied"); }, 1600);
  }
  function formCard(fields) {
    const card = h("section", { class: "pv-subm-card pv-subm-form", id: "pv-subm-form", tabindex: "-1", "aria-labelledby": "pv-subm-form-h" },
      h("h3", { id: "pv-subm-form-h" }, h("span", { text: "For the submission form" }),
        h("span", { class: "n", text: fields ? plural(fields.length, "field") : "" })),
      h("p", { class: "pv-subm-note", text: "Plain text for the journal's web form, with the values and citations of the Word files." }));
    if (!fields) { card.append(h("p", { class: "pv-subm-note", text: "Loading…" })); return card; }
    for (const field of fields) {
      const box = h("p", { class: "pv-subm-text" + (field.text && !field.error ? "" : " empty"),
        text: field.error ? `Not available: ${field.error}` : field.text || "Not set" });
      const copy = h("button", { type: "button", class: "pv-btn sm pv-subm-copy", "aria-label": `Copy ${field.label}`,
        disabled: !field.text, text: "Copy" });
      copy.addEventListener("click", () => copyField(field, copy, box));
      card.append(h("div", { class: "pv-subm-field" },
        h("div", { class: "pv-subm-field-head" }, h("h4", { text: field.label }),
          field.form_only ? h("span", { class: "pv-subm-tag", text: "Form only", title: "Not in any Word file: type it into the journal's form" }) : null,
          copy),
        box));
    }
    return card;
  }
  function renderSubmission() {
    if (view !== "submission") return;
    renderSubmissionIndex();
    if (!submission) {
      submissionView.replaceChildren(h("p", { class: "pv-fempty", role: submissionError ? "alert" : null,
        text: submissionError ? `The submission check failed: ${submissionError}` : "Checking the submission…" }));
      return;
    }
    const journal = submission.journal || {};
    const meta = h("div", { class: "pv-subm-meta" });
    if (/^https?:\/\//i.test(journal.guidelines || "")) {
      meta.append(h("a", { href: journal.guidelines, target: "_blank", rel: "noopener noreferrer", text: "Instructions for authors ↗" }));
    }
    meta.append(h("span", { text: submission.placements }));
    const sum = h("p", { class: "pv-subm-sum" });
    if (submission.errors) sum.append(h("b", { class: "error", text: `${submission.errors} to fix` }));
    if (submission.errors && submission.warnings) sum.append(" · ");
    if (submission.warnings) sum.append(h("b", { class: "warn", text: `${submission.warnings} to check` }));
    if (!submission.errors && !submission.warnings) sum.append(h("b", { class: "ok", text: "Nothing to fix or check" }));
    const again = h("button", { type: "button", class: "pv-btn sm pv-subm-again", title: "Check every file again, now" });
    again.addEventListener("click", () => {
      if (submissionChecking) return;
      loadSubmission(0).then((changed) => { if (changed === false) toast("Nothing changed since the last check"); });
    });
    const head = h("header", { class: "pv-subm-head" }, h("h2", { text: journal.journal || "No journal set" }), meta,
      journal.journal ? null : h("p", { class: "pv-subm-hint", text: "Ask your coding agent to use the journal-submission skill: "
        + "it reads the journal's instructions for authors and sets the journal, its limits and where the title page and declarations go." }),
      h("div", { class: "pv-subm-sumrow" }, sum, again), submissionError ? h("p", { class: "pv-subm-hint", role: "alert", text: `Not refreshed: ${submissionError}` }) : null);
    submissionView.replaceChildren(head, ...submission.groups.map(subCard), formCard(submission.fields));
    showChecking();
  }
