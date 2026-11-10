/* Diagram editor: the short notice (toast) and the banner above the canvas,
 * with what a save says about other programs' writes (trazo.source_publish).
 * editor.js calls DiagramNotices(h) with its element builder. */
(function () {
  "use strict";
  window.DiagramNotices = function (h) {
    const $ = (sel) => document.querySelector(sel);
    let toastTimer = 0;
    /* A short notice; with `action` ({label, run}) it stays longer and offers
     * a button (Undo after a comment change). Hovering keeps it open. */
    function toast(text, icon = "check", action = null) {
      const t = $("#toast");
      t.replaceChildren(Icons.node(icon, 15), h("span", {}, text), action ? h("button", { type: "button",
        onclick: () => { clearTimeout(toastTimer); t.hidden = true; action.run(); } }, action.label) : null);
      t.hidden = false;
      t.style.animation = "none";
      void t.offsetWidth;
      t.style.animation = "";
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => { t.hidden = true; }, action ? 6000 : 2000);
    }
    $("#toast").addEventListener("mouseenter", () => clearTimeout(toastTimer));
    $("#toast").addEventListener("mouseleave", () => { toastTimer = setTimeout(() => { $("#toast").hidden = true; }, 2000); });

    function showBanner(kind, message, lines, buttons = []) {
      const b = $("#banner");
      b.className = kind === "info" ? "info" : "";
      b.replaceChildren(Icons.node(kind === "info" ? "info" : "error", 18),
        h("div", {}, h("div", { class: "msg" }, message),
          lines.length ? h("ul", {}, lines.map((l) => h("li", {}, l))) : null,
          h("div", { class: "buttons" }, ...buttons,
            h("button", { type: "button", class: "btn small ghost", onclick: () => { b.hidden = true; } }, "Dismiss"))));
      for (const btn of b.querySelectorAll(".buttons button:not(.btn)")) btn.className = "btn small";
      b.hidden = false;
    }
    // After a save: `warning` is trouble after the file was written; true when shown.
    function warned(data) {
      if (data.warning) showBanner("error", data.warning, []);
      return !!data.warning;
    }

    // `file` changed on disk while the page has edits; `actions` are the editor's
    // keepMine, loadTheirs and downloadMine. A 409's `kept`: another program wrote
    // the file during the save, and a copy holds this page's version.
    function showConflict(file, conflict, actions) {
      showBanner("error",
        `${file} changed on disk while this page also has edits. `
        + "Nothing was overwritten.", [
          "Keep mine writes this page over the file, using the hash that is on disk now.",
          "Load theirs discards the unsaved edits in this page and reloads the file.",
          ...(conflict && conflict.kept ? [`This page's version is also kept at ${conflict.kept.join(", ")}.`] : []),
        ], [
          h("button", { type: "button", class: "btn small primary", onclick: () => actions.keepMine() }, "Keep mine"),
          h("button", { type: "button", onclick: () => actions.loadTheirs() }, "Load theirs"),
          h("button", { type: "button", onclick: actions.downloadMine }, "Download my version"),
        ]);
    }
    return { toast, showBanner, warned, showConflict };
  };
})();
