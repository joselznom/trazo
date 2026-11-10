"""The preview's /__status, which a page may ask to hold until something it shows changes.

A visible preview page asks `/__status?since=<digest>&wait=<seconds>` between its
one-second workspace scans (assets/app/app.js). The reply waits until its `digest`
differs from `since` (a new version, a save not built yet or built, an error,
comments, the Word build) or `wait` seconds pass, HOLD at most: about two requests
a second for an idle page, and a build reaches it as soon as it lands. Whatever
changes that state notifies local_server.CHANGES; comments and the Word build,
which other processes change, are read again every RECHECK seconds instead.

The preview's paths and state stay in trazo.preview, and this code reads them from
there when it runs (preview.NAME), so patching them on trazo.preview reaches it.
"""

import json
import os
from typing import Any

from .dependencies import digest
from .local_server import CHANGES, RequestError

HOLD = 1.0  # seconds: the pages scan the workspace every second
RECHECK = 0.25  # seconds: the pages' poll before requests were held


def current() -> dict[str, Any]:
    from . import preview
    with preview.LOCK:
        docs = {name: dict(state) for name, state in preview.STATE.items()}
        # Pages ask this between /__workspace scans, so it must not lag a save.
        unbuilt = preview.building()
    shown = {**docs, "comments": preview.comments_stamp(), "docx": preview.docx_status(), "building": unbuilt}
    # "server" lets trazo mcp tell its own preview from another one on the port.
    return {**shown, "digest": digest(json.dumps(shown, sort_keys=True).encode()),
            "synced": preview.WATCHER["synced"], "server": {"pid": os.getpid(), "project": str(preview.PROJECT.root)}}


def status(since: str | None = None, wait: str | None = None) -> dict[str, Any]:
    """The status, once its digest differs from `since` or `wait` seconds have passed."""
    try:
        seconds = 0.0 if wait is None else float(wait)
    except ValueError:
        seconds = -1.0
    if not 0 <= seconds < float("inf"):
        raise RequestError("wait must be a number of seconds")
    return CHANGES.hold(current, lambda reply: reply["digest"] == since, min(seconds, HOLD), RECHECK)
