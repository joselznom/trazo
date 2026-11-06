"""Edits between two versions of a Markdown file and the preview's marks for them."""

import re
import shutil
import subprocess
import unittest

import _support  # noqa: F401  (points trazo at the test project before it is imported)

from trazo import edit_diff

BASE = """# Results

The score increased over the follow-up in both groups [@first].

Patients in the treatment group had a higher score.

- first item
- second item

| a | b |
|---|---|
| 1 | 2 |

![A caption.](figures/x.png){#fig:x}
"""
CURRENT = """# Results

The score rose steadily over the follow-up in both groups [@first; @second].

Patients in the treatment group reached a higher score.

These differences persisted after adjustment.

- first item
- second changed item

| a | b |
|---|---|
| 1 | 3 |

![A caption, edited.](figures/x.png){#fig:x}
"""


def html(markdown):
    return subprocess.run(["pandoc", "-f", "markdown", "-t", "html", "--wrap=none"], input=markdown,
                          capture_output=True, text=True, check=True).stdout


class EditsTests(unittest.TestCase):
    def test_accepting_or_rejecting_every_edit_gives_the_other_text(self):
        found = edit_diff.edits(BASE, CURRENT)
        self.assertEqual([(e.old, e.new) for e in found][:3], [
            ("increased", "rose steadily"), ("", "; @second"), ("had", "reached")])
        self.assertEqual(edit_diff.reject(CURRENT, found), BASE)
        self.assertEqual(edit_diff.accept(BASE, found), CURRENT)
        # One edit at a time, in any order, too.
        text = CURRENT
        for e in reversed(found):
            text = edit_diff.reject(text, [next(x for x in edit_diff.edits(BASE, text) if x.old == e.old and x.new == e.new)])
        self.assertEqual(text, BASE)

    def test_ids_stay_while_the_text_around_them_does(self):
        before = {e.new: e.id for e in edit_diff.edits(BASE, CURRENT)}
        later = CURRENT.replace("- first item", "- first good item")
        after = {e.new: e.id for e in edit_diff.edits(BASE, later)}
        self.assertEqual(after["rose steadily"], before["rose steadily"])
        self.assertEqual(edit_diff.edits(BASE, CURRENT)[0].line, 3)

    def test_spacing_alone_is_no_edit_unless_it_splits_a_paragraph(self):
        self.assertEqual(edit_diff.edits("One two\nthree.\n", "One two three.\n"), [])
        self.assertEqual(len(edit_diff.edits("One. Two.\n", "One.\n\nTwo.\n")), 1)

    def test_converting_line_endings_is_no_edit(self):
        crlf = BASE.replace("\n", "\r\n")
        self.assertEqual(edit_diff.edits(BASE, crlf), [])
        [edit] = edit_diff.edits(BASE, crlf.replace("increased", "rose"))
        self.assertEqual((edit.old, edit.new), ("increased", "rose"))

    def test_a_last_line_without_an_ending_keeps_its_place(self):
        found = edit_diff.edits("First", "First\nSecond\n")
        self.assertEqual(edit_diff.accept("First", found), "First\nSecond\n")
        self.assertEqual(edit_diff.reject("First\nSecond\n", found), "First")
        found = edit_diff.edits("First\nSecond\n", "First")
        self.assertEqual(edit_diff.reject("First", found), "First\nSecond\n")
        self.assertEqual(edit_diff.merge("First\nSecond\n", "First", "First\nThird\n").count("First"), 1)

    def test_a_very_long_changed_run_still_gives_exact_edits(self):
        base = "".join(f"line {i} of the text\n" for i in range(1500))
        current = "".join(f"> line {i} of the text\n" for i in range(1500))
        found = edit_diff.edits(base, current)
        self.assertEqual(edit_diff.reject(current, found), base)
        self.assertEqual(edit_diff.accept(base, found), current)

    def test_a_merge_keeps_both_sides_and_theirs_wins_where_both_changed(self):
        # The author's save (before -> after) goes into the baseline; the
        # pending edits it did not touch stay pending.
        after = CURRENT.replace("first item", "first good item")
        merged = edit_diff.merge(CURRENT, BASE, after)
        self.assertEqual(merged, BASE.replace("first item", "first good item"))
        self.assertEqual(len(edit_diff.edits(merged, after)), len(edit_diff.edits(BASE, CURRENT)))
        rewritten = CURRENT.replace("rose steadily", "climbed")
        merged = edit_diff.merge(CURRENT, BASE, rewritten)
        self.assertIn("The score climbed over", merged)
        self.assertNotIn("climbed", " ".join(e.new for e in edit_diff.edits(merged, rewritten)))


    def test_an_authors_change_next_to_a_pending_edit_leaves_it_pending(self):
        self.assertEqual(edit_diff.merge("Score rose.\n", "Score fell.\n", "Score rose!\n"), "Score fell!\n")
        self.assertEqual(edit_diff.merge("Score rose.\n", "Score fell.\n", "Score rose, indeed.\n"),
                         "Score fell, indeed.\n")

    def test_an_authors_change_in_a_very_long_paragraph_leaves_the_edits_around_it_pending(self):
        words = ["the", "word"] * 1500  # no rare word for the diff to hold on to
        changed = lambda *pairs: " ".join(dict(pairs).get(i, w) for i, w in enumerate(words)) + "\n"
        base, current = changed(), changed((800, "new"), (2400, "final"))
        saved = changed((800, "new"), (1600, "kept"), (2400, "final"))
        merged = edit_diff.merge(current, base, saved)
        self.assertIn("kept", merged)
        pending = " ".join(e.new for e in edit_diff.edits(merged, saved))
        self.assertIn("new", pending)
        self.assertIn("final", pending)

    def test_an_authors_correction_in_a_pending_insertion_takes_only_that_correction(self):
        new = "One first text.\n\nTwo second text.\n"
        fixed = new.replace("first", "1st")
        merged = edit_diff.merge(new, "", fixed)
        self.assertIn("1st", merged)
        self.assertNotIn("Two", merged)
        self.assertEqual("".join(e.new for e in edit_diff.edits(merged, fixed)).count("Two second text."), 1)

    def test_the_same_edit_in_two_files_has_two_ids(self):
        [a], [b] = edit_diff.edits("x y\n", "x z\n", "a.md"), edit_diff.edits("x y\n", "x z\n", "b.md")
        self.assertNotEqual(a.id, b.id)


@unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
class MarksTests(unittest.TestCase):
    def marked(self, base, current):
        found = edit_diff.edits(base, current)
        return found, edit_diff.marked(current, found)

    def test_marks_wrap_the_changed_words_and_change_nothing_else(self):
        found, text = self.marked(BASE, CURRENT)
        page = html(text)
        by_new = {e.new.strip(): e for e in found}
        rose = by_new["rose steadily"].id
        self.assertIn(f'<span class="pv-edit-del" data-edit="{rose}">increased</span>'
                      f'<span class="pv-edit-ins" data-edit="{rose}">rose steadily</span> over', page)
        # Spaces stay outside the spans, so the words do not run together.
        self.assertIn('second <span class="pv-edit-ins" data-edit="' + by_new["changed"].id + '">changed</span> item', page)
        self.assertIn('caption<span class="pv-edit-ins" data-edit="' + by_new[", edited"].id + '">, edited</span>.', page)
        new = next(e for e in found if "persisted" in e.new)
        self.assertRegex(page, rf'<p><span class="pv-blk pv-new" data-edit="{new.id}"></span>These differences')
        # The citation, the table and the figure still are what Pandoc makes of the plain text.
        unmarked = html(CURRENT)
        strip = lambda h: re.sub(r'<span class="pv-(?:edit-ins|blk)[^"]*" data-edit="[^"]*">(.*?)</span>', r"\1",
                                 re.sub(r'<span class="pv-edit-del[^"]*" data-edit="[^"]*">.*?</span>', "", h))
        self.assertEqual(strip(page), unmarked)
        self.assertNotIn(by_new["3"].id, text)  # a table cell: no mark
        # Inside a citation's brackets: the paragraph is marked as changed instead.
        self.assertIn(f'[]{{.pv-blk data-edit="{by_new["; @second"].id}"}}The score', text)

    def test_lines_trazo_reads_before_pandoc_get_no_mark(self):
        base = "# Title\n\n!include a.md\n\nMRI\n:   Imaging\n"
        current = "# New title\n\n!include b.md\n\nfMRI\n:   Imaging\n"
        found, text = self.marked(base, current)
        self.assertEqual(len(found), 3)
        self.assertEqual(text, current)

    def test_a_changed_block_marker_keeps_its_block(self):
        found, text = self.marked("1. First item\n2. Second\n", "3. First item\n2. Second\n")
        self.assertIn("<ol", html(text))
        self.assertIn(f'[]{{.pv-blk data-edit="{found[0].id}"}}First item', text)
        found, text = self.marked("::: note\nText.\n:::\n", "::: table-note\nText.\n:::\n")
        self.assertEqual(text, "::: table-note\nText.\n:::\n")
        found, text = self.marked("Quote\n", ">Quote\n")
        self.assertIn("<blockquote>", html(text))

    def test_an_edit_that_ends_on_a_list_marker_keeps_the_list(self):
        found, text = self.marked("1. Old Two\n", "1. New words:\n   - Two\n")
        self.assertIn("<ul>", html(text))

    def test_an_edit_across_the_end_of_a_bold_keeps_it(self):
        found, text = self.marked("**Abc.** one\n", "**Abc!** two\n")
        self.assertIn("<strong>", html(text))

    def test_an_edit_that_ends_inside_a_run_of_markers_keeps_the_emphasis(self):
        lines = edit_diff.Lines("x ***a*** y\n")
        self.assertTrue(lines.inline(2, 9))
        self.assertFalse(lines.inline(2, 7))  # 4 markers in all, but the closing run is cut

    def test_an_edit_after_a_backslash_keeps_the_escape_and_its_mark(self):
        lines = edit_diff.Lines("a \\> b\n")
        self.assertFalse(lines.inline(3, 4))
        self.assertTrue(lines.inline(2, 4))
        self.assertFalse(lines.inline(0, 3))  # it would end on the backslash

    def test_a_new_file_marks_its_paragraphs_as_new(self):
        found, text = self.marked("", "# Methods\n\nWe did it.\n\nTwice.\n")
        self.assertEqual(len(found), 1)
        self.assertEqual(text.count("pv-new"), 2)
        self.assertNotIn("data-edit", text.split("\n")[0])  # the heading

    def test_deleted_text_shows_as_written(self):
        found, text = self.marked("Keep *this* {{v:n}} [@first].\n", "Keep.\n")
        self.assertIn(r"[\*this\* \{\{v\:n\}\} \[\@first\]]{.pv-edit-del", text)
        self.assertIn('<span class="pv-edit-del"', html(text))
