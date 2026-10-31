"""What a scenario shows for its verdict: commands, notes and kept paths."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

LIMIT = 1500  # characters kept of one output, half from each end


def trim(text: str, limit: int = LIMIT) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n[... {len(text) - limit} characters trimmed ...]\n{text[-half:]}"


MIN_SECRET = 8  # shorter values would also blank ordinary words


def redact(text: str, secrets: Iterable[str]) -> str:
    """`text` with every secret replaced by [secret]. The values are credentials a host supplied
    to a child process; they must not reach evidence, traces, logs or error messages."""
    for secret in secrets:
        if len(secret) >= MIN_SECRET:
            text = text.replace(secret, "[secret]")
    return text


class Evidence:
    """Collected during a scenario and written into report.md and report.json.
    Sandbox.run, the MCP client and the hook runner add to it by themselves."""

    def __init__(self) -> None:
        self.items: list[dict] = []

    def command(self, argv: list[str], code: int | None, output: str, seconds: float) -> None:
        self.items.append({"kind": "command", "argv": [str(a) for a in argv], "exit": code,
                           "seconds": round(seconds, 2), "output": trim(output)})

    def note(self, text: str) -> None:
        """A fact the scenario checked, worth reading in the report."""
        self.items.append({"kind": "note", "text": text})

    def path(self, label: str, path: Path | str) -> None:
        """A file or folder a reader may want to open (kept with --keep)."""
        self.items.append({"kind": "path", "label": label, "path": str(path)})

    def data(self, label: str, value: object) -> None:
        """A value, e.g. the reply of a tool call (trimmed when long)."""
        self.items.append({"kind": "data", "label": label, "value": trim(repr(value))})
