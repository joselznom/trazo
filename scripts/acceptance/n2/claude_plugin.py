"""n2: installing and updating the plugin with the real claude CLI, from a folder marketplace."""

import shutil
from pathlib import Path

from ..context import Context
from ..hosts.claude import Claude
from ..registry import scenario
from ..sandbox import clone

SAY_OK = "Reply with the single word: ok"


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
    claude.marketplace_update()
    ctx.evidence.note(f"plugin update: {claude.update().strip()}")
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
