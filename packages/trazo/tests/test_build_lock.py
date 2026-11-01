"""One build of a project at a time: concurrent runs wait, runs a build starts do not, and the lock outlives
`rm -rf .build` and is freed when its holder dies."""

import contextlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)

from trazo import build_lock, preview, project
from trazo.project import PACKAGE_DIR

WAITING = "[build] waiting for another build of this project"
# A step: `step.py NAME OUTPUT [--hold]`. With --hold it writes NAME.started
# and waits for the file `go` before writing its output.
STEP = """import sys, time
from pathlib import Path
name, output, *hold = sys.argv[1:]
if hold:
    Path(name + ".started").write_text("")
    deadline = time.monotonic() + 120
    while not Path("go").exists() and time.monotonic() < deadline:
        time.sleep(0.05)
Path(output).write_text(name)
"""
# `nested.py STEP`: `trazo run STEP` from inside the build that runs it.
NESTED = """import subprocess, sys
sys.exit(subprocess.run([sys.executable, "-m", "trazo", "run", sys.argv[1]]).returncode)
"""


def step(name, *run, outputs=()):
    return f'[[build.steps]]\nname = "{name}"\nrun = {json.dumps(list(run))}\noutputs = {json.dumps(list(outputs))}\n'


class BuildLock(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.lock = build_lock.lock_path(project.Project(self.root, project.DEFAULTS, True))
        self.addCleanup(self.lock.unlink, True)
        self.addCleanup(lambda: (self.root / "go").write_text(""))  # never leave a held step behind
        (self.root / "step.py").write_text(STEP)
        (self.root / "nested.py").write_text(NESTED)
        (self.root / "trazo.toml").write_text(f"""[commands]
values = [{json.dumps(sys.executable)}, "nested.py", "inner"]
tables = [{json.dumps(sys.executable)}, "-c", "open('tables.txt', 'w').write('regenerated')"]
""" + step("slow", "step.py", "slow", "slow.txt", "--hold", outputs=["slow.txt"])
            + step("fast", "step.py", "fast", "fast.txt", outputs=["fast.txt"])
            + step("inner", "step.py", "inner", "inner.txt", outputs=["inner.txt"])
            + step("outer", "nested.py", "inner"))
        self.env = {**os.environ, "TRAZO_ROOT": str(self.root), "PYTHONPATH": str(PACKAGE_DIR.parent)}
        self.env.pop(build_lock.LOCK_ENV, None)

    def start(self, step, env=None, **options):
        process = subprocess.Popen([sys.executable, "-m", "trazo", "run", step], cwd=self.root,
                                   env=env or self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                   **options)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        self.addCleanup(lambda: process.stdout.close())
        return process

    def run_step(self, step, env=None):
        return subprocess.run([sys.executable, "-m", "trazo", "run", step], cwd=self.root, env=env or self.env,
                              capture_output=True, text=True, timeout=60)

    def hold(self, **options):
        """A `trazo run slow` that holds the lock until the file `go` exists."""
        holder = self.start("slow", **options)
        deadline = time.monotonic() + 60
        while not (self.root / "slow.started").exists():
            self.assertIsNone(holder.poll(), holder.stdout.read() if holder.poll() is not None else "")
            self.assertLess(time.monotonic(), deadline, "slow.started never appeared")
            time.sleep(0.05)
        return holder

    def assert_waits(self, process):
        self.assertEqual(process.stdout.readline().strip(), WAITING)
        self.assertFalse((self.root / "fast.txt").exists())

    def test_a_second_build_waits_and_both_records_are_kept(self):
        holder = self.hold()
        waiter = self.start("fast")
        self.assert_waits(waiter)
        (self.root / "go").write_text("")
        self.assertEqual(holder.wait(60), 0)
        self.assertEqual(waiter.wait(60), 0)
        self.assertEqual([(self.root / f"{name}.txt").read_text() for name in ("slow", "fast")], ["slow", "fast"])
        state = json.loads((self.root / ".build/steps.json").read_text())
        self.assertEqual({key: state[key]["outputs"] for key in ("step:slow", "step:fast")},
                         {"step:slow": ["slow.txt"], "step:fast": ["fast.txt"]})
        self.assertTrue(all(state[key]["hash"] for key in ("step:slow", "step:fast")))

    def test_removing_the_build_folder_during_a_build_keeps_the_lock(self):
        (self.root / ".build").mkdir()
        (self.root / ".build/steps.json").write_text("{}")
        holder = self.hold()
        shutil.rmtree(self.root / ".build")  # a person or an agent cleaning up
        waiter = self.start("fast")
        self.assert_waits(waiter)
        (self.root / "go").write_text("")
        self.assertEqual((holder.wait(60), waiter.wait(60)), (0, 0))

    def test_a_build_folder_that_contains_the_lock_is_refused(self):
        # verify deletes the build folder while it holds the lock: the next build would lock a new file.
        config = (self.root / "trazo.toml").read_text()
        for build in (".cache", ".cache/trazo"):
            with self.subTest(build):
                (self.root / "trazo.toml").write_text(f'[paths]\nbuild = "{build}"\n' + config)
                result = self.run_step("fast")
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
                self.assertIn("[paths] build", result.stderr)
                self.assertFalse((self.root / "fast.txt").exists())

    def test_a_build_started_by_a_build_does_not_wait_for_it(self):
        # outer: a [[build.steps]] entry; values: a [commands] entry run as written.
        for name in ("outer", "values"):
            with self.subTest(name):
                (self.root / "inner.txt").unlink(missing_ok=True)
                result = self.run_step(name)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn(WAITING, result.stdout)
                self.assertEqual((self.root / "inner.txt").read_text(), "inner")

    def test_killing_the_holder_frees_the_lock(self):
        holder = self.hold(start_new_session=True)
        waiter = self.start("fast")
        self.assert_waits(waiter)
        os.killpg(holder.pid, signal.SIGKILL)  # the holder and its step, as MCP cancellation does
        self.assertEqual(waiter.wait(60), 0)
        self.assertEqual((self.root / "fast.txt").read_text(), "fast")

    def test_a_stale_marker_takes_the_lock_normally(self):
        holder = self.hold()
        gone = subprocess.Popen([sys.executable, "-c", ""])
        gone.wait()
        # A live process that does not hold the lock, and one that has exited.
        for pid in (os.getpid(), gone.pid):
            with self.subTest(pid=pid):
                waiter = self.start("fast", {**self.env, build_lock.LOCK_ENV: f"{pid}:{self.lock}"})
                self.assert_waits(waiter)
                waiter.kill()
                waiter.communicate()
        (self.root / "go").write_text("")
        self.assertEqual(holder.wait(60), 0)

    def copy_tables(self):
        """A table spec, its data and the data's source; returns the table's output."""
        for folder in ("tables", "data"):
            shutil.copytree(Path(__file__).parent / "fixture" / folder, self.root / folder)
        output = self.root / "tables/output/md/cohorts.md"
        output.unlink(missing_ok=True)
        return output

    def test_a_build_with_another_temp_folder_waits(self):
        # The MCP server and an agent's sandboxed shell can have different temp folders.
        holder = self.hold()
        other = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, other, True)
        waiter = self.start("fast", {**self.env, **{name: str(other) for name in ("TMPDIR", "TEMP", "TMP")}})
        self.assert_waits(waiter)
        (self.root / "go").write_text("")
        self.assertEqual((holder.wait(60), waiter.wait(60)), (0, 0))

    def test_table_render_waits_for_a_running_build(self):
        output = self.copy_tables()
        holder = self.hold()
        waiter = subprocess.Popen([sys.executable, "-m", "trazo", "table", "render", "cohorts"], cwd=self.root,
                                  env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(lambda: waiter.poll() is None and waiter.kill())
        self.addCleanup(lambda: waiter.stdout.close())
        self.assertEqual(waiter.stdout.readline().strip(), WAITING)
        self.assertFalse(output.exists())
        (self.root / "go").write_text("")
        self.assertEqual((holder.wait(60), waiter.wait(60)), (0, 0))
        self.assertTrue(output.is_file())

    def in_preview(self, action, done):
        """Run a preview watcher action while a build holds the lock: it
        waits, says so, and `done()` is false until the build finishes."""
        data = tomllib.loads((self.root / "trazo.toml").read_text())
        paper = project.Project(self.root, project.merge(project.DEFAULTS, data), True)
        holder = self.hold()
        output, result = io.StringIO(), []
        with patch.object(preview, "PROJECT", paper), patch.object(preview, "ROOT", self.root), \
                contextlib.redirect_stdout(output):
            worker = threading.Thread(target=lambda: result.append(action()))
            worker.start()
            deadline = time.monotonic() + 60
            while WAITING not in output.getvalue() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertIn(f"[preview] {WAITING}", output.getvalue())
            self.assertFalse(done(output.getvalue()))
            (self.root / "go").write_text("")
            worker.join(60)
        self.assertEqual(holder.wait(60), 0)
        self.assertTrue(done(output.getvalue()))
        return result

    def test_the_preview_regenerates_tables_after_a_running_build(self):
        result = self.in_preview(preview.regenerate_tables, lambda _: (self.root / "tables.txt").exists())
        self.assertEqual(result, [True])

    def test_a_trazo_the_preview_tables_command_runs_does_not_wait_for_it(self):
        data = tomllib.loads((self.root / "trazo.toml").read_text())
        data["commands"]["tables"] = [sys.executable, "nested.py", "inner"]
        paper = project.Project(self.root, project.merge(project.DEFAULTS, data), True)
        result = []
        with patch.object(preview, "PROJECT", paper), patch.object(preview, "ROOT", self.root), \
                patch.dict(os.environ, {"TRAZO_ROOT": str(self.root)}), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            worker = threading.Thread(target=lambda: result.append(preview.regenerate_tables()), daemon=True)
            worker.start()
            worker.join(60)
        self.assertEqual(result, [True], output.getvalue())
        self.assertEqual((self.root / "inner.txt").read_text(), "inner")

    def test_the_preview_renders_spec_tables_after_a_running_build(self):
        self.in_preview(preview.render_spec_tables, lambda output: "[preview] tables: rendered" in output)


if __name__ == "__main__":
    unittest.main()
