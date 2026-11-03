// Table editor: the Table tab of the right panel, for the whole table, a
// label or a number from the data (columns and groups: editor-columns.js). Technical settings
// (fields, ids, format templates, row keys, pivots, rules) show only with
// Advanced. editor.js calls TableEditor.panel(E, K) with its shared state
// and the controls of editor-controls.js.
(function () {
  "use strict";
  const T = (window.TableEditor = window.TableEditor || {});
  T.panel = function (E, K) {
    const { h } = E;
    const { row, textField, selectCtl, orderList, scopeChoice, ctx } = K;
    let scope = "table", scopeKey = "";  // "this table only" or "everywhere", per selection

    function currentScope() {
      const key = JSON.stringify(E.sel);
      if (key !== scopeKey) { scopeKey = key; scope = "table"; }
      return scope;
    }
    const scopeRow = (others) => (others.length ? scopeChoice(others.join(", "), currentScope(), (v) => { scope = v; }) : null);
    const { fieldNames, fieldType } = E;
    function formatOthers(name) {
      const u = (E.st.format_usage || {})[name] || { tables: [], prose: [] };
      return [...new Set(u.tables.filter((t) => !t.local && t.label !== E.label).map((t) => E.tableName(t.label))),
        ...(u.prose.length ? ["the text"] : [])];
    }
    const mapped = (field, v) => {
      const m = { ...((E.st.shared.maps || {})[field] || {}), ...((E.spec.maps || {})[field] || {}) };
      return m[String(v)] !== undefined ? m[String(v)] : String(v);
    };
    const distinct = (field) => (E.st.data.fields[field] || { values: [] }).values;
    const adv = (...nodes) => (E.advanced ? nodes : []);
    const btn = (text, onclick, cls) => h("button", { type: "button", class: "pv-btn sm" + (cls ? " " + cls : ""), text, onclick });

    function propsPanel() {
      if (E.sel.kind === "cell") return cellProps();
      if (E.sel.kind === "block") return labelProps(E.sel.label, null);
      if (E.sel.kind === "column") return C.columnProps();
      if (E.sel.kind === "group") return C.groupProps();
      return tableProps();
    }

    // ------------------------------------------------------------ table
    function tableProps() {
      const spec = E.spec, rowsSpec = spec.rows || { keys: [] }, keys = rowsSpec.keys || [];
      const out = [ctx("Table · from your data", E.label, "Settings for the whole table. Click a header or a label to rename it.")];
      const values = Object.entries(E.st.data.values || {});
      out.push(h("div", { class: "te-sec" }, h("h4", { text: "Caption" }),
        row("Title", textField(spec.title, "title", { label: "Title", ids: ["title", "spec"] })),
        row("Legend", textField(spec.legend, "legend", { label: "Legend", multiline: true, empty: null, ids: ["legend"] }), true),
        row("Note", textField(spec.note, "note", { label: "Note", multiline: true, empty: null, ids: ["note"],
          placeholder: "Below the table: abbreviations, symbols, sources. A blank line starts a paragraph." }), true),
        values.length ? row("Values", h("div", { class: "te-chips" }, values.map(([k, v]) =>
          h("span", { class: "te-chip", title: `Write {${k}} in the title, legend or note`, text: `{${k}} = ${typeof v === "object" ? E.show(v) : v}` })))) : null));
      const rowsSec = h("div", { class: "te-sec" }, h("h4", { text: "Rows" }),
        h("p", { class: "te-muted te-small", text: `One row per ${keys.join(", ")}.` }),
        row("Group rows by", selectCtl([["", "nothing"], ...keys.map((k) => [k, k])], rowsSpec.block || "",
          (v) => E.save([{ op: "set", path: "rows.block", value: v || null }]), "Block rows")));
      if (E.advanced) rowsSec.append(row("One row per", rowKeys(rowsSpec, keys)));
      for (const k of keys) {
        const all = distinct(k);
        if (!all.length || all.length > 40) continue;
        const listed = (rowsSpec.order || {})[k] || [];
        const ordered = [...listed.filter((v) => all.includes(v)), ...all.filter((v) => !listed.includes(v))];
        rowsSec.append(row(`Order of ${k}`, orderList(ordered.map((v) => ({ value: v, text: E.plain(mapped(k, v)), code: v })),
          (next) => E.save([{ op: "set", path: `rows.order.${k}`, value: next.map((x) => x.value) }])), true));
      }
      for (const k of keys) {
        const all = distinct(k);
        if (all.length < 2 || all.length > 40) continue;
        const allowed = (rowsSpec.filter || {})[k];
        const on = (v) => !allowed || allowed.includes(v);
        rowsSec.append(row(`Show ${k}`, h("div", { class: "te-chips" }, all.map((v) => h("label", { class: "te-kchip" },
          h("input", { type: "checkbox", checked: on(v) ? "" : null, "aria-label": `Show ${k} ${v}`, onchange: (e) => {
            const next = all.filter((x) => (x === v ? e.target.checked : on(x)));
            E.save([{ op: "set", path: `rows.filter.${k}`, value: next.length === all.length ? null : next }]);
          } }), E.plain(mapped(k, v)))))));
      }
      rowsSec.append(...adv(row("Missing", textField(spec.missing === undefined ? "–" : spec.missing, "missing",
        { label: "Missing value text", ids: ["missing"] }))));
      out.push(rowsSec);
      out.push(h("div", { class: "te-sec te-acts" }, btn("Ask the agent for a column", () => E.ask("column")),
        btn("Ask the agent for a row", () => E.ask("row"))));
      if (E.advanced) {
        const rules = spec.highlight || [];
        out.push(h("div", { class: "te-sec" }, h("h4", { text: "Bold the best value" }),
          rules.map((rule, i) => ruleRow(rule, i, true)),
          h("div", null, btn("+ Rule", () => { const first = numericLeaf(); if (first) saveRules([...rules, newRule(first)]); })),
          (spec.emphasis || []).length ? h("p", { class: "te-muted te-small", text: `${spec.emphasis.length} cell(s) bold by hand (select a cell to change).` }) : null));
      }
      return out;
    }
    function rowKeys(rowsSpec, keys) {
      let dragKey = null;
      const chips = keys.map((k, i) => h("span", { class: "te-kchip", draggable: "true", title: "Drag to reorder",
        ondragstart: () => { dragKey = i; }, ondragover: (e) => { if (dragKey !== null) e.preventDefault(); },
        ondrop: (e) => { e.preventDefault(); if (dragKey === null || dragKey === i) return;
          const next = keys.slice(); const [x] = next.splice(dragKey, 1); next.splice(i, 0, x); dragKey = null;
          E.save([{ op: "set", path: "rows.keys", value: next }]); } },
      k, h("span", { class: "te-muted", text: fieldType(k) }),
      keys.length > 1 ? h("button", { type: "button", "aria-label": `Remove row key ${k}`, text: "✕",
        onclick: () => E.save([{ op: "set", path: "rows.keys", value: keys.filter((x) => x !== k) },
          ...(rowsSpec.block === k ? [{ op: "set", path: "rows.block", value: null }] : [])]) }) : null));
      const candidates = fieldNames().filter((f) => !keys.includes(f) && !fieldType(f).includes("composite"));
      if (candidates.length) {
        chips.push(selectCtl([["", "+ key"], ...candidates.map((f) => [f, f])], "", (v) => {
          if (v) E.save([{ op: "set", path: "rows.keys", value: [...keys, v] }]);
        }, "Add a row key"));
        chips[chips.length - 1].className = "te-kchip add";
      }
      return h("div", { class: "te-chips", dataset: { ids: "rows" } }, chips);
    }
    function numericLeaf() {
      const body = E.st.model ? E.st.model.body.find((b) => b.cells) : null;
      const c = body ? body.cells.find((x) => !x.hidden && (typeof x.raw === "number" || (x.raw && typeof x.raw === "object"))) : null;
      return c ? c.id : (E.leafIds()[0] || {}).id;
    }
    const newRule = (column) => ({ column, best: "max", within: (E.spec.rows || {}).block ? "block" : "table" });
    const saveRules = (rules) => E.save([{ op: "set", path: "highlight", value: rules.length ? rules : null }]);
    function ruleRow(rule, i, withColumn) {
      const rules = E.spec.highlight || [];
      const change = (key, value) => { const next = E.clone(rules); next[i][key] = value; saveRules(next); };
      const keys = (E.spec.rows || {}).keys || [];
      const within = [["table", "the table"], ...((E.spec.rows || {}).block ? [["block", "each block"]] : []), ...keys.map((k) => [k, `each ${k}`])];
      return h("div", { class: "te-rule", dataset: { ids: `highlight.${i}` } }, "Bold the",
        selectCtl([["max", "highest"], ["min", "lowest"]], rule.best, (v) => change("best", v), "Highest or lowest"),
        withColumn ? selectCtl(E.leafIds().map((c) => [c.id, `${E.plain(c.text_html)} (${c.id})`]), rule.column, (v) => change("column", v), "Column") : null,
        "within", selectCtl(within, rule.within || "table", (v) => change("within", v), "Within"),
        h("span", { class: "te-grow" }),
        h("button", { type: "button", class: "te-icon", style: "width:24px;height:24px", "aria-label": "Remove this rule", text: "✕",
          onclick: () => saveRules(rules.filter((_, j) => j !== i)) }));
    }

    // ------------------------------------------------------------ decimals and formats
    // The −/+ decimals of a column's format; a shared format that something
    // else uses asks where the change applies (a local copy overrides it).
    function decimals(col) {
      const all = E.formats(), name = col.format;
      const m = name && all[name] !== undefined && all[name].match(/\.(\d+)f/);
      if (!m) return [];
      const n = Number(m[1]);
      const shared = !E.isLocal(name), others = shared ? formatOthers(name) : [];
      const step = (d) => {
        const value = all[name].replace(/\.(\d+)f/g, () => `.${Math.max(0, n + d)}f`);
        E.save([{ op: "set", path: `formats.${name}`, value }], { shared: K.toShared(shared, others, currentScope()) });
      };
      return [row("Decimals", h("div", { class: "te-step", role: "group", "aria-label": "Decimals" },
        h("button", { type: "button", "aria-label": "Fewer decimals", text: "−", disabled: n === 0 ? "" : null, onclick: () => step(-1) }),
        h("span", { text: String(n) }),
        h("button", { type: "button", "aria-label": "More decimals", text: "+", onclick: () => step(1) }))), scopeRow(others)];
    }
    // ------------------------------------------------------------ cells and labels
    function cellProps() {
      const sel = E.sel, at = E.selectedCell();
      if (!at) return [ctx("Cell", sel.id, "Not in the table any more.")];
      const { item, cell } = at;
      if (cell.label) return labelProps(cell.label, { item, cell });
      const col = E.colAt(E.leafPath(sel.id)), raw = cell.raw;
      const readable = raw && typeof raw === "object"
        ? Object.entries(raw).map(([k, v]) => `${k} ${typeof v === "number" ? +v.toPrecision(4) : v}`).join(" · ")
        : raw === null ? "missing" : typeof raw === "number" ? String(+raw.toPrecision(6)) : String(raw);
      const inputs = E.st.data.inputs || [];
      const source = h("details", { class: "te-src" }, h("summary", { text: "Where does it come from?" }),
        h("p", null, "Calculated by the project's script", inputs.length ? " from " : " into ",
          ...(inputs.length ? inputs : [E.st.data.file || "the table data"]).flatMap((f, i) => [i ? ", " : "", h("code", { text: f })]), "."),
        h("p", { class: "te-muted", text: `Exact value: ${readable}` }));
      return [ctx("Cell · from your data", E.plain(cell.text_html), null),
        h("div", { class: "te-sec" }, ...(col ? decimals(col.col) : []),
          h("div", { class: "te-acts" }, btn("Ask the agent to change this", () => E.ask("cell"), "primary")),
          E.markRow(E.targetOf(sel), cell.mark), E.boldCtl(at), source),
        ...adv(h("div", { class: "te-sec" }, h("h4", { text: "Value from the data" }),
          h("pre", { class: "te-mono te-raw", text: JSON.stringify(raw, null, 1) }),
          h("p", { class: "te-muted te-small" }, "Row ", h("code", { text: Object.values(item.key).join(" · ") }), " · column ", h("code", { text: sel.id }))))];
    }
    // A text value from the data, renamed through its map entry: every cell
    // that shows the same value in this table changes with it.
    function labelProps(label, at) {
      const others = label.shared ? E.labelOthers(label) : [];
      const field = h("input", { class: "te-in", "aria-label": "Label" });
      field.value = label.text;
      const commit = () => { if (field.value !== label.text) E.rename(label, field.value, others.length ? currentScope() : null); };
      field.addEventListener("change", commit);
      field.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); commit(); } });
      return [ctx(at ? "Label · from your data" : "Group label · from your data", E.plain(label.text), null),
        h("div", { class: "te-sec" }, row("Text", field),
          h("p", { class: "te-muted te-small", text: `Renames every “${E.plain(label.text)}” of this table; the data keeps “${label.value}”.` }),
          scopeRow(others),
          at ? E.markRow(E.targetOf(E.sel), at.cell.mark) : null, at ? E.boldCtl(at) : null,
          ...adv(h("p", { class: "te-muted te-small" }, "Map entry ", h("code", { text: `maps.${label.field}.${label.value}` }),
            label.shared ? " (shared)" : ""))),
        h("div", { class: "te-sec te-acts" }, btn("Ask the agent about this", () => E.ask(at ? "cell" : "table")))];
    }
    const C = T.columns(E, K, { formatOthers, mapped, distinct, adv, btn, decimals, ruleRow, newRule, saveRules });
    return { propsPanel, updateSample: C.updateSample };
  };
})();
