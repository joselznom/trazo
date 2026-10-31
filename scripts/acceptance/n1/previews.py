"""The preview's port and who may share it (docs/plugin.md checks 2 and 10)."""

from ..context import Context
from ..registry import scenario
from ..sandbox import busy_port
from .support import answers, mcp_session, new_paper, preview_record, serving, wait_until

PREVIEW_PORT = 8765  # trazo.mcp_preview.PREVIEW_PORT: mcp.sh gives no way to move it


@scenario("n1-port-busy", needs=("darwin", "seed"))
def port_busy(ctx: Context) -> None:
    sb, head = ctx.sandbox, ctx.checkout()
    sb.install(head)
    mcp = mcp_session(sb, sb.plugin(head))
    paper = new_paper(mcp, sb.work / "paper")
    with busy_port(PREVIEW_PORT, ctx.evidence):  # a process that already holds the port counts
        started = mcp.call("preview_start", project=str(paper))
        assert not started.is_error, started.text
        info = started.data
        assert info["port"] != PREVIEW_PORT, f"preview_start took the busy port: {info}"
        assert info["url"] == f"http://127.0.0.1:{info['port']}", info
        # The agent reads the URL from preview.json, so it must be the one that answers.
        record = preview_record(paper)
        assert record["url"] == info["url"] and record["port"] == info["port"], (record, info)
        assert serving(record), f"nothing at {record['url']} says it is this paper's preview"
        ctx.evidence.note(f"port {PREVIEW_PORT} busy: the preview of this paper answers at {record['url']}")
    assert mcp.close()[0] == 0
    assert wait_until(lambda: not answers(info["port"])), "the preview outlived its server"
    assert not (paper / ".build" / "preview.json").exists(), "preview.json outlived the preview"


@scenario("n1-two-sessions-share-preview", needs=("darwin", "seed"))
def two_sessions(ctx: Context) -> None:
    sb, head = ctx.sandbox, ctx.checkout()
    sb.install(head)
    plugin = sb.plugin(head)
    first, second = mcp_session(sb, plugin), mcp_session(sb, plugin)
    paper = new_paper(first, sb.work / "paper")

    started = first.call("preview_start", project=str(paper)).data
    assert started["reused"] is False, started
    again = second.call("preview_start", project=str(paper)).data
    assert again["reused"] is True and (again["pid"], again["url"]) == (started["pid"], started["url"]), (started, again)
    assert serving(started), f"nothing at {started['url']} says it is this paper's preview"
    ctx.evidence.note(f"the second session reused pid {again['pid']} at {again['url']}")

    # The preview belongs to the session that started it, not to the one that reused it.
    assert second.close()[0] == 0
    assert answers(started["port"]) and (paper / ".build" / "preview.json").is_file(), \
        "closing the session that only reused the preview stopped it"
    assert first.close()[0] == 0
    assert wait_until(lambda: not answers(started["port"])), "the preview outlived the session that started it"
    assert wait_until(lambda: not (paper / ".build" / "preview.json").exists()), "preview.json outlived the preview"
