// Table editor: short notices (flash) and the banner above the table, with
// what a save says about other programs' writes (trazo.source_publish).
// editor.js calls TableEditor.messages(h) with its element builder.
(function () {
  "use strict";
  const T = (window.TableEditor = window.TableEditor || {});
  T.messages = function (h) {
    const $ = (s) => document.querySelector(s);
    let flashTimer = null;
    // A short notice; with `action` ({label, run}) it stays longer and offers
    // a button (Undo after a comment change). Hovering keeps it open.
    function flash(text, action) {
      let el = $(".te-flash");
      if (!el) {
        el = h("div", { class: "te-flash", role: "status" });
        el.addEventListener("mouseenter", () => clearTimeout(flashTimer));
        el.addEventListener("mouseleave", () => { flashTimer = setTimeout(() => el.remove(), 2600); });
        document.body.append(el);
      }
      el.textContent = text;
      if (action) el.append(h("button", { type: "button", text: action.label,
        onclick: () => { clearTimeout(flashTimer); el.remove(); action.run(); } }));
      clearTimeout(flashTimer);
      flashTimer = setTimeout(() => el.remove(), action ? 6000 : 2600);
    }
    function banner(content, kind, actions) {
      const el = $("#te-banner");
      el.replaceChildren();
      if (!content) { el.hidden = true; return; }
      el.className = "te-banner" + (kind === "err" ? " err" : "");
      el.append(h("div", { class: "te-grow" }, content), ...(actions || []),
        h("button", { type: "button", class: "te-icon", "aria-label": "Dismiss", text: "✕", onclick: () => banner(null) }));
      el.hidden = false;
    }
    // After a save or an undo: `warning` is trouble after the file was written.
    function warned(data) { if (data.warning) banner(data.warning, "err"); }
    // A 409. `kept`: another program wrote the file during the save, and these copies hold the edits.
    function conflict(data, reload) {
      const kept = data.kept ? ` A copy of them is at ${data.kept.join(", ")}.` : "";
      banner(`This table changed on disk since it was loaded (another edit or an agent). Your last edits are not saved.${kept}`, "err",
        [h("button", { type: "button", class: "pv-btn sm", text: "Reload (lose my edits)", onclick: reload })]);
    }
    // A refused reply: one that names copies of what was not written (`kept`) stays as a banner.
    function failed(data, fallback) {
      if (data.kept) banner(data.error, "err");
      else flash(data.error || fallback);
    }
    return { flash, banner, warned, conflict, failed };
  };
})();
