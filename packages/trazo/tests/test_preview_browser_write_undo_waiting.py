"""Cmd+Z while writing, in Chrome, with saves still on their way: the server's answers wait
(UndoCase.held) while the author goes on, as with a slow build check.

The steps go back in the order they were made, and the page and the file stay alike.
"""

import unittest

from _preview_undo import BOLD, PARAGRAPHS, TEXT, UndoCase


class WaitingSaveTests(UndoCase):
    def test_steps_go_back_in_the_order_they_were_made_whatever_order_they_saved_in(self):
        self.build(TEXT)
        self.write_in("sentence")
        self.caret_after("One sentence.")
        with self.held("change_block"):
            self.enter()  # its save answers after the next Enter
            self.caret_after("Two sentences here.")
            self.enter()
            self.type("Unsaved words.")
        self.undo()  # the typing, by the browser
        self.wait_for(f"!{PARAGRAPHS}.includes('Unsaved words.')")
        self.assertEqual(len(self.paragraphs()), 5)  # the introduction's, two halves, the new one, the next
        self.undo()  # the new paragraph
        self.wait_for(f"{PARAGRAPHS}.length === 4")
        self.undo()  # the break
        self.wait_for(f"{PARAGRAPHS}.includes('One sentence. Two sentences here.')")
        self.settled()
        self.assertEqual(self.source(), TEXT)

    def test_a_new_paragraph_taken_back_keeps_the_key_its_neighbour_got_meanwhile(self):
        self.build(TEXT)
        self.write_in("sentence")
        self.caret_after("One sentence.")
        with self.held("change_block"):
            self.enter()
            self.caret_after("Two sentences here.")
            self.enter()  # a new empty paragraph after the half whose save is still on its way
        self.undo()
        self.wait_for(f"{PARAGRAPHS}.length === 4")
        self.type(" More.")
        self.leave()
        self.saved_now()
        self.assertFalse(self.evaluate("!!document.querySelector('.pv-write-failed')"))
        self.assertEqual(self.source(), "One sentence.\n\nTwo sentences here. More.\n\nNext paragraph.\n")

    def test_cmd_z_does_not_take_text_typed_while_a_style_saved_with_the_style(self):
        self.build(TEXT)
        self.select_word("Two")
        with self.held("change_format"):
            self.chord("b", 66)
            self.caret_after("here.")
            self.type(" Late words.")
        self.wait_for("document.querySelector('.pv-writing strong')")
        self.undo()
        self.settled()
        # The style came after the words on the page, so the browser cannot take them back
        # and the style is not the latest step there: nothing goes, and nothing is lost.
        self.assertTrue(self.evaluate("!!document.querySelector('.pv-writing strong')"))
        self.assertIn("One sentence. Two sentences here. Late words.", self.paragraphs())
        self.assertEqual(self.source(), "One sentence. **Two** sentences here.\n\nNext paragraph.\n")

    def test_text_saved_before_a_style_stays_saved_after_undoing_the_style(self):
        self.build(TEXT)
        self.write_in("sentence")
        self.caret_after("here.")
        self.type(" Extra.")
        self.select_word("Two")
        self.chord("b", 66)  # saves " Extra." first, then the style
        self.wait_for("document.querySelector('.pv-writing strong') && !saves")
        self.undo()
        self.wait_for("!document.querySelector('.pv-writing strong')")
        self.settled()
        self.assertEqual(self.source(), "One sentence. Two sentences here. Extra.\n\nNext paragraph.\n")
        self.caret_after("One")
        self.type(" more")
        self.leave()
        self.saved_now()
        self.assertEqual(self.source(), "One more sentence. Two sentences here. Extra.\n\nNext paragraph.\n")

    def test_styles_made_while_the_one_before_saves_go_back_one_by_one(self):
        self.build("One sentence. *Two* sentences here.\n")
        self.select_word("One")
        with self.held("change_format"):
            self.chord("b", 66)
            self.select_word("here")
            self.chord("b", 66)  # while the first one saves
        self.wait_for(f"{BOLD}.length === 2")
        self.assertEqual(self.source(), "**One** sentence. *Two* sentences **here**.\n")
        self.undo()
        self.wait_for(f"{BOLD}.length === 1")
        self.settled()
        self.assertEqual(self.evaluate(BOLD), ["One"])
        self.assertEqual(self.source(), "**One** sentence. *Two* sentences here.\n")
        self.undo()
        self.wait_for(f"{BOLD}.length === 0")
        self.settled()
        self.assertEqual(self.source(), "One sentence. *Two* sentences here.\n")
        self.assertEqual(self.evaluate("[...document.querySelectorAll('.pv-writing em')].map(e => e.textContent)"), ["Two"])

    def test_a_new_paragraph_taken_back_leaves_a_style_saved_meanwhile_on_its_neighbour(self):
        self.build("One *sentence* here.\n")
        self.select_word("One")
        with self.held("change_format"):
            self.chord("b", 66)
            self.caret_after("here.")
            self.enter()
        self.wait_for(f"{BOLD}.length === 1")
        self.undo()
        self.wait_for("document.querySelectorAll('.pv-page p.pv-can-write').length === 2")  # the introduction's and this
        self.assertEqual(self.evaluate(BOLD), ["One"])
        self.assertEqual(self.source(), "**One** *sentence* here.\n")

    def test_a_join_made_while_a_style_saves_goes_back_with_the_style_kept(self):
        self.build(TEXT)
        self.select_word("paragraph")
        with self.held("change_format"):
            self.chord("b", 66)
            self.caret_before_next()
            self.key("Backspace", 8)
        self.wait_for(f"{PARAGRAPHS}.includes('One sentence. Two sentences here. Next paragraph.')")
        self.settled()
        self.assertEqual(self.source(), "One sentence. Two sentences here. Next **paragraph**.\n")
        self.undo()
        self.wait_for(f"{PARAGRAPHS}.includes('Next paragraph.')")
        self.settled()
        self.assertEqual(self.source(), "One sentence. Two sentences here.\n\nNext **paragraph**.\n")
        self.assertEqual(self.evaluate(BOLD), ["paragraph"])

    def test_a_join_waits_for_a_style_saving_in_the_paragraph_before(self):
        self.build("One sentence.\n\nNext paragraph.\n")
        self.select_word("One")
        with self.held("change_format"):
            self.chord("b", 66)
            self.caret_after("sentence.")
            self.key("ArrowRight", 39)  # on to the next paragraph, at its start
            self.wait_for("document.querySelector('.pv-writing')?.textContent.includes('Next')")
            self.key("Backspace", 8)
        self.wait_for(f"{PARAGRAPHS}.includes('One sentence. Next paragraph.')")
        self.settled()
        self.assertEqual(self.source(), "**One** sentence. Next paragraph.\n")
        self.undo()
        self.wait_for(f"{PARAGRAPHS}.includes('Next paragraph.')")
        self.settled()
        self.assertEqual(self.source(), "**One** sentence.\n\nNext paragraph.\n")
        self.assertEqual(self.evaluate(BOLD), ["One"])

    def test_enter_while_a_style_saves_waits_for_it_and_cmd_z_then_redo_keep_the_style(self):
        self.build("One *sentence* here.\n")
        self.select_word("One")
        with self.held("change_format"):
            self.chord("b", 66)
            self.caret_after("here.")
            self.enter()
            self.undo()  # pressed after Enter: it takes Enter back, once Enter is done
        self.wait_for(f"{BOLD}.length === 1 && document.querySelectorAll('.pv-page p.pv-can-write').length === 2")
        self.settled()
        self.assertEqual(self.source(), "**One** *sentence* here.\n")
        self.redo()
        self.wait_for("document.querySelector('.pv-writing')?.textContent === ''")
        self.assertEqual(self.evaluate(BOLD), ["One"])
        self.key("Escape", 27)
        self.wait_for("!document.querySelector('.pv-writing')")
        self.assertEqual(self.evaluate(BOLD), ["One"])
        self.assertEqual(self.source(), "**One** *sentence* here.\n")


if __name__ == "__main__":
    unittest.main()
