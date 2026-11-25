/* Diagram editor: writing SVG, PDF and PNG (POST /__diagrams/export/NAME),
 * from the Export button and, quietly, QUIET_MS after the last save, so the
 * preview shows the figure as saved. editor.js calls DiagramExport with its
 * state and helpers. */
(function () {
  "use strict";
  window.DiagramExport = function ({ S, $, api, save, updateStatus, toast, showBanner, isDraft, hasErrors, output }) {
    const QUIET_MS = 1500;
    let quietTimer = 0;
    let again = false;

    function exportAfterSave() {
      clearTimeout(quietTimer);
      quietTimer = setTimeout(exportQuietly, QUIET_MS);
    }

    function exportQuietly() {
      if (!S.name || !S.diagram || isDraft() || S.blocked || JSON.stringify(S.diagram) !== S.disk.json || hasErrors()) return;
      if (S.exporting) again = true;
      else exportDiagram(true);
    }

    async function exportDiagram(quiet = false) {
      if (!S.name || S.exporting) return;
      if (S.diagram && JSON.stringify(S.diagram) !== S.disk.json) await save(false);
      if (S.blocked || (S.diagram && JSON.stringify(S.diagram) !== S.disk.json)) {
        toast("Save the diagram before exporting", "warning");
        return;
      }
      S.exporting = true;
      S.exportFailed = null;
      updateStatus();
      const sha = S.disk.sha;
      const res = await api("POST", `export/${encodeURIComponent(S.name)}`, {});
      if (res.status === 409) {
        S.exporting = false;
        if (quiet) again = true;
        else toast("An export is already running", "info");
        pollExport(quiet, sha);
        return;
      }
      if (!res.ok) {
        S.exporting = false;
        updateStatus();
        const error = `Export did not start: ${(res.data && res.data.error) || `HTTP ${res.status}`}`;
        if (quiet) failedQuietly(S.name, [error]);
        else showBanner("error", error, []);
        return;
      }
      pollExport(quiet, sha);
    }

    function failedQuietly(name, lines) {
      S.exportFailed = { name, lines };
      updateStatus();
    }

    async function pollExport(quiet, sha) {
      const res = await api("GET", "export");
      const st = (res.ok && res.data) || { state: "error", messages: ["No export status"] };
      const el = $("#export-state");
      if (el) {
        const running = st.state === "running";
        el.hidden = st.state === "idle";
        el.textContent = running ? `Exporting ${st.name || ""}…` : st.state === "ok" ? "Exported" : st.state === "error" ? "Export failed" : "";
        el.className = "status" + (st.state === "error" ? " error" : running ? " saving" : "");
      }
      if (st.state === "running") {
        clearTimeout(S.exportTimer);
        S.exportTimer = setTimeout(() => pollExport(quiet, sha), 800);
        return;
      }
      S.exporting = false;
      updateStatus();
      if (st.state === "error") {
        const lines = Array.isArray(st.messages) ? st.messages.slice(-8) : [];
        if (quiet) failedQuietly(st.name, lines);
        else showBanner("error", `Export failed${st.exit_code != null ? ` (exit ${st.exit_code})` : ""}.`, lines);
      } else if (st.state === "ok") {
        if (!quiet) toast(`Exported SVG, PDF and PNG to ${output}`);
        clearTimeout(S.exportTimer);
        S.exportTimer = setTimeout(() => { if (el && !S.exporting) el.hidden = true; }, 4000);
      }
      if (again || (quiet && S.disk.sha !== sha)) {
        again = false;
        exportAfterSave();
      }
    }

    return { exportDiagram, exportAfterSave };
  };
})();
