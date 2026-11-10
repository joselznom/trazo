"""Fail when mypy reports an error in a module of trazo's strict override.

`make typecheck` reports every module and blocks nothing; the modules listed
under the strict `[[tool.mypy.overrides]]` of packages/trazo/pyproject.toml
must stay clean, so this runs the same mypy once and fails only on their
errors. Run it through `make typecheck-strict` (trazo's environment has mypy).
"""

import re
import subprocess
import sys
import tomllib
from pathlib import Path

TRAZO = Path(__file__).resolve().parent.parent / "packages" / "trazo"
ERROR = re.compile(r"(src/[^:]+\.py):(?:\d+:)*\s*error:")


def strict_modules() -> set[str]:
    """The modules of every override that turns on the strict checks."""
    config = tomllib.loads((TRAZO / "pyproject.toml").read_text(encoding="utf-8"))
    strict = set()
    for override in config["tool"]["mypy"].get("overrides", []):
        if override.get("disallow_untyped_defs"):
            modules = override["module"]  # mypy takes one name or a list
            strict.update([modules] if isinstance(modules, str) else modules)
    return strict


def has_source(module: str) -> bool:
    base = TRAZO / "src" / Path(*module.split("."))
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def module_of(path: str) -> str:
    parts = Path(path).relative_to("src").with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def main() -> int:
    strict = strict_modules()
    # A misspelt or removed module would silently leave the gate.
    missing = sorted(m for m in strict if not has_source(m))
    if missing:
        print(f"strict override lists modules with no source file: {', '.join(missing)}")
        return 1
    run = subprocess.run([sys.executable, "-m", "mypy", "--no-color-output"], cwd=TRAZO, capture_output=True, text=True)
    # mypy exits 0 when clean and 1 with errors; anything else means it did not check.
    if run.returncode not in (0, 1):
        print(run.stdout + run.stderr, end="")
        return run.returncode or 1
    lines = run.stdout.splitlines()
    if run.returncode and not any(map(ERROR.match, lines)):  # errors in a format this cannot read
        print(run.stdout + run.stderr, end="")
        return 1
    bad = [line for line in lines if (m := ERROR.match(line)) and module_of(m[1]) in strict]
    print("\n".join(bad) if bad else f"{len(strict)} strict modules clean")
    print(f"whole package: {lines[-1] if lines else 'no output'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
