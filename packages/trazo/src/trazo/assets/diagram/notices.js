/* Diagram editor: the short notice (toast) and the banner above the canvas.
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
    return { toast, showBanner };
  };
})();
