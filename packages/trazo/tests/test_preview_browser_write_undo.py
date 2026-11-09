"""Cmd+Z and Cmd+Shift+Z inside the text, in Chrome: real keys against the real page and server.

Enter, Backspace at the start and a style save at once, so the browser's own
undo has nothing of theirs to take back; the viewer's history does. Steps whose
saves are still on their way: test_preview_browser_write_undo_waiting.py.
"""

import time
import unittest

from _preview_undo import PARAGRAPHS, TEXT, UndoCase

from trazo import preview


class WriteUndoTests(UndoCase):
    def broken(self):
        self.build(TEXT)
        self.write_in("sentence")
        self.caret_after("One sentence.")
        self.enter()
        self.saved_now()
        self.assertEqual(self.source(), "One sentence.\n\nTwo sentences here.\n\nNext paragraph.\n")

    def test_cmd_z_right_after_enter_joins_the_paragraph_again_on_the_page_and_in_the_file(self):
        self.broken()
        self.undo()
        self.wait_for(f"{PARAGRAPHS}.includes('One sentence. Two sentences here.')")
        self.settled()
        self.assertEqual(self.source(), TEXT)
        self.assertTrue(self.evaluate("document.activeElement.classList.contains('pv-writing')"))
        # The caret is back where Enter was pressed.
        self.type("X")
        self.assertIn("One sentence.X Two sentences here.", self.paragraphs())

    def test_typing_after_enter_goes_first_then_the_break(self):
        self.broken()
        self.type("Now ")
        self.undo()
        self.wait_for(f"{PARAGRAPHS}.includes('Two sentences here.')")
        self.assertEqual(self.source(), "One sentence.\n\nTwo sentences here.\n\nNext paragraph.\n")
        self.undo()
        self.wait_for(f"{PARAGRAPHS}.includes('One sentence. Two sentences here.')")
        self.settled()
        self.assertEqual(self.source(), TEXT)

    def test_text_typed_before_enter_stays_on_the_page_and_saves_on_leaving(self):
        self.build(TEXT)
        self.write_in("sentence")
        self.caret_after("One sentence.")
        self.type(" Extra.")
        self.enter()
        self.saved_now()
        self.undo()
        self.wait_for(f"{PARAGRAPHS}.includes('One sentence. Extra. Two sentences here.')")
        self.settled()
        self.assertEqual(self.source(), TEXT)
        self.leave()
        self.saved_now()
        self.assertEqual(self.source(), "One sentence. Extra. Two sentences here.\n\nNext paragraph.\n")

    def test_cmd_shift_z_breaks_it_again(self):
        self.broken()
        self.undo()
        self.wait_for(f"{PARAGRAPHS}.includes('One sentence. Two sentences here.')")
        self.settled()
        self.redo()
        self.wait_for(f"{PARAGRAPHS}.includes('Two sentences here.')")
        self.settled()
        self.assertEqual(self.source(), "One sentence.\n\nTwo sentences here.\n\nNext paragraph.\n")
        self.undo()
        self.wait_for(f"{PARAGRAPHS}.includes('One sentence. Two sentences here.')")
        self.settled()
        self.assertEqual(self.source(), TEXT)

    def test_cmd_z_removes_a_new_empty_paragraph(self):
        self.build(TEXT)
        self.write_in("sentence")
        self.caret_after("Two sentences here.")
        self.enter()
        self.wait_for("document.querySelector('.pv-writing')?.textContent === ''")
        self.undo()
        self.wait_for(f"{PARAGRAPHS}.length === 3")  # the introduction's, this one and the next
        self.type(" More.")
        self.assertIn("One sentence. Two sentences here. More.", self.paragraphs())

    def test_cmd_z_takes_bold_back(self):
        self.build(TEXT)
        self.select_word("Two")
        self.chord("b", 66)
        self.wait_for("document.querySelector('.pv-writing strong') && !saves")
        self.assertEqual(self.source(), "One sentence. **Two** sentences here.\n\nNext paragraph.\n")
        self.undo()
        self.wait_for("!document.querySelector('.pv-writing strong')")
        self.settled()
        self.assertEqual(self.source(), TEXT)
        self.assertEqual(self.evaluate("getSelection().toString()"), "Two")

    def test_cmd_z_splits_a_joined_paragraph_again(self):
        self.build(TEXT)
        self.write_in("Next")
        self.caret_before_next()
        self.key("Backspace", 8)
        self.wait_for(f"{PARAGRAPHS}.includes('One sentence. Two sentences here. Next paragraph.') && !saves")
        self.assertEqual(self.source(), "One sentence. Two sentences here. Next paragraph.\n")
        self.undo()
        self.wait_for(f"{PARAGRAPHS}.includes('Next paragraph.')")
        self.settled()
        self.assertEqual(self.source(), TEXT)

    def test_a_paste_of_two_paragraphs_goes_back_piece_by_piece(self):
        self.build(TEXT)
        self.write_in("sentence")
        self.caret_after("One sentence.")
        self.evaluate("""(() => {
          const data = new DataTransfer();
          data.setData('text/plain', ' First piece.\\n\\nSecond piece.');
          document.activeElement.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
        })()""")
        self.saved_now()
        self.assertIn("Second piece. Two sentences here.", self.paragraphs())
        for _ in range(3):
            self.undo()
            self.settled()
        self.wait_for(f"{PARAGRAPHS}.includes('One sentence. Two sentences here.')")
        self.assertEqual(self.source(), TEXT)

    def test_enter_over_a_selection_goes_back_with_the_words_it_took(self):
        self.build("One sentence here\n")
        self.select_word("here")
        self.enter()  # the selection goes, and what is left of the paragraph is empty
        self.wait_for("document.querySelector('.pv-writing')?.textContent === ''")
        self.undo()
        self.wait_for("document.querySelectorAll('.pv-page p.pv-can-write').length === 2")
        self.leave()
        self.settled()
        self.assertIn("One sentence here", self.paragraphs())
        self.assertEqual(self.source(), "One sentence here\n")

    def test_a_new_paragraph_in_a_loose_list_goes_back_into_its_item(self):
        text = "- First words.\n\n  Continuation.\n\n- Second item.\n"
        self.build(text)
        self.write_in("First")
        self.caret_after("First words.")
        self.enter()
        self.wait_for("document.querySelector('.pv-writing')?.textContent === ''")
        self.undo()
        self.wait_for("document.querySelectorAll('.pv-page li').length === 2")
        self.assertEqual(self.evaluate("[...document.querySelectorAll('.pv-page li')].map(li => li.querySelectorAll('p').length)"), [2, 1])
        self.leave()
        self.settled()
        self.assertEqual(self.source(), text)

    def test_two_breaks_go_back_and_come_again(self):
        self.build(TEXT)
        self.write_in("sentence")
        self.caret_after("One sentence.")
        self.enter()
        self.settled()
        self.caret_after("Two")
        self.enter()
        self.settled()
        twice = "One sentence.\n\nTwo\n\nsentences here.\n\nNext paragraph.\n"
        self.assertEqual(self.source(), twice)
        for _ in range(2):
            self.undo()
            self.settled()
        self.wait_for(f"{PARAGRAPHS}.includes('One sentence. Two sentences here.')")
        self.assertEqual(self.source(), TEXT)
        for _ in range(2):
            self.redo()
            self.settled()
        self.wait_for(f"{PARAGRAPHS}.includes('sentences here.')")
        self.assertEqual(self.source(), twice)
        self.assertEqual(self.paragraphs()[1:4], ["One sentence.", "Two", "sentences here."])

    def test_cmd_z_does_not_undo_over_a_change_on_disk(self):
        self.broken()
        changed = "One sentence.\n\nTwo sentences here.\n\nNext paragraph, edited elsewhere.\n"
        self.chapter.write_text(changed, encoding="utf-8")
        self.undo()
        self.wait_for("document.querySelector('.pv-toast')?.textContent.startsWith('Not undone')")
        self.assertEqual(self.source(), changed)
        self.assertIn("Two sentences here.", self.paragraphs())


class ReloadAfterSavesTests(UndoCase):
    def test_the_page_waits_for_the_build_of_its_last_save(self):
        self.build(TEXT)
        self.evaluate("window.samePage = true")
        self.write_in("Next")
        self.type("Edited ")
        with preview.LOCK:  # a build of an earlier save finished while the caret was in the text
            preview.STATE["manuscript"]["version"] += 1
        self.leave()
        self.saved_now()
        # The watcher has not caught up with this save: that build lacks it, so no reload yet.
        time.sleep(2.5)  # two polls of the page's status
        self.assertTrue(self.evaluate("window.samePage === true"))
        with preview.LOCK:
            preview.STATE["manuscript"]["version"] += 1
            preview.WATCHER["synced"] = time.time()
        self.wait_for("window.samePage === undefined")


if __name__ == "__main__":
    unittest.main()
