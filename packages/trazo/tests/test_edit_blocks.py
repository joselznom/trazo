"""Figures, tables and display equations go into the review's baseline as they are."""

import unittest

import _support  # noqa: F401  (points trazo at the test project before it is imported)

from trazo import edit_diff
from trazo.edit_blocks import take_blocks

FIGURE = "![A flow.](figures/flow.png){#fig:flow}\n"
TABLE = "| a | b |\n|---|---|\n| 1 | 2 |\n"


def pending(base, current):
    return [(e.old, e.new) for e in edit_diff.edits(take_blocks(base, current), current)]


class TakeBlocksTests(unittest.TestCase):
    def test_a_new_figure_table_or_equation_is_no_edit(self):
        for block in (FIGURE, TABLE + "\nTable: Counts. {#tbl:t}\n", "$$ a = b $$ {#eq:x}\n",
                      "-----  -----\n12     13\n-----  -----\n"):
            with self.subTest(block=block):
                self.assertEqual(pending("One.\n\nTwo.\n", f"One.\n\n{block}\nTwo.\n"), [])
                self.assertEqual(pending("Intro.\n", f"Intro.\n\n{block}"), [])

    def test_a_changed_or_removed_block_is_no_edit(self):
        self.assertEqual(pending(f"One.\n\n{FIGURE}", f"One.\n\n{FIGURE.replace('flow.png', 'flow2.png')}"), [])
        self.assertEqual(pending(f"One.\n\n{TABLE}", f"One.\n\n{TABLE.replace('2', '3')}"), [])
        self.assertEqual(pending("One.\n\n$$ a = b $$\n", "One.\n\n$$ a = c $$\n"), [])
        self.assertEqual(pending(f"One.\n\n{FIGURE}\nTwo.\n", "One.\n\nTwo.\n"), [])

    def test_a_caption_stays_reviewed_word_by_word(self):
        changed = FIGURE.replace("A flow.", "The flow.").replace("flow.png", "flow2.png")
        self.assertEqual(pending(f"One.\n\n{FIGURE}", f"One.\n\n{changed}"), [("A", "The")])
        table = TABLE + "\nTable: Counts. {#tbl:t}\n"
        self.assertEqual(pending(table, table.replace("Counts", "All counts").replace("2", "3")), [("Counts", "All counts")])
        inside = TABLE + "Table: Counts. {#tbl:t}\n"
        self.assertEqual(pending(inside, inside.replace("Counts", "All counts").replace("2", "3")), [("Counts", "All counts")])

    def test_a_new_figure_before_one_with_a_new_caption_leaves_that_caption_pending(self):
        other = "![Old caption.](a.png){#fig:a}\n"
        current = f"{FIGURE}\n{other.replace('Old', 'New')}"
        self.assertEqual(pending(other, current), [("Old", "New")])

    def test_a_figure_with_a_new_label_keeps_its_caption_reviewed(self):
        old = "![Old caption.](a.png){#fig:a}\n"
        self.assertEqual(pending(old, "![New caption.](a.png){#fig:b}\n"), [("Old", "New")])

    def test_code_next_to_a_figure_stays_reviewed_and_the_figure_does_not(self):
        base = f"{FIGURE}```\nprint(1)\n```\n"
        self.assertEqual(pending(base, base.replace("print(1)", "print(2)")), [("1", "2")])
        base = f"```\nprint(1)\n```\n{FIGURE}"
        self.assertEqual(pending(base, base.replace("flow.png", "flow2.png")), [])
        self.assertEqual(take_blocks(base, base.replace("flow.png", "flow2.png")), base.replace("flow.png", "flow2.png"))

    def test_unlabeled_blocks_keep_their_captions_reviewed(self):
        self.assertEqual(pending("![Old caption.](a.png)\n", "![New caption.](b.png)\n"), [("Old", "New")])
        table = TABLE + "\nTable: Counts.\n"
        self.assertEqual(pending(table, table.replace("Counts", "All counts")), [("Counts", "All counts")])

    def test_a_relabeled_table_keeps_only_its_caption_text_reviewed(self):
        table = TABLE + "\nTable: Counts. {#tbl:a}\n"
        self.assertEqual(pending(table, table.replace("Counts", "All counts").replace("tbl:a", "tbl:b")),
                         [("Counts", "All counts")])

    def test_code_removed_before_a_figure_comes_back_before_it(self):
        base = f"```\nprint(1)\n```\n\n{FIGURE}"
        current = FIGURE.replace("flow.png", "flow2.png")
        found = edit_diff.edits(take_blocks(base, current), current)
        self.assertEqual(edit_diff.reject(current, found), base.replace("flow.png", "flow2.png"))

    def test_inline_code_over_two_lines_keeps_an_image_in_its_paragraph(self):
        base = "![A flow.](a.png)\n`some\ncode`\n"
        self.assertEqual(pending(base, base.replace("a.png", "b.png")), [("a", "b")])

    def test_braces_that_are_no_attributes_stay_caption_text(self):
        for caption in ("Set {A, B}", "Set {A, B} {#tbl:set}"):
            table = TABLE + f"\nTable: {caption}\n"
            self.assertEqual(pending(table, table.replace("{A, B}", "{A, C}")), [("B", "C")])

    def test_a_rule_before_a_paragraph_is_no_table(self):
        base = "-----  -----\nOld text here.\n"
        self.assertEqual(pending(base, base.replace("Old", "New")), [("Old", "New")])

    def test_a_paragraph_added_with_a_figure_stays_pending_alone(self):
        found = pending("One.\n\nTwo.\n", f"One.\n\nSee @fig:flow.\n\n{FIGURE}\nTwo.\n")
        self.assertEqual([new.strip() for _, new in found], ["See @fig:flow."])

    def test_prose_and_code_stay_as_they_were(self):
        base = "One.\n\n```\n| not | a table |\n|---|---|\n```\n\nTwo.\n"
        current = base.replace("not", "never").replace("One.", "First.")
        self.assertEqual(take_blocks(base, current), base)
        self.assertEqual(take_blocks(base, base), base)

    def test_taking_the_blocks_twice_changes_nothing_more(self):
        base = f"One.\n\n{FIGURE}\nTwo.\n"
        current = f"One, edited.\n\n{FIGURE.replace('A flow.', 'B flow.').replace('flow.png', 'x.png')}\n{TABLE}\nTwo.\n"
        taken = take_blocks(base, current)
        self.assertEqual(take_blocks(taken, current), taken)

    def test_a_new_file_keeps_its_prose_pending(self):
        found = pending("", f"# New\n\nText.\n\n{FIGURE}")
        self.assertEqual("".join(new for _, new in found).strip(), "# New\n\nText.")


if __name__ == "__main__":
    unittest.main()
