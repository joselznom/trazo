/* Diagram editor: writing SVG, PDF and PNG (POST /__diagrams/export/NAME),
 * from the Export button and, quietly, QUIET_MS after the last save, so the
 * preview shows the figure as saved. editor.js calls DiagramExport with its
 * state and helpers. */
(function () {
  "use strict";
  window.DiagramExport = function ({ S, $, api, save, updateStatus, toast, showBanner, isDraft, hasErrors, output }) {
    const QUIET_MS = 1500;
    const timers = new Map();
    const pending = new Set();
    const conflicted = new Set();
    S.exportFailures = {};

    function exportAfterSave() {
      const name = S.name;
      clearTimeout(timers.get(name));
      timers.delete(name);
      if (isDraft()) return;
      timers.set(name, setTimeout(() => {
        timers.delete(name);
        pending.add(name);
        exportPending();
      }, QUIET_MS));
    }

    const ready = (name) => name !== S.name
      || (S.diagram && !isDraft() && !S.blocked && JSON.stringify(S.diagram) === S.disk.json && !hasErrors());

    function exportPending() {
      if (S.exporting) return;
      for (const name of pending) {
        pending.delete(name);
        if (ready(name)) {
          exportDiagram(true, name);
          return;
        }
      }
    }

    async function request(method, url) {
      try {
        return await api(method, url, method === "POST" ? {} : undefined);
      } catch (err) {
        return { ok: false, status: 0, data: { error: String(err) } };
      }
    }

    function failed(quiet, name, message, lines) {
      if (!quiet) showBanner("error", message, lines);
      else {
        S.exportFailures[name] = [message, ...lines];
        updateStatus();
      }
    }

    async function exportDiagram(quiet = false, name = S.name) {
      if (!name || S.exporting) return;
      if (!quiet && S.diagram && JSON.stringify(S.diagram) !== S.disk.json) await save(false);
      if (!quiet && (S.blocked || (S.diagram && JSON.stringify(S.diagram) !== S.disk.json))) {
        toast("Save the diagram before exporting", "warning");
        return;
      }
      S.exporting = true;
      delete S.exportFailures[name];
      updateStatus();
      const sha = name === S.name ? S.disk.sha : null;
      const res = await request("POST", `export/${encodeURIComponent(name)}`);
      if (res.status === 409) {
        if (!quiet) toast("An export is already running", "info");
        else if (conflicted.has(name)) conflicted.delete(name);
        else {
          conflicted.add(name);
          pending.add(name);
        }
        pollExport({ quiet, name, sha, ours: false });
        return;
      }
      conflicted.delete(name);
      if (!res.ok) {
        S.exporting = false;
        updateStatus();
        failed(quiet, name, `Export did not start: ${(res.data && res.data.error) || `HTTP ${res.status}`}`, []);
        exportPending();
        return;
      }
      pollExport({ quiet, name, sha, ours: true });
    }

    async function pollExport(job) {
      const res = await request("GET", "export");
      const st = (res.ok && res.data) || { state: "error", messages: [`No export status: ${(res.data && res.data.error) || `HTTP ${res.status}`}`] };
      const el = $("#export-state");
      if (el) {
        const running = st.state === "running";
        el.hidden = st.state === "idle";
        el.textContent = running ? `Exporting ${st.name || ""}…` : st.state === "ok" ? "Exported" : st.state === "error" ? "Export failed" : "";
        el.className = "status" + (st.state === "error" ? " error" : running ? " saving" : "");
      }
      if (st.state === "running") {
        clearTimeout(S.exportTimer);
        S.exportTimer = setTimeout(() => pollExport(job), 800);
        return;
      }
      S.exporting = false;
      updateStatus();
      if (st.state === "error" && (job.ours || !job.quiet)) {
        const lines = Array.isArray(st.messages) ? st.messages.slice(-8) : [];
        failed(job.quiet, job.name, `Export failed${st.exit_code != null ? ` (exit ${st.exit_code})` : ""}.`, lines);
      } else if (st.state === "ok") {
        if (!job.quiet) toast(`Exported SVG, PDF and PNG to ${output}`);
        clearTimeout(S.exportTimer);
        S.exportTimer = setTimeout(() => { if (el && !S.exporting) el.hidden = true; }, 4000);
      }
      if (job.quiet && job.ours && job.name === S.name && S.disk.sha !== job.sha && !timers.has(job.name)) pending.add(job.name);
      exportPending();
    }

    return { exportDiagram, exportAfterSave };
  };
})();
