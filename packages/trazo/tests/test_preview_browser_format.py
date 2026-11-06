"""Bold and italic while writing, in Chrome: real keys and the bar against the real page and server."""

import unittest
from unittest.mock import patch

from _preview_writing import WritingCase

from trazo import preview

META = 4  # the modifier bit of Cmd in Input.dispatchKeyEvent


class FormatTests(WritingCase):
    def chord(self, key, code):
        """Cmd, or Ctrl where Chrome sends it, with a key."""
        for kind in ("keyDown", "keyUp"):
            self.chrome.call("Input.dispatchKeyEvent", {"type": kind, "key": key, "code": f"Key{key.upper()}", "modifiers": META,
                                                        "windowsVirtualKeyCode": code}, self.session)

    def select(self, word):
        self.mouse_double_click(*self.word_at(word))
        self.wait_for("document.querySelector('.pv-writing') && !getSelection().isCollapsed")

    def saved(self, text):
        self.wait_for("document.querySelector('.pv-save-state').textContent === 'Saved' && !saves")
        self.assertEqual(self.source(), text)

    def test_cmd_b_makes_the_selection_bold_and_again_takes_it_off(self):
        self.build("A quick brown fox.\n")
        self.select("quick")
        self.chord("b", 66)
        self.wait_for("document.querySelector('.pv-writing strong')?.textContent === 'quick'")
        self.saved("A **quick** brown fox.\n")
        self.assertEqual(self.evaluate("getSelection().toString()"), "quick")
        self.chord("b", 66)
        self.wait_for("!document.querySelector('.pv-writing strong')")
        self.saved("A quick brown fox.\n")

    def test_cmd_i_on_part_of_a_bold_leaves_the_rest_as_it_was(self):
        self.build("A **quick brown** fox.\n")
        self.select("quick")
        self.chord("i", 73)
        self.wait_for("document.querySelector('.pv-writing strong em')?.textContent === 'quick'")
        self.saved("A ***quick* brown** fox.\n")

    def test_the_bar_offers_bold_and_italic_for_a_selection_in_a_paragraph(self):
        self.build("A quick brown fox.\n")
        self.select("quick")
        self.wait_for("document.querySelector('.pv-fab [data-select=strong]')")
        self.click_text(".pv-fab [data-select=em]")
        self.wait_for("document.querySelector('.pv-writing em')?.textContent === 'quick'")
        self.saved("A *quick* brown fox.\n")

    def test_what_was_typed_is_saved_with_the_format(self):
        self.build("A quick brown fox.\n")
        self.write_in("brown")
        self.type("very ")
        self.evaluate("""(() => {
          const text = document.querySelector('.pv-writing .pv-src').nextSibling;
          const range = document.createRange();
          range.setStart(text, 2); range.setEnd(text, 7);
          const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
        })()""")
        self.chord("b", 66)
        self.wait_for("document.querySelector('.pv-writing strong')")
        self.wait_for("!saves")
        self.assertIn("**quick**", self.source())
        self.assertIn("very ", self.source())

    def test_a_refused_format_says_so_and_leaves_the_text(self):
        text = "A **quick** fox.\n"
        self.build(text)
        self.select("fox")
        self.evaluate("""(() => {
          const page = document.querySelector('.pv-writing');
          const range = document.createRange();
          range.setStart(page.querySelector('strong').firstChild, 2);
          range.setEnd(page.querySelector('strong').nextSibling, 2);
          const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
        })()""")
        self.chord("i", 73)
        self.wait_for("document.querySelector('.pv-toast')?.textContent.startsWith('Not formatted')")
        self.assertEqual(self.source(), text)

    def test_cmd_b_on_a_selection_across_paragraphs_starts_no_build(self):
        self.build()
        with patch.object(preview, "start_docx_build") as build:
            start, end = self.word_at("quick"), self.word_at("plain")
            self.mouse("mousePressed", *start)
            self.mouse("mouseMoved", *end)
            self.mouse("mouseReleased", *end)
            self.wait_for("document.querySelector('.pv-fab').hidden === false")
            self.assertFalse(self.evaluate("!!document.querySelector('.pv-fab [data-select=strong]')"))
            self.chord("b", 66)
            self.evaluate("new Promise(resolve => setTimeout(resolve, 200))")
        build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
