"""One containment policy for public files, rendering inputs and downloads."""

from pathlib import Path


def local_file(root: Path, relative: str) -> Path | None:
    parts = relative.split("/")
    if ("\\" in relative or "\0" in relative
            or any(p in ("", ".", "..") or p.startswith(".") for p in parts)):
        return None
    path = root.resolve() / relative
    try:
        return path if path.resolve() == path and path.is_file() else None
    except OSError:
        return None
