"""trazo new without Copier. The byte comparison with `copier copy` is
scripts/check-paper-create.py, run by `make test-template` (it needs uvx)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)

from trazo import cli, manifest, new, variants

HOSTILE = 'A "quoted" title: C:\\papers 😀'


class NewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_creates_the_variant_with_answers_and_manifest(self):
        folder = self.root / "My Paper"
        made = new.paper_create(folder, kind="article", example=False, title=HOSTILE)
        self.assertEqual(made["project_name"], "my-paper")
        store = variants.Store()
        self.assertEqual(sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()),
                         sorted([*store.variant("article", False)["files"], manifest.PATH.as_posix()]))
        self.assertIn('description = "A \\"quoted\\" title: C:\\\\papers 😀"',
                      (folder / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertTrue((folder / "data").is_dir())
        self.assertTrue(os.access(folder / "scripts/trazo", os.X_OK))
        self.assertFalse(os.access(folder / "trazo.toml", os.X_OK))
        self.assertFalse((folder / ".git").exists())
        saved = manifest.read(folder)
        self.assertEqual(saved["template"], store.template)
        answers = {"example": False, "kind": "article", "project_name": "my-paper", "title": HOSTILE}
        self.assertEqual(saved, manifest.build(store.template, answers, store.render(
            "article", False, {"title": HOSTILE, "project_name": "my-paper"}), store.index["skip_if_exists"]))

    def test_refuses_a_folder_that_is_not_empty_or_bad_answers(self):
        (self.root / "note.txt").write_text("mine")
        with self.assertRaisesRegex(ValueError, "not empty"):
            new.paper_create(self.root, kind="article", example=True, title="T")
        self.assertEqual(os.listdir(self.root), ["note.txt"])
        with self.assertRaisesRegex(ValueError, "kind must be one of article, report"):
            new.paper_create(self.root / "a", kind="letter", example=True, title="T")
        with self.assertRaisesRegex(ValueError, "one line"):
            new.paper_create(self.root / "b", kind="article", example=True, title="Two\nlines")
        self.assertEqual(os.listdir(self.root), ["note.txt"])

    def test_a_failed_copy_leaves_nothing(self):
        empty = self.root / "empty"
        empty.mkdir()
        with patch.object(manifest, "write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                new.paper_create(empty, kind="report", example=True, title="T")
            with self.assertRaises(OSError):
                new.paper_create(self.root / "missing", kind="report", example=True, title="T")
        self.assertEqual(os.listdir(empty), [])
        self.assertEqual(os.listdir(self.root), ["empty"])

    def test_a_killed_create_leaves_no_partial_paper(self):
        # SIGKILL runs no cleanup: the paper is only ever written in a hidden sibling folder.
        target = self.root / "paper"
        with patch.object(manifest, "write", side_effect=OSError("killed")), \
                patch.object(new.shutil, "rmtree"):
            with self.assertRaises(OSError):
                new.paper_create(target, kind="article", example=True, title="T")
        self.assertFalse(target.exists())
        self.assertTrue(all(name.startswith(".paper.trazo-new-") for name in os.listdir(self.root)))
        new.paper_create(target, kind="article", example=True, title="T")
        self.assertTrue((target / "trazo.toml").is_file())

    def test_refuses_a_folder_inside_a_paper(self):
        new.paper_create(self.root / "outer", kind="article", example=False, title="T")
        with self.assertRaisesRegex(ValueError, "is inside the paper .*outer"):
            new.paper_create(self.root / "outer" / "notes" / "inner", kind="article", example=False, title="T")
        self.assertFalse((self.root / "outer" / "notes" / "inner").exists())

    def test_executables_get_all_three_x_bits(self):
        path = self.root / "tool"
        path.write_text("#!/bin/sh\n")
        path.chmod(0o640)
        new.make_executable(path)
        self.assertEqual(path.stat().st_mode & 0o777, 0o751)

    def test_command_line(self):
        with patch("sys.stdout"):
            code = cli.main(["new", str(self.root / "r"), "--kind", "report", "--no-example",
                             "--title", "Report", "--project-name", "rep"])
        self.assertEqual(code, 0)
        self.assertEqual(manifest.read(self.root / "r")["answers"],
                         {"example": False, "kind": "report", "project_name": "rep", "title": "Report"})


if __name__ == "__main__":
    unittest.main()
