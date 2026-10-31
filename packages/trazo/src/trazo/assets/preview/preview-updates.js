// Preview viewer: the update notice, a small pill in the top bar that
// opens a short dialog (trazo.preview_updates). Nothing shows when
// trazo, the plugin and the paper are current.
// The parts of the viewer are classic scripts loaded in the order of
// trazo.preview.VIEWER_SCRIPTS; their top-level names are shared globals.
  if (!window.PreviewModel) throw new Error("preview-model.js did not load");  // see preview-page.js
  // A restart reloads the page; the result is shown again after it.
  const UPDATED_KEY = "trazo-updated";
  const updatePill = h("button", { type: "button", class: "pv-update-pill", "aria-haspopup": "dialog",
    "aria-expanded": "false", "aria-controls": "pv-update-pop", hidden: true });
  const updatePop = h("div", { class: "pv-update-pop", id: "pv-update-pop", role: "dialog",
    "aria-label": "trazo update", hidden: true });
  bar.insertBefore(updatePill, wordsEl);
  document.body.append(updatePop);
  let updatePlan = null, updateBusy = false, updateChecked = 0, updateShown = null;

  function updateCard(title, note, actions, busy) {
    updatePop.replaceChildren(...[
      h("div", { class: "pv-update-t" }, busy ? h("span", { class: "pv-dot building" }) : null, title),
      note ? h("div", { class: "pv-update-s", text: note }) : null,
      actions ? h("div", { class: "pv-update-acts" }, ...actions) : null].filter(Boolean));
  }
  function setUpdatePill(kind, text) {
    updatePill.className = `pv-update-pill ${kind}`;
    updatePill.textContent = text;
    updatePill.hidden = false;
  }
  function placeUpdate() {
    const r = updatePill.getBoundingClientRect();
    updatePop.style.top = `${r.bottom + 6}px`;
    updatePop.style.left = `${Math.max(8, Math.min(r.left, innerWidth - updatePop.offsetWidth - 8))}px`;
  }
  function openUpdate(open) {
    updatePop.hidden = !open;
    updatePill.setAttribute("aria-expanded", String(open));
    if (open) placeUpdate();
  }
  function offerUpdate(plan) {
    updatePlan = plan;
    if (plan.action === "plugin") {
      setUpdatePill("plugin", "Plugin");
      updateCard("Plugin out of date.", "Update trazo in the app's plugin settings.");
    } else if (plan.action === "update") {
      setUpdatePill("", plan.version);
      const note = plan.install ? (plan.paper ? "Updates trazo and this paper. Your edits are kept." : "Updates trazo.")
        : plan.paper ? "Updates this paper. Your edits are kept." : "Restarts the preview with the installed trazo.";
      const later = h("button", { type: "button", class: "pv-btn sm ghost", text: "Later" });
      const go = h("button", { type: "button", class: "pv-btn sm primary", text: "Update" });
      later.addEventListener("click", () => { openUpdate(false); updatePill.focus(); });
      go.addEventListener("click", runUpdate);
      updateCard(`trazo ${plan.version}`, note, [later, go]);
    } else if (!updatePill.classList.contains("done")) {
      updatePill.hidden = true;
      openUpdate(false);
    }
  }
  function updateDone(result) {
    updateShown = result;
    setUpdatePill("done", result.version);
    const notes = [];
    if (result.needs_merge.length) notes.push(`Ask the agent to merge ${plural(result.needs_merge.length, "file")}.`);
    if (result.new_chat) notes.push("New tools load in a new chat.");
    updateCard(`Updated to ${result.version}.`, notes.join(" "));
  }
  async function runUpdate() {
    updateBusy = true;
    updatePill.disabled = true;
    updateCard("Updating…", "", null, true);
    let result;
    try {
      const r = await fetch("/__update", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      result = await r.json();
      if (!r.ok) result = { ok: false, error: result.error || `HTTP ${r.status}` };
    } catch (e) {
      result = { ok: false, error: "The preview server does not answer." };
    }
    updatePill.disabled = false;
    if (result.ok === false) {
      updateBusy = false;
      if (result.download) updateCard("Needs a download.", "Ask the agent to set up trazo.");
      else updateCard("Update failed.", result.error);
      placeUpdate();
      return;
    }
    if (!result.restart) {
      updateBusy = false;
      updateDone(result);
      placeUpdate();
      return;
    }
    // The server restarts as the new trazo: reload once it answers as that version.
    sessionStorage.setItem(UPDATED_KEY, JSON.stringify(result));
    const deadline = Date.now() + 60000;
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 500));
      try {
        const plan = await (await fetch("/__updates", { cache: "no-store" })).json();
        if (plan.versions.running === result.version) { (window.TRAZO_VIEW || location).reload(); return; }
      } catch (e) { /* restarting */ }
    }
    sessionStorage.removeItem(UPDATED_KEY);
    updateBusy = false;
    updateCard("Updated.", "Reopen the preview to see it.");
  }
  async function checkUpdates() {
    if (updateBusy || Date.now() - updateChecked < 60000) return;
    updateChecked = Date.now();
    try {
      offerUpdate(await (await fetch("/__updates", { cache: "no-store" })).json());
    } catch (e) { /* the build status already says when the server is gone */ }
  }

  updatePill.addEventListener("click", () => openUpdate(updatePop.hidden));
  document.addEventListener("mousedown", (e) => {
    if (!updatePop.hidden && !updatePop.contains(e.target) && !updatePill.contains(e.target)) openUpdate(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !updatePop.hidden) { openUpdate(false); updatePill.focus(); }
  });
  window.addEventListener("resize", () => { if (!updatePop.hidden) placeUpdate(); });
  // The restarted server builds the pages again, so the viewer may reload once more
  // while the result is open: show it again after that reload.
  window.addEventListener("beforeunload", () => {
    if (updateShown && !updatePop.hidden) sessionStorage.setItem(UPDATED_KEY, JSON.stringify(updateShown));
  });
  // The word count and build status fill in after load and move the pill.
  const updateMoves = new ResizeObserver(() => { if (!updatePop.hidden) placeUpdate(); });
  [wordsEl, $(".pv-build", bar)].forEach((el) => updateMoves.observe(el));
  // The plugin updates in the app while the preview is open: look again when the tab is shown.
  document.addEventListener("visibilitychange", () => { if (!document.hidden) checkUpdates(); });
  const updated = sessionStorage.getItem(UPDATED_KEY);
  if (updated) {
    // The result first; the next check (when the tab is shown again) offers what is left.
    sessionStorage.removeItem(UPDATED_KEY);
    updateChecked = Date.now();
    try { updateDone(JSON.parse(updated)); openUpdate(true); } catch (e) { /* ignore a bad entry */ }
  }
  checkUpdates();
