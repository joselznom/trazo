// Table editor: editing on the table itself. The format bar, renaming
// headers and labels in place (double-click, Enter or F2), the "this table
// only or everywhere" choice for shared labels, marks (a, *, †), alignment
// and requests to the agent, which open a prefilled comment.
// editor.js calls TableEditor.inline(E, K) with its shared state.
(function () {
  "use strict";
  const T = (window.TableEditor = window.TableEditor || {});
  const MARKS = ["a", "b", "c", "*", "†", "‡"];
  T.inline = function (E, K) {
    const { h, $, $$ } = E;

    // ------------------------------------------------------------ requests to the agent
    const ASK = { column: "Add a column: ", row: "Add a row: ", cell: "Change this: ",
      "change-column": "Change this column: ", table: "" };
    function ask(kind) {
      if (kind === "column" || kind === "row") E.sel = { kind: "table" };
      E.startComment(ASK[kind] || "");
    }

    // ------------------------------------------------------------ marks, bold and alignment
    // A rule of `marks` or `emphasis` that names this column (and row).
    const names = (rule, column, row) => rule.column === column && E.stable(rule.row) === E.stable(row);
    function setMark(target, mark) {
      E.save(() => {
        const marks = (E.spec.marks || []).filter((m) => !names(m, target.column, target.row));
        if (mark) marks.push(target.row ? { column: target.column, row: target.row, mark } : { column: target.column, mark });
        return [{ op: "set", path: "marks", value: marks.length ? marks : null }];
      });
    }
    // The mark of a cell or header: common symbols, or any 1 to 3 characters.
    function markRow(target, current) {
      if (!target || E.hand) return null;
      const chips = MARKS.map((m) => h("button", { type: "button", class: "te-mchip" + (current === m ? " on" : ""),
        "aria-pressed": String(current === m), text: m, title: current === m ? "Remove the mark" : `Mark with ${m}`,
        onclick: () => setMark(target, current === m ? null : m) }));
      const other = h("input", { class: "te-in te-mark-in", "aria-label": "Other mark", maxlength: "3", placeholder: "other",
        value: current && !MARKS.includes(current) ? current : "" });
      other.addEventListener("change", () => setMark(target, other.value.trim() || null));
      return K.row("Mark", h("div", { class: "te-chips", dataset: { ids: "marks" } }, chips, other));
    }
    const setAlign = (base, a) => E.save([{ op: "set", path: `${base}.align`, value: a === "left" ? null : a }]);
    function alignCtl(col, base) {
      const now = col.align || "left";
      return h("div", { class: "te-seg", role: "group", "aria-label": "Align" }, ["left", "center", "right"].map((a) =>
        h("button", { type: "button", class: now === a ? "on" : "", "aria-pressed": String(now === a),
          text: a[0].toUpperCase() + a.slice(1), onclick: () => setAlign(base, a) })));
    }
    // Bold by hand (`emphasis`) for one cell ({item, cell} of the model).
    const isBold = (at) => (E.spec.emphasis || []).some((e) => names(e, at.cell.id, at.item.key));
    function toggleBold(at) {
      E.save(() => {
        const emphasis = E.spec.emphasis || [];
        const next = isBold(at) ? emphasis.filter((e) => !names(e, at.cell.id, at.item.key))
          : [...emphasis, { row: at.item.key, column: at.cell.id }];
        return [{ op: "set", path: "emphasis", value: next.length ? next : null }];
      });
    }
    const boldCtl = (at) => h("div", null, K.row("Bold", K.switchCtl(isBold(at), "This cell only", () => toggleBold(at), "Bold this cell")),
      at.cell.bold && !isBold(at) ? h("p", { class: "te-muted te-small", text: "Bold by the rule that bolds the best value (Advanced)." }) : null);

    // ------------------------------------------------------------ renaming labels
    // A label is a map entry (maps.<field>.<value>). "This table only" writes
    // the spec's maps (it overrides _shared.json); "everywhere" writes
    // _shared.json. A shared label nothing else uses stays shared.
    function rename(label, text, scope, anchor) {
      const others = label.shared ? E.labelOthers(label) : [];
      if (others.length && !scope) { chooseScope(label, text, others, anchor); return; }
      const value = text.trim(), shared = K.toShared(label.shared, others, scope);
      E.save(() => {
        const map = { ...(((shared ? E.st.shared : E.spec).maps || {})[label.field] || {}) };
        // Back to the data's own text (or, here, to the shared label) when cleared.
        if (!value || (value === label.value && (shared || !label.shared))) delete map[label.value]; else map[label.value] = value;
        return [{ op: "set", path: `maps.${label.field}`, value: Object.keys(map).length ? map : null }];
      }, { shared });
    }
    function closePop() { const pop = $(".te-pop"); if (pop) pop.remove(); }
    function chooseScope(label, text, others, anchor) {
      closePop();
      let scope = "table";
      const done = (ok) => { closePop(); if (ok) rename(label, text, scope); else E.renderTable(); };
      const pop = h("div", { class: "te-pop", role: "dialog", "aria-label": "Where the rename applies",
        onkeydown: (e) => { if (e.key === "Escape") { e.stopPropagation(); done(false); } } },
      h("div", { class: "te-pop-t", text: `Rename “${E.plain(label.text)}” to “${text.trim()}”` }),
      K.scopeChoice(others.join(", "), scope, (v) => { scope = v; }),
      h("div", { class: "te-acts", style: "justify-content:flex-end" },
        h("button", { type: "button", class: "pv-btn sm ghost", text: "Cancel", onclick: () => done(false) }),
        h("button", { type: "button", class: "pv-btn sm primary", text: "Rename", onclick: () => done(true) })));
      document.body.append(pop);
      const box = (anchor && anchor.isConnected ? anchor : $("#te-page")).getBoundingClientRect();
      pop.style.left = `${Math.max(8, Math.min(box.left, innerWidth - pop.offsetWidth - 8))}px`;
      pop.style.top = `${Math.min(box.bottom + 6, innerHeight - pop.offsetHeight - 8)}px`;
      $("input", pop).focus();
    }

    // ------------------------------------------------------------ editing in place
    // What double-click, Enter or F2 edits on a header or a cell, or null.
    function target(el) {
      const model = E.st && E.st.model;
      if (!model || !el) return null;
      const labelled = (x) => (x && x.label ? { name: "Label", text: x.label.text, commit: (v) => rename(x.label, v, null, el) } : null);
      if (el.matches("thead th")) {
        const c = model.header.flat().find((x) => x.id === el.dataset.id);
        if (!c || c.label) return labelled(c);
        const path = c.kind === "leaf" ? E.leafPath(c.id) : c.id.split(".")[0];
        const at = E.colAt(path);
        return at && (!at.col.columns || c.kind === "group") ? header(at.col, path) : null;
      }
      if (el.matches("td.te-block")) {
        const item = model.body.filter((b) => b.block !== undefined)[Number(el.dataset.block)];
        if (E.hand) return { name: "Block text", text: item.text, commit: (v) => E.handUI.setBlock(item.id, v) };
        return labelled(item);
      }
      if (el.matches("td[data-row]")) {
        const item = model.body.find((b) => b.row === Number(el.dataset.row));
        const cell = item && item.cells.find((x) => x.id === el.dataset.id);
        if (E.hand && cell) return { name: "Cell text", text: cell.raw, commit: (v) => E.handUI.setCell(item.key.row, cell.id, v) };
        return labelled(cell);
      }
      return null;
    }
    const header = (col, path) => ({ name: "Header", text: col.header,
      commit: (v) => E.save([{ op: "set", path: `columns.${path}.header`, value: v }]) });
    function startEdit(el) {
      const what = target(el);
      if (!what) return false;
      E.editing = true;
      const input = h("input", { class: "te-cellin", "aria-label": what.name });
      input.value = what.text;
      el.classList.add("te-editing");
      el.replaceChildren(input);
      input.focus();
      input.select();
      let done = false;
      const finish = (commit, move) => {
        if (done) return;
        done = true;
        E.editing = false;
        const next = move ? neighbour(el, move) : null;
        if (commit && input.value !== what.text) what.commit(input.value);
        else E.renderTable();
        if (next && !$(".te-pop")) {  // not while "this table only or everywhere" waits
          E.select(next);
          const td = $(`td[data-id="${CSS.escape(next.id)}"][data-row="${next.row}"]`);
          if (td) td.focus();
        }
      };
      input.addEventListener("keydown", (e) => {
        e.stopPropagation();
        if (e.key === "Enter") { e.preventDefault(); finish(true, "down"); }
        else if (e.key === "Tab") { e.preventDefault(); finish(true, e.shiftKey ? "left" : "right"); }
        else if (e.key === "Escape") { e.preventDefault(); finish(false); }
      });
      input.addEventListener("click", (e) => e.stopPropagation());  // the cell's click would start over
      input.addEventListener("blur", () => finish(true));
      return true;
    }
    // The cell beside a body cell, as a selection (null at the edge).
    function neighbour(el, move) {
      if (!el.matches("td[data-row]")) return null;
      const vertical = move === "down";
      const cells = vertical ? $$("tbody td[data-row]").filter((td) => td.dataset.id === el.dataset.id)
        : $$("td[data-row]", el.parentElement);
      const next = cells[cells.indexOf(el) + (move === "left" ? -1 : 1)];
      return next ? { kind: "cell", id: next.dataset.id, row: Number(next.dataset.row) } : null;
    }

    // ------------------------------------------------------------ the format bar
    // The bar's alignment of the selected column.
    function alignTools() {
      const sel = E.sel, id = sel.kind === "cell" || sel.kind === "column" ? sel.id : null;
      const col = id ? E.colAt(E.leafPath(id)) : null, now = col ? col.col.align || "left" : null;
      return [["⇤", "left"], ["↔", "center"], ["⇥", "right"]].map(([text, a]) =>
        K.icon(text, `Align ${a}`, now === a, !col, () => setAlign(`columns.${E.leafPath(id)}`, a)));
    }
    function renderBar() {
      const bar = $("#te-bar");
      if (!bar) return;
      if (!E.st || !E.spec) { bar.replaceChildren(); return; }
      if (E.hand) { E.handUI.renderBar(bar); return; }
      const sel = E.sel, at = E.selectedCell();
      const colId = sel.kind === "cell" || sel.kind === "column" ? sel.id : null;
      const { icon } = K;
      const focusMark = () => { E.tab = "props"; E.renderTabs(); E.renderPanel(); const chip = $('[data-ids="marks"] button'); if (chip) chip.focus(); };
      bar.replaceChildren(
        icon(h("b", { text: "B" }), "Bold this cell", at && isBold(at), !at, () => toggleBold(at)),
        icon("ᵃ", "Add a mark", false, !colId, focusMark),
        h("span", { class: "te-sep" }),
        ...alignTools(),
        h("span", { class: "te-sep" }),
        h("button", { type: "button", class: "pv-btn sm", text: "Ask the agent for a column", onclick: () => ask("column") }),
        h("button", { type: "button", class: "pv-btn sm", text: "Ask for a row", onclick: () => ask("row") }));
    }
    return { ask, markRow, alignCtl, alignTools, boldCtl, rename, startEdit, target, renderBar };
  };
})();
