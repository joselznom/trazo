"""The shared runner: every way a command can hang ends within its bounds.

These run on Windows too (windows.yml), the only coverage its path has:
reader threads and the job object instead of select and process groups.
"""

import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)
from test_mcp import gone, wait_until

from trazo import portable, processes

PY = sys.executable
# Starts a child that sleeps a minute with the pipes it inherited, prints its pid and exits 0.
HOLD = ("import subprocess, sys; p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']{0}); "
        "print('done', p.pid, flush=True)")
DETACHED = (", creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP"
            if os.name == "nt" else ", start_new_session=True")


class RunTests(unittest.TestCase):
    def test_a_hung_command_and_its_child_are_stopped_at_the_deadline(self) -> None:
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        pid_file = folder / "child.pid"
        code = ("import subprocess, sys, time; p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
                f"open({str(pid_file)!r}, 'w').write(str(p.pid)); print('started', flush=True); time.sleep(60)")
        started = time.monotonic()
        with self.assertRaises(processes.Timeout) as caught:
            processes.run([PY, "-c", code], timeout=3, text=True)
        self.assertLess(time.monotonic() - started, 3 + processes.STOP_WAIT + 3)
        self.assertIn("started", caught.exception.stdout)
        self.assertIn("did not finish within 3 s and was stopped", str(caught.exception))
        self.assertIsInstance(caught.exception, subprocess.TimeoutExpired)  # existing handlers still catch it
        self.assertTrue(wait_until(lambda: gone(int(pid_file.read_text()))), "a child outlived the deadline")

    def test_a_child_holding_the_output_does_not_turn_success_into_a_timeout(self) -> None:
        started = time.monotonic()
        with patch.object(processes, "LINGER", 1):
            result = processes.run([PY, "-c", HOLD.format("")], timeout=60, text=True)
        self.assertLess(time.monotonic() - started, 15)
        self.assertIsInstance(result, processes.Held)
        self.assertEqual((result.returncode, result.stdout.split()[0], result.stopped), (0, "done", True))
        self.assertTrue(wait_until(lambda: gone(int(result.stdout.split()[1]))), "the holder was not stopped")

    def test_a_detached_child_holding_the_output_ends_the_wait_after_the_linger(self) -> None:
        started = time.monotonic()
        with patch.object(processes, "LINGER", 1):
            result = processes.run([PY, "-c", HOLD.format(DETACHED)], timeout=60, text=True)
        holder = int(result.stdout.split()[1])
        self.addCleanup(lambda: portable.pid_alive(holder) and portable.kill_group(holder))
        self.assertLess(time.monotonic() - started, 15)
        self.assertIsInstance(result, processes.Held)
        self.assertEqual(result.returncode, 0)
        if os.name == "nt":  # a detached process stays in the job: it ends with it
            self.assertTrue(wait_until(lambda: gone(holder)), "the job did not contain the detached holder")
        else:  # its own session: not ours to stop
            self.assertFalse(result.stopped)
            self.assertTrue(portable.pid_alive(holder))
            self.assertIn("left running", result.note)

    def test_large_input_and_output_round_trip(self) -> None:
        text = "é\n" * 1_500_000
        result = processes.run([PY, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"],
                               timeout=60, input=text, text=True)
        self.assertEqual((result.returncode, result.stdout), (0, text))

    def test_undecodable_output_is_replaced(self) -> None:
        result = processes.run([PY, "-c", "import sys; sys.stderr.buffer.write(b'a\\xf1o')"], timeout=30, text=True)
        self.assertEqual(result.stderr, "a�o")

    def test_lines_reach_on_line_as_they_come_and_the_command_can_be_stopped(self) -> None:
        lines: list[str] = []
        code = "import sys, time; print('one', flush=True); print('two', file=sys.stderr, flush=True); time.sleep(60)"
        started: list[subprocess.Popen] = []
        deadline = time.monotonic() + 30

        def stop_when_both_arrived(line: str) -> None:
            lines.append(line)
            if len(lines) == 2:
                self.assertTrue(processes.stop(started[0], grace=0))
        result = processes.run([PY, "-c", code], timeout=60, on_line=stop_when_both_arrived, started=started.append)
        self.assertLess(time.monotonic(), deadline)
        self.assertEqual(sorted(lines), ["one", "two"])
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(processes.stop(started[0]), "a finished command is no longer stoppable")


@unittest.skipIf(os.name == "nt", "POSIX signals; Windows has no SIGTERM to forward")
class SignalTests(unittest.TestCase):
    def test_ctrl_c_and_sigterm_stop_the_commands_group(self) -> None:
        for signum in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=signum.name), tempfile.TemporaryDirectory() as folder:
                pid_file = Path(folder) / "child.pid"
                child = ("import subprocess, sys, time; "
                         "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
                         f"open({str(pid_file)!r}, 'w').write(str(p.pid)); time.sleep(60)")
                script = ("import sys; from trazo import processes; processes.stop_on_signals(); "
                          f"processes.run([sys.executable, '-c', {child!r}], timeout=600, capture=False)")
                runner = subprocess.Popen([PY, "-c", script], stderr=subprocess.DEVNULL,
                                          env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
                self.addCleanup(runner.kill)
                deadline = time.monotonic() + 30
                while not (pid_file.is_file() and pid_file.read_text()):
                    self.assertLess(time.monotonic(), deadline, "the command did not start")
                    time.sleep(0.05)
                runner.send_signal(signum)
                self.assertNotEqual(runner.wait(15), 0)
                self.assertTrue(wait_until(lambda: gone(int(pid_file.read_text()))), "the command's child survived")


if __name__ == "__main__":
    unittest.main()
