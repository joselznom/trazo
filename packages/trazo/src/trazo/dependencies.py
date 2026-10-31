"""Content revisions and watched inputs shared by every workspace feature.

Source hashes protect writes. Render revisions include scientific styles,
used values, embeds, images, the renderer and actual font bytes. UI themes
never participate in scientific render revisions or export stamps.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

from .project import ASSETS_DIR, PROJECT


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# The preview polls every second and re-reads many files each time (values and
# table inputs, assets, fonts, specs), so a file's digest is reused while its
# stat is unchanged. ctime is in the key because an edit that restores the
# mtime still sets it. A file changed less than SETTLE_NS ago may change again
# within one timestamp tick (a second or two on some file systems) with no
# visible stat change, so it is hashed every time until it settles, as git does
# for "racily clean" files. A digest is stored under the stat of the open file
# it was read from, so a symlink or a rename that swaps the file during the
# read cannot leave another file's digest under its key.
SETTLE_NS = 2_000_000_000
_DIGESTS: dict[Path, tuple[tuple[int, ...], str]] = {}
_DIGESTS_LOCK = threading.Lock()


def _stat_key(st: os.stat_result, now: int) -> tuple[int, ...] | None:
    """The cache key of a settled file, None while it may still change unseen."""
    if now - max(st.st_mtime_ns, st.st_ctime_ns) < SETTLE_NS:
        return None
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def file_digest(path: Path) -> str:
    """sha256 of a file's bytes, reused while its stat is unchanged."""
    now = time.time_ns()
    key = _stat_key(os.stat(path), now)
    with _DIGESTS_LOCK:
        cached = _DIGESTS.get(path)
    if key is not None and cached is not None and cached[0] == key:
        return cached[1]
    with open(path, "rb") as file:
        key = _stat_key(os.fstat(file.fileno()), now)
        value = hashlib.file_digest(file, "sha256").hexdigest()
    if key is not None:
        with _DIGESTS_LOCK:
            _DIGESTS[path] = (key, value)
    return value


def fingerprint(paths) -> str:
    manifest = {}
    for path in paths:
        try:
            manifest[str(path)] = file_digest(path) if path.is_file() else None
        except OSError:
            manifest[str(path)] = None
    return digest(json.dumps(manifest, sort_keys=True).encode())


def render_revision(stamp, tokens):
    fonts = [PROJECT.path("figure_fonts") / f["file"] for f in tokens["fonts"]["files"]]
    # Use font names rather than absolute paths so project relocation is harmless.
    hashes = {p.name: file_digest(p) if p.is_file() else None for p in fonts}
    return digest(json.dumps({"source": stamp, "fonts": hashes}, sort_keys=True).encode())


def snapshot(root=None, watch=None):
    root = root or PROJECT.root
    patterns = watch if watch is not None else PROJECT.config["preview"]["watch"]
    paths = {p for pattern in patterns for p in root.glob(pattern)}
    folders = [*(folder.relative_to(PROJECT.root) for name in PROJECT.documents
                 for folder in PROJECT.document_roots(name)), *(PROJECT.relative(key) for key in (
        "table_specs", "table_data", "table_output", "figures",
        "diagrams", "diagram_output", "figure_fonts", "figure_embeds"))]
    for folder in folders:
        paths.update((root / folder).rglob("*"))
    paths.update(root / PROJECT.relative(key) for key in (
        "metadata", "bibliography", "csl", "values", "figure_tokens"))
    paths.update(ASSETS_DIR.rglob("*"))
    stamps = {}
    for path in paths:
        try:
            if path.is_file():
                info = path.stat()
                stamps[path] = (info.st_mtime_ns, info.st_size, info.st_ino)
        except OSError:
            pass
    return stamps
