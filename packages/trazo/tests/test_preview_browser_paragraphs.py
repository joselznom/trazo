"""Writing across paragraphs in Chrome: Enter, Backspace, the arrows and a paste."""

import json
import unittest

from _preview_writing import TEXT, WritingCase


class ParagraphTests(WritingCase):
    def caret_after(self, word):
        self.evaluate(f"""(() => {{
          const walker = document.createTreeWalker(document.querySelector('.pv-writing'), NodeFilter.SHOW_TEXT);
          while (walker.nextNode()) {{
            const at = walker.currentNode.data.indexOf({json.dumps(word)});
            if (at >= 0) {{ getSelection().collapse(walker.currentNode, at + {len(word)}); return; }}
          }}
        }})()""")

    def saved(self, text):
        self.wait_for("document.querySelector('.pv-save-state').textContent === 'Saved' && !saves")
        self.assertEqual(self.source(), text)

    def test_enter_breaks_the_paragraph_and_the_caret_stays_with_the_second_part(self):
        self.build("One sentence. Two [@smith2020] sentences.\n")
        self.write_in("sentence")
        self.caret_after("One sentence.")
        self.key("Enter", 13)
        self.saved("One sentence.\n\nTwo [@smith2020] sentences.\n")
        self.type("Now ")
        self.leave()
        self.saved("One sentence.\n\nNow Two [@smith2020] sentences.\n")

    def test_enter_at_the_end_opens_a_paragraph_saved_once_it_holds_text(self):
        self.build("One sentence.\n\nNext.\n")
        self.write_in("sentence")
        self.caret_after("One sentence.")
        self.key("Enter", 13)
        self.wait_for("document.querySelector('.pv-writing')?.textContent === ''")
        self.type("A new one.")
        self.key("Enter", 13)
        self.type("And another.")
        self.leave()
        self.saved("One sentence.\n\nA new one.\n\nAnd another.\n\nNext.\n")

    def test_a_new_paragraph_left_empty_is_not_saved(self):
        self.build("One sentence.\n")
        self.write_in("sentence")
        self.caret_after("One sentence.")
        self.key("Enter", 13)
        self.wait_for("document.querySelector('.pv-writing')?.textContent === ''")
        self.leave()
        self.assertEqual(self.evaluate("document.querySelectorAll('.pv-can-write').length"), 2)  # the introduction's and this
        self.assertEqual(self.source(), "One sentence.\n")

    def test_enter_in_a_list_makes_a_new_item(self):
        self.build("- First item words.\n- Second.\n")
        self.write_in("First")
        self.caret_after("First item")
        self.key("Enter", 13)
        self.saved("- First item\n- words.\n- Second.\n")

    def test_a_refused_break_puts_the_text_back_in_one_paragraph(self):
        self.build("One **bold words** here.\n")
        self.write_in("bold")
        self.caret_after("bold")
        self.key("Enter", 13)
        self.wait_for("document.querySelector('.pv-write-bar')")
        self.assertEqual(self.source(), "One **bold words** here.\n")
        self.assertIn("bold words", self.evaluate("document.querySelector('.pv-write-failed').textContent"))

    def test_backspace_at_the_start_joins_the_paragraph_before(self):
        self.build("One *word*.\n\nTwo words.\n")
        self.write_in("Two")
        self.evaluate("getSelection().collapse(document.querySelector('.pv-writing .pv-src').nextSibling, 0)")
        self.key("Backspace", 8)
        self.wait_for("!saves && document.querySelector('.pv-writing')?.textContent.includes('word. Two words.')")
        self.assertEqual(self.source(), "One *word*. Two words.\n")
        self.type("Then ")
        self.leave()
        self.saved("One *word*. Then Two words.\n")

    def test_deleting_all_of_a_paragraph_takes_it_out_of_the_file(self):
        self.build("One.\n\nAll of this.\n\nThree.\n")
        self.write_in("this")
        self.evaluate("getSelection().selectAllChildren(document.querySelector('.pv-writing'))")
        self.key("Backspace", 8)
        self.leave()
        self.saved("One.\n\nThree.\n")

    def test_the_arrows_go_on_to_the_next_paragraph_and_save_this_one(self):
        self.build("One.\n\nTwo.\n")
        self.write_in("One")
        self.caret_after(".")
        self.type("x")
        self.key("ArrowDown", 40)
        self.wait_for("document.querySelector('.pv-writing')?.textContent.includes('Two.')")
        self.wait_for("!saves")
        self.assertIn("x", self.source().split("\n")[0])
        self.key("ArrowUp", 38)
        self.wait_for("document.querySelector('.pv-writing')?.textContent.includes('One.')")

    def test_a_paste_of_paragraphs_makes_paragraphs_and_keeps_no_formatting(self):
        self.build("One here.\n")
        self.write_in("here")
        self.caret_after("One")
        self.evaluate("""(() => {
          const data = new DataTransfer();
          data.setData('text/html', '<b>Bold</b>');
          data.setData('text/plain', ' first line\\nwrapped\\n\\nA second.\\n\\nThird');
          document.activeElement.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
        })()""")
        self.leave()
        self.saved("One first line wrapped\n\nA second.\n\nThird here.\n")

    def test_enter_at_the_start_opens_a_paragraph_before_this_one(self):
        self.build("One sentence.\n")
        self.write_in("sentence")
        self.evaluate("getSelection().collapse(document.querySelector('.pv-writing .pv-src').nextSibling, 0)")
        self.key("Enter", 13)
        self.wait_for("document.querySelector('.pv-writing')?.textContent === ''")
        self.type("New.")
        self.leave()
        self.saved("New.\n\nOne sentence.\n")

    def test_enter_in_a_loose_list_makes_a_new_item_and_backspace_joins_it(self):
        self.build("- First words.\n\n- Second.\n")
        self.write_in("First")
        self.caret_after("First")
        self.key("Enter", 13)
        self.saved("- First\n- words.\n\n- Second.\n")
        self.assertEqual(self.evaluate("document.querySelectorAll('.pv-page li').length"), 3)
        self.evaluate("getSelection().collapse(document.querySelector('.pv-writing .pv-src').nextSibling, 0)")
        self.key("Backspace", 8)
        self.wait_for("!saves && document.querySelector('.pv-writing')?.textContent.includes('First words.')")
        self.assertEqual(self.source(), "- First words.\n\n- Second.\n")
        self.assertEqual(self.evaluate("document.querySelectorAll('.pv-page li').length"), 2)

    def test_enter_leaves_an_item_with_a_sublist_alone(self):
        text = "- First words.\n\n  - Nested.\n\n- Second.\n"
        self.build(text)
        self.write_in("First")
        self.caret_after("First")
        self.key("Enter", 13)
        self.evaluate("new Promise(resolve => setTimeout(resolve, 100))")
        self.assertEqual(self.source(), text)
        self.assertEqual(self.evaluate("document.querySelectorAll('.pv-writing').length"), 1)

    def test_backspace_in_an_emptied_paragraph_takes_it_out_and_leaves_the_one_before(self):
        self.build("One.\n\nAll of this.\n\nThree.\n")
        self.write_in("this")
        self.evaluate("getSelection().selectAllChildren(document.querySelector('.pv-writing'))")
        self.key("Backspace", 8)
        self.key("Backspace", 8)
        self.wait_for("document.querySelector('.pv-writing')?.textContent.includes('One.')")
        self.saved("One.\n\nThree.\n")

    def test_delete_on_a_selection_across_paragraphs_only_says_so(self):
        self.build()
        start, end = self.word_at("quick"), self.word_at("plain")
        self.mouse("mousePressed", *start)
        self.mouse("mouseMoved", *end)
        self.mouse("mouseReleased", *end)
        self.wait_for("document.querySelector('.pv-fab').hidden === false")
        self.key("Delete", 46)
        self.wait_for("document.querySelector('.pv-toast')?.textContent.includes('inside one paragraph')")
        self.assertEqual(self.source(), TEXT)


if __name__ == "__main__":
    unittest.main()
