"""Writing on the preview's text in Chrome: real clicks and keys against the real page and server."""

import json
import unittest
from unittest.mock import patch

import _support
from _preview_browser import PreviewBrowserTestCase

from trazo import preview

CHAPTER = "manuscript/main/02_writing.md"
TEXT = """# Writing

The quick brown fox [@smith2020] jumps over the *lazy* dog in {{v:cohort.n}} fields.

A second paragraph with plain words in it.

- A list item with words.
"""


class WritingCase(PreviewBrowserTestCase):
    def setUp(self):
        super().setUp()
        self.chapter = _support.ROOT / CHAPTER
        self.keep_files(self.chapter, preview.FILE_HISTORY_PATH, _support.ROOT / "notes/review/edits.json")

    def build(self, text=TEXT):
        self.chapter.write_text(text, encoding="utf-8")
        self.assertEqual(preview.render("manuscript", preview.OUT_DIR)[0], "")
        self.open()

    def source(self):
        return self.chapter.read_text(encoding="utf-8")

    def word_at(self, word):
        """Where a word of the page's text is on the screen."""
        return self.evaluate(f"""(() => {{
          const walker = document.createTreeWalker(document.querySelector('.pv-page'), NodeFilter.SHOW_TEXT);
          while (walker.nextNode()) {{
            const at = walker.currentNode.data.indexOf({json.dumps(word)});
            if (at < 0) continue;
            const range = document.createRange();
            range.setStart(walker.currentNode, at); range.setEnd(walker.currentNode, at + {len(word)});
            const box = range.getBoundingClientRect();
            return [box.left + box.width / 2, box.top + box.height / 2];
          }}
        }})()""")

    def click_word(self, word):
        point = self.word_at(word)
        self.mouse("mousePressed", *point)
        self.mouse("mouseReleased", *point)

    def write_in(self, word):
        self.click_word(word)
        self.wait_for("document.activeElement.classList.contains('pv-writing')")

    def type(self, text):
        self.chrome.call("Input.insertText", {"text": text}, self.session)

    def leave(self):
        self.click_word("Introduction")
        self.wait_for("!document.querySelector('.pv-writing')")


class WritingTests(WritingCase):
    def test_a_click_writes_where_it_lands_and_leaving_saves(self):
        self.build()
        before = self.word_at("second")
        self.write_in("second")
        self.assertEqual(self.word_at("second"), before)  # the text does not move
        self.type("really ")
        self.leave()
        self.wait_for("document.querySelector('.pv-save-state').textContent === 'Saved'")
        self.assertIn("really ", self.source())
        self.assertEqual(self.source().replace("really ", ""), TEXT)
        self.assertEqual(self.evaluate("document.querySelectorAll('[contenteditable=false]').length"), 0)

    def test_escape_puts_the_paragraph_back_and_it_still_works(self):
        self.build()
        self.write_in("plain")
        self.type("nonsense ")
        self.key("Escape", 27)
        self.wait_for("!document.querySelector('.pv-writing')")
        self.assertEqual(self.source(), TEXT)
        self.assertNotIn("nonsense", self.evaluate("document.querySelector('.pv-page').textContent"))
        self.assertEqual(self.evaluate("document.querySelector('.pv-save-state').textContent"), "")
        self.mouse_double_click(*self.word_at("plain"))  # the bar for a selection reads the restored text
        self.wait_for("document.querySelector('.pv-fab').hidden === false")

    def test_a_chip_stays_whole_and_a_click_on_it_stays_a_click(self):
        self.build()
        self.click_text(".pv-atom .citation")
        self.evaluate("new Promise(resolve => setTimeout(resolve, 100))")
        self.assertFalse(self.evaluate("!!document.querySelector('.pv-writing')"))
        self.write_in("quick")
        self.assertEqual(self.evaluate("document.querySelector('.pv-writing .pv-atom').isContentEditable"), False)

    def test_deleting_a_chip_deletes_its_source(self):
        self.build("Keep [@smith2020] going.\n")
        self.write_in("going")
        self.evaluate("""(() => {
          const chip = document.querySelector('.pv-writing .pv-atom');
          const range = document.createRange(); range.setStartAfter(chip); range.collapse(true);
          const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
        })()""")
        self.key("Backspace", 8)
        self.leave()
        self.wait_for("document.querySelector('.pv-save-state').textContent === 'Saved'")
        self.assertNotIn("smith2020", self.source())

    def test_a_selection_across_paragraphs_is_not_writing(self):
        self.build()
        start, end = self.word_at("quick"), self.word_at("plain")
        self.mouse("mousePressed", *start)
        self.mouse("mouseMoved", *end)
        self.mouse("mouseReleased", *end)
        self.wait_for("document.querySelector('.pv-fab').hidden === false")
        self.assertFalse(self.evaluate("!!document.querySelector('.pv-writing')"))

    def test_a_refused_save_keeps_the_text_and_offers_the_markdown(self):
        self.build("Plain words here.\n")
        with patch("trazo.preview_block_save.check", lambda *args: "it does not read the same"):
            self.write_in("words")
            self.type("my ")
            self.leave()
            self.wait_for("document.querySelector('.pv-write-bar')")
        self.assertIn("it does not read the same", self.evaluate("document.querySelector('.pv-write-bar').textContent"))
        self.assertIn("my ", self.evaluate("document.querySelector('.pv-write-failed').textContent"))
        self.assertEqual(self.source(), "Plain words here.\n")
        self.evaluate("[...document.querySelectorAll('.pv-write-bar button')].find(b => b.textContent === 'Edit Markdown').click()")
        self.wait_for("document.activeElement.classList.contains('pv-write-md')")
        self.assertEqual(self.evaluate("document.activeElement.value"), "Plain words here.")
        self.evaluate("document.activeElement.value = 'Plain *new* words.'")
        self.evaluate("[...document.querySelectorAll('.pv-write-box button')].find(b => b.textContent === 'Save').click()")
        self.wait_for("!document.querySelector('.pv-write-box') && !document.querySelector('.pv-write-bar')")
        self.assertEqual(self.source(), "Plain *new* words.\n")

    def test_markdown_that_cannot_be_saved_stays_and_can_be_kept(self):
        for marker in ("", "- "):
            with self.subTest(marker=marker):
                self.build(f"{marker}Plain words here today.\n")
                with patch("trazo.preview_block_save.check", lambda *args: "it does not read the same"):
                    self.write_in("words")
                    self.type("my ")
                    self.leave()
                    self.wait_for("document.querySelector('.pv-write-bar')")
                self.evaluate("[...document.querySelectorAll('.pv-write-bar button')].find(b => b.textContent === 'Edit Markdown').click()")
                self.wait_for("document.activeElement.classList.contains('pv-write-md')")
                self.evaluate("document.activeElement.value = 'Plain *new* words here today.'")
                self.chapter.write_text(f"{marker}Plain words here tomorrow.\n", encoding="utf-8")
                self.evaluate("[...document.querySelectorAll('.pv-write-box button')].find(b => b.textContent === 'Save').click()")
                self.wait_for("document.querySelector('.pv-write-bar')?.textContent.includes('changed on disk')")
                self.assertEqual(self.evaluate("document.querySelector('.pv-write-md').value"), "Plain *new* words here today.")
                self.assertTrue(self.evaluate("document.querySelector('.pv-write-bar').checkVisibility()"))
                self.evaluate("[...document.querySelectorAll('.pv-write-bar button')].find(b => b.textContent === 'Keep mine').click()")
                self.wait_for("!document.querySelector('.pv-write-box') && !document.querySelector('.pv-write-bar')")
                self.assertEqual(self.source(), f"{marker}Plain *new* words here tomorrow.\n")

    def test_closing_the_markdown_leaves_the_choices_of_a_failed_save(self):
        self.build("Plain words here today.\n")
        with patch("trazo.preview_block_save.check", lambda *args: "it does not read the same"):
            self.write_in("words")
            self.type("my ")
            self.leave()
            self.wait_for("document.querySelector('.pv-write-bar')")
        self.evaluate("[...document.querySelectorAll('.pv-write-bar button')].find(b => b.textContent === 'Edit Markdown').click()")
        self.wait_for("document.activeElement.classList.contains('pv-write-md')")
        self.chapter.write_text("Plain words here tomorrow.\n", encoding="utf-8")
        self.evaluate("[...document.querySelectorAll('.pv-write-box button')].find(b => b.textContent === 'Save').click()")
        self.wait_for("document.querySelector('.pv-write-bar')?.textContent.includes('changed on disk')")
        self.evaluate("[...document.querySelectorAll('.pv-write-box button')].find(b => b.textContent === 'Cancel').click()")
        self.wait_for("!document.querySelector('.pv-write-box')")
        self.assertTrue(self.evaluate("document.querySelector('.pv-write-bar').checkVisibility()"))
        self.evaluate("[...document.querySelectorAll('.pv-write-bar button')].find(b => b.textContent === \"Take the file's\").click()")
        self.wait_for("!document.querySelector('.pv-write-bar') && !document.querySelector('.pv-write-failed')")

    def test_a_paragraph_changed_on_disk_keeps_the_text_until_the_author_picks(self):
        text = "The quick brown fox jumps over the lazy dog.\n"
        for choice, result in (("Keep mine", "The slow quick brown fox jumps over the sleepy dog.\n"),
                               ("Take the file's", "The quick brown fox jumps over the sleepy dog.\n")):
            with self.subTest(choice=choice):
                self.build(text)
                self.write_in("quick")
                self.evaluate("""(() => {
                  const walker = document.createTreeWalker(document.querySelector('.pv-writing'), NodeFilter.SHOW_TEXT);
                  while (walker.nextNode()) {
                    const at = walker.currentNode.data.indexOf('quick');
                    if (at >= 0) { getSelection().collapse(walker.currentNode, at); return; }
                  }
                })()""")
                self.chapter.write_text(text.replace("lazy", "sleepy"), encoding="utf-8")
                self.type("slow ")
                self.leave()
                self.wait_for("document.querySelector('.pv-write-bar')")
                self.assertIn("changed on disk", self.evaluate("document.querySelector('.pv-write-bar').textContent"))
                self.assertEqual(self.source(), text.replace("lazy", "sleepy"))
                self.evaluate(f"[...document.querySelectorAll('.pv-write-bar button')].find(b => b.textContent === {json.dumps(choice)}).click()")
                self.wait_for("!document.querySelector('.pv-write-bar')")
                self.assertEqual(self.source(), result)

    def test_a_struck_edit_hides_while_writing_and_a_mark_opens_its_bar(self):
        self.build("A plain sentence with a word.\n")
        preview.edits_payload("manuscript")  # the baseline
        self.build("A plain sentence with some word.\n")
        self.wait_for("document.querySelector('.pv-edit-ins')")
        self.click_text(".pv-edit-ins")
        self.wait_for("!document.querySelector('.pv-edit-bar').hidden")
        self.assertFalse(self.evaluate("!!document.querySelector('.pv-writing')"))
        self.write_in("plain")
        self.assertEqual(self.evaluate("getComputedStyle(document.querySelector('.pv-writing .pv-edit-del')).display"), "none")
        self.type("very ")
        self.leave()
        self.wait_for("document.querySelector('.pv-save-state').textContent === 'Saved'")
        self.assertEqual([e["new"] for e in preview.edits_payload("manuscript")["edits"]], ["some"])

    def test_the_page_reloads_only_after_leaving_the_text(self):
        self.build()
        self.evaluate("window.samePage = true")
        self.write_in("plain")
        with preview.LOCK:
            preview.STATE["manuscript"]["version"] += 1
        self.wait_for("!document.querySelector('.pv-hold').hidden")
        self.assertTrue(self.evaluate("window.samePage === true"))
        self.leave()
        self.wait_for("window.samePage === undefined")


class ParagraphTests(WritingCase):
    """Writing across paragraphs: Enter, Backspace, the arrows and a paste."""

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
        self.saved("Onefirst line wrapped\n\nA second.\n\nThird here.\n")

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
