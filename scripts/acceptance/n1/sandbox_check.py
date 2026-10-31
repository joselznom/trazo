"""Self-checks of the harness itself: not product scenarios."""

from pathlib import Path

from .. import guard
from ..context import Context
from ..drive import McpClient, run_hook
from ..registry import scenario
from ..sandbox import busy_port, free_port, port_free


@scenario("n1-sandbox")
def isolation(ctx: Context) -> None:
    sb = ctx.sandbox
    env = sb.env()
    assert set(env) == {"PATH", "HOME", "TMPDIR", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "TRAZO_DATA_DIR"}, env
    for name in ("HOME", "TMPDIR", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "TRAZO_DATA_DIR"):
        assert Path(env[name]).resolve().is_relative_to(sb.root), f"{name} = {env[name]} is outside {sb.root}"
    assert sb.home != guard.real_home()
    assert "TRAZO_DATA_DIR" not in sb.env(data_dir=False)

    # A child sees exactly that environment, and a file it writes lands in the sandbox.
    seen = dict(line.split("=", 1) for line in sb.run(["/usr/bin/env"], check=True).stdout.splitlines())
    assert seen == env, seen
    sb.run(["/bin/sh", "-c", 'mkdir -p "$TRAZO_DATA_DIR" && echo hi > "$TRAZO_DATA_DIR/probe"'], check=True)
    assert (sb.data / "probe").read_text() == "hi\n"

    # The guard refuses the real locations before anything starts.
    home = guard.real_home()
    for bad in ({"HOME": str(home)}, {"CLAUDE_CONFIG_DIR": str(home / ".claude")}, {"CODEX_HOME": str(home / ".codex")},
                {"TRAZO_DATA_DIR": str(home / "Library/Application Support/trazo")},
                {"CLAUDE_CONFIG_DIR": str(home / ".claude-personal")}, {"TMPDIR": "/tmp"}, {"FOO": f"{home}/.codex/x"}):
        try:
            sb.run(["/usr/bin/true"], env=sb.env(bad))
        except guard.GuardError:
            continue
        raise AssertionError(f"the guard let {bad} through")
    ctx.evidence.note("the guard refused the real HOME, .claude*, .codex, the data folder and a TMPDIR outside the sandbox")

    port = free_port()
    assert port_free(port)
    with busy_port(port):
        assert not port_free(port)
    assert port_free(port)

    # Cleanup stops what the harness started (and what that left running), nothing else.
    process = sb.spawn(["/bin/sh", "-c", "sleep 300; :", str(sb.root)])
    assert process.pid in sb.stray_pids()


@scenario("n1-sandbox-chain", needs=("darwin", "seed"))
def chain(ctx: Context) -> None:
    sb, head, last = ctx.sandbox, ctx.checkout(1), ctx.last_release()
    assert head.version == "99.0.1" and last.version != head.version, (head.version, last.version)

    setup = sb.install(head)
    assert "Downloading" not in setup.stdout + setup.stderr, "an offline setup must download nothing"
    root = sb.plugin(head)
    assert run_hook(sb, root).context is None, "the hook must be silent when setup is done"

    mcp = McpClient(sb, root)
    mcp.initialize()
    assert {"setup_status", "preview_start", "build"} <= set(mcp.tools())
    status = mcp.call("setup_status").data
    assert status["next"] == "ok" and status["install"]["version"] == head.version, status
    assert mcp.close()[0] == 0

    # The release before is an older plugin than the install now.
    note = run_hook(sb, sb.plugin(last)).context
    assert note and "newer than this plugin" in note, note
