"""A disposable Pandoc boundary: delegate real calls except the selected failure."""

import os
import subprocess
import sys
import time
from pathlib import Path

real, mode, folder, *args = sys.argv[1:]
root = Path(folder)
if mode == "hang" and "--version" not in args and "--help" not in args:
    time.sleep(3600)
if mode == "invalid" and "-o" in args:
    output = Path(args[args.index("-o") + 1])
    if output.suffix == ".docx":
        output.write_bytes(b"This is not a Word document.")
        raise SystemExit(0)
if mode == "race" and "--to=json" in args and any(arg.endswith("manuscript.md") for arg in args):
    if not (root / "race.done").exists():
        (root / "race.ready").touch()
        deadline = time.monotonic() + 10
        while not (root / "race.release").exists() and time.monotonic() < deadline:
            time.sleep(.01)
        (root / "race.done").touch()
if os.name != "nt":
    os.execv(real, [real, *args])
raise SystemExit(subprocess.call([real, *args]))
