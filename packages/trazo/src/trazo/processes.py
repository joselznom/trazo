"""Commands with a finite deadline, each run in its own process group.

trazo runs other programs (Pandoc, uv, a paper's build steps, `trazo`
itself for the MCP server and the preview's jobs) through run(). Unlike
subprocess.run(timeout=...), which on POSIX kills only the command and not
what it started, and on Windows then reads the pipes without a bound:

- each command starts in a group of its own (POSIX: a new session; Windows:
  a job object, which nests inside its parent's) and the group ends on the
  deadline, on Ctrl+C or SIGTERM (cli.main turns SIGTERM and SIGHUP into an
  exception, so a trazo run by another trazo ends its own groups first), on
  stop() from another thread and at stop_all(), which the preview calls on
  shutdown and Python at exit;
- a process the command started that keeps the output pipes open after the
  command exits delays the result by LINGER seconds at most: the group is
  then killed and the result is a Held with the command's own exit code (a
  holder outside the group is left running);
- output is UTF-8 with replacement characters (text=True), so one stray byte
  is never an exception.

Stopping is graceful on POSIX: SIGTERM (SIGINT after Ctrl+C), then SIGKILL
of what is left after the run's grace. A command gets half of that grace as
its own ($TRAZO_STOP_GRACE), and a trazo's SIGTERM handler passes SIGTERM on
to its groups before anything else, so each nested level kills what ignores
it before its parent kills that level. Limit: a level whose handler is held
up longer than its share of the grace (a main thread stuck in C code) can be
killed first, leaving what it ran.
Windows ends a job at once. Processes still running
after a command exited normally, without holding its pipes, are left alone.
"""

from __future__ import annotations

import atexit
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from . import portable
from .process_pipes import Selector, Threads, decode

LINGER = 5.0  # seconds a descendant may hold the pipes after the command exits
STOP_WAIT = 3.0  # seconds to reap a killed command; one stuck in the kernel is not waited for
STOP_ENV = "TRAZO_STOP_GRACE"  # the grace a run's command gets for its own runs: half the run's
GRACE = 5.0  # stop()'s default, and the MCP calls' and the preview Word job's grace
KILL = getattr(signal, "SIGKILL", signal.SIGTERM)  # Windows ends a group whatever the signal


class Timeout(subprocess.TimeoutExpired, TimeoutError):
    """The deadline passed and the group was stopped; output and stderr hold
    what was read until then. A TimeoutExpired for code that expects one, and
    an OSError, so the commands that report those report it; `hint` says
    which setting sets the limit."""

    hint = ""

    def __str__(self) -> str:
        return (f"{Path(str(self.cmd[0])).name} did not finish within {self.timeout:g} s and was stopped"
                + (f" ({self.hint})" if self.hint else ""))


class Held(subprocess.CompletedProcess[Any]):
    """The command exited, but a process it started kept its output open for
    LINGER seconds: what was read until then, with the command's exit code."""

    def __init__(self, args: list[str], returncode: int, stdout: Any, stderr: Any, stopped: bool) -> None:
        super().__init__(args, returncode, stdout, stderr)
        self.stopped = stopped  # False: the holder is outside the group and still runs

    @property
    def note(self) -> str:
        fate = "stopped" if self.stopped else "left running (it is outside the command's process group)"
        return (f"{Path(self.args[0]).name} exited, but a process it started kept its output open; "
                f"that process was {fate}")


def env_seconds(name: str, default: float) -> float:
    """$name as a deadline in seconds, else `default`; ValueError unless it is positive and finite."""
    try:
        value = float(os.environ.get(name) or default)
    except ValueError:
        value = float("nan")
    if not 0 < value < float("inf"):
        raise ValueError(f"{name} must be a positive finite number of seconds")
    return value


class _Run:
    """A started command, its group, and how to end it."""

    def __init__(self, process: subprocess.Popen[bytes], grace: float) -> None:
        self.process, self.stopping, self.grace = process, False, grace
        self.timers: list[threading.Timer] = []
        self.job = None
        if os.name == "nt":
            try:
                self.job = portable.Job(process)
            except OSError:  # taskkill /T still ends the tree it can see
                pass
        else:  # waitpid in a thread: the exit is seen at once, without polling
            self.waiter = threading.Thread(target=process.wait, daemon=True)
            self.waiter.start()

    def exited(self, wait: float | None) -> bool:
        """Whether the command (not its group) exited, waiting `wait` seconds at most (None: until it does)."""
        if os.name == "nt":
            try:
                self.process.wait(wait)
            except subprocess.TimeoutExpired:
                return False
            return True
        self.waiter.join(wait)
        return not self.waiter.is_alive()

    def signal(self, signum: int) -> None:
        """Send `signum` to the group; Windows ends it."""
        if self.job is not None:
            self.job.terminate()
        else:
            portable.kill_group(self.process.pid, signum)

    def kill(self) -> None:
        self.signal(KILL)

    def settled(self, end: float) -> bool:
        """Wait until the command and everything left in its group ended, or `end`."""
        while not self.exited(min(0.05, max(0.0, end - time.monotonic()))) or self.group_alive():
            if time.monotonic() >= end:
                return False
            time.sleep(0.02)
        return True

    def group_alive(self) -> bool:
        if os.name == "nt":  # a job ends all at once
            return False
        try:
            os.killpg(self.process.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


_LIVE: dict[subprocess.Popen[bytes], _Run] = {}  # every command run() is waiting for
_LOCK = threading.RLock()  # reentrant: the SIGTERM handler may run while this thread holds it
_CLOSED = False  # stop_all(close=True) ran: no command starts until reopen()


def run(argv: Sequence[str | os.PathLike[str]], *, timeout: float | None, cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None, input: bytes | str | None = None, capture: bool = True,
        text: bool = False, on_line: Callable[[str], None] | None = None, hint: str = "",
        grace: float | None = None,
        started: Callable[[subprocess.Popen[bytes]], None] | None = None) -> subprocess.CompletedProcess[Any]:
    """Run argv for `timeout` seconds at most (positive and finite; None only
    where a user turned the limit off); raises OSError when it cannot start
    and Timeout when it runs out of time.

    capture: stdout and stderr are read (bytes, or text with `text`); without
    it the command shares this process's stdin, stdout and stderr. on_line:
    stderr joins stdout and each line goes to on_line as it comes, decoded,
    without being kept. started(process) runs once the command started (to
    register it for stop()); what it raises stops the command. `hint` names
    the setting that sets the limit, for the Timeout's message. `grace`: the
    seconds a stopped group gets between SIGTERM and SIGKILL (default
    $TRAZO_STOP_GRACE's, else 4)."""
    if timeout is not None and not 0 < timeout < float("inf"):
        raise ValueError("timeout must be a positive finite number of seconds")
    command = [os.fspath(arg) for arg in argv]
    if grace is None:
        grace = _inherited_grace()
    # Halved at each level, with no floor: a nested run's SIGKILL always comes before its parent's.
    env = {**(os.environ if env is None else env), STOP_ENV: repr(grace / 2)}
    data = input.encode("utf-8") if isinstance(input, str) else input
    pipes = capture or on_line is not None
    options: dict[str, Any] = {"cwd": cwd, "env": env}
    if pipes:
        options.update(stdin=subprocess.PIPE if data else subprocess.DEVNULL, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT if on_line is not None else subprocess.PIPE,
                       **portable.group_options())
    else:  # as before: the console, its Ctrl+C included, stays the user's on Windows
        options.update({} if os.name == "nt" else {"start_new_session": True})
    with _LOCK:  # so stop_all() never misses one that is starting
        if _CLOSED:
            raise OSError(f"{Path(command[0]).name} was not started: trazo is stopping")
        process = subprocess.Popen(command, **options)
        entry = _Run(process, grace)
        _LIVE[process] = entry
    reader = None
    try:
        if pipes:
            reader = (Threads if os.name == "nt" else Selector)(process, data, on_line)
        if started is not None:
            started(process)
        result = _wait(entry, command, timeout, reader)
    except Timeout as exc:
        exc.hint = hint
        if text:
            exc.output, exc.stderr = _text(exc.output, exc.stderr)
        raise
    except BaseException as exc:  # Ctrl+C, SIGTERM (cli.main), started(): never leave the group behind
        if not entry.stopping:
            _stop(entry, signal.SIGINT if isinstance(exc, KeyboardInterrupt) and os.name != "nt" else signal.SIGTERM)
        raise
    finally:
        _forget(entry, reader)
    if text and pipes:
        result.stdout, result.stderr = _text(result.stdout, result.stderr)
    return result


def _text(*values: bytes | None) -> Any:
    return tuple(None if value is None else decode(value) for value in values)


def _inherited_grace() -> float:
    """$TRAZO_STOP_GRACE (seconds, 0 included), set by the run that started this process; else 4."""
    try:
        value = float(os.environ.get(STOP_ENV, "4"))
    except ValueError:
        return 4.0
    return value if 0 <= value < float("inf") else 4.0


def _stop(entry: _Run, signum: int) -> None:
    """End the group: `signum`, then SIGKILL what is left after its grace at most."""
    entry.stopping = True
    entry.signal(signum)
    entry.settled(time.monotonic() + entry.grace)
    entry.kill()


def _wait(entry: _Run, argv: list[str], timeout: float | None,
          reader: Selector | Threads | None) -> subprocess.CompletedProcess[Any]:
    limit = float("inf") if timeout is None else timeout
    deadline = time.monotonic() + limit
    give_up: float | None = None  # when the pipes held after the command exited stop counting
    stopped = False
    while reader is not None and reader.open():
        now = time.monotonic()
        if give_up is None and entry.exited(0):  # from now on only the linger counts
            give_up = now + LINGER
        if give_up is None and now >= deadline:  # what it says while it stops is read meanwhile
            entry.stopping = True
            entry.signal(signal.SIGTERM)
            _drain(reader, now + entry.grace, lambda: entry.settled(time.monotonic()))
            entry.settled(now + entry.grace)
            entry.kill()
            _drain(reader, time.monotonic() + 1.0)
            raise Timeout(argv, limit, *reader.output())
        if give_up is not None and now >= give_up:
            if stopped:  # a process outside the group holds them
                return Held(argv, entry.process.returncode, *reader.output(), stopped=False)
            entry.kill()  # the holder is in the group: one more second for the end of the output
            stopped, give_up = True, now + 1.0
            continue
        reader.pump(min(0.2, max(0.0, (give_up or deadline) - now)))
    if not entry.exited(None if timeout is None else max(0.0, deadline - time.monotonic())):
        _stop(entry, signal.SIGTERM)
        raise Timeout(argv, limit, *(reader.output() if reader else (None, None)))
    output = reader.output() if reader else (None, None)
    if stopped:
        return Held(argv, entry.process.returncode, *output, stopped=True)
    return subprocess.CompletedProcess(argv, entry.process.returncode, *output)


def _drain(reader: Selector | Threads, end: float, done: Callable[[], bool] = lambda: False) -> None:
    """Read the pipes until they close, done() or `end`."""
    while reader.open() and not done() and (left := end - time.monotonic()) > 0:
        reader.pump(min(0.2, left))


def _forget(entry: _Run, reader: Selector | Threads | None) -> None:
    if entry.stopping:  # a trazo in the group may still be stopping its own groups
        entry.settled(time.monotonic() + entry.grace)
    with _LOCK:
        _LIVE.pop(entry.process, None)
        for timer in entry.timers:
            timer.cancel()
        if entry.stopping or not entry.exited(0):
            entry.kill()  # what ignored SIGTERM, or the rest of a stopped group
    entry.exited(STOP_WAIT)  # reaped; a process stuck in the kernel is not waited for
    if reader is not None:
        reader.close()
    if entry.job is not None:
        entry.job.close()


def stop(process: subprocess.Popen[bytes], grace: float = GRACE) -> bool:
    """End the group of a command run() is waiting for, from another thread:
    SIGTERM, then SIGKILL after `grace` seconds (at once with 0, and always on
    Windows). False when the command already finished."""
    with _LOCK:
        entry = _LIVE.get(process)
        if entry is None:
            return False
        entry.stopping, entry.grace = True, min(entry.grace, grace)
        if grace <= 0 or os.name == "nt":
            entry.kill()
            return True
        entry.signal(signal.SIGTERM)
        timer = threading.Timer(grace, _kill_live, [process])
        timer.daemon = False  # the process stays until the group is gone
        entry.timers.append(timer)
        timer.start()
    return True


def _kill_live(process: subprocess.Popen[bytes]) -> None:
    with _LOCK:
        entry = _LIVE.get(process)
        if entry is not None:
            entry.kill()


def stop_all(close: bool = False) -> None:
    """End every command run() is waiting for in this process (SIGTERM, then
    SIGKILL after GRACE seconds). With `close`, no command starts after it
    until reopen(): a thread still running (the preview's watcher) cannot
    start one once it returns."""
    global _CLOSED
    with _LOCK:
        _CLOSED = _CLOSED or close
        entries = list(_LIVE.values())
        for entry in entries:
            entry.stopping = True
            entry.signal(signal.SIGTERM)
    deadline = time.monotonic() + GRACE
    for entry in entries:
        entry.settled(deadline)
    with _LOCK:
        for entry in entries:
            if entry.process in _LIVE:
                entry.kill()


def reopen() -> None:
    """Let commands start again after stop_all(close=True)."""
    global _CLOSED
    with _LOCK:
        _CLOSED = False


atexit.register(stop_all, close=True)


def stop_on_signals() -> None:
    """In the main thread of a command line run: SIGTERM and SIGHUP end it
    like Ctrl+C (its commands' groups are stopped, then it exits with
    128 + the signal), unless something set their handlers already."""
    if os.name == "nt" or threading.current_thread() is not threading.main_thread():
        return

    def end(signum: int, frame: object) -> None:
        # SIGTERM reaches every group at once, before this process starts unwinding, so the
        # SIGKILL deadlines of all levels run from about the same moment.
        with _LOCK:
            for entry in _LIVE.values():
                if not entry.stopping:
                    entry.stopping = True
                    entry.signal(signal.SIGTERM)
        raise SystemExit(128 + signum)
    for signum in (signal.SIGTERM, signal.SIGHUP):
        if signal.getsignal(signum) == signal.SIG_DFL:
            signal.signal(signum, end)
