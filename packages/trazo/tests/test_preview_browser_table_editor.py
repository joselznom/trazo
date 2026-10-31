"""Viewer regressions in Chrome: the table editor."""

import json
import unittest

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)
from _preview_browser import PreviewBrowserTestCase


class TableEditorTests(PreviewBrowserTestCase):
    def test_table_editor_saves_a_header_and_undoes_it(self):
        from trazo import table
        spec = table.spec_path("results")
        before = spec.read_bytes()
        self.addCleanup(table.HISTORY_PATH.unlink, missing_ok=True)
        self.addCleanup(table.output_path("results").unlink, missing_ok=True)
        self.addCleanup(spec.write_bytes, before)
        self.open_table()
        self.edit_header("Method")
        self.wait_for("document.querySelector('#te-undo').disabled === false")
        self.assertIn('"header": "Method"', spec.read_text())
        self.assertIn("| Method", table.output_path("results").read_text())
        self.evaluate("document.body.dispatchEvent(new KeyboardEvent('keydown', "
                      "{key: 'z', metaKey: true, bubbles: true}))")
        self.wait_for("document.querySelector('#te-undo').disabled === true")
        self.assertEqual(spec.read_bytes(), before)
        self.assertIn("| Model", table.output_path("results").read_text())

    def test_editors_show_their_first_view_without_fading_and_fade_a_switch(self):
        self.chrome.call("Page.enable", None, self.session)
        self.chrome.call("Page.addScriptToEvaluateOnNewDocument", {"source": """
          window.fades = 0;
          const animate = Element.prototype.animate;
          Element.prototype.animate = function (...args) { window.fades += 1; return animate.apply(this, args); };"""},
            self.session)
        self.open_table()
        self.evaluate("new Promise(resolve => setTimeout(resolve, 300))")
        self.assertEqual(self.evaluate("window.fades"), 0)
        self.evaluate("location.hash = 'cohorts'")
        self.wait_for("window.fades === 1")
        self.chrome.call("Page.navigate", {"url": self.url.replace("/manuscript.html", "/diagrams#example")}, self.session)
        self.wait_for("document.readyState === 'complete' && document.querySelector('#docname').textContent === 'example'")
        self.evaluate("new Promise(resolve => setTimeout(resolve, 300))")
        self.assertEqual(self.evaluate("window.fades"), 0)

    def edit_header(self, text):
        self.evaluate(f"""(() => {{
          document.querySelector('thead th[data-id="method"]').click();
          const field = document.querySelector('input[aria-label="Header"]');
          field.value = {json.dumps(text)};
          field.dispatchEvent(new Event('input', {{bubbles: true}}));
          field.blur();
        }})()""")

    def test_table_editor_structural_edits_keep_emphasis_valid(self):
        from trazo import table
        path = table.spec_path("cohorts")
        self.addCleanup(path.write_bytes, path.read_bytes())
        self.addCleanup(table.HISTORY_PATH.unlink, missing_ok=True)
        self.addCleanup(table.output_path("cohorts").unlink, missing_ok=True)
        data = json.loads(path.read_text())
        data["highlight"] = [{"column": "patients", "best": "max"}]
        data["emphasis"] = [{"column": "patients", "row": {"cohort": "test"}}]
        path.write_text(json.dumps(data))
        self.chrome.call("Page.navigate", {"url": self.url.replace("/manuscript.html", "/tables#cohorts")}, self.session)
        self.wait_for("document.querySelector('thead th[data-id=\"patients\"]')")
        self.evaluate('''document.querySelector('thead th[data-id="patients"]').click();
          Array.from(document.querySelectorAll('#te-panel button')).find(b => b.textContent === 'Group with next').click();''')
        self.wait_for("document.querySelector('#te-save').classList.contains('err') || document.querySelector('thead th[data-id=\"group.patients\"]')")
        self.assertTrue(self.evaluate("!!document.querySelector('thead th[data-id=\"group.patients\"]')"),
                        self.evaluate("document.querySelector('#te-save').textContent"))
        grouped = json.loads(path.read_text())
        self.assertEqual(grouped["highlight"][0]["column"], "group.patients")
        self.assertEqual(grouped["emphasis"][0]["column"], "group.patients")
        self.evaluate('''document.querySelector('thead th[data-id="group.patients"]').click();
          Array.from(document.querySelectorAll('#te-panel button')).find(b => b.textContent === 'Ungroup').click();''')
        self.wait_for("document.querySelector('#te-save').classList.contains('err') || document.querySelector('thead th[data-id=\"patients\"]')")
        self.assertTrue(self.evaluate("!!document.querySelector('thead th[data-id=\"patients\"]')"))
        self.assertEqual(json.loads(path.read_text()), data)
        self.evaluate('''document.querySelector('thead th[data-id="patients"]').click();
          Array.from(document.querySelectorAll('#te-panel button')).find(b => b.textContent === 'Delete column').click();''')
        self.wait_for("document.querySelector('#te-save').classList.contains('err') || !document.querySelector('thead th[data-id=\"patients\"]')")
        self.assertFalse(self.evaluate("!!document.querySelector('thead th[data-id=\"patients\"]')"),
                         self.evaluate("document.querySelector('#te-save').textContent"))
        saved = json.loads(path.read_text())
        self.assertFalse(saved.get("highlight"))
        self.assertFalse(saved.get("emphasis"))

    def test_table_editor_refreshes_external_reverts_and_data(self):
        from trazo import table
        for kind in ("spec", "data"):
            with self.subTest(kind=kind):
                path = table.spec_path("results") if kind == "spec" else table.data_path("results")
                before = path.read_bytes()
                self.addCleanup(path.write_bytes, before)
                self.open_table()
                data = json.loads(before)
                if kind == "spec":
                    data["columns"][2]["header"] = "External header"
                    selector, changed = 'thead th[data-id="method"]', "External header"
                else:
                    data["records"][0]["value"]["est"] = 0.123
                    selector, changed = "#te-page", "0.123"
                path.write_text(json.dumps(data))
                self.wait_for(f"document.querySelector({json.dumps(selector)}).textContent.includes({json.dumps(changed)})")
                path.write_bytes(before)
                self.wait_for(f"!document.querySelector({json.dumps(selector)}).textContent.includes({json.dumps(changed)})")

    def test_table_editor_retries_a_failed_first_load(self):
        self.fail_fetches("/__tables", "/__table?")
        self.chrome.call("Page.navigate", {"url": self.url.replace("/manuscript.html", "/tables#results")},
                         self.session)
        self.wait_for("window.failed >= 2")
        self.evaluate("window.serverUp = true")
        self.wait_for("document.querySelector('thead th[data-id=\"method\"]')")
        self.wait_for("Array.from(document.querySelectorAll('#te-list .te-lbl'), s => s.textContent).includes('results')")

    def test_table_editor_can_save_after_a_network_failure(self):
        from trazo import table
        path = table.spec_path("results")
        self.addCleanup(path.write_bytes, path.read_bytes())
        self.addCleanup(table.HISTORY_PATH.unlink, missing_ok=True)
        self.addCleanup(table.output_path("results").unlink, missing_ok=True)
        self.open_table()
        self.evaluate("""(() => {
          const fetch = window.fetch;
          window.fetch = (url, options) => {
            if (url === '/__table' && options?.method === 'POST') {
              window.fetch = fetch;
              return Promise.reject(new TypeError('Simulated network failure'));
            }
            return fetch(url, options);
          };
        })()""")
        self.edit_header("First edit")
        self.wait_for("document.querySelector('#te-save').classList.contains('err')")
        self.edit_header("Recovered edit")
        self.wait_for("document.querySelector('#te-save').textContent.startsWith('Saved')")
        self.assertEqual(json.loads(path.read_text())["columns"][2]["header"], "Recovered edit")

    def test_external_table_refresh_cannot_replace_a_later_caption_edit(self):
        from trazo import table
        path, data_path = table.spec_path("results"), table.data_path("results")
        self.addCleanup(path.write_bytes, path.read_bytes())
        self.addCleanup(data_path.write_bytes, data_path.read_bytes())
        self.addCleanup(table.HISTORY_PATH.unlink, missing_ok=True)
        self.addCleanup(table.output_path("results").unlink, missing_ok=True)
        self.open_table()
        self.evaluate("document.querySelector('.te-cap').click()")
        self.evaluate('''(() => {
          const real = window.fetch;
          window.fetch = async (url, options) => {
            const response = await real(url, options);
            if (String(url).startsWith('/__table?') && window.holdTable) {
              window.heldTable = true;
              await new Promise(resolve => { window.releaseTable = resolve; });
            }
            return response;
          };
          window.holdTable = true;
        })()''')
        data = json.loads(data_path.read_text())
        data["records"][0]["value"]["est"] = 0.456
        data_path.write_text(json.dumps(data))
        self.wait_for("window.heldTable === true")
        self.evaluate('''(() => {
          for (const [name, value] of [['Title', 'Shortened title'], ['Legend', '']]) {
            const field = document.querySelector(`[aria-label="${name}"]`);
            field.focus(); field.value = value;
            field.dispatchEvent(new Event('input', {bubbles:true}));
          }
          document.activeElement.blur();
        })()''')
        self.wait_for("document.querySelector('#te-save').textContent.startsWith('Saved')")
        self.evaluate("window.holdTable = false; window.releaseTable(); window.refreshReleased = true")
        self.evaluate("new Promise(resolve => setTimeout(resolve, 100))")
        self.assertEqual(self.evaluate("document.querySelector('[aria-label=Title]').value"), "Shortened title")
        self.assertEqual(self.evaluate("document.querySelector('[aria-label=Legend]').value"), "")
        table.render_all()
        self.open_table()
        self.evaluate("document.querySelector('.te-cap').click()")
        self.assertEqual(self.evaluate("document.querySelector('[aria-label=Title]').value"), "Shortened title")
        self.assertEqual(self.evaluate("document.querySelector('[aria-label=Legend]').value"), "")


if __name__ == "__main__":
    unittest.main()
