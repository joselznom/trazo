"""Tables typed by hand: cells in the spec, rendered like any table."""

import json
import unittest

import _support  # noqa: F401  (sets TRAZO_ROOT first)
from test_table import SPECS, Restore, run

from trazo import table, table_hand


def hand_spec(**extra):
    return {"schema_version": 1, "title": "Included studies.",
            "columns": [{"id": "study", "header": "Study"},
                        {"id": "patients", "header": "Patients", "align": "right"}],
            "body": [{"id": "b1", "block": "Europe"},
                     {"id": "r1", "cells": {"study": "Smith 2020", "patients": "120"}},
                     {"id": "r2", "cells": {"study": "García *et al.*", "patients": "1,540"}}],
            **extra}


def write(label="studies", **extra):
    (SPECS / f"{label}.json").write_text(json.dumps(hand_spec(**extra)), encoding="utf-8")
    return label


class RenderTests(Restore):
    def test_cells_render_as_typed_with_blocks_in_place(self):
        result = table.build(write())
        self.assertEqual(result.errors, [])
        self.assertIn("| **Europe**", result.markdown)
        self.assertIn("| Smith 2020", result.markdown)
        self.assertIn("| García *et al.*", result.markdown)
        self.assertIn("1,540", result.markdown)
        self.assertIn("{#tbl:studies}", result.markdown)

    def test_rows_before_the_first_block_and_a_last_empty_block_stay(self):
        body = [{"id": "r1", "cells": {"study": "A"}}, {"id": "b1", "block": "Later"}]
        result = table.build(write(body=body))
        self.assertEqual(result.errors, [])
        model = table.render_model(result)
        self.assertEqual([("block" in item) for item in model["body"]], [False, True])
        self.assertEqual((model["body"][1]["id"], model["body"][1]["text"]), ("b1", "Later"))

    def test_only_the_allowed_markup_stays_active(self):
        for typed, written in (("*a* **b** x^2^ H~2~O", "*a* **b** x^2^ H~2~O"),
                               ("p<0.05 | $5 #1 _x_ [y]", r"p\<0.05 \| \$5 \#1 \_x\_ \[y\]"),
                               ("- 3 items", r"\- 3 items"), ("1. first", r"1\. first"),
                               ("* point", r"\* point"), ("see [@smith2020]", "see [@smith2020]"),
                               ("two\nlines", "two lines"), ("***", r"\*\*\*"),
                               ("~~old~~", r"\~\~old\~\~"), ("***both***", "***both***"),
                               ("**b ^2^**", "**b ^2^**"), ("**bold *italic***", "**bold *italic***"),
                               ("**bold*italic***", "**bold*italic***"), ("H~2~^+^", "H~2~^+^"),
                               ("* * *", r"\* \* \*"), ("[mail a@b](https://x.y)", r"\[mail a\@b\](https://x.y)"),
                               ("[@a; @b, p. 3]", "[@a; @b, p. 3]"), ("2 * 3 * 4", "2 * 3 * 4"),
                               ("(1) one", r"(1\) one"), ("a. item", r"a\. item"), ("::: div", r"\::: div")):
            with self.subTest(typed=typed):
                self.assertEqual(table_hand.cell_markdown(typed), written)

    def test_the_editor_draws_nested_bold_and_italic(self):
        self.assertEqual(table.md_inline("**bold *italic***"), "<strong>bold <em>italic</em></strong>")
        self.assertEqual(table.md_inline("***both***"), "<strong><em>both</em></strong>")

    def test_block_text_is_literal_like_a_cell(self):
        body = [{"id": "b1", "block": "A | B <x>"}, {"id": "r1", "cells": {"study": "S"}}]
        result = table.build(write(body=body))
        self.assertIn(r"**A \| B \<x\>**", result.markdown)
        self.assertEqual(table.render_model(result)["body"][0]["text"], "A | B <x>")

    def test_the_model_gives_each_cell_its_raw_text(self):
        model = table.render_model(table.build(write()))
        cell = model["body"][1]["cells"][0]
        self.assertEqual((cell["raw"], model["body"][1]["key"]), ("Smith 2020", {"row": "r1"}))
        self.assertNotIn("label", cell)

    def test_bold_and_marks_name_a_row_by_id(self):
        label = write(emphasis=[{"row": {"row": "r1"}, "column": "study"}],
                      marks=[{"row": {"row": "r2"}, "column": "patients", "mark": "a"}])
        markdown = table.build(label).markdown
        self.assertIn("**Smith 2020**", markdown)
        self.assertIn("1,540^a^", markdown)

    def test_repeated_headers_are_allowed_in_a_table_typed_by_hand(self):
        columns = [{"id": "a", "header": "Value"}, {"id": "b", "header": "Value"}]
        self.assertEqual(table.build(write(columns=columns, body=[{"id": "r1", "cells": {}}])).errors, [])

    def test_bad_specs_are_errors(self):
        for extra, message in (({"data": "x"}, "unknown key 'data'"),
                               ({"body": [{"id": "r1", "cells": {"gone": "x"}}]}, "unknown column 'gone'"),
                               ({"body": [{"id": "r1"}, {"id": "r1", "block": "x"}]}, "duplicate row id"),
                               ({"body": [{"id": "r1", "cells": {"study": 3}}]}, "must be text"),
                               ({"body": [{"id": "b1", "block": "only"}]}, "no rows"),
                               ({"columns": [{"id": "a", "field": "x", "header": "A"}]}, "unknown key 'field'")):
            with self.subTest(message=message):
                errors = table.build(write(**extra)).errors
                self.assertTrue(errors and message in errors[0]["message"], errors)


class PatchTests(Restore):
    def spec(self, label="studies"):
        return json.loads((SPECS / f"{label}.json").read_text(encoding="utf-8"))

    def test_cell_block_and_row_operations_are_undoable(self):
        label = write()
        before = (SPECS / "studies.json").read_bytes()
        table.patch(label, [{"op": "cell", "row": "r1", "column": "study", "text": "Smith 2021"},
                            {"op": "cell", "row": "r1", "column": "patients", "text": ""},
                            {"op": "block", "row": "b1", "text": "Asia"},
                            {"op": "row-add", "id": "r3", "before": "r2"},
                            {"op": "row-add", "id": "b2", "block": "Other"},
                            {"op": "row-move", "row": "r2", "before": "r3"}])
        body = self.spec()["body"]
        self.assertEqual([item["id"] for item in body], ["b1", "r1", "r2", "r3", "b2"])
        self.assertEqual(body[1]["cells"], {"study": "Smith 2021"})
        self.assertEqual(body[0]["block"], "Asia")
        self.assertIn("Smith 2021", table.output_path(label).read_text())
        table.undo(label)
        self.assertEqual((SPECS / "studies.json").read_bytes(), before)

    def test_removing_a_row_drops_its_bold_and_marks(self):
        label = write(emphasis=[{"row": {"row": "r1"}, "column": "study"}],
                      marks=[{"row": {"row": "r1"}, "column": "study", "mark": "a"}])
        table.patch(label, [{"op": "row-remove", "row": "r1"}])
        spec = self.spec()
        self.assertEqual(([item["id"] for item in spec["body"]], spec.get("emphasis"), spec.get("marks")),
                         (["b1", "r2"], None, None))

    def test_removing_a_column_drops_its_cells(self):
        label = write()
        table.patch(label, [{"op": "remove", "column": "patients"}])
        self.assertEqual(self.spec()["body"][1]["cells"], {"study": "Smith 2020"})

    def test_cells_stay_when_their_column_joins_a_group(self):
        label = write()
        spec = self.spec()
        table.patch(label, [{"op": "set", "path": "columns",
                             "value": [{"id": "g", "header": "Group", "columns": spec["columns"]}]}])
        self.assertIn("Smith 2020", table.build(label).markdown)

    def test_paste_fills_from_the_cell_and_grows_the_table(self):
        label = write()
        table.patch(label, [{"op": "paste", "row": "r2", "column": "patients",
                             "rows": [["1", "12.5%"], ["2", "n/a"], ["3", "7"]]}])
        spec = self.spec()
        ids = [c["id"] for c in spec["columns"]]
        self.assertEqual(len(ids), 3)
        rows = [item for item in spec["body"] if "cells" in item]
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[1]["cells"], {"study": "García *et al.*", "patients": "1", ids[2]: "12.5%"})
        self.assertEqual(rows[3]["cells"], {"patients": "3", ids[2]: "7"})
        self.assertNotIn("align", spec["columns"][2])  # "n/a" is not a number

    def test_a_new_column_of_numbers_is_aligned_right_once(self):
        label = write()
        table.patch(label, [{"op": "paste", "row": "r1", "column": "patients", "rows": [["1", "2.5"], ["3", "-4,1"]]}])
        self.assertEqual(self.spec()["columns"][2]["align"], "right")

    def test_a_pasted_header_is_one_line(self):
        spec = table_hand.new_spec("T.", [["First\nline"], ["x"]])
        self.assertEqual(spec["columns"][0]["header"], "First line")

    def test_a_cell_of_a_table_from_data_is_refused(self):
        with self.assertRaisesRegex(table.TableError, "typed by hand"):
            table.patch("results", [{"op": "cell", "row": "r1", "column": "n", "text": "9"}])


class NewTests(Restore):
    def test_new_spec_from_pasted_rows_uses_the_first_as_headers(self):
        spec = table_hand.new_spec("Studies.", [["Study", "n"], ["A", "10"], ["B", "20"]])
        self.assertEqual([(c["header"], c.get("align")) for c in spec["columns"]],
                         [("Study", None), ("n", "right")])
        self.assertEqual(len(spec["body"]), 2)
        self.assertEqual(table.Checker().spec(spec), [])

    def test_new_empty_spec_has_three_columns_and_three_rows(self):
        spec = table_hand.new_spec("Studies.", None)
        self.assertEqual((len(spec["columns"]), len(spec["body"])), (3, 3))

    def test_labels_come_from_the_title_and_stay_unique(self):
        write("incluidos-a-o")
        self.assertEqual(table_hand.free_label("Incluidos: año!"), "incluidos-ano")
        self.assertEqual(table_hand.free_label("Incluidos a-o"), "incluidos-a-o-2")
        self.assertEqual(table_hand.free_label("¿?"), "table")

    def test_the_command_scaffolds_a_clean_spec_and_describe_works(self):
        code, out, err = run(["new", "typed", "--hand"])
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(table.build("typed").errors, [])
        code, out, _ = run(["describe", "typed"])
        self.assertEqual(code, 0)
        self.assertIn("typed by hand", out)


if __name__ == "__main__":
    unittest.main()
