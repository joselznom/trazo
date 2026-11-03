"""Viewer regressions in Chrome: editing on the table in the table editor."""

import json
import shutil
import unittest

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)
from _preview_browser import PreviewBrowserTestCase


class TableInlineTests(PreviewBrowserTestCase):
    def keep(self, *paths):
        from trazo import table
        self.keep_files(*paths, table.HISTORY_PATH, table.output_path("results"))

    def double_click(self, selector, text):
        """A real double click: the first click redraws the table."""
        x, y = self.evaluate(f"""(() => {{
          const el = Array.from(document.querySelectorAll({json.dumps(selector)})).find(x => x.textContent.trim() === {json.dumps(text)});
          const box = el.getBoundingClientRect();
          return [box.left + box.width / 2, box.top + box.height / 2];
        }})()""")
        self.mouse_double_click(x, y)

    def mouse_double_click(self, x, y):
        for count in (1, 2):
            for kind in ("mousePressed", "mouseReleased"):
                self.chrome.call("Input.dispatchMouseEvent", {
                    "type": kind, "x": x, "y": y, "button": "left", "clickCount": count,
                    "buttons": 0 if kind == "mouseReleased" else 1}, self.session)

    def rename(self, old, new, selector="#te-page td"):
        self.double_click(selector, old)
        self.wait_for("document.querySelector('.te-cellin')")
        self.evaluate(f"""(() => {{
          const input = document.querySelector('.te-cellin');
          input.value = {json.dumps(new)};
          input.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Enter', bubbles: true}}));
        }})()""")

    def test_a_label_renamed_on_the_table_is_saved_in_the_spec(self):
        from trazo import table
        self.keep(table.spec_path("results"))
        self.open_table()
        self.assertNotIn("null", self.evaluate("document.querySelector('#te-page').innerText"))
        self.rename("Site 1", "First site")
        self.wait_for("document.querySelector('#te-save').textContent.startsWith('Saved') && !document.querySelector('.te-cellin')"
                      " && document.querySelector('#te-page').textContent.includes('First site')")
        self.assertEqual(json.loads(table.spec_path("results").read_text())["maps"]["cohort"]["site1"], "First site")
        self.assertIn("First site", table.output_path("results").read_text())

    def test_a_double_click_inside_the_field_keeps_the_text_being_typed(self):
        from trazo import table
        self.keep(table.spec_path("results"))
        self.open_table()
        self.double_click("#te-page td", "Site 1")
        self.wait_for("document.querySelector('.te-cellin')")
        self.evaluate("document.querySelector('.te-cellin').value = 'First site revised'")
        x, y = self.evaluate("""(() => {
          const box = document.querySelector('.te-cellin').getBoundingClientRect();
          return [box.left + box.width / 2, box.top + box.height / 2];
        })()""")
        self.mouse_double_click(x, y)
        self.assertEqual(self.evaluate("document.querySelector('.te-cellin').value"), "First site revised")
        self.evaluate("document.querySelector('.te-cellin').dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))")

    def test_a_block_label_stays_selected_with_its_new_text_after_a_rename(self):
        from trazo import table
        self.keep(table.spec_path("results"))
        self.open_table()
        self.rename("Internal validation", "Inside", "#te-page td.te-block")
        self.wait_for("document.querySelector('td.te-block').textContent === 'Inside'")
        self.wait_for("document.querySelector('#te-save').textContent.startsWith('Saved')")
        self.assertEqual(self.evaluate("document.querySelector('td.te-block.te-cellsel').textContent"), "Inside")
        self.assertEqual(self.evaluate("document.querySelector('[aria-label=Label]').value"), "Inside")

    def test_two_quick_renames_of_one_field_keep_both(self):
        from trazo import table
        self.keep(table.spec_path("results"))
        self.open_table()
        self.evaluate("""(() => {
          const real = window.fetch;
          window.fetch = async (url, options) => {
            if (url === '/__table' && options?.method === 'POST' && !window.held) {
              window.held = true;
              await new Promise(resolve => { window.release = resolve; });
            }
            return real(url, options);
          };
        })()""")
        self.rename("Site 1", "First site")
        self.wait_for("window.held === true")
        self.rename("Site 2", "Second site")
        self.evaluate("window.release()")
        self.wait_for("document.querySelector('#te-page').textContent.includes('Second site')")
        self.wait_for("document.querySelector('#te-save').textContent.startsWith('Saved')")
        maps = json.loads(table.spec_path("results").read_text())["maps"]["cohort"]
        self.assertEqual((maps["site1"], maps["site2"]), ("First site", "Second site"))

    def test_a_shared_label_asks_this_table_only_or_everywhere(self):
        from trazo import table
        copy = table.spec_path("results2")
        self.keep(table.spec_path("results"), table.SHARED_PATH, copy, table.output_path("results2"))
        shutil.copy(table.spec_path("results"), copy)
        self.open_table()
        self.rename("Logistic regression", "Logistic")
        self.wait_for("document.querySelector('.te-pop')")
        self.assertIn("also results2", self.evaluate("document.querySelector('.te-pop').innerText"))
        self.evaluate("Array.from(document.querySelectorAll('.te-pop button')).find(b => b.textContent === 'Rename').click()")
        self.wait_for("document.querySelector('#te-page').textContent.includes('Logistic') && "
                      "!document.querySelector('#te-page').textContent.includes('Logistic regression')")
        self.assertEqual(json.loads(table.spec_path("results").read_text())["maps"]["method"],
                         {"logistic": "Logistic"})
        self.assertEqual(json.loads(table.SHARED_PATH.read_text())["maps"]["method"]["logistic"],
                         "Logistic regression")

    def test_a_mark_is_added_from_the_panel_and_a_number_stays_locked(self):
        from trazo import table
        self.keep(table.spec_path("results"))
        self.open_table()
        self.double_click("#te-page td", "0.812")
        self.wait_for("document.querySelector('#te-panel').innerText.toLowerCase().includes('from your data')")
        self.assertFalse(self.evaluate("!!document.querySelector('.te-cellin')"))
        self.assertIn("from your data", self.evaluate("document.querySelector('#te-panel').innerText").lower())
        self.evaluate("Array.from(document.querySelectorAll('.te-mchip')).find(b => b.textContent === 'a').click()")
        self.wait_for("Array.from(document.querySelectorAll('#te-page td sup')).some(s => s.textContent === 'a')")
        marks = json.loads(table.spec_path("results").read_text())["marks"]
        self.assertEqual(marks, [{"column": "metric.auc.est", "mark": "a", "row": {
            "validation": "internal", "cohort": "site1", "method": "logistic"}}])
        self.assertIn("0.812^a^", table.output_path("results").read_text())


if __name__ == "__main__":
    unittest.main()
