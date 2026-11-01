"""One background command, its bounded log, and process-group cancellation."""

import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from .portable import group_options, kill_group


class CommandJob:
    def __init__(self, label: str, log_key: str, limit: int, grace: float = 0,
                 **details: Any) -> None:
        self.label, self.log_key, self.limit, self.grace = label, log_key, limit, grace
        self.lock = threading.Lock()
        self.state = {"state": "idle", "started": "", "finished": "", "exit_code": None,
                      log_key: [], **details}
        self.proc: subprocess.Popen | None = None
        self.reason: str | None = None

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {**self.state, self.log_key: list(self.state[self.log_key])}

    @staticmethod
    def signal_group(proc: subprocess.Popen, graceful: bool) -> None:
        kill_group(proc.pid, graceful)

    def stop(self, reason: str, expected: subprocess.Popen | None = None) -> bool:
        with self.lock:
            if self.state["state"] != "running" or (expected is not None and self.proc is not expected):
                return False
            if self.reason is not None:
                return True
            self.reason = reason
            if self.proc is not None:
                self.terminate(self.proc)
            return True

    def terminate(self, proc: subprocess.Popen) -> None:
        self.signal_group(proc, bool(self.grace))
        if self.grace:
            # Descendants can hold the pipe open after the leader exits.
            killer = threading.Timer(self.grace, self.signal_group, [proc, False])
            killer.daemon = False
            killer.start()

    def start(self, command: list[str], cwd: Path, timeout: float,
              initial: tuple[str, ...] = (), **details: Any) -> bool:
        with self.lock:
            if self.state["state"] == "running":
                return False
            self.reason = None
            self.state.update(state="running", started=time.strftime("%H:%M:%S"),
                              finished="", exit_code=None, **{self.log_key: []}, **details)
        threading.Thread(target=self.run, args=(command, cwd, timeout, initial), daemon=True).start()
        return True

    def run(self, command: list[str], cwd: Path, timeout: float, initial: tuple[str, ...]) -> None:
        lines = deque(initial, maxlen=self.limit)
        code, timer = -1, None
        try:
            proc = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, errors="replace",
                                    **group_options())
            with self.lock:
                self.proc = proc
                if self.reason is not None:  # cancellation during process startup
                    self.terminate(proc)
            timer = threading.Timer(timeout, self.stop, ["timeout", proc])
            timer.daemon = True
            timer.start()
            assert proc.stdout is not None
            with proc.stdout:
                for line in proc.stdout:
                    lines.append(line.rstrip())
                    with self.lock:
                        self.state[self.log_key] = list(lines)
            code = proc.wait()
        except Exception as exc:
            lines.append(f"could not run {command[0]}: {exc}")
            with self.lock:
                proc = self.proc
                if proc is not None:
                    self.terminate(proc)
            if proc is not None:
                proc.wait()
        finally:
            if timer:
                timer.cancel()
        with self.lock:
            reason, self.proc = self.reason, None
            if reason == "timeout":
                lines.append(f"{self.label} stopped: timed out after {timeout:g} s")
            elif reason:
                lines.append(f"{self.label} cancelled")
            self.state.update(state="ok" if code == 0 and not reason else "error", exit_code=code,
                              finished=time.strftime("%H:%M:%S"), **{self.log_key: list(lines)})
        print(f"[{self.label}] {' '.join(command)}: exit {code}"
              f"{' (' + reason + ')' if reason else ''}", flush=True)
