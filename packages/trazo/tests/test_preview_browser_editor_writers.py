"""The table and diagram editors in Chrome tell the author where text that was not saved went."""

import json
import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)
from _preview_browser import PreviewBrowserTestCase

from trazo import diagram, portable, preview, review_history, table

MARKER = "spec-key-another-program-wrote"


class EditorWriterNoticeTests(PreviewBrowserTestCase):
    def setUp(self):
        super().setUp()
        self.spec, self.diagram = table.spec_path("results"), diagram.diagram_path("example")
        self.keep_files(self.spec, self.diagram, table.HISTORY_PATH, diagram.HISTORY_PATH, table.output_path("results"))
        for kept in ("conflicts", "displaced"):
            self.addCleanup(shutil.rmtree, preview.REVIEW_DIR / kept, True)
        for folder in (self.spec.parent, self.diagram.parent):
            self.addCleanup(lambda f=folder: [p.unlink() for p in f.glob("*.swap")])

    def another_program_writes(self, path):
        """Once, inside the next save: after its undo history, before its publication."""
        save, done = review_history.History.save, []

        def history_then_write(history, entries):
            save(history, entries)
            if not done:
                done.append(True)
                path.write_text(path.read_text().replace("{", '{"' + MARKER + '": 1, ', 1))
        return patch.object(review_history.History, "save", history_then_write)

    def parking_fails(self):
        if not portable.can_exchange(self.spec.parent):
            self.skipTest("no atomic exchange on this filesystem")
        blocker = preview.REVIEW_DIR / "displaced"
        blocker.write_text("a file where the folder should be")
        self.addCleanup(blocker.unlink, missing_ok=True)

    def copies(self):
        return [p for p in (preview.REVIEW_DIR / "conflicts").rglob("*") if p.is_file()]

    def edit_header(self, text):
        self.evaluate(f"""(() => {{
          document.querySelector('thead th[data-id="method"]').click();
          const field = document.querySelector('input[aria-label="Header"]');
          field.value = {json.dumps(text)};
          field.dispatchEvent(new Event('input', {{bubbles: true}}));
          field.blur();
        }})()""")

    def edit_diagram(self, text):
        self.chrome.call("Page.navigate", {"url": self.url.replace("/manuscript.html", "/diagrams#example")}, self.session)
        self.wait_for("document.readyState === 'complete' && document.querySelector('#docname')?.textContent === 'example'")
        self.wait_for("!!DiagramEditor.state.disk?.sha")
        self.evaluate(f"""DiagramEditor.commit('typed', d => {{ d.nodes[0].text = {json.dumps(text)}; }});
            clearTimeout(DiagramEditor.state.saveTimer);""")

    def test_a_table_collision_says_where_the_edit_was_kept(self):
        self.open_table()
        with self.another_program_writes(self.spec):
            self.edit_header("Method typed in the editor")
            self.wait_for("!document.querySelector('#te-banner').hidden")
        [copy] = self.copies()
        self.assertIn("Method typed in the editor", copy.read_text())
        self.assertIn(MARKER, self.spec.read_text())
        self.assertIn(copy.relative_to(_support.ROOT).as_posix(), self.evaluate("document.querySelector('#te-banner').textContent"))

    def test_a_table_undo_collision_keeps_the_copy_path_on_screen(self):
        self.open_table()
        self.edit_header("Method")
        self.wait_for("document.querySelector('#te-undo').disabled === false")
        read, done = Path.read_bytes, []

        def read_then_write(path):  # right after the undo's locked read of the spec
            data = read(path)
            if not done and path == self.spec and sys._getframe(1).f_code.co_name == "revert_file":
                done.append(True)
                self.spec.write_text(self.spec.read_text().replace("{", '{"' + MARKER + '": 1, ', 1))
            return data
        with patch.object(Path, "read_bytes", read_then_write):
            self.evaluate("document.querySelector('#te-undo').click()")
            self.wait_for("!document.querySelector('#te-banner').hidden")
        [copy] = self.copies()
        self.assertIn(MARKER, self.spec.read_text())
        self.evaluate("new Promise(resolve => setTimeout(resolve, 3000))")  # longer than a flash lasts
        self.assertIn(copy.relative_to(_support.ROOT).as_posix(), self.evaluate("document.querySelector('#te-banner').textContent"))

    def test_a_table_save_shows_trouble_after_publishing(self):
        self.parking_fails()
        self.open_table()
        self.edit_header("Method")
        self.wait_for("document.querySelector('#te-banner').textContent.includes('could not set the replaced version aside')")
        self.assertIn('"header": "Method"', self.spec.read_text())

    def test_a_diagram_collision_says_where_the_edit_was_kept(self):
        self.edit_diagram("Typed in the editor")
        with self.another_program_writes(self.diagram):
            self.evaluate("DiagramEditor.save(false)")
            self.wait_for("!document.querySelector('#banner').hidden")
        [copy] = self.copies()
        self.assertIn("Typed in the editor", copy.read_text())
        self.evaluate("new Promise(resolve => setTimeout(resolve, 3000))")  # the page reads the file's inputs again
        self.assertIn(copy.relative_to(_support.ROOT).as_posix(), self.evaluate("document.querySelector('#banner').textContent"))

    def test_a_diagram_save_shows_trouble_after_publishing(self):
        self.parking_fails()
        self.edit_diagram("Typed in the editor")
        self.evaluate("DiagramEditor.save(false)")
        self.wait_for("document.querySelector('#banner').textContent.includes('could not set the replaced version aside')")
        self.assertIn("Typed in the editor", self.diagram.read_text())


if __name__ == "__main__":
    unittest.main()
