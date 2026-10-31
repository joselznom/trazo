"""Plugin folders to test: a git tag, and this checkout stamped as a later version.

A plugin folder is a plain copy of the repository's files, as the desktop
apps' plugin cache holds them (no .git, no venv). Folders are cached under
cache_dir() and are read-only for scenarios: Sandbox.plugin() hands each
scenario its own copy.

The checkout is stamped 99.0.N so that it is a *later* release than any real
one, in every place a release bump touches (README "Release"):

- both plugin manifests, packages/trazo/pyproject.toml and its uv.lock entry,
  trazo/__init__.py, and copier.yml's trazo_version;
- trazo's template store (assets/template). Decision: the store is patched,
  not re-rendered with Copier. It holds exactly two things that depend on the
  version: index.json's "template" (what paper_update and the preview's pill
  compare a paper's manifest with, so it must be 99.0.N for "update the
  paper" to have something to do) and the trazo.toml of each variant (whose
  `version` line the paper's scripts/trazo compares with the installed
  trazo). A Copier render would write the same bytes elsewhere, needs the
  network the first time, and takes minutes; patch_store() refuses, and
  tells you to re-render, if any other stored file carries the old version.
  The checked-in store is what `make test-template` keeps equal to template/,
  so run `make template-variants` after a template/ change, as always.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import guard

REPO = Path(__file__).resolve().parents[2]
CACHE_ENV = "TRAZO_ACCEPTANCE_CACHE"
TAG_PATTERN = re.compile(r"v(\d+)\.(\d+)\.(\d+)")
STORE = Path("packages/trazo/src/trazo/assets/template")
# Bump when the stamping changes, so cached folders built by the old logic are not reused.
STAMP_FORMAT = 1
KEEP_CHECKOUTS = 6  # every edit of the checkout makes a new folder (about 6 MB)
# Worktrees share the cache and a run reads its plugin folders for as long as it lasts (a long run can take
# an hour), so nothing that was used within a day is pruned: whether another run still needs a folder
# cannot be known cheaply.
PRUNE_MIN_AGE = 24 * 3600


def check_cache(cache: Path, home: Path | None = None) -> Path:
    """`cache` if the guard allows writing, pruning and running setup in it, else GuardError.
    The cache holds the seed's whole data folder, so it follows the sandbox's rules: it must not be,
    contain or lie inside the real home's trazo data, Claude or Codex state."""
    guard.check_path(cache, "the acceptance cache", home)
    return cache


def main_checkout(repo: Path = REPO) -> Path:
    """The checkout that owns this clone's `.git`: the same for every worktree and for pre-push's
    clean checkout, which lies in a temporary folder. `repo` itself when git cannot say."""
    done = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=repo,
                          capture_output=True, text=True)
    common = Path(done.stdout.strip()) if done.returncode == 0 and done.stdout.strip() else None
    return common.parent if common is not None and common.name == ".git" else repo


def cache_dir(environ: Mapping[str, str] | None = None, home: Path | None = None, repo: Path = REPO) -> Path:
    """Where the data seed, the plugin folders and the cached passes live: $TRAZO_ACCEPTANCE_CACHE, else
    `.build/acceptance/cache` of the clone's main checkout (see main_checkout).

    One cache per clone, so every worktree and every pre-push run shares one seed (a few hundred MB
    of downloads) and one plugin cache. Folders are keyed by content or commit, so sharing is safe.
    A value the guard refuses (see check_cache) raises before anything is written."""
    given = (os.environ if environ is None else environ).get(CACHE_ENV)
    default = main_checkout(repo) / ".build" / "acceptance" / "cache"
    return check_cache(Path(given).expanduser().resolve() if given else default, home)


class VersionError(Exception):
    """A plugin folder could not be built or stamped."""


class FirstRelease(VersionError):
    """A tag run for the repository's first release: there is no last release to start from."""


@contextmanager
def locked(path: Path) -> Iterator[None]:
    """Hold an exclusive lock on `path` (made if absent) against every other process that locks it,
    other worktrees' runs included. The system drops it when the process ends, however it ends."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


@contextmanager
def plugins_locked(cache: Path) -> Iterator[None]:
    """Creating and pruning plugin folders happen one process at a time (the guard allows the cache first)."""
    check_cache(cache)
    with locked(cache / ".plugins.lock"):
        yield


@dataclass(frozen=True)
class PluginFolder:
    path: Path      # in the cache: read it, never run from it (Sandbox.plugin copies it)
    version: str    # what its manifests say, e.g. "0.8.1" or "99.0.1"
    label: str      # for reports, e.g. "tag v0.8.1" or "checkout 99.0.1"
    key: str        # the cache key: a commit, or a hash of the checkout's content


def git(*args: str, repo: Path = REPO, **options) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, **options)


# Set by a tag run (auto.configure) to the release being pushed, which already exists locally: the last
# release is then the highest tag below it, for the scenarios that update from it and for the fingerprints.
RELEASING = "ACCEPTANCE_RELEASING"


def latest_tag(repo: Path = REPO) -> str:
    tags = git("tag", "--list", "v*", repo=repo, text=True).stdout.split()
    found = sorted((tuple(map(int, m.groups())), tag) for tag in tags if (m := TAG_PATTERN.fullmatch(tag)))
    if (releasing := TAG_PATTERN.fullmatch(os.environ.get(RELEASING, ""))) is not None:
        found = [(n, tag) for n, tag in found if n < tuple(map(int, releasing.groups()))]
        if not found:
            raise FirstRelease(f"{releasing.group(0)} is the first release: there is no earlier one to update from")
    if not found:
        raise VersionError("no vX.Y.Z tag in this repository (fetch the tags)")
    return found[-1][1]


def _publish(build: Path, final: Path) -> None:
    """Move a finished folder into the cache; a concurrent build that won is kept."""
    try:
        build.rename(final)
    except OSError:
        shutil.rmtree(build, ignore_errors=True)
        if not final.is_dir():
            raise


def _scratch(cache: Path, name: str) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f".{name}-", dir=cache))


def manifest_version(folder: Path) -> str:
    return json.loads((folder / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["version"]


def tag_plugin(cache: Path, tag: str | None = None, repo: Path = REPO) -> PluginFolder:
    """The files of a tag (default: the highest vX.Y.Z), as released."""
    tag = tag or latest_tag(repo)
    try:
        commit = git("rev-parse", f"{tag}^{{commit}}", repo=repo, text=True).stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise VersionError(f"{tag} is not a tag or commit of this repository") from exc
    final = cache / f"plugin-{commit[:16]}"
    with plugins_locked(cache):
        if not final.is_dir():  # not made by a run that held the lock before us
            build = _scratch(cache, "tag")
            archive = subprocess.Popen(["git", "archive", commit], cwd=repo, stdout=subprocess.PIPE)
            subprocess.run(["tar", "-x", "-C", str(build)], stdin=archive.stdout, check=True)
            archive.stdout.close()
            if archive.wait():
                raise VersionError(f"git archive {commit} failed")
            _publish(build, final)
    return PluginFolder(final, manifest_version(final), f"tag {tag}", commit)


def checkout_files(repo: Path = REPO) -> list[str]:
    """Tracked files still in the working tree, plus untracked ones that are not ignored."""
    def listed(*args: str) -> list[str]:
        return [n for n in git("ls-files", "-z", *args, repo=repo).stdout.decode().split("\0") if n]
    names = set(listed()) | set(listed("--others", "--exclude-standard"))
    # A deleted tracked file is gone; a nested repository (a worktree) is a directory.
    return sorted(n for n in names if (repo / n).is_file() or (repo / n).is_symlink())


def content_key(repo: Path, names: list[str], n: int) -> str:
    digest = hashlib.sha256(f"{STAMP_FORMAT} {n}\0".encode())
    for name in names:
        path = repo / name
        if path.is_symlink():
            body = b"link " + os.readlink(path).encode()
        else:
            body = (b"x " if os.access(path, os.X_OK) else b"- ") + hashlib.sha256(path.read_bytes()).digest()
        digest.update(name.encode() + b"\0" + body)
    return digest.hexdigest()


def checkout_plugin(cache: Path, n: int = 1, repo: Path = REPO) -> PluginFolder:
    """This checkout, uncommitted changes included, stamped as 99.0.<n>.
    Use two values of n to have two different later releases."""
    names = checkout_files(repo)
    key = content_key(repo, names, n)
    final = cache / f"plugin-checkout-{key[:16]}"
    version = f"99.0.{n}"
    with plugins_locked(cache):
        if not final.is_dir():  # not made by a run that held the lock before us
            build = _scratch(cache, "checkout")
            for name in names:
                source, target = repo / name, build / name
                target.parent.mkdir(parents=True, exist_ok=True)
                if source.is_symlink():
                    target.symlink_to(os.readlink(source))
                else:
                    shutil.copy2(source, target)
            stamp(build, version)
            _publish(build, final)
        os.utime(final)
    prune(cache, final)
    return PluginFolder(final, version, f"checkout {version}", key)


def prune(cache: Path, keep: Path) -> None:
    """Delete the least recently used checkout folders beyond KEEP_CHECKOUTS (never `keep`), under
    the plugins lock and only those not used for PRUNE_MIN_AGE: another run may be reading the rest."""
    with plugins_locked(cache):
        folders = sorted(cache.glob("plugin-checkout-*"), key=lambda p: p.stat().st_mtime, reverse=True)
        stale = time.time() - PRUNE_MIN_AGE
        for old in [f for f in folders[KEEP_CHECKOUTS:] if f != keep and f.stat().st_mtime < stale]:
            shutil.rmtree(old, ignore_errors=True)


def _replace_once(path: Path, pattern: str, replacement: str, old: str) -> None:
    """Replace the one match of `pattern` (its group 1 is the old version)."""
    text = path.read_text(encoding="utf-8")
    matches = list(re.finditer(pattern, text, re.M))
    if len(matches) != 1 or matches[0][1] != old:
        raise VersionError(f"{path.name}: expected one version {old} at {pattern!r}, found "
                           f"{[m[1] for m in matches]}")
    start, end = matches[0].span(1)
    path.write_text(text[:start] + replacement + text[end:], encoding="utf-8")


def stamp(folder: Path, version: str) -> None:
    """Rewrite every place that carries the release version, and the template store."""
    old = manifest_version(folder)
    if old == version:
        raise VersionError(f"{folder} is already {version}")
    for manifest in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
        _replace_once(folder / manifest, r'^  "version": "([^"]*)"', version, old)
    package = folder / "packages/trazo"
    _replace_once(package / "pyproject.toml", r'^version = "([^"]*)"', version, old)
    _replace_once(package / "src/trazo/__init__.py", r'^__version__ = "([^"]*)"', version, old)
    _replace_once(package / "uv.lock", r'^name = "trazo"\nversion = "([^"]*)"', version, old)
    _replace_once(folder / "copier.yml", r"^trazo_version:\n  type: str\n  default: v([^\n]*)", version, old)
    patch_store(folder / STORE, f"v{old}", f"v{version}")


def patch_store(store: Path, old: str, new: str) -> None:
    """Move a template store from release tag `old` to `new` (see the module docstring)."""
    text = (store / "index.json").read_text(encoding="utf-8")
    index = json.loads(text)
    if json.dumps(index, indent=1, ensure_ascii=False) + "\n" != text:
        raise VersionError("index.json is not in the format scripts/template-variants.py writes; "
                           "re-render the store instead of patching it")
    if index["template"] != old:
        raise VersionError(f"the store is at {index['template']}, expected {old}")
    # The bare number too: a file naming "trazo-0.8.1" would go stale as well.
    carriers = {blob.name for blob in (store / "blobs").iterdir() if old.removeprefix("v").encode() in blob.read_bytes()}
    versioned = {digest for variant in index["variants"].values() for path, digest in variant["files"].items()
                 if path == "trazo.toml"}
    if carriers - versioned:
        raise VersionError(f"stored files other than trazo.toml carry {old} "
                           f"({sorted(carriers - versioned)[0][:12]}...): re-render the store with "
                           "Copier (scripts/template-variants.py --from) instead of patching it")
    moved = {}
    for digest in versioned:
        data = (store / "blobs" / digest).read_bytes()
        patched, count = re.subn(rb'^version = "' + re.escape(old.encode()) + rb'"$',
                                 b'version = "' + new.encode() + b'"', data, flags=re.M)
        if count != 1:
            raise VersionError(f"a stored trazo.toml has no single `version = \"{old}\"` line")
        moved[digest] = hashlib.sha256(patched).hexdigest()
        (store / "blobs" / moved[digest]).write_bytes(patched)
    for variant in index["variants"].values():
        if variant["files"]["trazo.toml"] in moved:
            variant["files"]["trazo.toml"] = moved[variant["files"]["trazo.toml"]]
    still = {d for variant in index["variants"].values() for d in variant["files"].values()}
    for digest in set(moved) - still:
        (store / "blobs" / digest).unlink()
    index["template"] = new
    (store / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
