"""Saves that change a file's blocks: breaks, joins, deletions and headings, and what Cmd+Z takes."""

import unittest
from unittest.mock import patch

import _block_roundtrip as roundtrip
from _preview_saving import CHAPTER, SaveCase

from trazo import preview


class StructureTests(SaveCase):
    def split(self, text, rendered_cut, expected):
        key, rendered = self.block(text, "split")
        at = rendered.index(rendered_cut)
        answer = self.save(key, rendered, rendered[:at].rstrip() + roundtrip.BREAK + rendered[at:])
        self.assertTrue(answer.get("saved"), answer)
        self.assertEqual(self.chapter.read_bytes().decode("utf-8"), expected)
        return answer

    def test_a_paragraph_breaks_into_two(self):
        answer = self.split("One to split. Two *with* [@smith2020].\n", "Two",
                            "One to split.\n\nTwo *with* [@smith2020].\n")
        self.assertEqual(len(answer["parts"]), 2)

    def test_a_list_item_breaks_into_two_items(self):
        self.split("- One to split. Two.\n- Three.\n", "Two", "- One to split.\n- Two.\n- Three.\n")
        self.split("1. One to split. Two.\n", "Two", "1. One to split.\n2. Two.\n")

    def test_a_quoted_paragraph_breaks_inside_the_quote(self):
        self.split("> One to split. Two.\n", "Two", "> One to split.\n>\n> Two.\n")

    def test_a_break_keeps_a_windows_files_line_endings(self):
        self.split("One to split. Two.\r\n\r\nNext.\r\n", "Two", "One to split.\r\n\r\nTwo.\r\n\r\nNext.\r\n")

    def test_text_after_a_break_cannot_start_a_list(self):
        self.split("One to split - two.\n", "- two", "One to split\n\n\\- two.\n")

    def test_a_break_inside_a_bold_is_refused(self):
        text = "One **to split** here.\n"
        key, rendered = self.block(text, "split")
        answer = self.save(key, rendered, "One to" + roundtrip.BREAK + "split here.")
        self.assertIn("refused", answer)
        self.assertEqual(self.chapter.read_text(encoding="utf-8"), text)

    def test_a_heading_takes_new_text_and_keeps_its_id(self):
        key, rendered = self.block("# Old title {#sec:x .y}\n\nText.\n", "Old title")
        self.assertTrue(self.save(key, rendered, "New title")["saved"])
        self.assertEqual(self.chapter.read_text(encoding="utf-8"), "# New title {#sec:x .y}\n\nText.\n")

    def test_a_heading_cannot_be_emptied_or_broken(self):
        text = "# Old title\n\nText.\n"
        for after in ("", "Old" + roundtrip.BREAK + "title"):
            key, rendered = self.block(text, "Old title")
            self.assertIn("refused", self.save(key, rendered, after))
            self.assertEqual(self.chapter.read_text(encoding="utf-8"), text)
        for markdown in ("Title\n\nNew paragraph", "   "):
            key, _ = self.block(text, "Old title")
            answer = preview.post_route("/__block", lambda: {"file": CHAPTER, "h": key, "markdown": markdown})()
            self.assertIn("refused", answer)
            self.assertEqual(self.chapter.read_text(encoding="utf-8"), text)

    def test_keeping_a_heading_goes_to_a_heading(self):
        text = "# The quick brown fox\n\nThe quick brown fox.\n"
        key, rendered = self.block(text, "The quick brown fox")
        self.assertTrue(key.startswith("h"), key)
        self.chapter.write_text("# The quick brown wolf\n\nThe quick brown fox.\n", encoding="utf-8")
        preview.post_route("/__block", lambda: {"file": CHAPTER, "h": key, "before": rendered,
                                                "after": "The slow brown fox", "keep": True})()
        self.assertEqual(self.chapter.read_text(encoding="utf-8"), "# The slow brown wolf\n\nThe quick brown fox.\n")

    def test_cmd_z_takes_the_latest_of_a_save_and_a_comment_in_this_document(self):
        comment = {"op": "add", "quote": "Text", "para": "x", "section": "", "comment": "Look"}
        with patch.object(preview, "COMMENTS_PATH", self.folder / "comments.json"), \
                patch.object(preview, "HISTORY_PATH", self.folder / "history.json"):
            self.assertEqual(preview.latest_change("manuscript")["kind"], "")
            self.chapter.write_text("Text.\n", encoding="utf-8")
            state = preview.get_source(CHAPTER)
            preview.change_source({"file": CHAPTER, "text": "New.\n", "base_sha256": state["sha256"]})
            self.assertEqual(preview.latest_change("manuscript"), {"kind": "file", "file": CHAPTER})
            preview.change_comments({**comment, "doc": "supplementary"})
            self.assertEqual(preview.latest_change("manuscript")["kind"], "file")
            preview.change_comments({**comment, "doc": "manuscript"})  # as like as not in the second of the save
            self.assertEqual(preview.latest_change("manuscript")["kind"], "comment")

    def test_an_empty_paragraph_leaves_the_file(self):
        for text, expected in (("One.\n\nGone.\n\nThree.\n", "One.\n\nThree.\n"), ("- One.\n- Gone.\n- Three.\n", "- One.\n- Three.\n"),
                               ("One.\n\nGone.\n", "One.\n\n")):
            key, rendered = self.block(text, "Gone")
            answer = self.save(key, rendered, "")
            self.assertEqual(answer["parts"], [])
            self.assertEqual(self.chapter.read_text(encoding="utf-8"), expected)

    def join(self, text, words, **extra):
        blocks = {roundtrip.squash(roundtrip.flat(node)): key for (file, key), node in roundtrip.page_blocks(self.page(text)).items()
                  if file == CHAPTER}
        left, right = words
        return preview.post_route("/__block", lambda: {"file": CHAPTER, "h": blocks[right], "before": right, "after": right,
                                                       "previous": left, "into": blocks[left], **extra})()

    def test_a_paragraph_joins_the_one_before_it(self):
        answer = self.join("One *word*.\n\nTwo words.\n\nThree.\n", ("One word.", "Two words."))
        self.assertTrue(answer["saved"])
        self.assertEqual(self.chapter.read_text(encoding="utf-8"), "One *word*. Two words.\n\nThree.\n")
        self.assertEqual(len(answer["parts"]), 1)

    def test_a_list_item_joins_the_one_before_it(self):
        self.join("- One.\n- Two.\n", ("One.", "Two."))
        self.assertEqual(self.chapter.read_text(encoding="utf-8"), "- One. Two.\n")

    def test_paragraphs_with_something_between_them_do_not_join(self):
        text = "One.\n\n![Figure](a.png)\n\nTwo.\n"
        answer = self.join(text, ("One.", "Two."))
        self.assertIn("refused", answer)
        self.assertEqual(self.chapter.read_text(encoding="utf-8"), text)


if __name__ == "__main__":
    unittest.main()
