"""The @ menu while writing, in Chrome: real keys against the real page and server."""

import unittest

from _preview_writing import WritingCase


class MentionTests(WritingCase):
    def saved(self, text):
        self.wait_for("document.querySelector('.pv-save-state').textContent === 'Saved' && !saves")
        self.assertEqual(self.source(), text)

    def caret_after(self, word):
        self.evaluate(f"""(() => {{
          const walker = document.createTreeWalker(document.querySelector('.pv-writing'), NodeFilter.SHOW_TEXT);
          while (walker.nextNode()) {{
            const at = walker.currentNode.data.indexOf('{word}');
            if (at >= 0) {{ getSelection().collapse(walker.currentNode, at + {len(word)}); return; }}
          }}
        }})()""")

    def at_the_end_of(self, text):
        self.build(text)
        self.write_in("words")
        self.caret_after("words")

    def menu(self):
        self.wait_for("!document.querySelector('.pv-at-menu').hidden")
        return self.evaluate("[...document.querySelectorAll('.pv-at-menu b')].map(b => b.textContent)")

    def test_a_reference_goes_in_as_a_locked_chip(self):
        self.at_the_end_of("Plain words.\n")
        self.type(" @smi")
        self.assertEqual(self.menu(), ["smith2020"])
        self.key("Enter", 13)
        self.wait_for("document.querySelector('.pv-at-menu').hidden")
        self.assertEqual(self.evaluate("document.querySelector('.pv-writing .pv-atom').isContentEditable"), False)
        self.type(" more")
        self.leave()
        self.saved("Plain words [@smith2020] more.\n")

    def test_a_value_and_a_table_are_offered_too(self):
        self.at_the_end_of("Plain words.\n")
        self.type(" @cohort")
        self.assertEqual(self.menu(), ["120"])
        self.key("Enter", 13)
        self.type(" and @example")
        self.wait_for("[...document.querySelectorAll('.pv-at-menu button')].some(b => b.textContent.includes('Table'))")
        self.key("Enter", 13)
        self.leave()
        self.saved("Plain words {{v:cohort.n}} and [@tbl:example].\n")

    def test_esc_leaves_a_plain_at_sign(self):
        self.at_the_end_of("Plain words.\n")
        self.type(" @smi")
        self.menu()
        self.key("Escape", 27)
        self.wait_for("document.querySelector('.pv-at-menu').hidden")
        self.assertTrue(self.evaluate("!!document.querySelector('.pv-writing')"))  # Esc closed the menu, not the paragraph
        self.leave()
        self.saved("Plain words \\@smi.\n")

    def test_an_address_opens_no_menu(self):
        self.at_the_end_of("Plain words.\n")
        self.type(" name@smi")
        self.evaluate("new Promise(resolve => setTimeout(resolve, 150))")
        self.assertTrue(self.evaluate("document.querySelector('.pv-at-menu').hidden"))

    def test_the_arrows_choose_in_the_menu_and_a_click_inserts(self):
        self.at_the_end_of("Plain words.\n")
        self.type(" @")
        self.assertGreaterEqual(len(self.menu()), 3)
        self.key("ArrowDown", 40)
        self.wait_for("document.querySelectorAll('.pv-at-menu button')[1].getAttribute('aria-selected') === 'true'")
        self.assertTrue(self.evaluate("!!document.querySelector('.pv-writing')"))
        self.click_text(".pv-at-menu button")
        self.wait_for("document.querySelector('.pv-writing .pv-atom')")
        self.leave()
        self.saved("Plain words [@smith2020].\n")

    def test_a_chip_added_before_another_keeps_the_numbers_right(self):
        self.build("Plain words and [@smith2020] end.\n")
        self.write_in("words")
        self.caret_after("words")
        self.type(" @cohort")
        self.menu()
        self.key("Enter", 13)
        self.evaluate("getSelection().collapse(document.querySelector('.pv-writing'), document.querySelector('.pv-writing').childNodes.length)")
        self.key("Enter", 13)  # a break saves the chip and leaves the numbers to the server's
        self.wait_for("!saves")
        self.assertIn("{{v:cohort.n}}", self.source())
        self.assertIn("[@smith2020]", self.source())


if __name__ == "__main__":
    unittest.main()
