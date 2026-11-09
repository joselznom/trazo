"""Evidence must distinguish crash boundaries and must not hide changed Word content."""

import json
import signal
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from equivalence import normalized  # noqa: E402
from faults import clone, housekeeping, launch  # noqa: E402
from fixture import create  # noqa: E402


class CrashBoundaryTests(unittest.TestCase):
    def test_kill_cannot_cross_the_selected_atomic_publication_boundary(self) -> None:
        with tempfile.TemporaryDirectory(prefix="trazo-measure-test-") as directory:
            root = Path(directory)
            seed = root / "seed"
            create(seed, False)
            reference = root / "reference"
            clone(seed, reference)
            result = launch(reference, "source", root / "reference.json")
            self.assertEqual(result["result"]["status"], 200)
            file = "manuscript/main/01_introduction.md"
            old, new = (seed / file).read_bytes(), (reference / file).read_bytes()
            self.assertNotEqual(old, new)
            sites = [json.loads(line) for line in (root / "reference.sites.jsonl").read_text().splitlines()]
            for stage in ("before_replace", "write", "after_replace"):
                with self.subTest(stage=stage):
                    site = next(s["site"] for s in sites if s["site"].startswith(stage + " | ") and (
                        s["path"] == file or stage == "write" and s["path"].startswith(file + ".")))
                    paper = root / stage
                    clone(seed, paper)
                    killed = launch(paper, "source", root / f"{stage}.json", "kill", site)
                    self.assertEqual(killed["returncode"], -signal.SIGKILL)
                    self.assertTrue(killed["injection_hit"])
                    self.assertEqual((paper / file).read_bytes(), new if stage == "after_replace" else old)


class AcceptedExceptionTests(unittest.TestCase):
    def test_a_warned_success_needs_the_new_text_published_and_the_old_text_in_history(self) -> None:
        file = "manuscript/main/01_introduction.md"
        warned = {"status": 200, "body": {"warning": "could not set the replaced version aside"}}
        cases = (("accepted", "source", warned, b"new", "old", True),
                 ("no warning", "source", {"status": 200, "body": {}}, b"new", "old", False),
                 ("old text not in history", "source", warned, b"new", "other", False),
                 ("new text not published", "source", warned, b"old", "old", False),
                 ("another operation", "table", warned, b"new", "old", False))
        for case, operation, response, on_disk, recorded, accepted in cases:
            with self.subTest(case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / file).parent.mkdir(parents=True)
                (root / file).write_bytes(on_disk)
                (root / "notes/review").mkdir(parents=True)
                (root / "notes/review/file_history.json").write_text(json.dumps([{"before_text": recorded}]))
                found = housekeeping(root, operation, response, {file: b"old"}, {file: b"new"})
                self.assertEqual(bool(found), accepted, found)


class NormalizationTests(unittest.TestCase):
    def test_only_container_metadata_and_core_build_times_may_vary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for name, year, text in (("before", 2025, "Original text"), ("repeat", 2026, "Original text"),
                                     ("changed", 2026, "Lost or changed text")):
                with zipfile.ZipFile(folder / f"{name}.docx", "w") as archive:
                    core = ('<properties xmlns:dcterms="http://purl.org/dc/terms/">'
                            f'<dcterms:created>{year}</dcterms:created><dcterms:modified>{year}</dcterms:modified>'
                            '</properties>')
                    archive.writestr(zipfile.ZipInfo("docProps/core.xml", (year, 1, 1, 0, 0, 0)), core)
                    archive.writestr("word/document.xml", f"<document>{text}</document>")
            self.assertEqual(normalized(folder / "before.docx"), normalized(folder / "repeat.docx"))
            self.assertNotEqual(normalized(folder / "before.docx"), normalized(folder / "changed.docx"))


if __name__ == "__main__":
    unittest.main()
