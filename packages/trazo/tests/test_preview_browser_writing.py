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


class WritingTests(PreviewBrowserTestCase):
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


if __name__ == "__main__":
    unittest.main()
