"""Make editable: a Markdown table of a manuscript becomes a table typed by hand."""

import json
import unittest

import _support
from test_table import SPECS, Restore

from trazo import table, table_convert, table_editor
from trazo.local_server import NotFound
from trazo.preview_sources import change_source, get_source

DOC = _support.ROOT / "manuscript/main/01_introduction.md"
TABLE = """# One

Before.

| Outcome | Patients |
|---------|---------:|
| Response | **12** |
| *Remission* | 3^a^ [@smith2020] |

Table: [Outcomes.]{.caption-title} By group. {#tbl:outcomes}

::: {.table-note custom-style="Table Note"}
^a^ At week 12.
:::

After.
"""


class ConvertTests(Restore):
    def setUp(self):
        super().setUp()
        self.addCleanup(DOC.write_bytes, DOC.read_bytes())

    def plan(self, text=TABLE, identifier="tbl:outcomes"):
        DOC.write_text(text, encoding="utf-8")
        return table_convert.plan("manuscript", identifier)

    def refused(self, text, reason):
        with self.assertRaisesRegex(table_convert.CannotConvert, reason):
            self.plan(text)

    def test_a_table_becomes_cells_with_its_caption_and_note(self):
        spec = self.plan()["spec"]
        self.assertEqual((spec["title"], spec["legend"], spec["note"]), ("Outcomes.", "By group.", "^a^ At week 12."))
        self.assertEqual([(c["header"], c.get("align")) for c in spec["columns"]],
                         [("Outcome", None), ("Patients", "right")])
        self.assertEqual([list(item["cells"].values()) for item in spec["body"]],
                         [["Response", "**12**"], ["*Remission*", "3^a^ [@smith2020]"]])
        self.assertEqual(table.Checker().spec(spec), [])

    def test_the_source_keeps_a_line_that_includes_it(self):
        text = self.plan()["text"]
        self.assertEqual(text, "# One\n\nBefore.\n\n!include tables/output/md/outcomes.md\n\nAfter.\n")

    def test_a_caption_without_a_title_stays_as_it_was(self):
        spec = self.plan(TABLE.replace("[Outcomes.]{.caption-title} By group.", "Outcomes by group."))["spec"]
        self.assertEqual((spec["title"], spec["legend"]), ("", "Outcomes by group."))
        self.assertIn("Table: Outcomes by group. {#tbl:outcomes}", table.build("outcomes", spec).markdown)

    def test_a_full_width_bold_row_becomes_a_block(self):
        grid = """+-----+-----+
| A   | B   |
+=====+=====+
| **Europe** |
+-----+-----+
| x   | y   |
+-----+-----+

Table: Grid. {#tbl:outcomes}
"""
        spec = self.plan(grid)["spec"]
        self.assertEqual(spec["body"][0]["block"], "Europe")

    def test_what_would_be_lost_is_refused_with_the_reason(self):
        for old, new, reason in (("**12**", "[a link](https://x.y)", "a link"), ("**12**", "$x^2$", "an equation"),
                                 ("**12**", "`code`", "code"), ("**12**", "{{v:n}}", "a value")):
            with self.subTest(reason=reason):
                self.refused(TABLE.replace(old, new), reason)

    def test_merged_cells_are_refused(self):
        grid = """+-----+-----+
| A   | B   |
+=====+=====+
| x   | y   |
|     +-----+
|     | z   |
+-----+-----+

Table: Grid. {#tbl:outcomes}
"""
        self.refused(grid, "merged cells")

    def test_a_literal_mark_that_would_turn_into_formatting_is_refused(self):
        self.refused(TABLE.replace("**12**", r"a\*b\*c"), "would change")

    def test_a_label_in_use_or_an_unusable_one_is_refused(self):
        (SPECS / "outcomes.json").write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
        self.refused(TABLE, "already")
        (SPECS / "outcomes.json").unlink()
        with self.assertRaisesRegex(table_convert.CannotConvert, "name"):
            self.plan(TABLE.replace("tbl:outcomes", "tbl:Out.comes"), "tbl:Out.comes")

    def test_convert_and_undo_through_the_editor(self):
        DOC.write_text(TABLE, encoding="utf-8")
        request = {"doc": "manuscript", "id": "tbl:outcomes"}
        self.assertEqual(table_editor.convert_table({**request, "op": "check"}), {"ok": True})
        made = table_editor.convert_table({**request, "op": "convert"})
        self.assertEqual(made["label"], "outcomes")
        self.assertIn("!include", DOC.read_text(encoding="utf-8"))
        self.assertTrue(table.build("outcomes").hand)
        self.assertIn("Response", table.output_path("outcomes").read_text(encoding="utf-8"))
        table_editor.convert_table({"op": "undo", "doc": "manuscript", **made})
        self.assertEqual(DOC.read_text(encoding="utf-8"), TABLE)
        self.assertFalse(table.output_path("outcomes").exists())
        self.assertFalse((SPECS / "outcomes.json").exists())

    def test_undo_is_refused_after_the_table_was_edited(self):
        DOC.write_text(TABLE, encoding="utf-8")
        request = {"doc": "manuscript", "id": "tbl:outcomes"}
        made = table_editor.convert_table({**request, "op": "convert"})
        table.patch("outcomes", [{"op": "cell", "row": "r1", "column": "c1", "text": "Changed"}])
        with self.assertRaises(table.TableConflict):
            table_editor.convert_table({"op": "undo", "doc": "manuscript", **made})
        self.assertTrue((SPECS / "outcomes.json").exists())

    def test_undo_keeps_text_saved_after_the_conversion(self):
        DOC.write_text(TABLE, encoding="utf-8")
        made = table_editor.convert_table({"op": "convert", "doc": "manuscript", "id": "tbl:outcomes"})
        state = get_source("manuscript/main/01_introduction.md")
        change_source({"file": state["file"], "text": state["text"] + "\nNew prose.\n", "base_sha256": state["sha256"]})
        table_editor.convert_table({"op": "undo", "doc": "manuscript", **made})
        self.assertEqual(DOC.read_text(encoding="utf-8"), TABLE + "\nNew prose.\n")
        self.assertFalse((SPECS / "outcomes.json").exists())

    def test_undo_needs_the_line_that_includes_the_table(self):
        DOC.write_text(TABLE, encoding="utf-8")
        made = table_editor.convert_table({"op": "convert", "doc": "manuscript", "id": "tbl:outcomes"})
        DOC.write_text("# One\n", encoding="utf-8")
        with self.assertRaises(NotFound):
            table_editor.convert_table({"op": "undo", "doc": "manuscript", **made})
        self.assertTrue((SPECS / "outcomes.json").exists())

    def test_a_caption_right_under_the_table_keeps_the_next_paragraph(self):
        text = "| A | B |\n|---|---|\n| x | y |\nTable: Grid. {#tbl:outcomes}\n\nAfter.\n"
        self.assertEqual(self.plan(text)["text"], "!include tables/output/md/outcomes.md\n\nAfter.\n")

    def test_note_attributes_are_refused(self):
        special = TABLE.replace('{.table-note custom-style="Table Note"}',
                                '{#important .table-note .warning custom-style="Special Note"}')
        self.refused(special, "note has attributes")
        self.assertEqual(self.plan(TABLE.replace('{.table-note custom-style="Table Note"}', "table-note"))["spec"]["note"],
                         "^a^ At week 12.")

    def test_table_attributes_are_refused(self):
        self.refused(TABLE.replace("{#tbl:outcomes}", '{#tbl:outcomes .striped custom-style="Special"}'), "attributes")

    def test_links_and_footnotes_defined_elsewhere_are_found(self):
        for cell, reason in (("[website][ext]", "a link"), ("12[^data]", "a footnote")):
            with self.subTest(cell=cell):
                text = TABLE.replace("**12**", cell) + "\n[ext]: https://x.y\n\n[^data]: From the data.\n"
                self.refused(text, reason)

    def test_a_note_or_caption_that_would_change_is_refused(self):
        self.refused(TABLE.replace("^a^ At week 12.", "- One\n- Two"), "note would change")
        self.refused(TABLE.replace("By group.", "By\\\ngroup."), "caption would change")
        spec = self.plan(TABLE.replace("By group.", "By\ngroup."))["spec"]  # a wrapped line is the same text
        self.assertEqual(spec["legend"], "By group.")

    def test_a_check_says_why_not(self):
        DOC.write_text(TABLE.replace("**12**", "`code`"), encoding="utf-8")
        with self.assertRaisesRegex(table_convert.CannotConvert, "a cell has code"):
            table_editor.convert_table({"op": "check", "doc": "manuscript", "id": "tbl:outcomes"})

    def test_a_document_that_changed_leaves_no_table_behind(self):
        DOC.write_text(TABLE, encoding="utf-8")
        found = table_convert.plan("manuscript", "tbl:outcomes")
        DOC.write_text(TABLE + "\nMore.\n", encoding="utf-8")
        with self.assertRaises(Exception):
            table_editor.publish(found["label"], found["spec"], found["source"], found["text"])
        self.assertFalse((SPECS / "outcomes.json").exists())


if __name__ == "__main__":
    unittest.main()
