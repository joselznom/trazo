"""Pandoc options that changed between supported toolchain versions."""

import subprocess
from functools import lru_cache

from .project import PROJECT


@lru_cache(maxsize=16)
def mathml_flag(command: tuple[str, ...]) -> str:
    """Probe each configured command once; --math-method arrived in 3.11."""
    try:
        result = subprocess.run([*command, "--help"], cwd=PROJECT.root,
                                capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        # Let the conversion itself report an unavailable executable.
        return "--mathml"
    return "--math-method=mathml" if b"--math-method" in result.stdout else "--mathml"
