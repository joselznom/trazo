"""The hook and setup_status against an install of the same, an older and a newer
trazo than the plugin (docs/plugin.md checks 6 to 8)."""

from ..context import Context
from ..drive import run_hook
from ..registry import scenario
from .support import fingerprint, installed_version, mcp_session

# What setup downloads; an offline reinstall must leave them as they are.
DOWNLOADS = ("uv", "python", "pandoc", "chrome")


def next_step(ctx: Context, plugin) -> dict:
    """setup_status from a new chat of `plugin`."""
    mcp = mcp_session(ctx.sandbox, plugin)
    status = mcp.call("setup_status").data
    assert mcp.close()[0] == 0
    return status


@scenario("n1-setup-status-ok", needs=("darwin", "seed"))
def setup_status_ok(ctx: Context) -> None:
    sb, head = ctx.sandbox, ctx.checkout()
    sb.install(head)
    plugin = sb.plugin(head)
    assert run_hook(sb, plugin).context is None, "the hook must be silent once setup is done"
    status = next_step(ctx, plugin)
    assert status["next"] == "ok", status
    assert status["install"]["version"] == head.version and status["install"]["matches_plugin"], status
    assert status["uv"]["found"] and status["pandoc"]["found"], status


@scenario("n1-old-install-update", needs=("darwin", "seed"))
def old_install_update(ctx: Context) -> None:
    sb, head, last = ctx.sandbox, ctx.checkout(), ctx.last_release()
    sb.install(last)
    assert installed_version(sb) == last.version, installed_version(sb)
    plugin = sb.plugin(head)

    note = run_hook(sb, plugin).context
    assert note and f"trazo {last.version} is installed but the plugin is {head.version}" in note, note
    assert "setup" in note, note
    status = next_step(ctx, plugin)
    assert status["next"] == "update", status
    assert status["install"]["version"] == last.version, status

    before = {name: fingerprint(sb.data / name) for name in DOWNLOADS if (sb.data / name).exists()}
    assert {"uv", "python"} <= set(before), f"the seed gave no uv or Python: {sorted(before)}"
    done = sb.run(["/bin/sh", plugin / "plugin/scripts/setup.sh", "--offline", plugin], timeout=600)
    # Exit 3 is "something must be downloaded"; 0 with the same folders is no download at all.
    assert done.returncode == 0, f"offline setup exited {done.returncode}:\n{(done.stdout + done.stderr)[-800:]}"
    assert "Downloading" not in done.stdout + done.stderr, done.stdout + done.stderr
    for name, was in before.items():
        assert fingerprint(sb.data / name) == was, f"setup changed {name}/ although it downloads nothing offline"
    ctx.evidence.note(f"offline setup left {', '.join(before)} as they were")

    assert installed_version(sb) == head.version, installed_version(sb)
    assert run_hook(sb, plugin).context is None, "the hook must be silent after the reinstall"
    assert next_step(ctx, plugin)["next"] == "ok"


@scenario("n1-install-newer-than-plugin", needs=("darwin", "seed"))
def install_newer_than_plugin(ctx: Context) -> None:
    sb, newer, older = ctx.sandbox, ctx.checkout(2), ctx.checkout(1)
    sb.install(newer)  # the other app's newer plugin installed trazo
    plugin = sb.plugin(older)

    note = run_hook(sb, plugin).context
    assert note and f"trazo {newer.version} is installed, newer than this plugin ({older.version})" in note, note
    assert "update the trazo plugin" in note and "Do not run the trazo:setup skill" in note, note
    status = next_step(ctx, plugin)
    assert status["next"] == "plugin", status
    assert status["install"]["version"] == newer.version and status["plugin"]["version"] == older.version, status
    assert installed_version(sb) == newer.version, "looking must not change the install"
