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

    def test_keeping_mine_is_refused_between_equal_paragraphs(self):
        text = "Same words here.\n\nOther.\n\nSame words here.\n"
        for changed in ("Same words here.\n\nChanged.\n\nSame words here.\n",
                        "Same words here, changed.\n\nOther.\n\nSame words here.\n"):
            key, rendered = self.block(text, "Same")
            self.chapter.write_text(changed, encoding="utf-8")
            with self.assertRaises(preview.Conflict):
                preview.post_route("/__block", lambda: {"file": CHAPTER, "h": key, "before": rendered,
                                                        "after": "Mine words here.", "keep": True})()
            self.assertEqual(self.chapter.read_text(encoding="utf-8"), changed)

    def test_keeping_a_deletion_is_refused_if_the_file_changed_the_paragraph(self):
        text = "Gone words here for all.\n\nNext.\n"
        key, rendered = self.block(text, "Gone")
        changed = "Gone words here for all, and more.\n\nNext.\n"
        self.chapter.write_text(changed, encoding="utf-8")
        answer = preview.post_route("/__block", lambda: {"file": CHAPTER, "h": key, "before": rendered, "after": "", "keep": True})()
        self.assertIn("refused", answer)
        self.assertEqual(self.chapter.read_text(encoding="utf-8"), changed)

    def test_keeping_a_break_checks_each_paragraph(self):
        text = "One sentence. Two words here.\n"
        key, rendered = self.block(text, "sentence")
        changed = "**One sentence. Two words here.**\n"
        self.chapter.write_text(changed, encoding="utf-8")
        answer = preview.post_route("/__block", lambda: {
            "file": CHAPTER, "h": key, "before": rendered,
            "after": "One sentence." + roundtrip.BREAK + "Two words here.", "keep": True})()
        self.assertIn("refused", answer)
        self.assertEqual(self.chapter.read_text(encoding="utf-8"), changed)

    def test_keeping_mine_is_refused_if_the_merge_would_read_wrong(self):
        text = "A big brown fox jumps over the lazy dog.\n"
        key, rendered = self.block(text, "brown")
        changed = "A **big brown** fox jumps over the lazy dog.\n"
        self.chapter.write_text(changed, encoding="utf-8")
        answer = preview.post_route("/__block", lambda: {"file": CHAPTER, "h": key, "before": rendered,
                                                         "after": "A fox jumps over the lazy dog.", "keep": True})()
        self.assertIn("refused", answer)
        self.assertEqual(self.chapter.read_text(encoding="utf-8"), changed)

    def test_a_list_item_that_holds_more_cannot_be_emptied(self):
        for text in ("- Gone.\n  - Child.\n- Next.\n", "1. Gone.\n\n   More of it.\n\n2. Next.\n",
                     "> - Gone.\n>   - Child.\n> - Next.\n"):
            key, rendered = self.block(text, "Gone")
            self.assertIn("refused", self.save(key, rendered, ""))
            self.assertEqual(self.chapter.read_text(encoding="utf-8"), text)

    def test_only_what_the_page_showed_can_be_deleted(self):
        text = "Gone here.\n"
        key, _ = self.block(text, "Gone")
        self.assertIn("refused", self.save(key, "Something else.", ""))
        self.assertEqual(self.chapter.read_text(encoding="utf-8"), text)

    def test_a_paragraph_with_a_comment_the_page_does_not_show_is_not_deleted(self):
        text = "Gone <!-- a note to self --> here.\n"
        key, rendered = self.block(text, "Gone")
        self.assertIn("refused", self.save(key, rendered, ""))
        self.assertEqual(self.chapter.read_text(encoding="utf-8"), text)

    def test_a_citation_and_a_colon_are_no_link_definition(self):
        key, rendered = self.block("[@smith2020] said words.\n\nNext words.\n", "said")
        self.assertTrue(self.save(key, rendered, rendered.replace(" said", ": said"))["saved"])
        key, rendered = self.block(self.chapter.read_text(encoding="utf-8"), "Next")
        self.assertTrue(self.save(key, rendered, rendered.replace("Next", "Last"))["saved"])

    def test_a_paragraph_before_a_fence_leaves_the_code(self):
        key, rendered = self.block("Gone here.\n```python\nkeep()\n```\n\nNext.\n", "Gone")
        self.save(key, rendered, "")
        self.assertIn("keep()", self.chapter.read_text(encoding="utf-8"))

    def test_a_break_that_leaves_text_the_page_cannot_write_in_is_refused(self):
        text = "One to split[^1] here.\n\n[^1]: A note.\n"
        key, rendered = self.block(text, "split")
        at = next(i for i, ch in enumerate(rendered) if ord(ch) >= 0xE000)
        answer = self.save(key, rendered, rendered[:at] + roundtrip.BREAK + rendered[at:])
        self.assertIn("refused", answer)
        self.assertEqual(self.chapter.read_text(encoding="utf-8"), text)

    def test_a_quote_of_two_paragraphs_breaks_into_keys_of_their_own(self):
        answer = self.split("> One to split. Two.\n", "Two", "> One to split.\n>\n> Two.\n")
        self.assertEqual(len(answer["parts"]), 2)

    def test_a_title_that_reads_as_a_list_alone_takes_new_text(self):
        key, rendered = self.block("## 1. Old title\n\nText.\n", "Old title")
        self.assertTrue(self.save(key, rendered, rendered.replace("Old", "New"))["saved"])
        self.assertEqual(self.chapter.read_text(encoding="utf-8"), "## 1. New title\n\nText.\n")

    def test_emphasis_that_holds_only_a_chip_does_not_stop_a_save(self):
        key, rendered = self.block("**[@smith2020]** said words.\n", "said")
        self.assertTrue(self.save(key, rendered, rendered.replace("said", "wrote"))["saved"])
        self.assertEqual(self.chapter.read_text(encoding="utf-8"), "**[@smith2020]** wrote words.\n")

    def test_a_link_in_the_page_is_a_chip_that_does_not_stop_a_save(self):
        key, rendered = self.block("See [the methods](#sec:x) for words.\n", "words")
        self.assertTrue(self.save(key, rendered, rendered.replace("words", "more"))["saved"])
        self.assertEqual(self.chapter.read_text(encoding="utf-8"), "See [the methods](#sec:x) for more.\n")

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

    def test_successive_cmd_z_go_back_through_both_histories_in_order(self):
        comment = {"op": "add", "doc": "manuscript", "quote": "Text", "para": "x", "section": "", "comment": "Look"}

        def save(text):
            preview.change_source({"file": CHAPTER, "text": text, "base_sha256": preview.get_source(CHAPTER)["sha256"]})
        with patch.object(preview, "COMMENTS_PATH", self.folder / "comments.json"), \
                patch.object(preview, "HISTORY_PATH", self.folder / "history.json"), \
                patch("time.strftime", return_value="2026-11-06 10:00:00"):
            self.chapter.write_text("Text.\n", encoding="utf-8")
            save("One.\n")
            preview.change_comments(comment)
            save("Two.\n")
            self.assertEqual(preview.latest_change("manuscript")["kind"], "file")
            preview.change_source({"op": "undo", "file": CHAPTER})
            self.assertEqual(preview.latest_change("manuscript")["kind"], "comment")
            preview.change_comments({"op": "undo", "doc": "manuscript"})
            self.assertEqual(preview.latest_change("manuscript")["kind"], "file")

    def test_a_change_to_comments_of_other_documents_is_not_undone_here(self):
        comment = {"op": "add", "quote": "Text", "para": "x", "section": "", "comment": "Look", "kind": "highlight"}
        with patch.object(preview, "COMMENTS_PATH", self.folder / "comments.json"), \
                patch.object(preview, "HISTORY_PATH", self.folder / "history.json"):
            first = preview.change_comments({**comment, "doc": "supplementary"})["comments"][0]["id"]
            self.chapter.write_text("Text.\n", encoding="utf-8")
            preview.change_source({"file": CHAPTER, "text": "New.\n", "base_sha256": preview.get_source(CHAPTER)["sha256"]})
            preview.change_comments({"op": "delete", "ids": [first]})
            self.assertEqual(preview.latest_change("manuscript"), {"kind": "file", "file": CHAPTER})

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
