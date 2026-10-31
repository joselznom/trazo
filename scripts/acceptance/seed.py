"""A data folder with everything setup downloads, made once and cloned per scenario.

`sh plugin/scripts/setup.sh` downloads uv, Python, Pandoc (and a headless
Chrome when the computer has none), about 280 MB. The seed is the result of
one real run, keyed by what setup downloads: uv's version (setup.sh), the
Pandoc and Chrome versions (trazo/tool_setup.py) and the CPU. A scenario
gets a copy-on-write clone (`cp -c -R`, APFS clonefile; a normal copy
elsewhere) with the install itself left out: venv/ and machine.json hold
absolute paths of the seed, so Sandbox.install() rebuilds them with the real
`setup.sh --offline`, which downloads nothing and so also proves it can.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import guard, versions

# What a clone leaves out: the install (venv) and the machine file written
# after it; both hold absolute paths of the seed.
NOT_CLONED = ("venv", "machine.json")


@dataclass(frozen=True)
class Seed:
    root: Path   # cache/seed-<key>
    pins: dict

    @property
    def data(self) -> Path:
        return self.root / "data"


def pins(repo: Path = versions.REPO) -> dict:
    """The versions setup downloads, read from this checkout's sources."""
    setup = (repo / "plugin/scripts/setup.sh").read_text(encoding="utf-8")
    tools = (repo / "packages/trazo/src/trazo/tool_setup.py").read_text(encoding="utf-8")
    found = {"uv": re.search(r"^UV_VERSION=(\S+)", setup, re.M),
             "pandoc": re.search(r'^PANDOC_VERSION = "([^"]+)"', tools, re.M),
             # Absent before Chrome became a download.
             "chrome": re.search(r'^CHROME_VERSION = "([^"]+)"', tools, re.M)}
    if not (found["uv"] and found["pandoc"]):
        raise versions.VersionError("cannot find UV_VERSION in setup.sh or PANDOC_VERSION in tool_setup.py")
    result = {name: m[1] if m else None for name, m in found.items()}
    return {**result, "machine": platform.machine(), "system": platform.system()}


def key(wanted: dict) -> str:
    return hashlib.sha256(json.dumps(wanted, sort_keys=True).encode()).hexdigest()[:12]


def find(cache: Path, repo: Path = versions.REPO) -> Seed | None:
    """The finished seed for this checkout's pins, if any."""
    wanted = pins(repo)
    root = cache / f"seed-{key(wanted)}"
    return Seed(root, wanted) if (root / ".complete").is_file() else None


def build(cache: Path, plugin_root: Path, minimal_env, log, repo: Path = versions.REPO,
          home: Path | None = None) -> Seed:
    """Run the real setup.sh once into cache/seed-<key>/data (needs the network).
    `minimal_env(home, data)` is the sandbox's env builder; `log(argv, result)` records the run.
    The cache and the environment setup.sh gets must pass the guard, as a scenario's do, before
    anything is deleted or started."""
    wanted = pins(repo)
    root = versions.check_cache(cache, home) / f"seed-{key(wanted)}"
    env = minimal_env(root / "home", root / "data")
    guard.check_env(env, root, home)
    # Two worktrees that both miss .complete must not both build: the second would delete the first's
    # install while setup.sh runs in it. One lock per seed; the check is repeated under it.
    with versions.locked(root.with_name(f"{root.name}.lock")):
        if (root / ".complete").is_file():
            return Seed(root, wanted)
        shutil.rmtree(root, ignore_errors=True)  # a run that did not finish: nobody else builds while we hold the lock
        # Built where it stays: uv's Python and the venv record absolute paths.
        (root / "home").mkdir(parents=True)
        argv = ["/bin/sh", str(plugin_root / "plugin/scripts/setup.sh"), str(plugin_root)]
        result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=1800)
        log(argv, result)
        if result.returncode:
            shutil.rmtree(root, ignore_errors=True)
            raise versions.VersionError(f"setup.sh failed ({result.returncode}) while making the seed:\n"
                                        f"{(result.stdout + result.stderr)[-1500:]}")
        (root / ".complete").write_text(json.dumps(wanted, indent=1) + "\n", encoding="utf-8")
        return Seed(root, wanted)


def clone_into(seed: Seed, data: Path) -> None:
    """Fill `data` (absent or empty) with a clone of the seed, minus NOT_CLONED."""
    data.mkdir(parents=True, exist_ok=True)
    for source in sorted(seed.data.iterdir()):
        if source.name in NOT_CLONED or source.name.startswith(".setup."):
            continue
        target = data / source.name
        # -c asks for a clone and fails on volumes without them: then copy normally.
        if subprocess.run(["cp", "-c", "-R", str(source), str(target)], capture_output=True).returncode:
            shutil.rmtree(target, ignore_errors=True)
            subprocess.run(["cp", "-R", str(source), str(target)], check=True)
    retarget_links(data, seed.data)


def retarget_links(data: Path, old: Path) -> None:
    """Point the absolute symlinks that lead into `old` (uv's cpython-3.12-... link
    to the Python it installed) at the same place under `data`: a clone must not
    reach back into the seed."""
    for folder, dirs, files in os.walk(data):
        for name in dirs + files:
            link = Path(folder, name)
            if link.is_symlink() and (target := os.readlink(link)).startswith(f"{old}{os.sep}"):
                link.unlink()
                link.symlink_to(data / Path(target).relative_to(old))
