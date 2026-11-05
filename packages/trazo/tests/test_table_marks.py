"""Table marks (a, *, †) and the label cells the editor renames in place."""

import unittest

import _support  # noqa: F401  (sets TRAZO_ROOT first)
from test_table import Restore, spec

from trazo import table

ROW = {"validation": "internal", "cohort": "site1", "method": "logistic"}


class MarkTests(Restore):
    def test_marks_are_superscripts_on_cells_and_headers(self):
        data = spec("results")
        data["marks"] = [{"column": "method", "mark": "a"},
                         {"column": "metric.auc.est", "row": ROW, "mark": "*"}]
        result = table.build("results", data)
        self.assertEqual(result.errors, [])
        self.assertIn("| Model^a^", result.markdown)
        self.assertIn("0.812^\\*^", result.markdown)
        model = table.render_model(result)
        head = next(c for c in model["header"][0] if c["id"] == "method")
        self.assertEqual((head["text_html"], head["mark"]), ("Model<sup>a</sup>", "a"))
        cell = next(c for c in model["body"][1]["cells"] if c["id"] == "metric.auc.est")
        self.assertEqual((cell["text_html"], cell["mark"]), ("0.812<sup>*</sup>", "*"))

    def test_a_mark_follows_the_bold_of_its_cell(self):
        data = spec("results")
        best = {"validation": "internal", "cohort": "site1", "method": "forest"}
        data["marks"] = [{"column": "metric.auc.est", "row": best, "mark": "†"}]
        markdown = table.build("results", data).markdown
        self.assertRegex(markdown, r"\*\*0\.\d+\*\*\^†\^")

    def test_bad_and_stale_marks_are_errors(self):
        data = spec("results")
        for marks, message in (
                ([{"column": "method", "mark": "a b"}], "marks.0.mark must be"),
                ([{"column": "method", "mark": "a", "color": 1}], "unknown key 'color'"),
                ([{"column": "gone", "mark": "a"}], "no visible column 'gone'"),
                ([{"column": "n", "row": {"cohort": "site9"}, "mark": "a"}], "matches 0 rows")):
            with self.subTest(message=message):
                data["marks"] = marks
                errors = table.build("results", data).errors
                self.assertTrue(errors and message in errors[0]["message"], errors)


class LabelTests(Restore):
    def test_text_cells_name_the_map_entry_that_renames_them(self):
        model = table.render_model(table.build("results"))
        cells = {c["id"]: c for c in model["body"][1]["cells"]}
        self.assertEqual(cells["cohort"]["label"],
                         {"field": "cohort", "value": "site1", "text": "Site 1", "shared": False})
        self.assertEqual(cells["method"]["label"], {"field": "method", "value": "logistic",
                                                    "text": "Logistic regression", "shared": True})
        self.assertNotIn("label", cells["n"])  # a number from the data
        self.assertNotIn("label", cells["metric.auc.est"])
        self.assertEqual(model["body"][0]["label"], {"field": "validation", "value": "internal",
                                                     "text": "Internal validation", "shared": False})
        pivot = next(c for c in model["header"][0] if c["kind"] == "pivot-value")
        self.assertEqual(pivot["label"], {"field": "metric", "value": "auc", "text": "AUC",
                                          "shared": False})

    def test_a_single_column_pivot_header_is_its_value_label(self):
        data = spec("results")
        data["columns"][3]["columns"].pop()  # metric: one column per value
        head = table.render_model(table.build("results", data))["header"][0]
        auc = next(c for c in head if c["id"] == "metric.auc.est")
        self.assertEqual(auc["label"], {"field": "metric", "value": "auc", "text": "AUC", "shared": False})

    def test_an_unmapped_value_is_edited_as_its_plain_text(self):
        data = spec("results")
        del data["maps"]["cohort"]
        cell = table.render_model(table.build("results", data))["body"][1]["cells"][0]
        self.assertEqual(cell["label"], {"field": "cohort", "value": "site1", "text": "site1",
                                         "shared": False})


if __name__ == "__main__":
    unittest.main()
