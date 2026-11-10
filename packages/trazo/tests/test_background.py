"""Real command lifecycle: logs, cancellation, timeouts and stale callbacks."""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)

from trazo.background import CommandJob
from trazo.portable import kill_group, pid_alive


class CommandTests(unittest.TestCase):
    def wait_for(self, condition, seconds=5):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.01)
        self.fail("background command did not reach the expected state")

    def start(self, job, source, timeout=3):
        self.addCleanup(job.stop, "cancelled")
        self.assertTrue(job.start([sys.executable, "-u", "-c", source], Path.cwd(), timeout))

    def finished(self, job, seconds=5):
        self.wait_for(lambda: job.status()["state"] != "running", seconds)
        return job.status()

    def test_exit_status_and_bounded_log(self):
        job = CommandJob("build", "tail", 3)
        self.start(job, "for n in range(10): print(n)\nraise SystemExit(7)")
        state = self.finished(job)
        self.assertEqual((state["state"], state["exit_code"], state["tail"]),
                         ("error", 7, ["7", "8", "9"]))
        state["tail"].clear()
        self.assertEqual(job.status()["tail"], ["7", "8", "9"])
        self.start(job, "print('ready')")
        self.assertEqual(self.finished(job)["state"], "ok")

    def test_timeout_and_cancel_stop_the_whole_process_group(self):
        for grace in (0, 0.05):  # exports kill immediately; DOCX allows graceful termination
            for reason in ("timeout", "cancelled"):
                with self.subTest(grace=grace, reason=reason), tempfile.TemporaryDirectory() as folder:
                    marker = Path(folder) / "orphan"
                    # The timeout leaves both interpreters time to start on a slow
                    # machine, and the child writes only after it, so only a
                    # descendant that survived the cleanup can write.
                    timeout = 1.0 if reason == "timeout" else 3
                    delay = timeout + 0.5 if reason == "timeout" else 0.5
                    # The child ignores SIGTERM and holds stdout open after its parent exits.
                    child = ("import signal,time; from pathlib import Path; "
                             "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                             f"print('child ready', flush=True); time.sleep({delay}); Path({str(marker)!r}).touch()")
                    source = (f"import subprocess,sys,time; subprocess.Popen([sys.executable, '-c', {child!r}]); "
                              "time.sleep(10)")
                    job = CommandJob("export", "messages", 3, grace=grace)
                    self.start(job, source, timeout=timeout)
                    self.assertFalse(job.start([sys.executable, "-c", "pass"], Path.cwd(), 3))
                    self.wait_for(lambda: "child ready" in job.status()["messages"])
                    ready = time.monotonic()
                    if reason == "cancelled":
                        self.assertTrue(job.stop(reason))
                    state = self.finished(job)
                    self.assertEqual(state["state"], "error")
                    self.assertIn("timed out" if reason == "timeout" else "cancelled", state["messages"][-1])
                    time.sleep(max(0, ready + delay + 0.05 - time.monotonic()))
                    self.assertFalse(marker.exists(), "a descendant survived process-group cleanup")
                    self.assertFalse(job.stop("cancelled"))

    def test_a_deadline_belongs_to_its_own_command(self):
        job = CommandJob("export", "messages", 3)
        self.start(job, "print('first')", timeout=1)
        self.assertEqual(self.finished(job)["state"], "ok")
        # Longer than the first command's deadline: nothing of it may stop this one.
        self.start(job, "import time; print('second', flush=True); time.sleep(1.5)", timeout=10)
        self.assertEqual(self.finished(job)["state"], "ok", job.status())

    def held(self, flags: str) -> dict:
        """A command that exits 0 at once, leaving a child that keeps its log open for a minute."""
        job = CommandJob("build", "tail", 5, grace=5)
        marker = Path(self.enterContext(tempfile.TemporaryDirectory())) / "holder.pid"
        child = f"import os, pathlib, time; pathlib.Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(60)"
        if flags:  # outside the job's group: ours to end
            self.addCleanup(lambda: marker.is_file() and pid_alive(int(marker.read_text()))
                            and kill_group(int(marker.read_text())))
        started = time.monotonic()
        self.start(job, f"import subprocess, sys; subprocess.Popen([sys.executable, '-c', {child!r}]{flags})",
                   timeout=60)
        state = self.finished(job, 15)
        self.assertLess(time.monotonic() - started, 15)
        return state

    def test_a_child_holding_the_log_does_not_turn_success_into_a_timeout(self):
        state = self.held("")
        self.assertEqual((state["state"], state["exit_code"]), ("ok", 0), state)
        self.assertIn("kept its output open", state["tail"][-1])

    def test_a_detached_child_holding_the_log_does_not_keep_the_job_running(self):
        detached = (", creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP"
                    if os.name == "nt" else ", start_new_session=True")
        state = self.held(detached)
        self.assertEqual((state["state"], state["exit_code"]), ("ok", 0), state)


if __name__ == "__main__":
    unittest.main()
