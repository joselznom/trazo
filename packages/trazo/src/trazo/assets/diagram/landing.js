/* Diagram editor: the landing page (diagram cards and the "New diagram" form).
 * editor.js calls DiagramLanding with its state and helpers. */
(function () {
  "use strict";
  window.DiagramLanding = function ({ S, $, h, api, fileUrl, segmented, toast, revealSwitch, paths }) {
    function ago(ns) {
      if (!ns) return "";
      const sec = Math.max(0, (Date.now() - ns / 1e6) / 1000);
      if (sec < 90) return "edited just now";
      if (sec < 5400) return `edited ${Math.round(sec / 60)} min ago`;
      if (sec < 129600) return `edited ${Math.round(sec / 3600)} h ago`;
      return `edited ${Math.round(sec / 86400)} d ago`;
    }

    function card(d) {
      const thumb = h("div", { class: "thumb" });
      const img = h("img", { alt: "", loading: "lazy", src: `/${paths.diagram_output}/${encodeURIComponent(d.name)}.png?v=${d.mtime_ns || 0}` });
      img.addEventListener("error", () => img.replaceWith(Icons.node("file", 32)));
      thumb.append(img);
      return h("a", { class: "dcard", href: `#${encodeURIComponent(d.name)}` }, thumb,
        h("div", { class: "info" }, h("b", {}, d.name), h("span", {}, ago(d.mtime_ns))));
    }

    async function renderLanding(message) {
      const r = await api("GET", "list");
      const list = r.ok && Array.isArray(r.data) ? r.data : [];
      const nameInput = h("input", { type: "text", class: "input mono", placeholder: "fig3_new_diagram", pattern: "[a-z0-9][a-z0-9_\\-]*",
        required: true, title: "Lower-case letters, digits, - and _" });
      const width = segmented(Object.entries(S.tokens.widths_mm || { single: 89, double: 183 }).map(([n, w]) => (
        { value: String(w), label: `${w}`, title: `${n} column, ${w} mm` })), "183", (v) => {
        width.dataset.value = v;
        for (const b of width.querySelectorAll("button")) b.classList.toggle("on", b.title.endsWith(`${v} mm`));
      }, "new-width");
      width.dataset.value = "183";
      const height = h("input", { type: "number", class: "input", value: "60", min: "5", step: "1" });
      const form = h("form", { onsubmit: async (e) => {
        e.preventDefault();
        const name = nameInput.value.trim();
        if (list.some((d) => d.name === name)) {
          toast(`${name} already exists; pick another name`, "warning");
          nameInput.focus();
          return;
        }
        const diagram = { schema_version: 1, canvas: { width_mm: Number(width.dataset.value), height_mm: Number(height.value), grid: 1 },
          panels: [], nodes: [], edges: [], raw: [] };
        const res = await api("POST", fileUrl(name), { diagram, base_sha256: null });
        if (res.ok) location.hash = name;
        else if (res.status === 409) toast(`${name} already exists; pick another name`, "warning");
        else toast((res.data && res.data.error) || `HTTP ${res.status}`, "error");
      } },
      h("div", { class: "new-head" }, Icons.node("plus", 18), "New diagram"),
      h("div", {}, h("span", { class: "lab", style: { display: "block", fontSize: "11px", color: "var(--text-3)", marginBottom: "3px" } }, "Name"), nameInput),
      h("div", {}, h("span", { class: "lab", style: { display: "block", fontSize: "11px", color: "var(--text-3)", marginBottom: "3px" } }, "Width (mm)"), width),
      h("div", {}, h("span", { class: "lab", style: { display: "block", fontSize: "11px", color: "var(--text-3)", marginBottom: "3px" } }, "Height (mm)"), height),
      h("button", { type: "submit", class: "btn primary" }, "Create"));
      const landing = $("#landing");
      landing.replaceChildren(h("div", { class: "wrap" },
        h("div", { class: "lhead" }, h("div", {}, h("h1", {}, "Diagrams"),
          h("p", {}, `${list.length} in ${paths.diagrams} · exported to ${paths.diagram_output}`))),
        message ? h("div", { class: "msg" }, message) : null,
        h("div", { class: "cards" }, ...list.map(card), h("div", { class: "dcard new" }, form))));
      landing.hidden = false;
      revealSwitch(landing);
    }

    return { renderLanding };
  };
})();
