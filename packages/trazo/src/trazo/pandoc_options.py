"""Pandoc options that changed between supported toolchain versions."""

from functools import lru_cache

from . import processes
from .project import PROJECT


@lru_cache(maxsize=16)
def mathml_flag(command: tuple[str, ...]) -> str:
    """Probe each configured command once; --math-method arrived in 3.11."""
    try:
        result = processes.run([*command, "--help"], cwd=PROJECT.root, timeout=10)
    except OSError:  # processes.Timeout included
        # Let the conversion itself report an unavailable executable.
        return "--mathml"
    return "--math-method=mathml" if b"--math-method" in result.stdout else "--mathml"
