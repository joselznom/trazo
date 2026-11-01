"""trazo setup --paper: the line that tells the setup skill the chat's paper
has an older template than the trazo just installed."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT first)
from test_tool_setup import PANDOC_SCRIPT, run

from trazo import machine, manifest, new, tool_setup, variants

SHIPPED = variants.Store().template


def with_template(folder: Path, template: str) -> None:
    saved = manifest.read(folder)
    manifest.write(folder, {**saved, "template": template})


class PaperNoticeTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.paper = self.folder / "my paper"  # a space, as in real folder names
        new.create(self.paper, kind="article", example=False, title="T")

    def test_an_older_paper_gets_the_notice(self):
        with_template(self.paper, "v0.0.1")
        self.assertEqual(tool_setup.paper_notice(str(self.paper)),
                         f"paper: older template (v0.0.1; this trazo has {SHIPPED})")

    def test_a_current_or_newer_paper_gets_none(self):
        self.assertIsNone(tool_setup.paper_notice(str(self.paper)))
        with_template(self.paper, "v999.0.0")
        self.assertIsNone(tool_setup.paper_notice(str(self.paper)))

    def test_a_folder_whose_version_is_unknown_gets_none(self):
        # Not a paper; a paper without a manifest (made with Copier); a damaged manifest.
        self.assertIsNone(tool_setup.paper_notice(str(self.folder)))
        (self.paper / manifest.PATH).unlink()
        self.assertIsNone(tool_setup.paper_notice(str(self.paper)))
        (self.paper / manifest.PATH).write_text("{")
        self.assertIsNone(tool_setup.paper_notice(str(self.paper)))
        unreadable = '{"format": 1, "template": "latest", "answers": {}, "files": {}, "project": []}'
        (self.paper / manifest.PATH).write_text(unreadable)
        self.assertIsNone(tool_setup.paper_notice(str(self.paper)))


@unittest.skipIf(os.name == "nt", "the fake Pandoc is a shell script")
class SetupPrintsTheNoticeTests(unittest.TestCase):
    """trazo setup with a Pandoc on PATH and no Chrome to download, so it
    only writes machine.json."""

    def setUp(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        bin_folder = folder / "bin"
        bin_folder.mkdir()
        (bin_folder / "pandoc").write_bytes(PANDOC_SCRIPT)
        (bin_folder / "pandoc").chmod(0o755)
        for patcher in (patch.dict(os.environ, {"TRAZO_DATA_DIR": str(folder / "data"), "PATH": str(bin_folder)}),
                        patch.object(machine, "CHROME_PATHS", ()), patch.object(machine, "PANDOC_LOCATIONS", ()),
                        patch.object(tool_setup, "CHROME_ASSETS", {})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.paper = folder / "paper"
        new.create(self.paper, kind="article", example=False, title="T")

    def test_the_notice_is_the_last_line_only_for_an_older_paper(self):
        code, out, err = run(["--paper", str(self.paper)])
        self.assertEqual(code, 0, out + err)
        self.assertNotIn("paper:", out)
        with_template(self.paper, "v0.0.1")
        code, out, err = run(["--paper", str(self.paper)])
        self.assertEqual(code, 0, out + err)
        self.assertEqual(out.splitlines()[-1], f"paper: older template (v0.0.1; this trazo has {SHIPPED})")
        code, out, err = run([])  # no --paper: nothing about papers
        self.assertNotIn("paper:", out)


if __name__ == "__main__":
    unittest.main()
