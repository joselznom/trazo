"""Codex scenarios about what the agent gets in each state of the install."""

from ..context import Context
from ..hosts import codex
from ..registry import scenario

TOOLS = {"setup_status", "doctor", "build", "check", "paper_create", "paper_update", "preview_start", "preview_stop"}


@scenario("n2-codex-tools-by-state", needs=("darwin", "seed"))
def tools_by_state(ctx: Context) -> None:
    sb, head = ctx.sandbox, ctx.checkout()
    codex.check_isolated(sb)
    codex.marketplace_add(sb, sb.plugin(head))
    codex.plugin_add(sb)

    # The computer running this has trazo installed: seeing only the stand-in proves the sandbox's data folder is used.
    before = codex.mcp_state(sb)
    assert before.tools == ["setup_status"], f"no install should give the stand-in's one tool: {before}"

    sb.install(head)
    after = codex.mcp_state(sb)
    assert set(after.tools) == TOOLS, sorted(set(after.tools) ^ TOOLS)
    # 99.0.x exists only in this sandbox: the real server cannot have answered.
    assert after.version == head.version, after
    assert (sb.data / "machine.json").is_file()


@scenario("n2-codex-hook-note", needs=("darwin",))
def hook_note(ctx: Context) -> None:
    sb, head = ctx.sandbox, ctx.checkout()
    codex.check_isolated(sb)
    trace = codex.session(sb, head, "Reply with the single word ok. Use no tools.", cwd=sb.work)
    ctx.evidence.data("hook notes", trace.hook_notes)
    assert len(trace.hook_notes) == 1 and "not set up" in trace.hook_notes[0], trace.hook_notes
    assert trace.mcp_tools == ["setup_status"], trace.mcp_tools
    assert not trace.called("trazo.setup_status"), "the prompt asked for no tools"
