"""The marks that make the preview's text writable never change the page.

The blocks come from a line scanner, and Pandoc reads the same lines by its own grammar. Where the
two disagree a mark lands inside a list, a raw block or math, and the page shows it. So generated
files must read the same to Pandoc with their marks and without them.
"""

import json
import random
import shutil
import subprocess
import unittest

from trazo import edit_diff, preview_blocks

SEED, SAMPLES = 7, 200
# Lines where the two can disagree: list-like prose, raw content, fences, and what ends them.
LINES = ("- One item.", "- Two item.", "1. Num item.", "2. Num two.", "a. Let one.", "II. Roman.", "A. Cap prose",
         "A.  Cap two", "A.\tCap tab", "E. coli grew.", "(E. coli) grew.", "p. 3 words", "  continuation words",
         "  E. coli wrap", "  + plus wrap", "  - minus wrap", "  1. nested", "    - deep", "", "", "",
         "\\begin{equation}", "\\end{equation}", "  \\begin{equation}", "  \\end{equation}", "1. \\begin{equation}",
         "- \\begin{equation}", "<pre>", "</pre>", "  <pre>", "  </pre>", "- <b>x</b> y", "- <5% lost", "  <5% lost",
         "<5% lost", "< 5 lost", "- < 5 lost", "<!-- c -->", "  <!-- c -->", "> - Quoted item",
         "> 1. \\begin{equation}", "> <div>", "> text", "Plain words here.", "$$", "  $$", "```", "  ```",
         "::: note", ":::")


def samples():
    rng, found = random.Random(SEED), {}
    while len(found) < SAMPLES:
        found["\n".join(rng.choice(LINES) for _ in range(rng.randint(2, 7))) + "\n"] = True
    return list(found)


def read(text):
    answer = subprocess.run(["pandoc", "--from=markdown", "--to=json"], input=text.encode("utf-8"),
                            capture_output=True, check=True)
    return json.loads(answer.stdout)["blocks"]


def without_marks(node):
    if isinstance(node, dict):
        return {key: without_marks(value) for key, value in node.items()}
    if not isinstance(node, list):
        return node
    kept = []
    for item in node:
        classes = item["c"][0][1] if isinstance(item, dict) and item.get("t") == "Span" else ()
        if "pv-atom" in classes:
            kept.extend(without_marks(item["c"][1]))
        elif "pv-src" not in classes:
            kept.append(without_marks(item))
    return kept


@unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
class MarksKeepThePageTests(unittest.TestCase):
    def test_pandoc_reads_a_file_the_same_with_its_marks(self):
        changed = [text for text in samples()
                   if without_marks(read(edit_diff.with_marks(text, preview_blocks.marks(text, "a.md")))) != read(text)]
        self.assertEqual(changed[:5], [], f"{len(changed)} of {SAMPLES} files change")


if __name__ == "__main__":
    unittest.main()
