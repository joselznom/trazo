"""A build whose Pandoc, metadata read, uv sync or own step hangs ends with
an error that names what hung and its limit, and leaves nothing running."""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import _hang
import _support
from test_mcp import gone, wait_until

from trazo import frontmatter
from trazo.project import PACKAGE_DIR


class BuildDeadlines(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())) / "paper"
        shutil.copytree(_support.FIXTURE, self.root)
        self.pids = self.root.parent / "hang.pids"
        self.addCleanup(_hang.end, self.pids)

    def assert_stand_ins_gone(self) -> None:
        self.assertTrue(wait_until(lambda: all(map(gone, _hang.recorded(self.pids))), 10),
                        "the hung program or its child outlived the build")

    def stand_in(self, program: str, mode: str) -> list[str]:
        return _hang.command(self.pids, shutil.which(program) or program, mode)

    def build(self, **env: str) -> subprocess.CompletedProcess:
        environ = {**os.environ, "TRAZO_ROOT": str(self.root), "PYTHONPATH": str(PACKAGE_DIR.parent), **env}
        started = time.monotonic()
        result = subprocess.run([sys.executable, "-m", "trazo", "build", "--force"], cwd=self.root, env=environ,
                                capture_output=True, text=True, timeout=60)
        self.assertLess(time.monotonic() - started, 30, result.stderr)
        return result

    @unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
    def test_a_hung_pandoc_stops_the_build_and_keeps_the_earlier_word_file(self) -> None:
        _hang.set_command(self.root, "pandoc", self.stand_in("pandoc", "docx"))
        earlier = self.root / "output" / "supplementary.docx"
        earlier.parent.mkdir(exist_ok=True)
        earlier.write_bytes(b"last good")
        result = self.build(TRAZO_PANDOC_TIMEOUT="2")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("did not finish within 2 s and was stopped (TRAZO_PANDOC_TIMEOUT sets the limit)",
                      result.stderr)
        self.assertEqual(earlier.read_bytes(), b"last good")
        self.assert_stand_ins_gone()

    def test_a_step_past_the_projects_limit_is_stopped_and_named(self) -> None:
        _hang.set_command(self.root, "tables", _hang.command(self.pids, sys.executable, "any"),
                          "[build]", "timeout = 2")
        result = self.build()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("commands.tables: ", result.stderr)
        self.assertIn("did not finish within 2 s and was stopped ([build] timeout)", result.stderr)
        self.assert_stand_ins_gone()

    @unittest.skipIf(os.name == "nt", "the uv stand-in is started through a shell script")
    def test_a_hung_uv_sync_stops_the_build(self) -> None:
        tools = self.root.parent / "bin"
        tools.mkdir()
        uv = tools / "uv"
        uv.write_text(f"#!/bin/sh\nexec {' '.join(map(repr, self.stand_in('uv', 'sync')))} \"$@\"\n")
        uv.chmod(0o755)
        (self.root / "pyproject.toml").write_text('[project]\nname = "paper"\nversion = "0"\n')
        (self.root / "step.py").write_text("print('step')\n")
        with (self.root / "trazo.toml").open("a") as config:
            config.write('\n[[build.steps]]\nname = "uses-python"\nrun = ["step.py"]\noutputs = []\n')
        result = self.build(TRAZO_UV_TIMEOUT="2", PATH=f"{tools}{os.pathsep}{os.environ['PATH']}")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("did not finish within 2 s and was stopped (uv sync; check the network, "
                      "or set TRAZO_UV_TIMEOUT for a longer sync)", result.stderr)
        self.assert_stand_ins_gone()

    @unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
    def test_a_hung_metadata_read_is_a_metadata_error(self) -> None:
        metadata = self.root / "manuscript" / "metadata.yaml"
        command = tuple(self.stand_in("pandoc", "meta"))
        started = time.monotonic()
        key = (str(metadata), metadata.stat().st_mtime_ns, metadata.stat().st_size, command)
        with patch.object(frontmatter, "TIMEOUT", 2):
            with self.assertRaisesRegex(frontmatter.MetadataError, "cannot read metadata .*did not finish within 2 s"):
                frontmatter._read(*key)
            self.assertLess(time.monotonic() - started, 15)
            self.assert_stand_ins_gone()
            # A caller that falls back on the error (abbreviations) is followed by another reader:
            # within the same bound it fails at once instead of waiting again.
            again = time.monotonic()
            with self.assertRaisesRegex(frontmatter.MetadataError, "did not finish within 2 s"):
                frontmatter._read(*key)
            self.assertLess(time.monotonic() - again, 1)
            self.assertEqual(len(_hang.recorded(self.pids)), 2, "Pandoc was started again")


if __name__ == "__main__":
    unittest.main()
