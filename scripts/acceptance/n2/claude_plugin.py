"""n2: installing and updating the plugin with the real claude CLI, from a folder marketplace."""

import json
import os
import shutil
import sys
from pathlib import Path

from ..context import Context
from ..hosts.claude import Claude
from ..registry import scenario
from ..sandbox import clone

SAY_OK = "Reply with the single word: ok"
UPDATE = "import json; from trazo import updates; print(json.dumps(updates.plugin_update({})))"


def plugin_update(ctx: Context, claude: Claude, root: Path, head: Path) -> dict:
    """trazo.updates.plugin_update from the checkout's code, for the plugin installed at `root`."""
    env = {**claude.env, "TRAZO_PLUGIN_ROOT": str(root), "PYTHONPATH": str(head / "packages" / "trazo" / "src"),
           "PATH": os.pathsep.join([str(Path(claude.binary).parent), claude.env.get("PATH", "")])}
    done = ctx.sandbox.run([sys.executable, "-B", "-c", UPDATE], env=env, timeout=600, secrets=[claude.token])
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.splitlines()[-1])


@scenario("n2-install-claude", needs=("darwin",))
def install(ctx: Context) -> None:
    sb, head = ctx.sandbox, ctx.checkout()
    claude = Claude(sb)  # also proves the sandbox's claude knows only the built-in marketplace
    folder = sb.plugin(head)
    found = claude.install_from(folder)
    ctx.evidence.data("plugin list entry", {k: found.get(k) for k in ("id", "version", "enabled", "scope", "installPath")})
    assert found.get("version") == head.version and found.get("enabled"), found
    cache = Path(found["installPath"])
    assert cache.is_dir() and cache.is_relative_to(sb.claude_config), f"the plugin cache is {cache}, outside the sandbox"
    assert Path(found["readFromFolder"]).resolve() == folder.resolve(), found["readFromFolder"]
    assert claude.marketplaces() == ["anthropic-plugin-directory", "trazo"], claude.marketplaces()

    run = claude.run_session(None, SAY_OK, tag="install")
    assert run.mcp_status() == "connected", f"trazo MCP server: {run.mcp_status()} ({run.init.get('mcp_servers')})"
    assert run.plugin_version() == head.version, run.plugin_version()
    assert not run.result.get("is_error"), run.result
    ctx.evidence.note(f"session: trazo {run.plugin_version()} connected, tools {run.trace.mcp_tools}, "
                      f"${run.trace.cost_usd}, {run.trace.duration_s:.1f}s")


@scenario("n2-update-claude", needs=("darwin",))
def update(ctx: Context) -> None:
    sb, last, head = ctx.sandbox, ctx.last_release(), ctx.checkout()
    assert last.version != head.version, (last.version, head.version)
    claude = Claude(sb)
    # A folder marketplace is read live: replacing the folder's files is the release.
    market = sb.plugins / "marketplace"
    clone(last.path, market)
    before = claude.install_from(market)
    assert before.get("version") == last.version, before
    old_cache = Path(before["installPath"])
    assert not (old_cache / ".orphaned_at").exists()

    shutil.rmtree(market)
    clone(head.path, market)
    # Through trazo's plugin_update, as the agent does: it finds the config folder from the plugin's path
    # and runs `claude plugin marketplace update` and `claude plugin update` there.
    reply = plugin_update(ctx, claude, old_cache, sb.plugin(head))
    ctx.evidence.data("plugin_update", reply)
    assert reply == {"ok": True, "app": "claude", "from": last.version, "version": head.version,
                     "new_chat": True}, reply
    after = claude.installed() or {}
    new_cache = Path(after.get("installPath", ""))
    ctx.evidence.data("plugin list entry after", {k: after.get(k) for k in ("version", "installPath", "lastUpdated")})
    assert after.get("version") == head.version, after
    assert new_cache != old_cache and new_cache.is_dir(), (old_cache, new_cache)
    # The old version folder stays, marked, so a running session can finish with it.
    assert (old_cache / ".orphaned_at").is_file(), f"no .orphaned_at in {old_cache}: {sorted(p.name for p in old_cache.iterdir())}"
    assert not (new_cache / ".orphaned_at").exists()

    run = claude.run_session(None, SAY_OK, tag="updated")
    assert run.plugin_version() == head.version and run.mcp_status() == "connected", (run.plugin_version(), run.mcp_status())
