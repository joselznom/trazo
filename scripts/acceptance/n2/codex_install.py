"""n2-install-codex: the codex CLI installs the plugin from a folder marketplace, in a sandbox."""

from pathlib import Path

from ..context import Context
from ..hosts import codex
from ..registry import scenario

# What the installed copy needs to run in Codex (the cache drops symlinks, so check what is really there).
CACHED_FILES = (".codex-plugin/plugin.json", ".codex-mcp.json", "hooks/hooks.json", "plugin/mcp.sh",
                "plugin/session-start.sh", "plugin/scripts/setup.sh", "plugin/skills", "packages/trazo/src/trazo/skills")


@scenario("n2-install-codex", needs=("darwin",))
def install(ctx: Context) -> None:
    sb, head = ctx.sandbox, ctx.checkout()
    codex.check_isolated(sb)

    codex.marketplace_add(sb, sb.plugin(head))
    (before,) = codex.plugin_rows(sb)
    assert not before.installed, before
    root = codex.plugin_add(sb)

    (row,) = codex.plugin_rows(sb)
    assert row.status == "installed, enabled" and row.version == head.version, row
    cache = sb.codex_home / "plugins" / "cache" / "trazo" / "trazo" / head.version
    assert root.resolve() == cache.resolve(), f"installed into {root}, not the sandbox cache {cache}"
    assert codex.cache_versions(sb) == [head.version]
    missing = [name for name in CACHED_FILES if not (cache / name).exists()]
    assert not missing, f"the installed copy lacks {missing}"
    listing = codex.run(sb, ["plugin", "marketplace", "list"]).stdout
    assert [line.split()[0] for line in listing.splitlines()[1:]] == ["trazo"], f"other marketplaces visible:\n{listing}"

    # The hook is listed but waits for trust; the MCP server starts (the stand-in, as nothing is set up).
    (hook,) = codex.hook_list(sb)
    assert hook["trustStatus"] == "untrusted" and hook["eventName"] == "sessionStart", hook
    assert Path(hook["sourcePath"]).resolve().is_relative_to(sb.root), hook["sourcePath"]
    ctx.evidence.note(f"hook {hook['key']} is {hook['trustStatus']} right after the install")
    state = codex.mcp_state(sb)
    assert state.tools == ["setup_status"] and state.version == head.version, state
