"""The journal form's fields (trazo.submission_fields)."""

import shutil
import unittest

from _submission_project import COUNTS, SubmissionProject

from trazo import submission_check


@unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
class SubmissionFieldsTests(SubmissionProject, unittest.TestCase):
    def test_form_fields_are_plain_text_in_their_document(self):
        self.bind(self.project())
        fields = submission_check.report(lambda: dict(COUNTS), fields=True)["fields"]
        self.assertEqual([field["label"] for field in fields], [
            "Title", "Short title", "Abstract", "Keywords", "Authors", "Affiliations", "Corresponding author",
            "Ethics", "Consent for publication", "Funding", "Data", "Code"])
        text = {field["label"]: field["text"] for field in fields}
        self.assertEqual(text["Title"], "A study title")
        self.assertEqual(text["Short title"], "Study")
        # Sub-headings start their paragraph; values and citations as in Word.
        self.assertEqual(text["Abstract"].split("\n\n")[:2], ["Objectives: We tested 42 cases [1].", "Conclusions:"])
        self.assertRegex(text["Abstract"], r"\n\n-\s+One finding\.$")  # a list keeps its own paragraph
        self.assertEqual(text["Keywords"], "alpha; beta; gamma")
        self.assertEqual(text["Authors"],
                         "Ana Pérez; Dept, University; ana@example.org; ORCID 0000-0002-1825-0097\nBo Li")
        self.assertEqual(text["Affiliations"], "1. Dept, University")
        self.assertEqual(text["Corresponding author"], "Ana Pérez; ana@example.org")
        # The manuscript numbers the second source [2]; form-only text is kept.
        self.assertEqual(text["Ethics"], "Approved [2].")
        self.assertEqual(text["Funding"], "Grant 42.")
        self.assertEqual([field["form_only"] for field in fields if field["label"] in ("Ethics", "Funding")],
                         [False, True])
        self.assertEqual(text["Code"], "")
        self.assertFalse([field for field in fields if "error" in field])
        # A separate declarations file numbers its own references.
        self.bind(self.project({"submission": {"declarations": "separate"}}))
        fields = submission_check.report(lambda: dict(COUNTS), fields=True)["fields"]
        self.assertEqual(next(field["text"] for field in fields if field["label"] == "Ethics"), "Approved [1].")
        # A stale or missing values file is reported per document, not raised.
        (self.root / "data/values.json").unlink()
        fields = submission_check.report(lambda: dict(COUNTS), fields=True)["fields"]
        abstract = next(field for field in fields if field["label"] == "Abstract")
        self.assertEqual(abstract["text"], "")
        self.assertIn("values file not found", abstract["error"])

    def test_key_points_follow_the_abstract(self):
        self.write("manuscript/main/00_abstract.md",
                   "# Abstract\n\n## Objectives\n\nWe tested {{v:n}} cases [@first].\n\n"
                   "## Highlights:\n\n- First [@second].\n- Second.\n\n## Conclusions\n\nIt works.\n")
        self.write("manuscript/main/00b_key_points.md", "# 1. Key points\n\n- Tested {{v:n}} cases.\n- Works.\n")
        self.write("manuscript/supplementary/S1.md", "# Supplementary\n\n# Key results\n\n- Not a field.\n")
        self.bind(self.project())
        fields = submission_check.report(lambda: dict(COUNTS), fields=True)["fields"]
        self.assertEqual([field["label"] for field in fields][:6],
                         ["Title", "Short title", "Abstract", "Highlights:", "1. Key points", "Keywords"])
        text = {field["label"]: field["text"] for field in fields}
        # The abstract proper, around the highlights inside it.
        self.assertEqual(text["Abstract"], "Objectives: We tested 42 cases [1].\n\nConclusions: It works.")
        self.assertEqual(text["Highlights:"], "- First [2].\n- Second.")
        self.assertEqual(text["1. Key points"], "- Tested 42 cases.\n- Works.")
        self.assertEqual(text["Ethics"], "Approved [2].")
        self.assertNotIn("Key results", text)


if __name__ == "__main__":
    unittest.main()
