"""Refuse to run anything that could reach the user's real trazo or CLI state.

A scenario installs, updates and kills things, so its HOME, CLAUDE_CONFIG_DIR,
CODEX_HOME, TRAZO_DATA_DIR and TMPDIR must all be inside a throwaway
folder. The real locations are looked up in the account database, not in
$HOME, which is the very variable a mistake would have changed.
"""

from __future__ import annotations

import os
import pwd
from collections.abc import Mapping
from pathlib import Path

# Variables that must point into the sandbox when they are set (HOME always).
SANDBOX_VARS = ("HOME", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "TRAZO_DATA_DIR", "TMPDIR")
# Under the real HOME: the trazo data folder, both CLIs' state, Claude Code's
# per-project logs (docs/plugin.md reads them).
PROTECTED = ("Library/Application Support/trazo", "Library/Caches/claude-cli-nodejs", ".codex", ".claude.json")


class GuardError(Exception):
    """Something would touch the real home, data folder or CLI configuration."""


def real_home() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()


def protected_paths(home: Path | None = None) -> list[Path]:
    home = (home or real_home()).resolve()
    found = [home / name for name in PROTECTED] + [home / ".claude"]
    found += [path for path in home.glob(".claude*") if path not in found]  # .claude-personal, ...
    return found


def check_path(path: Path | str, what: str, home: Path | None = None) -> None:
    """Raise unless `path` is clear of the real home and everything protected."""
    home = (home or real_home()).resolve()
    resolved = Path(path).expanduser().resolve()
    if resolved == home or resolved in home.parents:
        raise GuardError(f"{what} = {resolved} is, or contains, the real home {home}")
    for danger in protected_paths(home):
        if resolved == danger or resolved.is_relative_to(danger) or danger.is_relative_to(resolved):
            raise GuardError(f"{what} = {resolved} overlaps {danger}, which acceptance runs never touch")


def check_env(env: Mapping[str, str], root: Path, home: Path | None = None) -> None:
    """Raise unless `env` (what a child process gets) is confined to the sandbox at `root`."""
    root = root.resolve()
    if "HOME" not in env:  # a child without HOME falls back to the account's real one
        raise GuardError("the environment has no HOME: a child would use the real home")
    for name in SANDBOX_VARS:
        if name not in env:
            continue
        value = Path(env[name])
        check_path(value, name, home)
        if not value.is_absolute() or not value.resolve().is_relative_to(root):
            raise GuardError(f"{name} = {env[name]} is not inside the sandbox {root}")
    for name, value in env.items():
        for danger in protected_paths(home):
            if str(danger) in value:
                raise GuardError(f"{name} mentions {danger}, which acceptance runs never touch")
