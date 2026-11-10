"""Temporary files that a killed writer leaves are removed by a later write of
the same file, never one that a live writer is still writing."""

import os
import shutil
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import _support

from trazo.project import PACKAGE_DIR

ABANDONED = 11 * 60  # seconds: older than the 600 s a writer is given

# Writes `target` through write_atomic but stops inside its write: "kill" dies
# there, before the rename; "hold" stays there, with the temporary file open.
WRITER = """import os, signal, sys, time
from pathlib import Path
from trazo import review_storage
target, mode = Path(sys.argv[1]), sys.argv[2]
real = os.fdopen
def stalled(fd, *args, **kwargs):
    handle = real(fd, *args, **kwargs)
    def stall(data):
        print("writing", flush=True)
        if mode == "kill":
            os.kill(os.getpid(), signal.SIGKILL)
        time.sleep(600)
    handle.write = stall
    return handle
review_storage.os.fdopen = stalled
review_storage.write_atomic(target, b"never published")
"""

# An after-docx step that waits, with the unfinished Word file in trazo's
# temporary folder, while the file `pause` exists.
PAUSE = """import time
from pathlib import Path
if Path("pause").exists():
    Path("paused").write_text("")
    time.sleep(600)
"""


def env(root=None):
    return {**os.environ, "PYTHONPATH": str(PACKAGE_DIR.parent), **({"TRAZO_ROOT": str(root)} if root else {})}


def age(path):
    old = time.time() - ABANDONED
    os.utime(path, (old, old))


@unittest.skipIf(os.name == "nt", "SIGKILL")
class AtomicWriter(unittest.TestCase):
    def writer(self, target, mode):
        proc = subprocess.Popen([sys.executable, "-c", WRITER, str(target), mode], env=env(),
                                stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: proc.poll() is None and (proc.kill(), proc.wait()))
        self.assertEqual(proc.stdout.readline().strip(), "writing")
        return proc

    @staticmethod
    def temporaries(folder):
        return sorted(p.name for p in folder.iterdir() if p.suffix == ".tmp")

    def test_a_later_write_removes_a_killed_writers_file_and_spares_a_live_one(self):
        from trazo.review_storage import write_atomic
        with TemporaryDirectory() as directory:
            target = Path(directory) / "source.md"
            target.write_text("before")
            self.writer(target, "kill").wait()
            killed = self.temporaries(target.parent)
            live = self.writer(target, "hold")
            live_name = sorted(set(self.temporaries(target.parent)) - set(killed))
            self.assertEqual((len(killed), len(live_name)), (1, 1))
            for name in killed + live_name:  # both look abandoned by age alone
                age(target.parent / name)
            write_atomic(target, b"after")
            self.assertEqual(target.read_bytes(), b"after")
            self.assertEqual(self.temporaries(target.parent), live_name)
            live.send_signal(signal.SIGKILL)
            live.wait()
            write_atomic(target, b"again")
            self.assertEqual(self.temporaries(target.parent), [])


@unittest.skipIf(os.name == "nt", "SIGKILL")
@unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
class WordBuild(unittest.TestCase):
    def test_the_next_build_removes_the_folder_a_killed_build_left(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(_support.FIXTURE, root, dirs_exist_ok=True)
            shutil.rmtree(root / "figures/diagrams")  # exporting needs Chrome
            (root / "pause.py").write_text(PAUSE)
            (root / "trazo.toml").write_text('version = "v0.0.0"\n[[build.steps]]\nname = "pause"\n'
                                             'run = ["pause.py"]\nafter = "docx"\ndocuments = ["manuscript"]\n')
            (root / "pause").write_text("")
            build = subprocess.Popen([sys.executable, "-m", "trazo", "build"], cwd=root, env=env(root),
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            self.addCleanup(lambda: build.poll() is None and (os.killpg(build.pid, signal.SIGKILL), build.wait()))
            deadline = time.monotonic() + 120
            while not (root / "paused").exists():
                self.assertIsNone(build.poll(), "the build ended before its step paused")
                self.assertLess(time.monotonic(), deadline, "the step never paused")
                time.sleep(0.1)
            os.killpg(build.pid, signal.SIGKILL)
            build.wait()
            left = list((root / "output").glob(".trazo-*"))
            self.assertEqual(len(left), 1)
            self.assertTrue((left[0] / "manuscript.docx").is_file())
            age(left[0])
            (root / "pause").unlink()
            result = subprocess.run([sys.executable, "-m", "trazo", "build"], cwd=root, env=env(root),
                                    capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(list((root / "output").glob(".trazo-*")), [])


if __name__ == "__main__":
    unittest.main()
