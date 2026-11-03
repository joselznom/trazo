// Table editor: form controls of the right panel, and the Check and Data
// tabs. editor.js calls TableEditor.controls(E) with its shared state.
(function () {
  "use strict";
  const T = (window.TableEditor = window.TableEditor || {});
  T.controls = function (E) {
    const { h } = E;
    function row(labelText, control, top) {
      const id = "te-f-" + Math.random().toString(36).slice(2, 8);
      const target = control.matches && control.matches("input, select, textarea") ? control : null;
      if (target) target.id = id;
      return h("div", { class: "te-row" + (top ? " top" : "") },
        target ? h("label", { for: id, text: labelText }) : h("span", { class: "te-lab", text: labelText }), control);
    }
    // A text field saved 600 ms after typing stops (or on blur); `path` is a
    // set op path ("shared:" for _shared.json); empty text sends `empty`.
    function textField(value, path, opts) {
      opts = opts || {};
      const el = h(opts.multiline ? "textarea" : "input", { class: "te-in" + (opts.mono ? " te-mono" : ""),
        "aria-label": opts.label, placeholder: opts.placeholder || null, dataset: { ids: (opts.ids || []).join(" ") }, spellcheck: opts.mono ? "false" : null });
      el.value = value || "";
      el.addEventListener("input", () => E.setPending(path, el.value === "" && "empty" in opts ? opts.empty : el.value));
      el.addEventListener("blur", () => E.flush());
      return el;
    }
    function switchCtl(on, text, onChange, name) {
      return h("button", { type: "button", class: "te-switch", role: "switch", "aria-checked": String(!!on), "aria-label": name || text,
        onclick: () => onChange(!on) }, h("i"), text);
    }
    function selectCtl(options, value, onChange, name) {
      const el = h("select", { class: "te-in", "aria-label": name });
      for (const [v, t] of options) el.append(h("option", { value: v, text: t, selected: v === value ? "" : null }));
      el.addEventListener("change", () => onChange(el.value));
      return el;
    }
    // A list reordered by drag (and the keyboard: Alt+↑/↓), e.g. row order.
    function orderList(items, onOrder, extra) {
      const ul = h("ul", { class: "te-order" });
      let from = null;
      items.forEach((it, i) => {
        const li = h("li", { draggable: "true", tabindex: "0", class: it.off ? "off" : "", title: "Drag (or Alt+↑/↓) to reorder",
          ondragstart: (e) => { from = i; e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", String(i)); },
          ondragover: (e) => { if (from !== null && from !== i) { e.preventDefault(); li.classList.add("drop"); } },
          ondragleave: () => li.classList.remove("drop"),
          ondrop: (e) => { e.preventDefault(); li.classList.remove("drop"); if (from !== null) move(from, i); from = null; },
          onkeydown: (e) => {
            if (!e.altKey || !["ArrowUp", "ArrowDown"].includes(e.key)) return;
            e.preventDefault();
            const to = e.key === "ArrowUp" ? i - 1 : i + 1;
            if (to >= 0 && to < items.length) move(i, to);
          } },
        h("span", { class: "te-h", "aria-hidden": "true", text: "⋮⋮" }), extra ? extra(it) : null, h("span", { text: it.text }),
        it.code !== undefined && E.advanced ? h("code", { text: it.code }) : null);
        ul.append(li);
      });
      function move(a, b) {
        const next = items.slice();
        const [it] = next.splice(a, 1);
        next.splice(b, 0, it);
        onOrder(next);
      }
      return ul;
    }
    // "This table only" or "Everywhere" for a shared label or format that
    // something else uses; `others` names them. The choice lasts for the
    // current selection.
    function scopeChoice(others, value, onChange) {
      const name = "te-scope-" + Math.random().toString(36).slice(2, 8);
      const opt = (v, text) => h("label", { class: "te-opt" }, h("input", { type: "radio", name, value: v,
        checked: value === v ? "" : null, onchange: () => onChange(v) }), h("span", null, text));
      return h("div", { class: "te-scope", role: "radiogroup", "aria-label": "Where the change applies" },
        opt("table", "In this table only"), opt("everywhere", `Everywhere (also ${others})`));
    }
    // Whether a change to something shared goes to _shared.json: when nothing
    // else uses it, or when the author chose "everywhere".
    const toShared = (shared, others, scope) => shared && (!others.length || scope === "everywhere");
    function ctx(kind, what, where) {
      return h("div", { class: "te-ctx" }, h("div", { class: "te-kind", text: kind }),
        h("div", { class: "te-what", text: what }), where ? h("div", { class: "te-where" }, where) : null);
    }

    function issuesPanel() {
      const issues = E.st.issues || [];
      if (!issues.length) {
        return [h("div", { class: "te-ok", text: "No issues: the data is current and the table renders (trazo table check)." })];
      }
      return issues.map((it) => h("button", { type: "button", class: `te-issue ${it.level}`, onclick: () => {
        if (it.id && E.leafIds().some((c) => c.id === it.id)) E.select({ kind: "column", id: it.id });
        else if (it.id && E.spec.columns.some((c) => c.id === it.id.split(".")[0] && (c.columns))) E.select({ kind: "group", id: it.id, path: it.id.split(".")[0] });
      } }, h("div", { class: "te-code", text: `${it.level} · ${it.code}${it.id ? " · " + it.id : ""}` }), it.message));
    }
    function dataPanel() {
      const d = E.st.data;
      const compact = (v) => (v && typeof v === "object" ? "{" + Object.entries(v).map(([k, x]) => `${k} ${typeof x === "number" ? +x.toFixed(4) : x}`).join(", ") + "}"
        : v === null ? "null" : String(v));
      const names = Object.keys(d.fields);
      return [h("div", { class: "te-sec" }, h("span", { class: "te-muted" }, h("code", { text: d.file || "" }), ` · ${d.count} records · ${Object.keys(d.values || {}).length} values`)),
        h("div", { class: "te-sec" }, h("h4", { text: "Fields" }), h("div", { class: "te-data-wrap", style: "margin:0" },
          h("table", { class: "te-data" }, h("tr", null, h("th", { text: "field" }), h("th", { text: "type" }), h("th", { text: "values" })),
            names.map((n) => h("tr", null, h("td", { text: n }), h("td", { text: d.fields[n].types.join(" | ") + (d.fields[n].keys.length ? ` {${d.fields[n].keys.join(", ")}}` : "") }),
              h("td", { class: "wrap", text: d.fields[n].values.slice(0, 12).join(", ") + (d.fields[n].values.length > 12 ? ", …" : "") })))))),
        Object.keys(d.values || {}).length ? h("div", { class: "te-sec" }, h("h4", { text: "Caption values" }),
          h("div", { class: "te-chips" }, Object.entries(d.values).map(([k, v]) => h("span", { class: "te-chip", text: `{${k}} = ${compact(v)}` })))) : null,
        h("div", { class: "te-sec" }, h("h4", { text: `Records${d.count > d.records.length ? ` (first ${d.records.length})` : ""}` })),
        h("div", { class: "te-data-wrap" }, h("table", { class: "te-data" }, h("tr", null, names.map((n) => h("th", { text: n }))),
          d.records.map((r) => h("tr", null, names.map((n) => h("td", { text: compact(r[n] === undefined ? null : r[n]) }))))))];
    }
    return { row, textField, switchCtl, selectCtl, orderList, scopeChoice, toShared, ctx, issuesPanel, dataPanel };
  };
})();
