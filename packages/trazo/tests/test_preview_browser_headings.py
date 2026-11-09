"""Headings and Cmd+Z outside the text, in Chrome: real clicks and keys against the real page and server."""

import unittest

from _preview_writing import WritingCase

META = 4  # the modifier bit of Cmd in Input.dispatchKeyEvent


class HeadingTests(WritingCase):
    def saved(self, text):
        self.wait_for("document.querySelector('.pv-save-state').textContent === 'Saved' && !saves")
        self.assertEqual(self.source(), text)

    def write_at_end_of_heading(self, words):
        self.write_in(words)
        self.evaluate("""(() => {
          const heading = document.querySelector('.pv-writing');
          getSelection().selectAllChildren(heading);
          getSelection().collapseToEnd();
        })()""")

    def test_a_heading_takes_new_text_and_keeps_its_id(self):
        self.build("# Old title {#sec:old}\n\nText here.\n")
        self.write_at_end_of_heading("Old title")
        self.type(" again")
        self.leave()
        self.saved("# Old title again {#sec:old}\n\nText here.\n")

    def test_enter_in_a_heading_goes_on_to_the_text(self):
        self.build("# Old title\n\nText here.\n")
        self.write_at_end_of_heading("Old title")
        self.type(" again")
        self.key("Enter", 13)
        self.wait_for("document.querySelector('.pv-writing')?.textContent.includes('Text here.')")
        self.saved("# Old title again\n\nText here.\n")

    def test_enter_in_a_heading_passes_over_the_heading_after_it(self):
        self.build("# Outer title\n\n## Inner title\n\nText here.\n")
        self.write_at_end_of_heading("Outer title")
        self.key("Enter", 13)
        self.wait_for("document.querySelector('.pv-writing')?.textContent.includes('Text here.')")

    def test_a_heading_left_empty_is_refused(self):
        text = "# Old title\n\nText here.\n"
        self.build(text)
        self.write_in("Old title")
        self.evaluate("getSelection().selectAllChildren(document.querySelector('.pv-writing'))")
        self.key("Backspace", 8)
        self.leave()
        self.wait_for("document.querySelector('.pv-write-bar')?.textContent.includes('heading')")
        self.assertEqual(self.source(), text)

    def test_renaming_a_heading_warns_of_a_link_to_its_old_name(self):
        self.build("# Old title\n\nSee [above](#old-title) here.\n")
        self.write_at_end_of_heading("Old title")
        self.type(" again")
        self.leave()
        self.wait_for("document.querySelector('.pv-toast')?.textContent.includes('link')")

    def test_a_heading_with_its_own_id_has_no_warning(self):
        self.build("# Old title {#sec:old}\n\nSee [above](#sec:old) here.\n")
        self.write_at_end_of_heading("Old title")
        self.type(" again")
        self.leave()
        self.saved("# Old title again {#sec:old}\n\nSee [above](#sec:old) here.\n")
        self.assertFalse(self.evaluate("!!document.querySelector('.pv-toast')"))


class UndoTests(WritingCase):
    def undo(self):
        for kind in ("keyDown", "keyUp"):
            self.chrome.call("Input.dispatchKeyEvent", {"type": kind, "key": "z", "code": "KeyZ", "modifiers": META,
                                                        "windowsVirtualKeyCode": 90}, self.session)

    def test_cmd_z_outside_the_text_undoes_the_saved_paragraph(self):
        self.build("Plain words.\n")
        self.write_in("words")
        self.type("x")
        self.leave()
        self.wait_for("document.querySelector('.pv-save-state').textContent === 'Saved' && !saves")
        self.assertNotEqual(self.source(), "Plain words.\n")
        self.undo()
        self.wait_for("document.querySelector('.pv-toast')?.textContent.startsWith('Undone')")
        self.assertEqual(self.source(), "Plain words.\n")

    def test_cmd_z_is_refused_with_a_notice_if_the_file_changed(self):
        self.build("Plain words.\n")
        self.write_in("words")
        self.type("x")
        self.leave()
        self.wait_for("document.querySelector('.pv-save-state').textContent === 'Saved' && !saves")
        self.chapter.write_text("Other words.\n", encoding="utf-8")
        self.undo()
        self.wait_for("document.querySelector('.pv-toast')?.textContent.startsWith('Not undone')")
        self.assertEqual(self.source(), "Other words.\n")

    def test_cmd_z_takes_a_comment_made_after_the_save(self):
        self.build("Plain words.\n")
        self.write_in("words")
        self.type("x")
        self.leave()
        self.wait_for("document.querySelector('.pv-save-state').textContent === 'Saved' && !saves")
        saved = self.source()
        self.evaluate("""fetch('/__comments', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ op: 'add', doc: 'manuscript', quote: 'Plain', para: 'x', section: '', comment: 'Check this' }) }).then(() => loadComments())""")
        self.wait_for("undoLabel")
        self.undo()
        self.wait_for("document.querySelector('.pv-toast')?.textContent.startsWith('Undone')")
        self.assertEqual(self.source(), saved)


if __name__ == "__main__":
    unittest.main()
