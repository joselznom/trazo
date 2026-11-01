"""Real command lifecycle: logs, cancellation, timeouts and stale callbacks."""

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)

from trazo.background import CommandJob


class CommandTests(unittest.TestCase):
    def wait_for(self, condition):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.01)
        self.fail("background command did not reach the expected state")

    def start(self, job, source, timeout=3):
        self.addCleanup(job.stop, "cancelled")
        self.assertTrue(job.start([sys.executable, "-u", "-c", source], Path.cwd(), timeout))

    def finished(self, job):
        self.wait_for(lambda: job.status()["state"] != "running")
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

    def test_expired_timer_cannot_cancel_a_later_command(self):
        callbacks = []

        class Timer:
            def __init__(self, timeout, callback, args):
                callbacks.append(lambda: callback(*args))

            def start(self):
                pass

            def cancel(self):
                pass

        job = CommandJob("export", "messages", 3)
        with patch("trazo.background.threading.Timer", Timer):
            self.start(job, "print('first')")
            self.assertEqual(self.finished(job)["state"], "ok")
            old_timeout = callbacks[0]
            self.start(job, "import time; print('second'); time.sleep(10)")
            self.wait_for(lambda: "second" in job.status()["messages"])
            self.assertFalse(old_timeout())
            self.assertEqual(job.status()["state"], "running")
            self.assertTrue(job.stop("cancelled"))
            self.assertEqual(self.finished(job)["state"], "error")


if __name__ == "__main__":
    unittest.main()
