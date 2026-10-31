"""What the server leaves behind when the app dies (docs/plugin.md check 4)."""

import json
import sys
from pathlib import Path

from ..context import Context
from ..registry import scenario
from .support import alive, answers, mcp_session, new_paper, serving, wait_until

HOST = Path(__file__).with_name("app_host.py")
# commands.docx of the paper: records its pid, then outlasts the scenario unless something stops it.
SLOW_DOCX = "import os, pathlib, time; pathlib.Path({marker!r}).write_text(str(os.getpid())); time.sleep(300)"


@scenario("n1-force-quit-build", needs=("darwin", "seed"))
def force_quit_build(ctx: Context) -> None:
    sb, head = ctx.sandbox, ctx.checkout()
    sb.install(head)
    plugin = sb.plugin(head)
    setup = mcp_session(sb, plugin)
    paper = new_paper(setup, sb.work / "paper")
    assert setup.close()[0] == 0

    marker = sb.tmp / "docx.pid"
    python = sb.data / "venv" / "bin" / "python"  # absolute: the apps' PATH has no usable python
    with open(paper / "trazo.toml", "a", encoding="utf-8") as config:
        config.write(f"\n[commands]\ndocx = {json.dumps([str(python), '-c', SLOW_DOCX.format(marker=str(marker))])}\n")
    docx = paper / "output" / "manuscript.docx"
    docx.parent.mkdir(exist_ok=True)
    docx.write_bytes(b"last good build")

    state_file = sb.tmp / "app-host.json"
    app = sb.spawn([sys.executable, "-I", HOST, plugin, paper, state_file])
    assert wait_until(state_file.is_file, 60), f"the app stand-in never got a preview (exit {app.poll()})"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    server, preview = state["server"], state["preview"]
    assert wait_until(lambda: marker.is_file() and marker.read_text(), 60), "the build never reached commands.docx"
    building = int(marker.read_text())
    assert serving(preview) and (paper / ".build" / "preview.json").is_file(), "no preview while the build runs"
    ctx.evidence.note(f"server {server}, preview {preview['pid']} on port {preview['port']}, "
                      f"build command {building} running: force-quitting the app (pid {app.pid})")

    app.kill()
    app.wait(10)

    assert wait_until(lambda: not alive(server), 30), f"the server (pid {server}) outlived the app"
    assert wait_until(lambda: not alive(building), 30), f"commands.docx (pid {building}) kept running after the app died"
    assert wait_until(lambda: not alive(preview["pid"]) and not answers(preview["port"]), 30), \
        f"the preview (pid {preview['pid']}) outlived the app"
    assert wait_until(lambda: not (paper / ".build" / "preview.json").exists(), 10), "preview.json is still there"
    assert docx.read_bytes() == b"last good build", "the previous Word file was changed"
    assert wait_until(lambda: not sb.stray_pids(), 30), f"processes still running: {sb.stray_pids()}"
