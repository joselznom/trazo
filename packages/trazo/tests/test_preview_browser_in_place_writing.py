"""The preview takes a new build in place while the author writes: the blocks
they are at wait, the rest change. In Chrome, against the real handler and real
builds."""

import unittest

import _support
from _preview_undo import UndoCase
from _preview_writing import TEXT

PAGE_TEXT = "document.querySelector('.pv-page').textContent"


class WritingInPlaceTests(UndoCase):
    def new_value(self, text):
        """The paper's one value (cohort.n, shown in the first paragraph) now reads `text`."""
        values = _support.ROOT / "data/values.json"
        self.keep_files(values)
        values.write_text(values.read_text().replace('"value": 120, "text": "120"', f'"value": {text}, "text": "{text}"'))

    def test_other_blocks_change_while_the_author_writes(self):
        self.build()
        self.evaluate("window.samePage = true")
        self.write_in("plain")
        self.type("very ")
        self.evaluate("window.caret = [getSelection().focusNode, getSelection().focusOffset]")
        version = self.built_again(TEXT.replace("quick brown fox", "quick red fox"))
        self.wait_for(f"loaded?.manuscript === {version}")
        # The file changed outside the page: the new word shows as a pending edit.
        self.assertEqual(self.evaluate("document.querySelector('.pv-edit-ins')?.textContent"), "red")
        self.assertTrue(self.evaluate("""window.samePage === true && document.activeElement === writing.el
          && getSelection().focusNode === window.caret[0] && getSelection().focusOffset === window.caret[1]"""))
        self.leave()
        self.settled()
        self.assertIn("quick red fox", self.source())
        self.assertIn("very", self.source())

    def test_the_paragraph_being_written_waits_until_it_is_left(self):
        self.build()
        self.write_in("quick")
        self.new_value("121")
        version = self.built_again(TEXT.replace("A second paragraph", "A rewritten second paragraph"))
        self.wait_for(f"loaded?.manuscript === {version}")
        self.assertIn("A rewritten second paragraph", self.evaluate(PAGE_TEXT))
        self.assertIn("120 fields", self.evaluate(PAGE_TEXT))
        self.leave()  # nothing typed, nothing saved
        self.wait_for(f"{PAGE_TEXT}.includes('121 fields')")

    def test_a_refused_save_holds_only_its_own_paragraph(self):
        self.build()
        self.write_in("quick")
        self.type("very ")
        self.chapter.write_text(TEXT.replace("lazy", "sleepy"), encoding="utf-8")  # changed on disk meanwhile
        self.leave()
        self.wait_for("document.querySelector('.pv-write-bar')")
        version = self.built_again(TEXT.replace("lazy", "sleepy").replace("A second paragraph", "A rewritten second paragraph"))
        self.wait_for(f"loaded.manuscript === {version}")
        self.assertIn("A rewritten second paragraph", self.evaluate(PAGE_TEXT))
        self.assertIn("very", self.evaluate("document.querySelector('.pv-write-failed').textContent"))

    def test_a_join_holds_only_its_own_paragraphs(self):
        text = "# Writing\n\nFirst paragraph.\n\nSecond paragraph.\n\nThird one in {{v:cohort.n}} fields.\n"
        self.build(text)
        self.write_in("Second")
        self.evaluate("""(() => { const w = document.createTreeWalker(writing.el, NodeFilter.SHOW_TEXT);
          let n; while ((n = w.nextNode())) if (n.data.includes('Second')) { getSelection().collapse(n, n.data.indexOf('Second')); return; } })()""")
        self.key("Backspace", 8)
        self.wait_for("!saves && !!writing")
        self.assertIn("First paragraph. Second paragraph.", self.source())
        self.new_value("121")
        version = self.built_again(self.source())
        self.wait_for(f"loaded.manuscript === {version}")
        self.assertIn("121 fields", self.evaluate(PAGE_TEXT))

    def test_undo_takes_back_a_break_after_a_build_changed_its_section(self):
        self.build()
        self.write_in("plain")
        self.caret_after("second")
        self.enter()
        self.saved_now()
        broken = self.source()
        self.new_value("121")  # the first paragraph changes, not the two being written
        version = self.built_again(broken)
        self.wait_for(f"loaded?.manuscript === {version}")
        self.assertIn("121 fields", self.evaluate(PAGE_TEXT))
        self.undo()
        self.settled()
        self.wait_for(f"{PAGE_TEXT}.includes('A second paragraph with plain words in it.')")
        self.assertEqual(self.source(), TEXT)
        self.assertIn("121 fields", self.evaluate(PAGE_TEXT))  # what the build brought stays


if __name__ == "__main__":
    unittest.main()
