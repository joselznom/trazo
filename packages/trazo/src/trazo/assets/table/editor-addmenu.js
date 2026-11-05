// Table editor: the + Column menu (Advanced), for tables from the data: a field,
// a group of columns or one group per value of a text field.
// editor.js calls TableEditor.addMenu(E) with its shared state.
(function () {
  "use strict";
  const T = (window.TableEditor = window.TableEditor || {});
  T.addMenu = function (E) {
    const { h, $ } = E;
    const menu = $("#te-add-menu");
    // Whether the menu was open.
    function close() {
      const open = !menu.hidden;
      menu.hidden = true;
      $("#te-add").setAttribute("aria-expanded", "false");
      return open;
    }
    $("#te-add").addEventListener("click", (e) => {
      e.stopPropagation();
      if (!menu.hidden) { close(); return; }
      if (!E.st) return;
      const keys = (E.spec.rows || {}).keys || [];
      const valueField = E.fieldNames().find((f) => /float|composite|int/.test(E.fieldType(f)) && !keys.includes(f)) || E.fieldNames()[0];
      menu.replaceChildren(h("h6", { text: "Field from the data" }),
        ...E.fieldNames().map((f) => h("button", { type: "button", role: "menuitem", onclick: () => addColumn(f) },
          h("span", { text: f }), h("span", { class: "te-muted te-small", text: E.fieldType(f) }))),
        h("h6", { text: "Group" }),
        h("button", { type: "button", role: "menuitem", onclick: () => addGroup(valueField) }, h("span", { text: "New group (spanning header)" })),
        h("h6", { text: "Pivot on a text field" }),
        ...E.fieldNames().filter((f) => E.fieldType(f) === "string" && !keys.includes(f)).map((f) =>
          h("button", { type: "button", role: "menuitem", onclick: () => addPivot(f, valueField) }, h("span", { text: `One column group per ${f}` }))));
      menu.hidden = false;
      $("#te-add").setAttribute("aria-expanded", "true");
      const first = $("button", menu);
      if (first) first.focus();
    });
    document.addEventListener("click", (e) => { if (!menu.hidden && !menu.contains(e.target)) close(); });
    function leafFor(field, id) {
      const item = { id, field, header: field };
      const ops = [];
      const type = E.fieldType(field);
      if (/composite|float/.test(type)) {
        const all = E.formats();
        const fresh = E.uniqueId(id, Object.keys(all));
        ops.push({ op: "set", path: `formats.${fresh}`, value: E.defaultTemplate(field) });
        item.format = fresh;
      }
      if (/int|float|composite/.test(type) && !type.includes("string")) item.align = "right";
      return { item, ops };
    }
    function insertBefore() {  // after the selected top-level column, else at the end
      if (E.sel.kind !== "column" && E.sel.kind !== "group") return null;
      const top = (E.sel.kind === "group" ? E.sel.path : E.leafPath(E.sel.id)).split(".")[0];
      const i = E.spec.columns.findIndex((c) => c.id === top);
      return i >= 0 && E.spec.columns[i + 1] ? E.spec.columns[i + 1].id : null;
    }
    function addColumn(field) {
      close();
      E.tab = "props";
      const id = E.uniqueId(field, E.spec.columns.map((c) => c.id));
      const { item, ops } = leafFor(field, id);
      E.save([...ops, { op: "add", into: "columns", item, before: insertBefore() }]);
      E.sel = { kind: "column", id };
    }
    function addGroup(field) {
      close();
      E.tab = "props";
      const id = E.uniqueId("group", E.spec.columns.map((c) => c.id));
      const { item, ops } = leafFor(field, E.uniqueId(field, []));
      E.save([...ops, { op: "add", into: "columns", item: { id, header: "Group", columns: [item] }, before: insertBefore() }]);
      E.sel = { kind: "group", id, path: id };
    }
    function addPivot(pivot, field) {
      close();
      E.tab = "props";
      const id = E.uniqueId(pivot, E.spec.columns.map((c) => c.id));
      const { item, ops } = leafFor(field, "value");
      item.header = "Value";
      E.save([...ops, { op: "add", into: "columns", item: { id, pivot, columns: [item] }, before: insertBefore() }]);
      E.sel = { kind: "group", id, path: id };
    }
    return { close };
  };
})();
