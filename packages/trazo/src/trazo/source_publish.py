"""Publishing a manuscript file that a program ignoring trazo's lock may be writing.

A save reads the file under review_lock and checks it against what the browser
loaded, but an agent, an editor or a sync client can still change the file
before the new text is published. publish() keeps that change:

- Where the filesystem can swap two names atomically (portable.exchange), the
  new file is swapped in and the file it displaced is compared with the bytes
  read under the lock. If they differ, the two are swapped back, so the other
  program's version stays at the path, and trazo's text is kept under
  conflicts/. If they match, the displaced file is parked in displaced/ for
  DISPLACED_GRACE seconds, so a program still holding it open writes into a
  file that sweep() then finds and keeps.
- Elsewhere (exact=False) the file is compared just before os.replace, and an
  in-place write that lands between that check and the rename is read back
  through a descriptor and kept; an atomic replacement in that instant is lost.

conflicts/ and displaced/ (in the review folder) hold a folder per source file,
named by its repository path. trazo never deletes a conflict copy. The undo
history is the journal: sweep() deletes a leftover file only when its bytes are
the current file or a version the history holds. Callers hold review_lock.
"""

from __future__ import annotations

import errno
import glob
import hashlib
import os
import stat
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from .portable import ExchangeUnsupported, exchange, replace
from .review_storage import REVIEW_DIR, ROOT, write_atomic

CONFLICTS_DIR = REVIEW_DIR / "conflicts"
DISPLACED_DIR = REVIEW_DIR / "displaced"
DISPLACED_GRACE = 600  # seconds a program may keep writing to a file it opened before a save


@dataclass
class Publication:
    """published: `data` is at the path. kept: the conflict copies made.
    exact: the comparison covered the instant of publication (an exchange).
    warning: what went wrong after publishing, for the reply."""
    published: bool
    exact: bool
    kept: list[Path] = field(default_factory=list)
    warning: str = ""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def shown(path: Path) -> str:
    """A kept file's path for a reply: relative to the project when inside it."""
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)


def _copy_path(name: str, kind: str) -> Path:
    folder = CONFLICTS_DIR / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}.{kind}.md"


def _keep(source: Path, name: str, kind: str) -> Path:
    """Move `source` into the conflict copies of `name`: the same file, so a
    program still writing to it writes into the copy."""
    target = _copy_path(name, kind)
    try:
        os.replace(source, target)
    except OSError as exc:
        if exc.errno != errno.EXDEV:  # the review folder is on another disk: copy it
            raise
        target.write_bytes(source.read_bytes())
        source.unlink()
    return target


def _keep_bytes(data: bytes, name: str, kind: str) -> Path:
    target = _copy_path(name, kind)
    write_atomic(target, data)
    return target


def publish(path: Path, data: bytes, expected: bytes, name: str) -> Publication:
    """Put `data` at `path` only if the file it replaces still holds `expected`
    (read under review_lock); otherwise publish nothing and keep `data` as a
    conflict copy. `name` is the file's repository path."""
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:  # removed since the locked read: the exchange below says so
        mode = 0o644
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".swap")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(tmp, mode)  # mkstemp creates 0600
        exchange(tmp, path)
    except ExchangeUnsupported:
        return _replace_checked(path, tmp, data, expected, name)
    except FileNotFoundError:  # another program removed the file meanwhile
        return Publication(False, True, [_keep(tmp, name, "not-saved")])
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if tmp.read_bytes() == expected:
        return _park(tmp, expected, name)
    # Another program changed the file after the locked read: its version goes back.
    try:
        exchange(tmp, path)
    except FileNotFoundError:  # and then removed ours: its version stays at tmp
        pass
    # Normally tmp now holds our bytes again. If a third version had replaced ours,
    # or a program wrote into ours meanwhile, it holds that: keep it and our bytes.
    if tmp.read_bytes() == data:
        return Publication(False, True, [_keep(tmp, name, "not-saved")])
    return Publication(False, True, [_keep(tmp, name, "other-program"), _keep_bytes(data, name, "not-saved")])


def _park(tmp: Path, expected: bytes, name: str) -> Publication:
    """The save is published; set the displaced file aside for sweep(), after
    sweeping the earlier ones, so files never opened in the Source tab expire too."""
    target = DISPLACED_DIR / name / f"{time.time_ns()}.{_sha(expected)}"
    try:
        _sweep_parked(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp, target)
    except OSError as exc:
        if exc.errno == errno.EXDEV:  # another disk: no parking, as a plain rename would do
            tmp.unlink()
            return Publication(True, True)
        return Publication(True, True, warning=(
            f"Saved, but trazo could not set the replaced version aside ({exc.strerror or exc}). "
            f"It stays at {shown(tmp)} until the file is opened again; Undo save restores it."))
    return Publication(True, True)


def _replace_checked(path: Path, tmp: Path, data: bytes, expected: bytes, name: str) -> Publication:
    """Without an exchange: compare just before the rename, and read back
    through a descriptor what an in-place write put in the replaced file."""
    try:
        handle = open(path, "rb")
    except FileNotFoundError:
        return Publication(False, False, [_keep(tmp, name, "not-saved")])
    with handle:
        if handle.read() != expected:
            return Publication(False, False, [_keep(tmp, name, "not-saved")])
        if os.name == "nt":  # Windows refuses to rename over an open file
            handle.close()
        try:
            replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        if handle.closed:
            return Publication(True, False)
        handle.seek(0)
        late = handle.read()
    if late != expected:
        return Publication(True, False, [_keep_bytes(late, name, "other-program")])
    return Publication(True, False)


def sweep(path: Path, name: str, known: Callable[[], set[str]]) -> None:
    """Keep what late writes and killed saves left behind for `name` (at `path`).

    A parked file whose bytes changed was written through a descriptor opened
    before its save: it becomes a conflict copy; an unchanged one is deleted
    after DISPLACED_GRACE. A `.swap` file left by a killed save is deleted when
    its sha256 is in known() (the current file and the undo history), else kept.
    """
    _sweep_parked(name)
    orphans = list(path.parent.glob(glob.escape(path.name) + ".*.swap"))
    hashes = known() if orphans else set()
    for orphan in orphans:
        if _sha(orphan.read_bytes()) in hashes:
            orphan.unlink()
        else:
            _keep(orphan, name, "recovered")


def _sweep_parked(name: str) -> None:
    folder = DISPLACED_DIR / name
    if not folder.is_dir():
        return
    for parked in folder.iterdir():
        stamp, _, sha = parked.name.partition(".")
        if not stamp.isdigit():
            continue
        if _sha(parked.read_bytes()) != sha:
            _keep(parked, name, "late")
        elif time.time_ns() - int(stamp) > DISPLACED_GRACE * 10**9:
            parked.unlink()


def conflicts(name: str) -> list[str]:
    """The conflict copies kept for `name`, oldest first."""
    folder = CONFLICTS_DIR / name
    return sorted(shown(p) for p in folder.iterdir() if p.is_file()) if folder.is_dir() else []
