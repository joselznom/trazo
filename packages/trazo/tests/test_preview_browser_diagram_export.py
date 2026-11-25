"""Viewer regressions in Chrome: the diagram editor exports what it saves."""

import json
import unittest

import _support
from _preview_browser import PreviewBrowserTestCase

from trazo import diagram


class DiagramExportTests(PreviewBrowserTestCase):
    def open_diagram(self, data=None):
        from trazo import diagram_editor
        self.addCleanup(diagram_editor.shutdown)
        self.path = _support.ROOT / "figures/diagrams/example.json"
        self.keep_files(self.path, *diagram.export_paths("example"))
        if data is not None:
            self.path.write_text(json.dumps(data))
        self.chrome.call("Page.navigate", {"url": self.url.replace("/manuscript.html", "/diagrams#example")}, self.session)
        self.wait_for("document.querySelector('#lint .source-note')?.textContent.includes('Checked on disk')")
        self.evaluate("""window.toasts = [];
            new MutationObserver(() => toasts.push(document.querySelector('#toast').textContent))
              .observe(document.querySelector('#toast'), {childList: true, subtree: true});""")

    def edit_text(self, text):
        self.evaluate(f"DiagramEditor.commit('text', d => {{ d.nodes[0].text = {json.dumps(text)}; }})")
        self.wait_for("!DiagramEditor.state.saving && JSON.stringify(DiagramEditor.state.diagram) === DiagramEditor.state.disk.json")
        self.assertIn(text, self.path.read_text())

    def test_a_save_exports_the_figure_quietly(self):
        self.open_diagram()
        self.edit_text("Exported after the save")
        self.wait_for("DiagramEditor.state.exporting")
        self.wait_for("!DiagramEditor.state.exporting")
        self.wait_for("document.querySelector('#export-state').textContent === 'Export current'")
        self.assertEqual(diagram.export_state(self.path), "current")
        self.assertIn("Exported after the save", (diagram.EXTERNAL_DIR / "example.svg").read_text())
        self.assertFalse(self.evaluate("toasts.some(t => t.includes('Exported'))"))

    def test_a_draft_is_not_exported(self):
        data = json.loads((_support.ROOT / "figures/diagrams/example.json").read_text())
        data["meta"] = {**data.get("meta", {}), "draft": True}
        self.open_diagram(data)
        self.edit_text("A draft stays unexported")
        self.evaluate("new Promise(resolve => setTimeout(resolve, 2500))")
        self.assertFalse(self.evaluate("DiagramEditor.state.exporting"))
        self.assertFalse(any(path.exists() for path in diagram.export_paths("example")))


if __name__ == "__main__":
    unittest.main()
