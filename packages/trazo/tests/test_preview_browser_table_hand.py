"""Viewer regressions in Chrome: tables typed by hand in the table editor."""

import json
import unittest

import _support
from _preview_browser import PreviewBrowserTestCase

from trazo import preview

SPEC = {"schema_version": 1, "title": "Included studies.",
        "columns": [{"id": "study", "header": "Study"}, {"id": "patients", "header": "Patients"}],
        "body": [{"id": "b1", "block": "Europe"},
                 {"id": "r1", "cells": {"study": "Smith 2020", "patients": "120"}},
                 {"id": "r2", "cells": {"study": "Lee 2019"}}]}


class HandTableTests(PreviewBrowserTestCase):
    def open_hand(self):
        from trazo import table
        path = table.spec_path("studies")
        self.keep_files(path, table.HISTORY_PATH, table.output_path("studies"))
        path.write_text(json.dumps(SPEC), encoding="utf-8")
        self.chrome.call("Page.navigate", {"url": self.url.replace("/manuscript.html", "/tables#studies")}, self.session)
        self.wait_for("document.querySelector('td[data-row]')")
        self.table_settled()
        return path

    def saved(self, path):
        self.wait_for("document.querySelector('#te-save').textContent.startsWith('Saved')")
        return json.loads(path.read_text(encoding="utf-8"))

    def type_enter(self, text):
        self.evaluate(f"""(() => {{
          const input = document.querySelector('.te-cellin');
          input.value = {json.dumps(text)};
          input.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Enter', bubbles: true}}));
        }})()""")

    def press(self, label):
        self.evaluate(f"Array.from(document.querySelectorAll('#te-bar button')).find(b => b.textContent === {json.dumps(label)} || b.title === {json.dumps(label)}).click()")

    def test_a_cell_is_typed_on_the_table_and_saved_in_the_spec(self):
        path = self.open_hand()
        self.assertEqual(self.evaluate("document.querySelector('#te-kindb').textContent"), "Typed by hand")
        self.double_click("#te-page td", "Smith 2020")
        self.wait_for("document.querySelector('.te-cellin')")
        self.type_enter("Smith 2021")
        self.wait_for("document.querySelector('#te-page').textContent.includes('Smith 2021')")
        self.assertEqual(self.saved(path)["body"][1]["cells"]["study"], "Smith 2021")

    def test_an_empty_cell_can_be_typed(self):
        path = self.open_hand()
        self.evaluate("""(() => {
          const td = document.querySelector('td[data-id="patients"][data-row="1"]');
          td.click();
        })()""")
        self.evaluate("document.querySelector('[aria-label=\"Cell text\"]').value = '86'")
        self.evaluate("document.querySelector('[aria-label=\"Cell text\"]').dispatchEvent(new Event('change', {bubbles: true}))")
        self.wait_for("document.querySelector('td[data-id=\"patients\"][data-row=\"1\"]').textContent === '86'")
        self.assertEqual(self.saved(path)["body"][2]["cells"]["patients"], "86")

    def test_formatting_wraps_the_selected_cell(self):
        path = self.open_hand()
        self.evaluate("document.querySelector('td[data-id=\"study\"][data-row=\"0\"]').click()")
        self.press("Italic")
        self.wait_for("document.querySelector('td[data-id=\"study\"][data-row=\"0\"] em')")
        self.assertEqual(self.saved(path)["body"][1]["cells"]["study"], "*Smith 2020*")

    def test_rows_and_columns_are_added_moved_and_deleted(self):
        path = self.open_hand()
        self.evaluate("document.querySelector('td[data-id=\"study\"][data-row=\"0\"]').click()")
        self.press("+ Row")
        self.wait_for("document.querySelectorAll('tbody td[data-id=\"study\"]').length === 3")
        ids = [item["id"] for item in self.saved(path)["body"]]
        self.assertEqual(ids[:2] + ids[3:], ["b1", "r1", "r2"])
        self.press("Delete row")
        self.wait_for("document.querySelectorAll('tbody td[data-id=\"study\"]').length === 2")
        self.evaluate("document.querySelector('td[data-id=\"study\"][data-row=\"1\"]').click()")
        self.press("Move the row up")
        self.wait_for("document.querySelector('td[data-id=\"study\"][data-row=\"0\"]').textContent === 'Lee 2019'")
        self.press("+ Column")
        self.wait_for("document.querySelectorAll('thead th').length === 3")
        self.assertEqual(len(self.saved(path)["columns"]), 3)

    def test_a_range_is_pasted_from_the_selected_cell(self):
        path = self.open_hand()
        self.evaluate("document.querySelector('td[data-id=\"patients\"][data-row=\"1\"]').click()")
        self.evaluate("""(() => {
          const data = new DataTransfer();
          data.setData('text/plain', '86\\tRCT\\r\\n40\\tCohort\\r\\n');
          document.body.dispatchEvent(new ClipboardEvent('paste', {clipboardData: data, bubbles: true, cancelable: true}));
        })()""")
        self.wait_for("document.querySelectorAll('tbody td[data-id=\"patients\"]').length === 3")
        rows = [item["cells"] for item in self.saved(path)["body"] if "cells" in item]
        self.assertEqual(rows[1]["patients"], "86")
        self.assertEqual(rows[2]["patients"], "40")
        self.assertIn("Cohort", rows[2].values())

    def test_a_quoted_spreadsheet_cell_stays_one_cell(self):
        path = self.open_hand()
        self.evaluate("document.querySelector('td[data-id=\"patients\"][data-row=\"0\"]').click()")
        self.evaluate(r"""(() => {
          const data = new DataTransfer();
          data.setData('text/plain', '"First ""a""\nSecond"\t"plain"\r\n');
          document.body.dispatchEvent(new ClipboardEvent('paste', {clipboardData: data, bubbles: true, cancelable: true}));
        })()""")
        self.wait_for("document.querySelectorAll('thead th').length === 3")
        cells = [item["cells"] for item in self.saved(path)["body"] if "cells" in item]
        self.assertEqual(len(cells), 2)
        self.assertEqual(cells[0]["patients"], 'First "a" Second')
        self.assertIn('"plain"', cells[0].values())

    def test_a_moved_row_stays_selected(self):
        path = self.open_hand()
        self.evaluate("document.querySelector('td[data-id=\"study\"][data-row=\"1\"]').click()")
        self.press("Move the row up")
        self.wait_for("document.querySelector('td[data-id=\"study\"][data-row=\"0\"]').textContent === 'Lee 2019'")
        self.wait_for("document.querySelector('.te-cellsel') && document.querySelector('.te-cellsel').textContent === 'Lee 2019'")
        self.press("Move the row up")
        self.wait_for("[...document.querySelectorAll('tbody td')][0].textContent === 'Lee 2019'")
        self.assertEqual([item["id"] for item in self.saved(path)["body"]], ["r2", "b1", "r1"])

    def test_bold_and_italic_combine_and_come_off_one_at_a_time(self):
        path = self.open_hand()
        self.evaluate("document.querySelector('td[data-id=\"study\"][data-row=\"0\"]').click()")
        cell = "document.querySelector('td[data-id=\"study\"][data-row=\"0\"]')"
        self.press("Bold")
        self.wait_for(f"{cell}.querySelector('strong')")
        self.press("Italic")
        self.wait_for(f"{cell}.querySelector('em')")
        self.assertEqual(self.saved(path)["body"][1]["cells"]["study"], "***Smith 2020***")
        self.press("Bold")
        self.wait_for(f"!{cell}.querySelector('strong')")
        self.assertEqual(self.saved(path)["body"][1]["cells"]["study"], "*Smith 2020*")

    def test_a_cell_with_other_formatting_is_left_as_it_is(self):
        from trazo import table
        path = self.open_hand()
        for text in ("**some *word* here**", "*italic* bold"):
            with self.subTest(text=text):
                table.patch("studies", [{"op": "cell", "row": "r1", "column": "study", "text": text}])
                self.wait_for("document.querySelector('td[data-id=\"study\"][data-row=\"0\"] em')")
                self.evaluate("document.querySelector('td[data-id=\"study\"][data-row=\"0\"]').click()")
                self.press("Bold")
                self.wait_for("document.querySelector('.te-flash').textContent.includes('other formatting')")
                self.assertEqual(self.saved(path)["body"][1]["cells"]["study"], text)

    def test_new_table_is_made_and_placed_in_a_section(self):
        from trazo import table
        doc = _support.ROOT / "manuscript/main/01_introduction.md"
        made = table.spec_path("my-new-table")
        self.keep_files(doc, made, table.output_path("my-new-table"))
        self.open_hand()
        self.evaluate("document.querySelector('#te-new').click()")
        self.wait_for("document.querySelector('.te-dialog select[aria-label=Section]')")
        self.evaluate("""(() => {
          const title = document.querySelector('[aria-label="Table title"]');
          title.value = 'My new table';
          Array.from(document.querySelectorAll('.te-dialog button')).find(b => b.textContent === 'Create table').click();
        })()""")
        self.wait_for("location.hash === '#my-new-table' && document.querySelector('td[data-row]')")
        self.assertTrue(made.is_file())
        self.assertIn("!include tables/output/md/my-new-table.md", doc.read_text(encoding="utf-8"))


class MakeEditableTests(PreviewBrowserTestCase):
    MARKDOWN = ("# One\n\n| Outcome | n |\n|---|---:|\n| Response | 12 |\n\n"
                "Table: Outcomes. {#tbl:outcomes}\n")

    def open_paper(self, text):
        from trazo import table
        doc = _support.ROOT / "manuscript/main/01_introduction.md"
        made = table.spec_path("outcomes")
        self.keep_files(doc, made, table.output_path("outcomes"), table.HISTORY_PATH)
        doc.write_bytes(text.encode("utf-8"))  # "\n" on Windows too: captions are found by line
        page = preview.OUT_DIR / "manuscript.html"
        page.write_text(page.read_text().replace('</section><script>',
            '<table id="tbl:outcomes"><caption>Table 1. Outcomes.</caption><tbody><tr><td>1</td></tr></tbody></table>'
            '</section><script>'))
        self.open()
        self.wait_for("Array.from(document.querySelectorAll('.pv-tbl-edit')).some(b => b.textContent === 'Make editable')")
        return doc, made

    def press_make_editable(self):
        self.evaluate("Array.from(document.querySelectorAll('.pv-tbl-edit')).find(b => b.textContent === 'Make editable').click()")
        self.wait_for("document.querySelector('.pk-confirm')")

    def test_a_table_is_made_editable_and_can_be_undone(self):
        doc, made = self.open_paper(self.MARKDOWN)
        self.press_make_editable()
        self.evaluate("Array.from(document.querySelectorAll('.pk-confirm button')).find(b => b.textContent === 'Make editable').click()")
        self.wait_for("location.pathname === '/tables' && document.querySelector('td[data-row]')")
        self.assertEqual(self.evaluate("document.querySelector('#te-kindb').textContent"), "Typed by hand")
        self.assertIn("!include", doc.read_text(encoding="utf-8"))
        self.wait_for("!document.querySelector('#te-banner').hidden")
        self.evaluate("Array.from(document.querySelectorAll('#te-banner button')).find(b => b.textContent === 'Undo').click()")
        self.wait_for("location.pathname !== '/tables'")
        self.assertEqual(doc.read_text(encoding="utf-8"), self.MARKDOWN)
        self.assertFalse(made.exists())

    def test_a_table_that_cannot_be_converted_says_why(self):
        self.open_paper(self.MARKDOWN.replace("12", "`code`"))
        self.press_make_editable()
        self.wait_for("document.querySelector('.pk-confirm').textContent.includes('a cell has code')")
        self.assertIn("Edit source", self.evaluate("document.querySelector('.pk-confirm').textContent"))


if __name__ == "__main__":
    unittest.main()
