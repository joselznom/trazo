"""A stand-in for Pandoc, uv or a build step that hangs on one kind of call,
with a child of its own, else runs the real program:

    python _hang.py PID_FILE REAL MODE ARG...

MODE picks the call that hangs: docx (writes a .docx), meta (reads the
metadata), page (writes a preview page), help (`--help`), sync (uv sync),
any, or several joined by "+". With MODE "once-page" only the first page
hangs; with "fail-page" every page fails at once, as Pandoc does on text it
rejects; with "stubborn-..." the call that hangs ignores SIGTERM; with
"killed-..." it kills itself with SIGKILL (POSIX). A call that hangs appends
"<its pid> <its child's pid>" to PID_FILE, so a test can check both ended
(a failing call appends its pid alone). Tests import the helpers below.
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

HANG = Path(__file__)


def command(pid_file: Path, real: str, mode: str) -> list[str]:
    """The argv of a stand-in for `real`."""
    return [sys.executable, str(HANG), str(pid_file), real, mode]


def recorded(pid_file: Path) -> list[int]:
    return [int(pid) for pid in pid_file.read_text().split()] if pid_file.is_file() else []


def end(pid_file: Path) -> None:
    """Kill what a stand-in recorded and still runs, so a failing test leaves nothing behind."""
    from trazo import portable
    for pid in recorded(pid_file):
        if portable.pid_alive(pid):  # one process each, not a group leader
            portable.kill_group(pid) if os.name == "nt" else os.kill(pid, signal.SIGKILL)


def set_command(root: Path, name: str, argv: list[str], *tables: str) -> None:
    """Set [commands] `name` in root's trazo.toml, then append `tables` (TOML lines)."""
    config = root / "trazo.toml"
    rows = [row for row in config.read_text().splitlines() if not row.startswith(f"{name} = ")]
    text = "\n".join(rows).replace("[commands]", f"[commands]\n{name} = {json.dumps(argv)}")
    config.write_text(text + "\n" + "\n".join(tables) + "\n")


def main() -> None:
    pid_file, real, mode, *args = sys.argv[1:]
    prefixes = ("once-", "fail-", "stubborn-", "killed-")
    once, fail, stubborn, killed = (mode.startswith(prefix) for prefix in prefixes)
    for prefix in prefixes:
        mode = mode.removeprefix(prefix)
    kinds = mode.split("+")
    calls = {"docx": any(arg.endswith(".docx") for arg in args),
             "meta": any("metadata.template" in arg for arg in args),
             "page": any(arg.endswith(".html.tmp") for arg in args),
             "help": args == ["--help"],
             "sync": args[:1] == ["sync"],
             "any": True}
    hit = any(calls[kind] for kind in kinds)
    if hit and killed:
        os.kill(os.getpid(), signal.SIGKILL)
    if hit and fail:
        with open(pid_file, "a") as record:
            record.write(f"{os.getpid()}\n")
        sys.exit("pandoc: the text was rejected")
    if hit and not (once and Path(pid_file).exists()):
        if stubborn and os.name != "nt":
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3600)"])
        with open(pid_file, "a") as record:
            record.write(f"{os.getpid()} {child.pid}\n")
        time.sleep(3600)
    sys.exit(subprocess.run([real, *args]).returncode)


if __name__ == "__main__":
    main()
