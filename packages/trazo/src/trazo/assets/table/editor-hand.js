// Table editor: tables typed by hand (trazo.table_hand). Their cells are text
// kept in the spec, so here a cell is typed like in a spreadsheet: the format
// bar, rows and columns, pasting a range, and New table.
// editor.js calls TableEditor.hand(E, K) with its shared state.
(function () {
  "use strict";
  const T = (window.TableEditor = window.TableEditor || {});
  const MIXED = "This text has other formatting: double-click the cell and select the words";
  const FORMATS = [["B", "Bold", "**"], ["I", "Italic", "*"], ["x²", "Superscript", "^"], ["x₂", "Subscript", "~"]];
  T.hand = function (E, K) {
    const { h, $ } = E;
    const notes = T.messages(h);  // the banner, and what a reply says about other programs' writes
    const { row, ctx, icon } = K;
    const shortId = (id) => id.split(".").pop();
    const body = () => E.spec.body;
    // The id of the selected row or block, or null.
    function selectedRow() {
      if (E.sel.kind === "block") return E.sel.id;
      const at = E.selectedCell();
      return at ? at.item.key.row : null;
    }
    function freshId(prefix, taken) {
      let n = 1;
      while (taken.includes(`${prefix}${n}`)) n += 1;
      return `${prefix}${n}`;
    }
    const setCell = (rowId, leaf, text) => E.save([{ op: "cell", row: rowId, column: shortId(leaf), text }]);
    const setBlock = (rowId, text) => E.save([{ op: "block", row: rowId, text }]);

    // ------------------------------------------------------------ formatting
    // Text with a mark on or off (bold and italic combine as ***text***), or null
    // when it already has other marks: those are the author's to edit by hand.
    function wrapped(text, mark) {
      if (mark.startsWith("*")) {
        const found = text.match(/^(\*{1,3})([^*]+)\1$/);
        if (!found && text.includes("*")) return null;
        const stars = "*".repeat((found ? found[1].length : 0) ^ mark.length);
        return `${stars}${found ? found[2] : text}${stars}`;
      }
      if (text.length > 2 && text.startsWith(mark) && text.endsWith(mark) && !text.slice(1, -1).includes(mark)) return text.slice(1, -1);
      return text.includes(mark) ? null : `${mark}${text}${mark}`;
    }
    // While typing, the selected words; otherwise the whole cell.
    function format(mark) {
      const input = $(".te-cellin");
      const spaced = (text) => (mark === "^" || mark === "~") && /\s/.test(text.trim());
      if (input) {
        const from = input.selectionStart, to = input.selectionEnd, part = input.value.slice(from, to);
        if (!part) { E.flash("Select the words to format"); return; }
        if (spaced(part)) { E.flash("Superscript and subscript work on text without spaces"); return; }
        const next = wrapped(part, mark);
        if (next === null) E.flash(MIXED); else input.setRangeText(next, from, to, "select");
        return;
      }
      const at = E.selectedCell();
      if (!at || !at.cell.raw) return;
      if (spaced(at.cell.raw)) { E.flash("Superscript and subscript work on text without spaces: double-click the cell and select the part"); return; }
      const next = wrapped(at.cell.raw, mark);
      if (next === null) E.flash(MIXED); else setCell(at.item.key.row, at.cell.id, next);
    }

    // ------------------------------------------------------------ rows and columns
    async function addRow(block) {
      const ids = body().map((item) => item.id), at = selectedRow();
      const id = freshId(block ? "b" : "r", ids);
      const before = at ? ids[ids.indexOf(at) + 1] || null : null;
      await E.save([{ op: "row-add", id, before, ...(block ? { block: "Block" } : {}) }]);
      if (block) { E.select({ kind: "block", id }); return; }
      const item = E.st.model && E.st.model.body.find((b) => b.key && b.key.row === id);
      if (item && item.cells.length) E.select({ kind: "cell", id: item.cells[0].id, row: item.row });
    }
    function addColumn() {
      const taken = E.spec.columns.flatMap((c) => (c.columns || [c]).map((x) => x.id));
      const id = freshId("c", taken);
      const top = E.sel.id && E.leafPath(E.sel.id).split(".")[0];
      const i = E.spec.columns.findIndex((c) => c.id === top);
      const before = i >= 0 && E.spec.columns[i + 1] ? E.spec.columns[i + 1].id : null;
      E.save([{ op: "add", into: "columns", item: { id, header: `Column ${taken.length + 1}` }, before }]);
      E.sel = { kind: "column", id };
    }
    function removeRow() {
      const id = selectedRow();
      if (!id) return;
      E.sel = { kind: "table" };
      E.save([{ op: "row-remove", row: id }]);
    }
    async function moveRow(step) {
      const id = selectedRow(), ids = body().map((item) => item.id), i = ids.indexOf(id);
      if (i < 0 || i + step < 0 || i + step >= ids.length) return;
      const leaf = E.sel.id;
      await E.save([{ op: "row-move", row: id, before: step < 0 ? ids[i - 1] : ids[i + 2] || null }]);
      // A cell is selected by its position: follow the row to its new one.
      const item = E.st.model && E.st.model.body.find((b) => b.key && b.key.row === id);
      if (item) E.select({ kind: "cell", id: leaf, row: item.row });
    }

    // ------------------------------------------------------------ after Make editable
    // The preview leaves a note when it converts a Markdown table: offer to undo it.
    // From the bar, which is drawn after the route clears the banner.
    function offerUndo() {
      let made = null;
      try {
        made = JSON.parse(sessionStorage.getItem("trazo.converted"));
        if (!made || made.label !== E.label) return;
        sessionStorage.removeItem("trazo.converted");
      } catch (e) { return; }
      const notice = $("#te-banner");
      const undo = async () => {
        const r = await E.api("/__table/convert", { op: "undo", ...made });
        if (!r.ok) { notes.failed(r.data, "Not undone"); return; }
        // The table is gone: acknowledge any warning, then go back to the text.
        if (r.data.warning) alert(`Undone: the Markdown is back in the text. ${r.data.warning}`);
        location.href = E.App.returnUrl || `/${made.doc}.html`;
      };
      notice.className = "te-banner";
      notice.replaceChildren(h("div", { class: "te-grow",
        text: `This table is now editable here. Its Markdown left the text.${made.warning ? ` ${made.warning}` : ""}` }),
        h("button", { type: "button", class: "pv-btn sm", text: "Undo", onclick: undo }),
        h("button", { type: "button", class: "te-icon", "aria-label": "Dismiss", text: "✕", onclick: () => { notice.hidden = true; } }));
      notice.hidden = false;
    }

    // ------------------------------------------------------------ the format bar
    function renderBar(bar) {
      offerUndo();
      const at = E.selectedCell(), rowId = selectedRow();
      const btn = (text, onclick, disabled) => h("button", { type: "button", class: "pv-btn sm", text, disabled: disabled ? "" : null, onclick });
      bar.replaceChildren(
        ...FORMATS.map(([text, label, mark]) => icon(text, label, false, !at, () => format(mark))),
        h("span", { class: "te-sep" }),
        ...E.alignTools(),
        h("span", { class: "te-sep" }),
        btn("+ Row", () => addRow(false)), btn("+ Block", () => addRow(true)), btn("+ Column", addColumn),
        icon("↑", "Move the row up", false, !rowId, () => moveRow(-1)), icon("↓", "Move the row down", false, !rowId, () => moveRow(1)),
        btn("Delete row", removeRow, !rowId));
    }

    // ------------------------------------------------------------ the panel
    function tableProps() {
      const spec = E.spec;
      return [ctx("Table · typed by hand", E.label, "Double-click a cell to type in it, or paste a range from Excel, Word or Sheets."),
        h("div", { class: "te-sec" }, h("h4", { text: "Caption" }),
          ...K.captionRows(spec))];
    }
    // A text field that saves when it changes.
    function typed(name, value, save) {
      const field = h("input", { class: "te-in", "aria-label": name });
      field.value = value;
      field.addEventListener("change", () => save(field.value));
      return field;
    }
    function cellProps(at) {
      return [ctx("Cell · typed by hand", E.plain(at.cell.text_html) || "(empty)", null),
        h("div", { class: "te-sec" }, row("Text", typed("Cell text", at.cell.raw, (v) => setCell(at.item.key.row, at.cell.id, v))),
          h("p", { class: "te-muted te-small", text: "Kept and published as typed: nothing is calculated." }))];
    }
    function blockProps() {
      const item = body().find((b) => b.id === E.sel.id);
      if (!item) return [ctx("Block row", E.sel.id, "Not in the table any more.")];
      return [ctx("Block row", item.block || "(empty)", null),
        h("div", { class: "te-sec" }, row("Text", typed("Block text", item.block, (v) => setBlock(item.id, v))))];
    }

    // ------------------------------------------------------------ pasting a range
    // Tab-separated cells. Spreadsheets quote a cell that holds a line break or
    // a tab, with "" for a quote; any other quote is the cell's own text.
    function range(text) {
      const source = text.replace(/\r\n?/g, "\n").replace(/\n+$/, "");
      const quoted = /"((?:[^"]|"")*[\n\t](?:[^"]|"")*)"(?=[\t\n]|$)/y;
      const rows = [[]];
      let at = 0;
      while (at <= source.length) {
        quoted.lastIndex = at;
        const match = quoted.exec(source);
        const end = match ? quoted.lastIndex : at + source.slice(at).search(/[\t\n]|$/);
        rows[rows.length - 1].push(match ? match[1].replace(/""/g, '"') : source.slice(at, end));
        if (source[end] === "\n") rows.push([]);
        at = end + 1;
      }
      return rows;
    }
    document.addEventListener("paste", async (e) => {
      if (!E.hand) return;
      const rows = range(e.clipboardData.getData("text/plain"));
      const inCell = e.target.matches && e.target.matches(".te-cellin");
      const big = rows.length > 1 || rows[0].length > 1;
      if (/^(INPUT|TEXTAREA)$/.test(e.target.tagName) && !(inCell && big)) return;
      const at = E.selectedCell();
      if (!at) { E.flash("Select the cell where the range starts"); return; }
      e.preventDefault();
      if (inCell) e.target.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      await E.save([{ op: "paste", row: at.item.key.row, column: shortId(at.cell.id), rows }]);
      E.flash(`Pasted ${rows.length} row${rows.length === 1 ? "" : "s"} · ⌘Z undoes`);
    });

    // ------------------------------------------------------------ New table
    async function openNew() {
      const r = await E.api("/__table/sections");
      if (!r.ok) { E.flash(r.data.error || "Cannot read the documents"); return; }
      const docs = r.data.documents;
      if (!docs.length) { E.flash("No document can take a table"); return; }
      let mode = "empty";
      const title = h("input", { class: "te-in", "aria-label": "Table title", placeholder: "Characteristics of the included studies." });
      const area = h("textarea", { class: "te-in", "aria-label": "Pasted cells", hidden: true, placeholder: "Paste here (⌘V). The first row becomes the headers." });
      const doc = K.selectCtl(docs.map((d) => [d.doc, d.title]), docs[0].doc, () => fillSections(), "Document");
      const section = h("select", { class: "te-in", "aria-label": "Section" });
      const fillSections = () => {
        const found = docs.find((d) => d.doc === doc.value).sections;
        section.replaceChildren(...found.map((s, i) => h("option", { value: String(i), text: s.title })));
      };
      fillSections();
      const choice = (value, text) => h("label", { class: "te-opt" }, h("input", { type: "radio", name: "te-new-mode", value,
        checked: value === mode ? "" : null, onchange: () => { mode = value; area.hidden = value !== "paste"; } }), h("span", { text }));
      const close = () => overlay.remove();
      const create = async () => {
        const found = docs.find((d) => d.doc === doc.value).sections[Number(section.value)];
        const rows = mode === "paste" && area.value.trim() ? range(area.value) : null;
        const made = await E.api("/__table/new", { title: title.value, file: found.file, line: found.line, ...(rows ? { rows } : {}) });
        if (!made.ok) { notes.failed(made.data, "The table was not created"); return; }
        close();
        await E.loadList();
        location.hash = made.data.label;
        E.flash(`Added at the end of “${found.title}”. Cite it as [@tbl:${made.data.label}]`);
        // Opening the new table clears the banner: a warning stays as a notice to dismiss.
        if (made.data.warning) E.flash(made.data.warning, { label: "OK", run: () => {} });
      };
      const overlay = h("div", { class: "te-modal", onkeydown: (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } } },
        h("div", { class: "te-dialog", role: "dialog", "aria-label": "New table" }, h("h3", { text: "New table" }),
          row("Title", title), h("div", { class: "te-scope" }, choice("empty", "Empty: 3 columns, 3 rows"), choice("paste", "Paste from Excel, Word or Sheets")),
          area, row("Document", doc), row("Section", section),
          h("p", { class: "te-muted te-small", text: "It is added at the end of the section." }),
          h("div", { class: "te-acts", style: "justify-content:flex-end" },
            h("button", { type: "button", class: "pv-btn sm ghost", text: "Cancel", onclick: close }),
            h("button", { type: "button", class: "pv-btn sm primary", text: "Create table", onclick: create }))));
      document.body.append(overlay);
      title.focus();
    }
    $("#te-new").addEventListener("click", openNew);
    return { renderBar, tableProps, cellProps, blockProps, setCell, setBlock };
  };
})();
