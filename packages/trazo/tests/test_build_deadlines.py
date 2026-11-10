"""A build whose Pandoc, metadata read, uv sync or own step hangs ends with
an error that names what hung and its limit, and leaves nothing running."""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import _support

from trazo import frontmatter, portable
from trazo.project import PACKAGE_DIR

HANG = Path(__file__).with_name("_hang.py")


class BuildDeadlines(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())) / "paper"
        shutil.copytree(_support.FIXTURE, self.root)
        self.pids = self.root.parent / "hang.pids"
        self.addCleanup(self.end_stand_ins)

    def end_stand_ins(self) -> None:
        """What the fix should have ended, so a failing run leaves nothing behind."""
        if self.pids.is_file():
            for pid in map(int, self.pids.read_text().split()):
                if portable.pid_alive(pid):  # one process each, not a group leader
                    portable.kill_group(pid) if os.name == "nt" else os.kill(pid, signal.SIGKILL)

    def assert_stand_ins_gone(self) -> None:
        pids = [int(pid) for pid in self.pids.read_text().split()]
        deadline = time.monotonic() + 10
        while any(portable.pid_alive(pid) for pid in pids):
            self.assertLess(time.monotonic(), deadline, "the hung program or its child outlived the build")
            time.sleep(0.05)

    def stand_in(self, program: str, mode: str) -> list[str]:
        return [sys.executable, str(HANG), str(self.pids), shutil.which(program) or program, mode]

    def configure(self, commands: dict[str, list[str]] | None = None, *tables: str) -> None:
        config = self.root / "trazo.toml"
        text = config.read_text()
        for name, command in (commands or {}).items():
            lines = [row for row in text.splitlines() if not row.startswith(f"{name} = ")]
            text = "\n".join(lines).replace("[commands]", f"[commands]\n{name} = {json.dumps(command)}") + "\n"
        config.write_text(text + "\n" + "\n".join(tables) + "\n")

    def build(self, **env: str) -> subprocess.CompletedProcess:
        environ = {**os.environ, "TRAZO_ROOT": str(self.root), "PYTHONPATH": str(PACKAGE_DIR.parent), **env}
        started = time.monotonic()
        result = subprocess.run([sys.executable, "-m", "trazo", "build", "--force"], cwd=self.root, env=environ,
                                capture_output=True, text=True, timeout=60)
        self.assertLess(time.monotonic() - started, 30, result.stderr)
        return result

    @unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
    def test_a_hung_pandoc_stops_the_build_and_keeps_the_earlier_word_file(self) -> None:
        self.configure({"pandoc": self.stand_in("pandoc", "docx")})
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
        child = "import time; time.sleep(3600)"
        script = ("import os, subprocess, sys, time; "
                  f"child = subprocess.Popen([sys.executable, '-c', {child!r}]); "
                  f"open({str(self.pids)!r}, 'a').write(f'{{os.getpid()}} {{child.pid}}\\n'); time.sleep(3600)")
        self.configure({"tables": [sys.executable, "-c", script]}, "[build]", "timeout = 2")
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
        self.configure({}, "[[build.steps]]", 'name = "uses-python"', 'run = ["step.py"]', "outputs = []")
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
        with patch.object(frontmatter, "TIMEOUT", 2), \
                self.assertRaisesRegex(frontmatter.MetadataError, "cannot read metadata .*did not finish within 2 s"):
            frontmatter._read(str(metadata), metadata.stat().st_mtime_ns, metadata.stat().st_size, command)
        self.assertLess(time.monotonic() - started, 15)
        self.assert_stand_ins_gone()


if __name__ == "__main__":
    unittest.main()
