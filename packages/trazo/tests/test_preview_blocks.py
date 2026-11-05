"""Writing on the preview: the marks of a page's blocks and saving an edit of one."""

import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _block_roundtrip as roundtrip
import _support

from trazo import edit_diff, preview, preview_block_save, preview_blocks, preview_edit_review, preview_render

SYNTAX = Path(__file__).with_name("_block_syntax.md")
CHAPTER = "manuscript/main/02_syntax.md"
INTRO = "manuscript/main/01_introduction.md"


class BlockTests(unittest.TestCase):
    def blocks(self, text):
        return [text[b.at:b.end] for b in preview_blocks.blocks_of(text)]

    def test_a_block_is_a_paragraph_or_a_list_item_without_its_marker(self):
        text = "One line\nand another.\n\n- First.\n- Second\n  wrapped.\n\n> Quoted.\n"
        self.assertEqual(self.blocks(text), ["One line\nand another.", "First.", "Second\n  wrapped.", "Quoted."])

    def test_text_a_mark_would_change_is_not_a_block(self):
        for text in ("---\n", "* * *\n", "Title\n=====\n", "<div>x</div>\n", "<!-- note -->\n", "| A line block\n",
                     "    code\n", "```\ncode\n```\n", "- [ ] Task\n", "(@) Example\n", "\\begin{center}\nx\n\\end{center}\n",
                     "![Alone](a.png)\n", "[ref]: https://example.org\n", "[^1]: Note\n", "Table: Caption\n", "# Heading\n"):
            self.assertEqual(self.blocks(text), [], text)

    def test_a_key_survives_changes_elsewhere_and_not_changes_to_its_block(self):
        before = preview_blocks.blocks_of("One.\n\nTwo.\n")
        after = preview_blocks.blocks_of("New.\n\nOne.\n\nTwo!\n")
        self.assertEqual(after[1].key, before[0].key)
        self.assertNotEqual(after[2].key, before[1].key)

    def test_equal_blocks_have_keys_of_their_own_that_any_change_ends(self):
        first, second = preview_blocks.blocks_of("Same.\n\nSame.\n")
        self.assertNotEqual(first.key, second.key)
        self.assertNotIn(first.key, [b.key for b in preview_blocks.blocks_of("Same.\n\nSame.\n\nNew.\n")])

    def test_each_atom_of_a_block_gets_a_span_of_its_own(self):
        text = "Code `x_1 * y`, math $a^2$, a note^[Text.], {{v:n}}, [@a], @b [p. 3] and ![i](a.png){height=1em}.\n"
        wrapped = edit_diff.with_marks(text, preview_blocks.marks(text, "a.md"))
        self.assertEqual(wrapped.count("pv-atom"), 7)
        self.assertIn('<span class="pv-atom" data-i="5">@b [p. 3]</span>', wrapped)

    def test_a_line_ending_is_not_part_of_a_block_in_a_windows_file(self):
        self.assertEqual(self.blocks("One\r\ntwo.\r\n\r\nThree.\r\n"), ["One\r\ntwo.", "Three."])


class PatchTests(unittest.TestCase):
    def patched(self, before, after, source, spans=()):
        return preview_block_save.patch(before, after, source, list(spans))

    def test_only_the_words_that_changed_change(self):
        self.assertEqual(self.patched("A big dog ran.", "A small dog ran.", "A *big*\ndog ran."), "A *small*\ndog ran.")

    def test_typed_text_reads_literally(self):
        self.assertEqual(self.patched("Two words.", "Two 5*3 a_b words.", "Two words."), r"Two 5\*3 a\_b words.")

    def test_a_chip_keeps_its_source_and_a_deleted_one_goes(self):
        chip = chr(0xE000)
        source = "See [@a] here."
        self.assertEqual(self.patched(f"See {chip} here.", f"Look {chip} here.", source, [(4, 8)]), "Look [@a] here.")
        self.assertEqual(self.patched(f"See {chip} here.", "See  here.", source, [(4, 8)]), "See  here.")

    def test_a_chip_cannot_move(self):
        a, b = chr(0xE000), chr(0xE001)
        self.assertIsNone(self.patched(f"{a} and {b}", f"{b} and {a}", "@a and @b", [(0, 2), (7, 9)]))

    def test_deleting_all_of_an_emphasis_takes_its_markers(self):
        self.assertEqual(self.patched("A big dog.", "A dog.", "A **big** dog."), "A dog.")

    def test_a_change_at_the_start_of_a_line_cannot_start_a_list(self):
        self.assertEqual(self.patched("Text.", "1. Text.", "Text."), r"1\. Text.")


@unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
class SaveTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        for name, value in (("EDITS_PATH", self.folder / "edits.json"),
                            ("FILE_HISTORY_PATH", self.folder / "file_history.json"),
                            ("WATCHER", {"synced": 0.0, "rebuild": set(), "outputs": []})):
            patcher = patch.object(preview, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.intro = _support.ROOT / INTRO
        self.addCleanup(self.intro.write_bytes, self.intro.read_bytes())
        self.chapter = _support.ROOT / CHAPTER
        self.addCleanup(self.chapter.unlink, missing_ok=True)

    def page(self, text=None):
        if text is not None:
            self.chapter.write_bytes(text.encode("utf-8"))
        error, _ = preview_render.render("manuscript", self.folder / "page")
        self.assertEqual(error, "")
        return (self.folder / "page" / "manuscript.html").read_text(encoding="utf-8")

    def block(self, text, words):
        """(key, rendered text) of the block of the chapter that holds `words`."""
        for (file, key), node in roundtrip.page_blocks(self.page(text)).items():
            rendered = roundtrip.squash(roundtrip.flat(node))
            if file == CHAPTER and words in rendered:
                return key, rendered
        self.fail(f"no block holds {words!r}")

    def save(self, key, before, after):
        return preview.post_route("/__block", lambda: {"file": CHAPTER, "h": key, "before": before, "after": after})()

    def test_the_marks_leave_the_page_as_it_was(self):
        self.chapter.write_bytes(SYNTAX.read_bytes())
        marked = self.page()
        with patch.object(preview_edit_review, "reader", lambda: (None, None)):
            plain = self.page()
        self.assertEqual(self.outline(marked), self.outline(plain))

    def outline(self, html):
        """A page as a list of tags and text, without the marks and their spaces."""
        def lines(node):
            for child in node.children:
                if isinstance(child, str):
                    yield child
                elif "pv-atom" in child.classes:
                    yield from lines(child)
                elif "pv-src" not in child.classes:
                    yield f"\0<{child.tag} {sorted(child.attrs.items())}>\0"
                    yield from lines(child)
                    yield f"\0</{child.tag}>\0"
        joined = re.sub(r"\s+", " ", "".join(lines(roundtrip.parse(html))))
        return [piece.strip() for piece in joined.split("\0") if piece.strip()]

    def test_a_save_writes_the_word_and_nothing_else(self):
        text = "Before.\n\nThe quick *brown* fox [@smith2020] ran {{v:cohort.n}} miles.\n\nAfter.\n"
        key, rendered = self.block(text, "quick")
        answer = self.save(key, rendered, rendered.replace("quick", "slow"))
        self.assertTrue(answer["saved"])
        self.assertEqual(self.chapter.read_text(encoding="utf-8"), text.replace("quick", "slow"))

    def test_two_words_join_across_a_source_line_break(self):
        text = "One\n  two.\n"
        key, rendered = self.block(text, "One")
        self.assertTrue(self.save(key, rendered, "Onetwo.")["saved"])
        self.assertEqual(self.chapter.read_text(encoding="utf-8"), "Onetwo.\n")

    def test_a_stale_save_cannot_land_on_an_equal_paragraph(self):
        key, rendered = self.block("Same.\n\nOther.\n\nSame.\n", "Same")
        self.chapter.write_text("Changed.\n\nSame.\n\nSame.\n", encoding="utf-8")
        with self.assertRaises(preview.Conflict):
            self.save(key, rendered, "Mine.")
        self.assertEqual(self.chapter.read_text(encoding="utf-8"), "Changed.\n\nSame.\n\nSame.\n")

    def test_a_save_is_the_authors_own_and_can_be_undone(self):
        text = "The quick fox.\n"
        key, rendered = self.block(text, "quick")
        self.save(key, rendered, "The slow fox.")
        self.assertEqual(preview.edits_payload("manuscript")["edits"], [])
        preview.change_source({"op": "undo", "file": CHAPTER})
        self.assertEqual(self.chapter.read_text(encoding="utf-8"), text)

    def test_a_save_keeps_a_windows_files_line_endings(self):
        text = "First line\r\nof the block.\r\n\r\nNext.\r\n"
        key, rendered = self.block(text, "First")
        self.save(key, rendered, rendered.replace("First", "Early"))
        self.assertEqual(self.chapter.read_bytes().decode("utf-8"), text.replace("First", "Early"))

    def test_an_edit_in_another_paragraph_of_the_file_does_not_stop_a_save(self):
        text = "One word.\n\nTwo words.\n"
        key, rendered = self.block(text, "Two")
        self.chapter.write_text("One changed word.\n\nTwo words.\n", encoding="utf-8")
        self.save(key, rendered, "Three words.")
        self.assertEqual(self.chapter.read_text(encoding="utf-8"), "One changed word.\n\nThree words.\n")

    def test_a_change_to_the_same_paragraph_is_a_conflict_and_writes_nothing(self):
        text = "Two words.\n"
        key, rendered = self.block(text, "Two")
        self.chapter.write_text("Two other words.\n", encoding="utf-8")
        with self.assertRaises(preview.Conflict):
            self.save(key, rendered, "Three words.")
        self.assertEqual(self.chapter.read_text(encoding="utf-8"), "Two other words.\n")

    def test_keeping_mine_merges_the_edit_into_the_paragraph_as_it_is_now(self):
        text = "The quick brown fox jumps over the lazy dog.\n"
        key, rendered = self.block(text, "quick")
        self.chapter.write_text("The quick brown fox jumps over the sleepy dog.\n", encoding="utf-8")
        answer = preview.post_route("/__block", lambda: {
            "file": CHAPTER, "h": key, "before": rendered, "after": rendered.replace("quick", "slow"),
            "source": text.strip()})()
        self.assertTrue(answer["saved"])
        self.assertEqual(self.chapter.read_text(encoding="utf-8"), "The slow brown fox jumps over the sleepy dog.\n")

    def test_keeping_mine_needs_the_paragraph_to_still_be_there(self):
        text = "The quick brown fox jumps over the lazy dog.\n"
        key, rendered = self.block(text, "quick")
        self.chapter.write_text("Nothing at all alike here, 12345.\n", encoding="utf-8")
        with self.assertRaises(preview.Conflict):
            preview.post_route("/__block", lambda: {"file": CHAPTER, "h": key, "before": rendered, "after": "Mine.",
                                                    "source": text.strip()})()

    def test_the_viewer_can_ask_for_a_blocks_markdown(self):
        text = "The *quick* fox.\n"
        key, _ = self.block(text, "quick")
        self.assertEqual(preview.get_block(CHAPTER, key)["markdown"], "The *quick* fox.")
        self.chapter.write_text("Other.\n", encoding="utf-8")
        with self.assertRaises(preview.Conflict):
            preview.get_block(CHAPTER, key)

    def test_a_paragraph_the_page_shows_differently_is_refused_with_its_markdown(self):
        text = "A sentence here.\n"
        key, _ = self.block(text, "sentence")
        answer = self.save(key, "Not what the Markdown says.", "Something else.")
        self.assertEqual(answer["markdown"], "A sentence here.")
        self.assertIn("refused", answer)
        self.assertEqual(self.chapter.read_text(encoding="utf-8"), text)

    def test_markdown_replaces_the_block_as_written(self):
        text = "One word.\n"
        key, _ = self.block(text, "word")
        preview.post_route("/__block", lambda: {"file": CHAPTER, "h": key, "markdown": "Some *new* [@smith2020] text."})()
        self.assertEqual(self.chapter.read_text(encoding="utf-8"), "Some *new* [@smith2020] text.\n")

    def test_typed_quotes_and_dashes_are_saved_though_the_page_makes_them_smart(self):
        text = "A plain sentence.\n"
        key, rendered = self.block(text, "plain")
        self.assertTrue(self.save(key, rendered, "A \"plain\" sentence -- isn't it...")["saved"])

    def test_every_edit_of_awkward_syntax_lands_exactly_or_is_refused(self):
        self.chapter.write_bytes(SYNTAX.read_bytes())
        results, wrong = roundtrip.run("manuscript", 3, self.folder / "round-trip")
        self.assertEqual(wrong, [])
        right = sum(results[kind, "right"] for kind in roundtrip.COMMON)
        refused = sum(results[kind, "refused"] for kind in roundtrip.COMMON)
        self.assertGreater(right, 0)
        self.assertGreaterEqual(right / (right + refused), 0.9, results)


if __name__ == "__main__":
    unittest.main()
