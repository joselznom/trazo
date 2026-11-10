// Table editor: short notices (flash) and the banner above the table.
// editor.js calls TableEditor.messages(h) with its element builder.
(function () {
  "use strict";
  const T = (window.TableEditor = window.TableEditor || {});
  T.messages = function (h) {
    const $ = (s, r = document) => r.querySelector(s);
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
    return { flash, banner };
  };
})();
