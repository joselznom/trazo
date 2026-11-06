"""What the writing tests in Chrome share: a chapter on the real page, and real clicks and keys."""

import json

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
        point = self.evaluate("""(() => {
          const box = document.querySelector('.pv-page').getBoundingClientRect();
          return [box.left + 4, Math.min(box.top + 300, innerHeight - 20)];
        })()""")  # the page's margin
        self.mouse("mousePressed", *point)
        self.mouse("mouseReleased", *point)
        self.wait_for("!document.querySelector('.pv-writing')")
