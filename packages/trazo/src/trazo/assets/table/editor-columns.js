// Table editor: the Table tab for a column or a group of columns: header,
// decimals, layout, moves and, with Advanced, the field and format.
// editor-panel.js calls TableEditor.columns(E, K, S) with its helpers.
(function () {
  "use strict";
  const T = (window.TableEditor = window.TableEditor || {});
  T.columns = function (E, K, S) {
    const { h } = E;
    const { row, textField, switchCtl, selectCtl, orderList, ctx } = K;
    const { formatOthers, mapped, distinct, adv, btn, decimals, ruleRow, newRule, saveRules } = S;
    const { fieldNames, fieldType } = E;
    let sampleEl = null;  // the format's live sample: raw value → shown text

    function formatSection(col, base) {
      const all = E.formats(), name = col.format || "";
      const opts = [["", "(none)"], ...Object.keys(all).map((n) => [n, E.isLocal(n) ? n : `${n} (shared)`]), ["__new__", "New format…"]];
      const sec = h("div", { class: "te-sec" }, h("h4", { text: "Format" }));
      sec.append(row("Named", selectCtl(opts, name, (v) => {
        if (v === "__new__") {
          const fresh = E.uniqueId(col.id, Object.keys(all));
          E.save([{ op: "set", path: `formats.${fresh}`, value: all[name] || E.defaultTemplate(col.field) },
            { op: "set", path: `${base}.format`, value: fresh }]);
        } else E.save([{ op: "set", path: `${base}.format`, value: v || null }]);
      }, "Named format")));
      if (name && all[name] !== undefined) {
        const shared = !E.isLocal(name);
        sec.append(row("Template", textField(all[name], shared ? `shared:formats.${name}` : `formats.${name}`,
          { label: "Format template", mono: true, ids: [E.sel.id] })));
        sampleEl = h("div", { class: "te-sample", "aria-live": "polite" });
        sec.append(sampleEl);
        updateSample();
        if (shared) sec.append(h("p", { class: "te-muted te-small" }, "Shared format (", h("code", { text: E.st.shared.file }),
          formatOthers(name).length ? `): the template also changes ${formatOthers(name).join(", ")}.` : ")."));
      }
      return sec;
    }
    function updateSample() {
      const sel = E.sel;
      if (!sampleEl || !sampleEl.isConnected || !E.st.model || sel.kind !== "column") return;
      const item = E.st.model.body.find((b) => b.cells && b.cells.some((x) => x.id === sel.id));
      const cell = item && item.cells.find((x) => x.id === sel.id);
      sampleEl.replaceChildren(h("code", { text: cell ? E.show(cell.raw) : "(no cell)" }), h("span", { text: cell ? `→ ${E.plain(cell.text_html)}` : "" }));
    }

    // ------------------------------------------------------------ column and group
    function columnProps() {
      const sel = E.sel, path = E.leafPath(sel.id), at = E.colAt(path);
      if (!at) return [ctx("Column", sel.id, "Not in the spec any more.")];
      const col = at.col, parent = at.parent, base = `columns.${path}`;
      const head = E.st.model.header.flat().find((c) => c.id === sel.id);
      const out = [ctx("Column", (parent && parent.header ? parent.header + " › " : "") + E.plain(head ? head.text_html : col.header),
        E.advanced ? h("span", null, h("code", { text: sel.id }), " · field ", h("code", { text: col.field })) : null)];
      out.push(h("div", { class: "te-sec" },
        row("Header", textField(col.header, `${base}.header`, { label: "Header", ids: [sel.id] })),
        parent && parent.pivot ? h("p", { class: "te-muted te-small", text: `One column per ${parent.pivot}; this header is shared by all of them.` }) : null,
        ...decimals(col), E.markRow({ column: sel.id }, head && head.mark),
        ...adv(row("Field", selectCtl(fieldNames().map((f) => [f, `${f} (${fieldType(f)})`]), col.field,
          (v) => E.save([{ op: "set", path: `${base}.field`, value: v }]), "Field")))));
      if (E.advanced) out.push(formatSection(col, base));
      const width = col.width || 0;
      const range = h("input", { type: "range", min: "0", max: "4", step: "0.25", value: String(width), "aria-label": "Width weight (0 is auto)",
        onchange: (e) => E.save([{ op: "set", path: `${base}.width`, value: Number(e.target.value) || null }]),
        oninput: (e) => { e.target.nextSibling.textContent = Number(e.target.value) ? e.target.value : "auto"; } });
      out.push(h("div", { class: "te-sec" }, h("h4", { text: "Layout" }),
        row("Align", E.alignCtl(col, base)),
        row("Width", h("div", { class: "te-chips" }, range, h("span", { class: "te-muted te-small", text: width ? String(width) : "auto" }))),
        row("Merge", switchCtl(col.merge, "Join repeated values", (v) => E.save([{ op: "set", path: `${base}.merge`, value: v || null }]), "Merge repeated values")),
        row("Hidden", switchCtl(col.hidden, "Leave out of the table", (v) => E.save([{ op: "set", path: `${base}.hidden`, value: v || null }]), "Hidden"))));
      if (E.advanced) {
        const rules = E.spec.highlight || [];
        const mine = rules.map((r, i) => [r, i]).filter(([r]) => r.column === sel.id);
        out.push(h("div", { class: "te-sec" }, h("h4", { text: "Bold the best value" }), mine.map(([r, i]) => ruleRow(r, i, false)),
          mine.length ? null : btn("+ Bold the best value", () => saveRules([...rules, newRule(sel.id)]))));
      }
      const sibs = at.siblings, i = sibs.indexOf(col);
      const acts = [
        h("button", { type: "button", class: "pv-btn sm", text: "← Move", disabled: i === 0 ? "" : null,
          onclick: () => E.save([{ op: "move", column: path, before: sibs[i - 1].id }]) }),
        h("button", { type: "button", class: "pv-btn sm", text: "Move →", disabled: i === sibs.length - 1 ? "" : null,
          onclick: () => E.save([{ op: "move", column: path, before: sibs[i + 2] ? sibs[i + 2].id : null }]) }),
      ];
      if (parent && !parent.pivot) acts.push(btn("Ungroup", () => ungroup(parent)));
      if (!parent && E.isLeaf(sibs[i + 1])) acts.push(btn("Group with next", () => groupWithNext(col, sibs[i + 1])));
      acts.push(btn("Delete column", () => removeColumn(path), "te-danger"));
      out.push(h("div", { class: "te-sec te-acts" }, acts, E.hand ? null : btn("Ask the agent to change this column", () => E.ask("change-column"))));
      return out;
    }
    function ungroup(group) {
      const ops = group.columns.map((c) => ({ op: "add", into: "columns", item: c, before: group.id }));
      ops.push({ op: "remove", column: group.id });
      E.save([...ops, ...remapColumnRules(group.columns.map((c) => [`${group.id}.${c.id}`, c.id]))]);
      E.sel = { kind: "table" };
    }
    // Rules and marks that name a column follow it when it moves into or out
    // of a group, and go with it when it is deleted (null).
    function remapColumnRules(pairs) {
      const names = new Map(pairs), ops = [];
      for (const key of ["highlight", "emphasis", "marks"]) {
        const rules = E.spec[key] || [];
        if (rules.some((rule) => names.has(rule.column))) {
          const next = rules.filter((rule) => !names.has(rule.column) || names.get(rule.column) !== null)
            .map((rule) => names.has(rule.column) ? { ...rule, column: names.get(rule.column) } : rule);
          ops.push({ op: "set", path: key, value: next.length ? next : null });
        }
      }
      return ops;
    }
    function groupWithNext(a, b) {
      const id = E.uniqueId("group", E.spec.columns.map((c) => c.id));
      E.save([{ op: "add", into: "columns", before: a.id, item: { id, header: "Group", columns: [a, b] } },
        { op: "remove", column: a.id }, { op: "remove", column: b.id },
        ...remapColumnRules([[a.id, `${id}.${a.id}`], [b.id, `${id}.${b.id}`]])]);
      E.sel = { kind: "column", id: `${id}.${a.id}` };
    }
    function removeColumn(path) {
      const spec = E.spec;
      const ids = [...new Set([...(spec.highlight || []), ...(spec.emphasis || []), ...(spec.marks || [])].map((rule) => rule.column))];
      const removed = ids.filter((id) => E.leafPath(id) === path || E.leafPath(id).startsWith(`${path}.`));
      E.save([{ op: "remove", column: path }, ...remapColumnRules(removed.map((id) => [id, null]))]);
      E.sel = { kind: "table" };
    }
    function groupProps() {
      const at = E.colAt(E.sel.path);
      if (!at) return [ctx("Group", E.sel.id, "Not in the spec any more.")];
      const col = at.col, out = [];
      const children = h("div", { class: "te-chips" }, col.columns.map((c) => h("button", { type: "button", class: "te-kchip",
        text: c.header, onclick: () => {
          const leaf = E.leafIds().find((x) => E.leafPath(x.id) === `${col.id}.${c.id}`);
          if (leaf) E.select({ kind: "column", id: leaf.id });
        } })));
      if (col.pivot) {
        out.push(ctx("Columns per value", `One group of columns per ${col.pivot}`, E.advanced ? h("code", { text: col.id }) : null));
        const all = distinct(col.pivot), chosen = col.values || all;
        const items = [...chosen, ...all.filter((v) => !chosen.includes(v))].map((v) => ({ value: v, text: E.plain(mapped(col.pivot, v)), code: v, off: !chosen.includes(v) }));
        const setValues = (vals) => E.save([{ op: "set", path: `columns.${col.id}.values`, value: vals }]);
        out.push(h("div", { class: "te-sec" },
          ...adv(row("Pivot field", selectCtl(fieldNames().filter((f) => fieldType(f) === "string").map((f) => [f, f]), col.pivot,
            (v) => E.save([{ op: "set", path: `columns.${col.id}.pivot`, value: v }, { op: "set", path: `columns.${col.id}.values`, value: null }]), "Pivot field"))),
          row("Values", orderList(items, (next) => setValues(next.filter((x) => !x.off).map((x) => x.value)),
            (it) => h("input", { type: "checkbox", checked: it.off ? null : "", "aria-label": `Show ${it.value}`, onclick: (e) => e.stopPropagation(),
              onchange: (e) => setValues(items.filter((x) => (x === it ? e.target.checked : !x.off)).map((x) => x.value)) })), true),
          row("Columns", children)));
      } else {
        out.push(ctx("Group", col.header, E.advanced ? h("code", { text: col.id }) : null));
        out.push(h("div", { class: "te-sec" },
          row("Header", textField(col.header, `columns.${col.id}.header`, { label: "Group header", ids: [col.id] })),
          row("Columns", children)));
      }
      const sibs = E.spec.columns, i = sibs.indexOf(col);
      out.push(h("div", { class: "te-sec te-acts" },
        h("button", { type: "button", class: "pv-btn sm", text: "← Move", disabled: i === 0 ? "" : null,
          onclick: () => E.save([{ op: "move", column: col.id, before: sibs[i - 1].id }]) }),
        h("button", { type: "button", class: "pv-btn sm", text: "Move →", disabled: i === sibs.length - 1 ? "" : null,
          onclick: () => E.save([{ op: "move", column: col.id, before: sibs[i + 2] ? sibs[i + 2].id : null }]) }),
        col.pivot ? null : btn("Ungroup", () => ungroup(col)),
        btn(col.pivot ? "Delete these columns" : "Delete group", () => removeColumn(col.id), "te-danger")));
      return out;
    }

    return { columnProps, groupProps, updateSample };
  };
})();
