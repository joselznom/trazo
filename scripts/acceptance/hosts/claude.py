"""Drive the real `claude` CLI in a sandbox: install and update the plugin from
a folder marketplace, and run headless sessions that return a Trace.

    claude = Claude(sb)                        # Skip when claude or its login is missing
    claude.marketplace_add(sb.plugin(folder))  # claude.install(), claude.update(), claude.plugins()
    run = claude.run_session(None, "Say ok.", model="haiku")   # run.trace, run.init, run.hook_responses...

Credentials: a temporary CLAUDE_CONFIG_DIR has no login, so the OAuth access
token of the real one is read from the macOS keychain into
CLAUDE_CODE_OAUTH_TOKEN of the child only. It is never printed, logged or
written: commands are recorded here, not by Sandbox.run, with the token
redacted, and files the session wrote are scrubbed of it."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from .. import guard
from ..evidence import redact
from ..registry import Skip
from ..sandbox import Sandbox
from . import claude_events
from .claude_events import Session

BUILTIN_MARKETPLACE = "anthropic-plugin-directory"
INHERITED = ("CLAUDE", "AI_AGENT")   # variables of an agent's own session, which would leak into the child
TOKEN_MARGIN_S = 600                  # a token that expires sooner may lapse mid-run; it cannot be refreshed here


def real_config_dir(environ: Mapping[str, str] | None = None) -> Path:
    """The config dir of the account running the harness: $CLAUDE_CONFIG_DIR of this process, else ~/.claude."""
    value = (os.environ if environ is None else environ).get("CLAUDE_CONFIG_DIR")
    return Path(value).expanduser() if value else guard.real_home() / ".claude"


def keychain_services(config_dir: Path, custom: bool) -> list[str]:
    """Keychain items that hold the login: `Claude Code-credentials-<first 8 hex of sha256(dir)>`;
    with the default dir Claude Code also uses the bare `Claude Code-credentials`."""
    suffixed = f"Claude Code-credentials-{hashlib.sha256(str(config_dir).encode()).hexdigest()[:8]}"
    return [suffixed] if custom else [suffixed, "Claude Code-credentials"]


SECURITY = "/usr/bin/security"
CREDENTIAL_ITEM = "Claude Code-credentials"


def keychain_read_argv(service: str) -> list[str]:
    """The one keychain command the harness may run: a read of a Claude Code credentials item.
    The keychain is read-only for the harness (writing, deleting, unlocking or resetting one
    loses the owner's passwords), so anything else is refused here, before a process starts."""
    if service != CREDENTIAL_ITEM and not service.startswith(f"{CREDENTIAL_ITEM}-"):
        raise ValueError(f"refusing a keychain lookup of {service!r}: only '{CREDENTIAL_ITEM}[-<hash>]' may be read")
    return [SECURITY, "find-generic-password", "-s", service, "-w"]


def read_token(environ: Mapping[str, str] | None = None) -> str:
    """The OAuth access token, or Skip. Runs `security` directly: Sandbox.run would put its output in the evidence."""
    environ = os.environ if environ is None else environ
    custom = bool(environ.get("CLAUDE_CONFIG_DIR"))
    for service in keychain_services(real_config_dir(environ), custom):
        done = subprocess.run(keychain_read_argv(service), capture_output=True, text=True, timeout=30)
        if done.returncode:
            continue
        try:
            oauth = json.loads(done.stdout)["claudeAiOauth"]
            token, expires = oauth["accessToken"], oauth.get("expiresAt")
        except (ValueError, KeyError, TypeError):
            continue
        if isinstance(expires, (int, float)) and expires / 1000 < time.time() + TOKEN_MARGIN_S:
            raise Skip("the Claude login in the keychain has expired or is about to: use `claude` once to renew it")
        return token
    raise Skip("no Claude login in the keychain (item 'Claude Code-credentials-...'): run `claude` and log in once")


def child_env(sb: Sandbox, token: str, extra: Mapping[str, str | None] | None = None) -> dict[str, str]:
    """The sandbox environment plus the token; nothing else of CLAUDE* or AI_AGENT* gets through."""
    env = {name: value for name, value in sb.env(extra).items()
           if name == "CLAUDE_CONFIG_DIR" or not name.startswith(INHERITED)}
    env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    return env


class Claude:
    """The claude CLI over one sandbox. Construction checks that the CLI is there, that a login can be
    used, and that the sandbox's config knows only the built-in marketplace."""

    def __init__(self, sb: Sandbox, check_isolation: bool = True) -> None:
        self.sb = sb
        self.binary = shutil.which("claude")
        if self.binary is None:
            raise Skip("the claude CLI is not on PATH")
        self.token = read_token()
        self.env = child_env(sb, self.token)
        names = self.marketplaces() if check_isolation else [BUILTIN_MARKETPLACE]
        if names != [BUILTIN_MARKETPLACE]:
            raise AssertionError(f"the sandbox's claude is not isolated: marketplaces {names}, "
                                 f"expected only {BUILTIN_MARKETPLACE}")

    # --- running ---------------------------------------------------------------

    def redact(self, text: str) -> str:
        return redact(text, [self.token])

    def run(self, argv: Sequence[str | Path], cwd: Path | None = None, timeout: float = 120,
            check: bool = True) -> subprocess.CompletedProcess:
        """Run claude with the sandbox environment. The run is recorded like Sandbox.run's, and the token is
        redacted from the evidence, from the returned output and from a timeout's partial output."""
        argv = [str(a) for a in argv]
        guard.check_env(self.env, self.sb.root)
        started = time.monotonic()
        try:
            done = subprocess.run([self.binary, *argv], env=self.env, cwd=cwd or self.sb.work, input="",
                                  capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            exc.stdout, exc.stderr = self.redact(_text(exc.stdout)), self.redact(_text(exc.stderr))
            self.sb.evidence.command(["claude", *argv], None, f"timed out after {timeout}s\n{exc.stdout}{exc.stderr}",
                                     time.monotonic() - started)
            raise
        done.stdout, done.stderr = self.redact(done.stdout), self.redact(done.stderr)
        output = done.stdout + done.stderr
        self.sb.evidence.command(["claude", *argv], done.returncode, output, time.monotonic() - started)
        if check and done.returncode:
            raise AssertionError(f"claude {' '.join(argv)} exited {done.returncode}:\n{output[-1500:]}")
        return done

    # --- marketplace and plugin --------------------------------------------------

    def marketplaces(self) -> list[str]:
        out = self.run(["plugin", "marketplace", "list"]).stdout
        return [line.split("❯", 1)[1].strip() for line in out.splitlines() if "❯" in line]

    def marketplace_add(self, folder: Path) -> None:
        self.run(["plugin", "marketplace", "add", folder])

    def marketplace_update(self, name: str = "trazo") -> None:
        self.run(["plugin", "marketplace", "update", name])

    def install(self, ref: str = "trazo@trazo") -> None:
        self.run(["plugin", "install", ref])

    def install_from(self, folder: Path) -> dict:
        """Add `folder` as the marketplace and install trazo from it; the plugin list entry."""
        self.marketplace_add(folder)
        self.install()
        return self.installed() or {}

    def update(self, ref: str = "trazo@trazo") -> str:
        return self.run(["plugin", "update", ref]).stdout

    def plugins(self) -> list[dict]:
        return json.loads(self.run(["plugin", "list", "--json"]).stdout)

    def installed(self, ref: str = "trazo@trazo") -> dict | None:
        return next((p for p in self.plugins() if p.get("id") == ref), None)

    # --- sessions -----------------------------------------------------------------

    def run_session(self, plugin: Path | None, prompt: str, *, cwd: Path | None = None, model: str = "haiku",
                    max_turns: int = 4, allowed_tools: Sequence[str] = (), timeout: float = 240,
                    resume: str | None = None, tag: str = "session") -> Session:
        """One `claude -p` run. `plugin` is loaded for this session only (--plugin-dir); None uses what the
        config has installed. Without `allowed_tools` every tool call is denied in -p mode. A run that
        outlives `timeout` raises subprocess.TimeoutExpired."""
        logs = self.sb.root / "logs"
        logs.mkdir(exist_ok=True)
        stamp = f"{tag}-{len(list(logs.iterdir()))}"
        raw, debug = logs / f"{stamp}.jsonl", logs / f"{stamp}.debug.log"
        argv = ["-p", prompt, "--output-format", "stream-json", "--verbose", "--include-hook-events",
                "--debug-file", debug, "--model", model, "--max-turns", str(max_turns)]
        if plugin is not None:
            argv += ["--plugin-dir", plugin]
        if resume:
            argv += ["--resume", resume]
        if allowed_tools:
            argv += ["--allowedTools", *allowed_tools]  # variadic: last
        started = time.monotonic()
        try:
            stdout = self.run(argv, cwd=cwd, timeout=timeout, check=False).stdout
        finally:  # a timeout too: the log may be kept as evidence
            log = debug.read_text(encoding="utf-8", errors="replace") if debug.exists() else ""
            if self.token in log:  # not expected; never leave it in a file that may be kept as evidence
                debug.write_text(self.redact(log), encoding="utf-8")
                log = self.redact(log)
        seconds = time.monotonic() - started
        raw.write_text(stdout, encoding="utf-8")
        session = claude_events.build_session(claude_events.parse_lines(stdout), raw, seconds)
        session.connect_ms = claude_events.connect_ms(log)
        return session


def _text(value: str | bytes | None) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value or ""
