"""What a scenario function receives."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import versions
from .evidence import Evidence
from .sandbox import Sandbox


@dataclass
class Shared:
    """Settings and lazily built things shared by all scenarios of a run."""
    cache: Path       # versions.cache_dir(): the clone's .build/acceptance/cache, or $TRAZO_ACCEPTANCE_CACHE
    online: bool
    repo: Path = versions.REPO


@dataclass
class Context:
    """The argument of a scenario function:

        ctx.sandbox         the throwaway world (acceptance/sandbox.py)
        ctx.evidence        notes and paths for the report
        ctx.last_release()  PluginFolder of the highest vX.Y.Z tag (N-1)
        ctx.checkout(n=1)   PluginFolder of this checkout, stamped 99.0.<n> (HEAD)
        ctx.online          True when run with --online (downloads are allowed)
    """
    id: str
    sandbox: Sandbox
    evidence: Evidence
    shared: Shared

    @property
    def online(self) -> bool:
        return self.shared.online

    def last_release(self, tag: str | None = None) -> versions.PluginFolder:
        return versions.tag_plugin(self.shared.cache, tag, self.shared.repo)

    def checkout(self, n: int = 1) -> versions.PluginFolder:
        return versions.checkout_plugin(self.shared.cache, n, self.shared.repo)
