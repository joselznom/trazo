/* Diagram editor: writing SVG, PDF and PNG (POST /__diagrams/export/NAME),
 * from the Export button and, quietly, QUIET_MS after the last save, so the
 * preview shows the figure as saved. editor.js calls DiagramExport with its
 * state and helpers. */
(function () {
  "use strict";
  window.DiagramExport = function ({ S, $, api, save, updateStatus, toast, showBanner, isDraft, hasErrors, output }) {
    const QUIET_MS = 1500;
    const timers = new Map();
    const waiting = new Set();

    function exportAfterSave() {
      const name = S.name;
      clearTimeout(timers.get(name));
      timers.set(name, setTimeout(() => {
        timers.delete(name);
        exportQuietly(name, false);
      }, QUIET_MS));
    }

    function exportQuietly(name, retried) {
      const open = name === S.name;
      if (open && (!S.diagram || isDraft() || S.blocked || JSON.stringify(S.diagram) !== S.disk.json || hasErrors())) return;
      if (S.exporting) waiting.add(name);
      else exportDiagram(true, name, retried);
    }

    async function request(method, url) {
      try {
        return await api(method, url, method === "POST" ? {} : undefined);
      } catch (err) {
        return { ok: false, status: 0, data: { error: String(err) } };
      }
    }

    async function exportDiagram(quiet = false, name = S.name, retried = false) {
      if (!name || S.exporting) return;
      if (!quiet && S.diagram && JSON.stringify(S.diagram) !== S.disk.json) await save(false);
      if (!quiet && (S.blocked || (S.diagram && JSON.stringify(S.diagram) !== S.disk.json))) {
        toast("Save the diagram before exporting", "warning");
        return;
      }
      S.exporting = true;
      S.exportFailed = null;
      updateStatus();
      const sha = name === S.name ? S.disk.sha : null;
      const res = await request("POST", `export/${encodeURIComponent(name)}`);
      if (res.status === 409) {
        if (!quiet) toast("An export is already running", "info");
        else if (!retried) waiting.add(name);
        pollExport({ quiet, name, sha, ours: false });
        return;
      }
      if (!res.ok) {
        S.exporting = false;
        const error = `Export did not start: ${(res.data && res.data.error) || `HTTP ${res.status}`}`;
        if (quiet) failedQuietly(name, [error]);
        else {
          updateStatus();
          showBanner("error", error, []);
        }
        return;
      }
      pollExport({ quiet, name, sha, ours: true });
    }

    function failedQuietly(name, lines) {
      S.exportFailed = { name, lines };
      updateStatus();
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
      if (st.state === "error") {
        const lines = Array.isArray(st.messages) ? st.messages.slice(-8) : [];
        if (!job.quiet) showBanner("error", `Export failed${st.exit_code != null ? ` (exit ${st.exit_code})` : ""}.`, lines);
        else if (job.ours) failedQuietly(job.name, lines);
      } else if (st.state === "ok") {
        if (!job.quiet) toast(`Exported SVG, PDF and PNG to ${output}`);
        clearTimeout(S.exportTimer);
        S.exportTimer = setTimeout(() => { if (el && !S.exporting) el.hidden = true; }, 4000);
      }
      if (job.quiet && job.ours && job.name === S.name && S.disk.sha !== job.sha && !timers.has(job.name)) waiting.add(job.name);
      const next = waiting.values().next().value;
      if (next) {
        waiting.delete(next);
        exportQuietly(next, true);
      }
    }

    return { exportDiagram, exportAfterSave };
  };
})();
