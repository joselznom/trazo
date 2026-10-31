"""Drive the real `codex` CLI in a sandbox: install, update, hook trust, MCP state and sessions.

Host constraints (Codex 0.164.0):

- Codex gives hooks `TRAZO_DATA_DIR` but not the MCP server, which then falls back to
  `$HOME/Library/Application Support/trazo`. So HOME must be temporary and the
  sandbox's data folder must be that path (`sandbox_env` checks it).
- With a login present, a sandboxed Codex still lists the account's connectors (Gmail,
  GitHub, Drive: the `codex_apps` MCP server, hundreds of tools). Every call here passes
  `--disable apps`, and the login is copied in only for the duration of a model run.
- Codex never touches the macOS keychain here: both credential stores are forced to
  `file`, and a login that lives only in the keychain makes a model run skip.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .. import guard, versions
from ..evidence import redact
from ..registry import Skip
from ..sandbox import Sandbox
from .codex_appserver import AppServer, CodexError, approve_edit, hooks_of, trust_edit
from .codex_events import find_rollout, hook_notes, parse_exec, scrub_rollouts
from .trace import SessionFailed, Trace

PLUGIN_ID = "trazo@trazo"
MCP_SERVER = "trazo"   # the name the plugin's MCP server has in Codex (`server` of an mcp_tool_call)
# The model every session uses: the n2 scenarios ask for "ok" and need nothing more, so the cheapest one.
MODEL = "gpt-6-luna"
EFFORT = "low"
# Options for every invocation: no account connectors; credentials only ever in files.
GLOBAL = ["--disable", "apps", "-c", 'cli_auth_credentials_store="file"', "-c", 'mcp_oauth_credentials_store="file"']
SESSIONS = versions.REPO / ".build" / "acceptance" / "sessions"
MIN_TOKEN_SECONDS = 3600  # a login that expires sooner would be refreshed inside the sandbox


def codex_bin() -> str:
    found = shutil.which("codex")
    if not found:
        raise Skip("the codex CLI is not installed (not on PATH)")
    return found


def sandbox_env(sb: Sandbox, extra: Mapping[str, str | None] | None = None) -> dict[str, str]:
    """The sandbox environment, after checking that the data folder is where Codex's MCP server looks."""
    if sb.data != sb.home / "Library" / "Application Support" / "trazo":
        raise CodexError(f"the data folder {sb.data} is not $HOME/Library/Application Support/trazo: "
                         "Codex does not pass TRAZO_DATA_DIR to the MCP server, so it would use the real one")
    return sb.env(extra)


def run(sb: Sandbox, args: list[str | Path], check: bool = True, **options):
    return sb.run([codex_bin(), *GLOBAL, *args], env=sandbox_env(sb), check=check, **options)


def server(sb: Sandbox, timeout: float = 30) -> AppServer:
    """`with server(sb) as s: s.request(...)`: an app-server in the sandbox."""
    return AppServer(sb, [codex_bin(), *GLOBAL], sandbox_env(sb), timeout)


# --- install and update -------------------------------------------------------

def check_isolated(sb: Sandbox) -> None:
    """Before anything is installed, Codex must see no marketplace at all: any would be the real ones.
    Run it before the login is copied in, which makes Codex add its own curated marketplace."""
    listing = run(sb, ["plugin", "marketplace", "list"]).stdout
    if "No plugin marketplaces" not in listing:
        raise CodexError(f"the sandboxed codex sees marketplaces before any install:\n{listing}")
    sb.evidence.note("codex plugin marketplace list in the fresh sandbox: no marketplaces")


def marketplace_add(sb: Sandbox, folder: Path) -> None:
    run(sb, ["plugin", "marketplace", "add", folder])


def plugin_add(sb: Sandbox) -> Path:
    """Install (or update to) the marketplace's trazo; the folder Codex says it installed into."""
    done = run(sb, ["plugin", "add", PLUGIN_ID])
    found = re.search(r"Installed plugin root: (.+)", done.stdout)
    if not found:
        raise CodexError(f"plugin add did not say where it installed:\n{done.stdout}")
    return Path(found[1].strip())


@dataclass(frozen=True)
class PluginRow:
    id: str
    status: str            # "installed, enabled", "not installed", ...
    version: str | None
    source: str

    @property
    def installed(self) -> bool:
        return self.status.startswith("installed")


def parse_plugin_list(text: str, plugin_id: str = PLUGIN_ID) -> list[PluginRow]:
    """The rows of `codex plugin list` for one plugin (with a login it also lists remote ones).
    Columns are separated by two or more spaces; a plugin that is not installed has no version."""
    rows = []
    for line in text.splitlines():
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) < 3 or parts[0] != plugin_id:
            continue
        installed = parts[1].startswith("installed")
        rows.append(PluginRow(parts[0], parts[1], parts[2] if installed else None, parts[-1]))
    return rows


def plugin_rows(sb: Sandbox) -> list[PluginRow]:
    return parse_plugin_list(run(sb, ["plugin", "list"]).stdout)


def cache_versions(sb: Sandbox) -> list[str]:
    """The version folders of trazo in this sandbox's plugin cache."""
    cache = sb.codex_home / "plugins" / "cache" / "trazo" / "trazo"
    return sorted(p.name for p in cache.iterdir()) if cache.is_dir() else []


def ensure_plugin(sb: Sandbox, root: Path) -> None:
    """Make the plugin folder `root` the installed trazo, with its hook trusted."""
    version = versions.manifest_version(root)
    rows = plugin_rows(sb)
    if not (rows and rows[0].installed and rows[0].version == version):
        if "trazo" in run(sb, ["plugin", "marketplace", "list"]).stdout:
            run(sb, ["plugin", "marketplace", "remove", "trazo"])
        marketplace_add(sb, root)
        plugin_add(sb)
    trust_hooks(sb)
    approve_tools(sb)


# --- hooks and MCP state, through the app-server ------------------------------

def hook_list(sb: Sandbox) -> list[dict]:
    """trazo's hooks as Codex reports them: `key`, `trustStatus` (untrusted, trusted, modified), `currentHash`."""
    with server(sb) as s:
        return hooks_of(s.request("hooks/list", {"cwds": [str(sb.work)]}), PLUGIN_ID)


def trust_hooks(sb: Sandbox) -> list[dict]:
    """Trust trazo's hooks as they are now (an untrusted or modified hook is skipped silently).
    `config/batchWrite` also works on a config without a hooks table, so there is one path."""
    with server(sb) as s:
        hooks = hooks_of(s.request("hooks/list", {"cwds": [str(sb.work)]}), PLUGIN_ID)
        if not hooks:
            raise CodexError("codex lists no hooks for trazo@trazo: is the plugin installed?")
        s.request("config/batchWrite", trust_edit(hooks))
        trusted = hooks_of(s.request("hooks/list", {"cwds": [str(sb.work)]}), PLUGIN_ID)
    if any(hook["trustStatus"] != "trusted" for hook in trusted):
        raise CodexError(f"the hooks are not trusted after the write: {trusted}")
    sb.evidence.note(f"trusted {len(trusted)} trazo hook(s): {[h['key'] for h in trusted]}")
    return trusted


def approve_tools(sb: Sandbox) -> None:
    """Approve the tools of trazo's MCP server, and no other. In the ChatGPT app the user approves each
    call; `codex exec` has nobody to ask and, with `approval_policy="never"`, refuses any MCP tool that
    is not pre-approved. `default_tools_approval_mode = "approve"` under the plugin's own server is the
    narrowest key Codex has: it leaves other servers and the sandbox as they are."""
    with server(sb) as s:
        s.request("config/batchWrite", approve_edit(PLUGIN_ID, MCP_SERVER))
    sb.evidence.note(f"approved the tools of the {MCP_SERVER} MCP server of {PLUGIN_ID} (config.toml, plugin table)")


@dataclass(frozen=True)
class McpState:
    tools: list[str]       # trazo's tool names, as the server lists them
    version: str | None    # the server's own version (serverInfo)


def mcp_state(sb: Sandbox, wait: float = 20) -> McpState:
    """trazo's MCP server as Codex sees it, without a model: `mcpServerStatus/list`.
    The server starts on demand, so ask again until its tools show up."""
    deadline, state = time.monotonic() + wait, McpState([], None)
    with server(sb) as s:
        while True:
            rows = s.request("mcpServerStatus/list", {"detail": "full"})["data"]
            row = next((r for r in rows if r.get("pluginId") == PLUGIN_ID), None)
            if row:
                state = McpState(sorted(row["tools"]), (row.get("serverInfo") or {}).get("version"))
            if not row or state.tools or time.monotonic() > deadline:
                break
            time.sleep(0.5)
    sb.evidence.note(f"mcp tools seen by codex: {', '.join(state.tools) or 'none'} (server version {state.version})")
    return state


def mcp_tools(sb: Sandbox) -> list[str]:
    return mcp_state(sb).tools


# --- credentials -------------------------------------------------------------

def real_codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or guard.real_home() / ".codex")


def seconds_left(auth: dict) -> float | None:
    """Until the ChatGPT access token expires (the `exp` of its JWT), or None when it has none."""
    token = (auth.get("tokens") or {}).get("access_token")
    if not token:
        return None
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["exp"] - time.time()
    except (IndexError, ValueError, KeyError):
        return None


def login_secrets(auth: dict) -> list[str]:
    """Every credential in an auth.json: the three ChatGPT tokens and an API key. The values the
    session output, the evidence and the saved logs must never show."""
    tokens = auth.get("tokens") or {}
    values = [auth.get("OPENAI_API_KEY"), *(tokens.get(name) for name in ("access_token", "refresh_token", "id_token"))]
    return [value for value in values if isinstance(value, str) and value]


def read_login() -> tuple[bytes, dict]:
    """The real `auth.json` and its parsed content, or Skip when there is no login a model run can use
    (a login kept in the keychain is not read; one about to expire would be refreshed inside the sandbox
    and leave the real login stale)."""
    source = real_codex_home() / "auth.json"
    if not source.is_file():
        raise Skip("model runs need a Codex login stored as a file (~/.codex/auth.json); there is none, and the "
                   "keychain is never read. Log in outside the harness with file storage, then run again")
    data = source.read_bytes()
    try:
        auth = json.loads(data)
    except ValueError:
        raise Skip("~/.codex/auth.json is not valid JSON") from None
    if not auth.get("OPENAI_API_KEY") and not auth.get("tokens"):
        raise Skip("~/.codex/auth.json holds no token or API key")
    left = seconds_left(auth)
    if left is not None and left < MIN_TOKEN_SECONDS:
        raise Skip(f"the Codex login expires in {max(left, 0) / 60:.0f} min: use codex once outside the harness "
                   "so it refreshes, then run again (a refresh inside the sandbox would leave the real login stale)")
    return data, auth


@contextmanager
def credentials(sb: Sandbox) -> Iterator[list[str]]:
    """The login for a model run: `auth.json` of the real Codex home copied into the sandbox with
    mode 600 and deleted afterwards, also when the run fails. Never printed or put in the evidence.
    Skips as read_login says. Yields the login's secrets, to redact from what the run prints."""
    data, auth = read_login()
    target = sb.codex_home / "auth.json"
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
    try:
        yield login_secrets(auth)
    finally:
        refreshed = target.is_file() and target.read_bytes() != data
        target.unlink(missing_ok=True)
        if refreshed:
            sb.evidence.note("WARNING: codex rewrote the copied login (a token refresh). The real login's refresh "
                             "token may have rotated: if Codex asks you to log in again, that is why.")


# --- sessions ----------------------------------------------------------------

def session(sb: Sandbox, plugin: versions.PluginFolder | Path | None, prompt: str, *, cwd: Path,
            timeout: float = 300, extra_env: Mapping[str, str | None] | None = None) -> Trace:
    """One `codex exec` turn in `cwd` (a folder inside the sandbox), as a host-neutral Trace.

    plugin: a PluginFolder, or a plugin folder already in the sandbox (`sb.plugin(...)`), is made the
        installed trazo with its hook trusted before the run (nothing changes when it already is);
        None leaves the sandbox's Codex home as it is (what an update or hook scenario wants).
    The agent runs with `-s workspace-write` (writes only inside cwd and the temporary folders, no network)
        and `approval_policy="never"` (nothing waits for a person; whatever the sandbox forbids fails).
        trazo's MCP tools are pre-approved in the sandbox's config (`approve_tools`), as the user
        approves them in the app; any other MCP tool would fail.
    Raises Skip without a usable login and SessionFailed (with the partial Trace) when the turn fails,
    is cut short or times out."""
    if not Path(cwd).resolve().is_relative_to(sb.root):
        raise CodexError(f"cwd {cwd} is outside the sandbox {sb.root}: the agent may write there")
    if plugin is not None:
        ensure_plugin(sb, plugin if isinstance(plugin, Path) else sb.plugin(plugin))
    argv = ["exec", "--json", "--skip-git-repo-check", "-C", str(cwd), "-m", MODEL,
            "-c", f'model_reasoning_effort="{EFFORT}"',
            "-s", "workspace-write", "-c", 'approval_policy="never"', "--", prompt]
    SESSIONS.mkdir(parents=True, exist_ok=True)
    raw = SESSIONS / f"codex-{time.strftime('%Y%m%dT%H%M%S')}-{time.monotonic_ns()}.jsonl"
    started = time.monotonic()
    with credentials(sb) as secrets:
        try:
            # input="" closes stdin at once, as </dev/null: exec waits for stdin otherwise.
            done = sb.run([codex_bin(), *GLOBAL, *argv], env=sandbox_env(sb, extra_env), cwd=cwd, timeout=timeout, input="",
                          secrets=secrets)
        except Exception as error:  # a timeout's partial output has been redacted by sb.run
            partial = redact(str(getattr(error, "stdout", None) or ""), secrets)
            raw.write_text(partial, encoding="utf-8")
            events = parse_exec(partial)
            scrub_rollouts(sb.codex_home, secrets)  # a session that died leaves its rollout too
            raise SessionFailed(f"codex exec did not finish ({type(error).__name__}); partial output in {raw}",
                                _trace(events, [], None, time.monotonic() - started, raw)) from error
        scrub_rollouts(sb.codex_home, secrets)  # before anything reads or keeps the rollout
    duration = time.monotonic() - started
    raw.write_text(done.stdout, encoding="utf-8")
    sb.evidence.path("codex exec JSONL", raw)
    events = parse_exec(done.stdout)
    problem = events.failure or (f"exit {done.returncode}" if done.returncode else None) or (
        None if events.completed else "no turn.completed event: the turn was cut short")
    if problem:
        raise SessionFailed(f"codex exec failed ({problem}): {events.failure or done.stderr[-500:]}\nlog: {raw}",
                            _trace(events, [], None, duration, raw))

    rollout = find_rollout(sb.codex_home, events.thread_id) if events.thread_id else None
    notes = [redact(note, secrets) for note in hook_notes(rollout)] if rollout else []
    try:
        tools = mcp_tools(sb)
    except CodexError as error:
        sb.evidence.note(f"mcp tools not read: {error}")
        tools = None
    return _trace(events, notes, tools, duration, raw)


def _trace(events, notes: Sequence[str], tools: list[str] | None, duration: float, raw: Path) -> Trace:
    # Codex reports no cost (None).
    return Trace("codex", events.tool_calls, list(notes), tools, None, duration, raw)
