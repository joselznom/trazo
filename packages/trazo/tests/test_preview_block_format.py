"""Bold, italic and underline on the preview's text: the markers in the Markdown and the check of the result."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _block_roundtrip as roundtrip
import _support

from trazo import preview, preview_render

CHAPTER = "manuscript/main/02_format.md"
SYNTAX = Path(__file__).with_name("_block_syntax.md")


@unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
class FormatTests(unittest.TestCase):
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
        self.chapter = _support.ROOT / CHAPTER
        self.addCleanup(self.chapter.unlink, missing_ok=True)

    def toggle(self, text, words, style):
        self.chapter.write_bytes(text.encode("utf-8"))
        self.assertEqual(preview_render.render("manuscript", self.folder / "page")[0], "")
        html = (self.folder / "page" / "manuscript.html").read_text(encoding="utf-8")
        for (file, key), node in roundtrip.page_blocks(html).items():
            rendered = roundtrip.squash(roundtrip.flat(node))
            if file == CHAPTER and words in rendered:
                start = rendered.index(words)
                request = {"file": file, "h": key, "before": rendered, "style": style, "from": start, "to": start + len(words)}
                return preview.post_route("/__block-format", lambda: request)()
        self.fail(f"no block holds {words!r}")

    def source(self):
        return self.chapter.read_text(encoding="utf-8")

    def test_plain_text_takes_the_style(self):
        for style, marked in (("strong", "A **big** dog.\n"), ("em", "A *big* dog.\n")):
            answer = self.toggle("A big dog.\n", "big", style)
            self.assertTrue(answer["on"])
            self.assertEqual(self.source(), marked)

    def test_text_with_the_style_loses_it(self):
        for text, style in (("A **big** dog.\n", "strong"), ("A __big__ dog.\n", "strong"), ("A *big* dog.\n", "em"), ("A _big_ dog.\n", "em")):
            answer = self.toggle(text, "big", style)
            self.assertFalse(answer["on"], answer)
            self.assertEqual(self.source(), "A big dog.\n")

    def test_bold_and_italic_add_up_and_come_off_one_at_a_time(self):
        self.toggle("A *big* dog.\n", "big", "strong")
        self.assertEqual(self.source(), "A ***big*** dog.\n")
        self.toggle("A ***big*** dog.\n", "big", "em")
        self.assertEqual(self.source(), "A **big** dog.\n")
        self.toggle("A ***big*** dog.\n", "big", "strong")
        self.assertEqual(self.source(), "A *big* dog.\n")

    def test_part_of_a_bold_loses_it_and_the_rest_stays_bold(self):
        self.toggle("A **very big black** dog.\n", "big", "strong")
        self.assertEqual(self.source(), "A **very** big **black** dog.\n")
        self.toggle("A **very big** dog.\n", "very", "strong")
        self.assertEqual(self.source(), "A very **big** dog.\n")

    def test_text_holding_some_of_the_style_takes_all_of_it(self):
        self.toggle("A **big** black dog.\n", "A big black", "strong")
        self.assertEqual(self.source(), "**A big black** dog.\n")

    def test_a_chip_inside_keeps_its_source(self):
        self.toggle("See [@smith2020] now.\n", "See", "em")
        self.assertEqual(self.source(), "*See* [@smith2020] now.\n")
        chip = chr(0xE000)
        self.toggle("See [@smith2020] now.\n", f"See {chip} now", "strong")
        self.assertEqual(self.source(), "**See [@smith2020] now**.\n")

    def test_spaces_at_the_edges_of_a_selection_stay_out(self):
        self.toggle("A big dog.\n", " big ", "strong")
        self.assertEqual(self.source(), "A **big** dog.\n")

    def test_every_format_edit_of_awkward_syntax_lands_exactly_or_is_refused(self):
        self.chapter.write_bytes(SYNTAX.read_bytes())
        results, wrong = roundtrip.run("manuscript", len(roundtrip.FORMATS), self.folder / "round-trip", tuple(roundtrip.FORMATS))
        self.assertEqual(wrong, [])
        self.assertGreater(sum(results[kind, "right"] for kind in roundtrip.FORMATS), 50, results)

    def test_a_selection_across_the_edge_of_a_bold_is_refused(self):
        text = "A **big** dog.\n"
        answer = self.toggle(text, "A bi", "em")
        self.assertIn("refused", answer)
        self.assertEqual(self.source(), text)

    def test_underline_is_a_span_that_comes_off_whole_or_in_part(self):
        answer = self.toggle("A big dog.\n", "big", "underline")
        self.assertTrue(answer["on"])
        self.assertEqual(self.source(), "A [big]{.underline} dog.\n")
        for text in ("A [big]{.underline} dog.\n", "A [big]{.ul} dog.\n"):
            answer = self.toggle(text, "big", "underline")
            self.assertFalse(answer["on"], answer)
            self.assertEqual(self.source(), "A big dog.\n")
        self.toggle("A [very big black]{.underline} dog.\n", "big", "underline")
        self.assertEqual(self.source(), "A [very]{.underline} big [black]{.underline} dog.\n")

    def test_underline_comes_off_bold_text_and_several_spans_at_once(self):
        self.toggle("A [**big black**]{.underline} dog.\n", "big black", "underline")
        self.assertEqual(self.source(), "A **big black** dog.\n")
        self.toggle("A [big]{.underline} [black]{.underline} dog.\n", "big black", "underline")
        self.assertEqual(self.source(), "A big black dog.\n")
        self.toggle("A [very big]{.underline} [black cat]{.underline} dog.\n", "big black", "underline")
        self.assertEqual(self.source(), "A [very]{.underline} big black [cat]{.underline} dog.\n")

    def test_underline_adds_up_with_bold_and_swallows_the_underlines_inside(self):
        self.toggle("A **big** dog.\n", "big", "underline")
        self.assertEqual(self.source(), "A **[big]{.underline}** dog.\n")
        self.toggle("A [big]{.underline} black dog.\n", "A big black", "underline")
        self.assertEqual(self.source(), "[A big black]{.underline} dog.\n")

    def test_underline_keeps_links_code_and_citations(self):
        chip = chr(0xE000)
        for text, words, marked in (
                ("See [the site](https://x.org) now.\n", "the site", "See [[the site]{.underline}](https://x.org) now.\n"),
                ("See [the site](https://x.org) now.\n", "See", "[See]{.underline} [the site](https://x.org) now.\n"),
                ("Run `a]b` now.\n", f"Run {chip} now", "[Run `a]b` now]{.underline}.\n"),
                ("See [@smith2020] now.\n", f"See {chip} now", "[See [@smith2020] now]{.underline}.\n"),
                ("See [the `x`](https://x.org) now.\n", f"See the {chip} now",
                 "[See [the `x`](https://x.org) now]{.underline}.\n")):
            answer = self.toggle(text, words, "underline")
            self.assertTrue(answer.get("saved"), answer)
            self.assertEqual(self.source(), marked)
            answer = self.toggle(marked, words, "underline")  # and back, finding the span's own bracket
            self.assertFalse(answer.get("on", True), answer)
            self.assertEqual(self.source(), text)

    def test_a_selection_across_the_edge_of_an_underline_joins_it(self):
        answer = self.toggle("A [big]{.underline} dog.\n", "A bi", "underline")
        self.assertTrue(answer["on"], answer)
        self.assertEqual(self.source(), "[A big]{.underline} dog.\n")


if __name__ == "__main__":
    unittest.main()
