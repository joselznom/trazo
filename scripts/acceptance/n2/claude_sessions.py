"""n2: what a real `claude` session shows once the plugin is installed."""

import json

from ..context import Context
from ..hosts.claude import Claude
from ..registry import scenario
from .claude_plugin import SAY_OK

TOOLS = {"build", "check", "doctor", "paper_create", "paper_update", "preview_start", "preview_stop", "setup_status"}
ASK_STATUS = "Call the trazo setup_status tool once, then reply with only the value of its `next` field."
STATUS_TOOL = ["mcp__plugin_trazo_trazo__setup_status"]
# Measured on this Mac (Claude Code 2.1.312, a real install, six sessions): 39 to 80 ms. A limit of 1 s is
# over ten times that, so only a real regression (a slow start of trazo mcp) trips it, and still far under
# the 30 s after which Claude Code gives up on an MCP server.
MAX_CONNECT_MS = 1000


@scenario("n2-slash-commands", needs=("darwin", "seed"))
def slash_commands(ctx: Context) -> None:
    sb, head = ctx.sandbox, ctx.checkout()
    sb.install(head)
    claude = Claude(sb)
    claude.install_from(sb.plugin(head))
    sb.run([sb.data / "venv/bin/trazo", "new", sb.work / "paper", "--kind", "article", "--title", "Sleep and memory",
            "--json"], check=True)

    plain = claude.run_session(None, SAY_OK, cwd=sb.work, tag="folder")
    paper = claude.run_session(None, SAY_OK, cwd=sb.work / "paper", tag="paper")
    wanted = {"trazo:open", "trazo:close", "trazo:comments"}
    for label, run in (("empty folder", plain), ("paper", paper)):
        trazo = {c for c in run.slash_commands if c.startswith("trazo:")}
        assert trazo == wanted, f"{label}: /trazo: commands are {sorted(trazo)}, expected {sorted(wanted)}"
    # What the paper's own .claude/skills add beside the plugin's three (docs/plugin.md check 1).
    added = [c for c in paper.slash_commands if c not in plain.slash_commands]
    ctx.evidence.data("slash commands the paper adds", added)
    ctx.evidence.data("skills the paper adds", [s for s in paper.init.get("skills", []) if s not in plain.init.get("skills", [])])
    ctx.evidence.data("template .claude/skills", sorted(p.name for p in (sb.work / "paper/.claude/skills").iterdir()))
    assert "comments" in added, f"the paper's own `comments` skill is not listed: {added}"
    assert not any(c.startswith("trazo:") for c in added), added


@scenario("n2-mcp-connection-time", needs=("darwin", "seed"))
def connection_time(ctx: Context) -> None:
    sb, head = ctx.sandbox, ctx.checkout()
    sb.install(head)
    claude = Claude(sb)
    claude.install_from(sb.plugin(head))
    run = claude.run_session(None, SAY_OK, tag="connect")
    assert run.connect_ms is not None, f"no 'Successfully connected' line for plugin:trazo:trazo in {run.trace.raw_path.name}'s debug log"
    ctx.evidence.note(f"trazo MCP server connected in {run.connect_ms} ms (limit {MAX_CONNECT_MS} ms)")
    assert run.connect_ms <= MAX_CONNECT_MS, f"connected in {run.connect_ms} ms, over {MAX_CONNECT_MS} ms"


@scenario("n2-resume-no-hook-note", needs=("darwin",))
def resume(ctx: Context) -> None:
    sb, head = ctx.sandbox, ctx.checkout()
    folder = sb.plugin(head)
    # Without a hook that does match `resume` a resumed session shows no hook events at all, which would
    # not tell "resume skipped the trazo hook" from "resume never ran hooks". This one fires on resume only.
    hooks_file = folder / "hooks/hooks.json"
    hooks = json.loads(hooks_file.read_text(encoding="utf-8"))
    trazo_matcher = hooks["hooks"]["SessionStart"][0]["matcher"]
    assert "resume" not in trazo_matcher.split("|"), f"trazo's SessionStart matcher is {trazo_matcher!r}"
    hooks["hooks"]["SessionStart"].append({"matcher": "resume", "hooks": [{"type": "command", "command": "/usr/bin/true"}]})
    hooks_file.write_text(json.dumps(hooks, indent=2), encoding="utf-8")

    claude = Claude(sb)
    claude.install_from(folder)
    first = claude.run_session(None, SAY_OK, tag="first")
    assert first.trace.hook_notes and "not set up" in first.trace.hook_notes[0], first.trace.hook_notes
    assert first.hook_names == ["SessionStart:startup"], first.hook_names
    assert first.session_id

    second = claude.run_session(None, SAY_OK, resume=first.session_id, tag="resumed")
    ctx.evidence.data("hook events of the resumed session", second.hook_responses)
    assert second.session_id == first.session_id, "--resume started another session"
    assert second.hook_names == ["SessionStart:resume"], f"expected the resume probe to fire: {second.hook_names}"
    assert second.trace.hook_notes == [], f"the hook's note came again on resume: {second.trace.hook_notes}"


@scenario("n2-no-venv-standin", needs=("darwin", "seed"))
def standin(ctx: Context) -> None:
    sb, head = ctx.sandbox, ctx.checkout()
    claude = Claude(sb)
    claude.install_from(sb.plugin(head))

    before = claude.run_session(None, ASK_STATUS, allowed_tools=STATUS_TOOL, max_turns=5, tag="standin")
    assert before.mcp_status() == "connected", before.init.get("mcp_servers")
    assert before.trace.mcp_tools == ["trazo.setup_status"], f"the stand-in has {before.trace.mcp_tools}"
    status = before.output_of("trazo.setup_status")
    assert status and not status.is_error, "setup_status was not called or failed"
    assert status.json()["next"] == "setup", status.text[:300]

    sb.install(head)  # what the setup skill does
    after = claude.run_session(None, ASK_STATUS, allowed_tools=STATUS_TOOL, max_turns=5, tag="installed")
    assert after.mcp_status() == "connected", after.init.get("mcp_servers")
    assert set(after.trace.mcp_tools or []) == {f"trazo.{t}" for t in TOOLS}, after.trace.mcp_tools
    status = after.output_of("trazo.setup_status")
    assert status and status.json()["next"] == "ok", status and status.text[:300]
