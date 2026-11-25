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

    def test_code_right_after_a_figure_stays_reviewed(self):
        base = f"{FIGURE}```\nprint(1)\n```\n"
        self.assertEqual(pending(base, base.replace("print(1)", "print(2)")), [("1", "2")])

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
