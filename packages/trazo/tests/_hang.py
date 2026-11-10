"""A stand-in for Pandoc or uv that hangs on one kind of call, with a child
of its own, else runs the real program:

    python _hang.py PID_FILE REAL MODE ARG...

MODE picks the call that hangs: docx (writes a .docx), meta (reads the
metadata), page (writes a preview page), sync (uv sync), any. With MODE
"once-page" only the first page hangs; with "fail-page" every page fails at
once, as Pandoc does on text it rejects. A call that hangs appends
"<its pid> <its child's pid>" to PID_FILE, so a test can check both ended
(a failing call appends its pid alone).
"""

import os
import subprocess
import sys
import time
from pathlib import Path

pid_file, real, mode, *args = sys.argv[1:]
once, fail = mode.startswith("once-"), mode.startswith("fail-")
kind = mode.removeprefix("once-").removeprefix("fail-")
hit = {"docx": any(arg.endswith(".docx") for arg in args),
       "meta": any("metadata.template" in arg for arg in args),
       "page": any(arg.endswith(".html.tmp") for arg in args),
       "sync": args[:1] == ["sync"],
       "any": True}[kind]
if hit and fail:
    with open(pid_file, "a") as record:
        record.write(f"{os.getpid()}\n")
    sys.exit("pandoc: the text was rejected")
if hit and not (once and Path(pid_file).exists()):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3600)"])
    with open(pid_file, "a") as record:
        record.write(f"{os.getpid()} {child.pid}\n")
    time.sleep(3600)
sys.exit(subprocess.run([real, *args]).returncode)
