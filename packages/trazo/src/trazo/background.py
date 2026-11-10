"""One background command, its bounded log, and process-group cancellation."""

import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from . import processes


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

    def stop(self, reason: str) -> bool:
        """End the running command's group: SIGTERM, then SIGKILL after
        `grace` seconds (at once without grace; Windows ends it at once)."""
        with self.lock:
            if self.state["state"] != "running":
                return False
            if self.reason is not None:
                return True
            self.reason = reason
            if self.proc is not None:
                processes.stop(self.proc, self.grace)
            return True

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
        """The command until it exits (trazo.processes: a process it started
        that keeps the log open is stopped after LINGER seconds), or until the
        deadline, which stops its group."""
        lines = deque(initial, maxlen=self.limit)
        code = -1

        def line(text: str) -> None:
            lines.append(text.rstrip())
            with self.lock:
                self.state[self.log_key] = list(lines)

        def started(proc: subprocess.Popen) -> None:
            with self.lock:
                self.proc = proc
                if self.reason is not None:  # cancellation during process startup
                    processes.stop(proc, self.grace)
        try:
            result = processes.run(command, cwd=cwd, timeout=timeout, on_line=line, grace=self.grace,
                                   started=started)
            code = result.returncode
            if isinstance(result, processes.Held):
                lines.append(result.note)
        except processes.Timeout:
            with self.lock:
                self.reason = self.reason or "timeout"
        except Exception as exc:
            lines.append(f"could not run {command[0]}: {exc}")
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
