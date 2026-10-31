"""n2-update-codex and n2-codex-hook-retrust: from the last release to this checkout with the codex CLI."""

import json
import shutil
from pathlib import Path

from ..context import Context
from ..hosts import codex
from ..registry import scenario
from ..sandbox import clone
from ..versions import PluginFolder, manifest_version


def codex_version(folder: Path) -> str:
    return json.loads((folder / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))["version"]


def put_release(market: Path, folder: PluginFolder) -> None:
    """Make the marketplace folder hold `folder`: a local marketplace is its folder, so a release
    is replacing the files (a git marketplace would `marketplace upgrade`, which a folder refuses)."""
    shutil.rmtree(market, ignore_errors=True)
    clone(folder.path, market)


ASK_OK = "Reply with the single word ok. Use no tools."


def install_last_release(ctx: Context):
    """Isolation check, then the last release installed from a folder marketplace with its hook trusted.
    Returns (marketplace folder, last release, this checkout, the trusted hook)."""
    sb, last, head = ctx.sandbox, ctx.last_release(), ctx.checkout()
    # Codex reads .codex-plugin/plugin.json; the hook and setup_status read .claude-plugin/plugin.json.
    for folder in (last, head):
        assert codex_version(folder.path) == manifest_version(folder.path) == folder.version, folder
    assert head.version != last.version
    codex.check_isolated(sb)

    market = sb.root / "codex-marketplace"
    put_release(market, last)
    codex.marketplace_add(sb, market)
    codex.plugin_add(sb)
    assert codex.cache_versions(sb) == [last.version]
    old_hook, = codex.trust_hooks(sb)
    return market, last, head, old_hook


@scenario("n2-update-codex", needs=("darwin",))
def update(ctx: Context) -> None:
    sb = ctx.sandbox
    market, last, head, old_hook = install_last_release(ctx)
    state = codex.mcp_state(sb)
    assert state.version == last.version, state

    put_release(market, head)
    root = codex.plugin_add(sb)       # what a folder marketplace offers: add again (`plugin/list` in the app does it too)
    (row,) = codex.plugin_rows(sb)
    assert row.installed and row.version == head.version, row
    assert codex.cache_versions(sb) == [head.version], f"the old release folder must be gone: {codex.cache_versions(sb)}"
    assert root.name == head.version and root.is_dir()
    ctx.evidence.note(f"cache: {last.version} replaced by {head.version}, old folder deleted")
    assert codex.mcp_state(sb).version == head.version

    # Product finding: the hook's hash includes the cache path, which has the version in it, so every
    # update leaves the hook "modified" and Codex skips it until it is trusted again.
    (hook,) = codex.hook_list(sb)
    assert hook["key"] == old_hook["key"], (hook["key"], old_hook["key"])
    assert hook["trustStatus"] == "modified", hook
    ctx.evidence.note(f"FINDING: after the update the hook {hook['key']} is '{hook['trustStatus']}', "
                      f"so Codex skips it until the user trusts it again (trusted at {last.version})")


@scenario("n2-codex-hook-retrust", needs=("darwin",))
def hook_retrust(ctx: Context) -> None:
    """The model-dependent half of the update finding. A separate scenario, so that n2-update-codex needs no
    login and never passes while this half was not checked: without a login this one is skipped."""
    sb = ctx.sandbox
    market, _last, head, old_hook = install_last_release(ctx)
    put_release(market, head)
    codex.plugin_add(sb)
    (hook,) = codex.hook_list(sb)
    assert hook["key"] == old_hook["key"] and hook["trustStatus"] == "modified", hook

    stale = codex.session(sb, None, ASK_OK, cwd=sb.work)  # Skip here: no usable login
    assert stale.hook_notes == [], f"a modified hook still ran: {stale.hook_notes}"
    codex.trust_hooks(sb)
    fresh = codex.session(sb, None, ASK_OK, cwd=sb.work)
    assert fresh.hook_notes and "not set up" in fresh.hook_notes[0], fresh.hook_notes
    ctx.evidence.note("session proof: no hook note while modified, the 'not set up' note after trusting again")
