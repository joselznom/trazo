"""Small helpers for deterministic Pandoc Markdown tables."""

from collections.abc import Mapping, Sequence
from pathlib import Path


def _escape_cell(value: object) -> str:
    """Return a pipe-table-safe representation of a cell value."""
    text = str(value)
    return text.replace("|", r"\|").replace("\n", "<br>")


def render_markdown_table(
    rows: Sequence[Mapping[str, object]], caption: str, label: str
) -> str:
    """Render rows as a Pandoc Markdown table with a cross-reference label."""
    if not rows:
        raise ValueError("rows must contain at least one row")

    headers = list(rows[0].keys())
    if not headers:
        raise ValueError("rows must contain at least one column")
    if any(list(row.keys()) != headers for row in rows):
        raise ValueError("all rows must have the same ordered columns")

    lines = [f"Table: {caption} {{#tbl:{label}}}", ""]
    lines.append("| " + " | ".join(_escape_cell(header) for header in headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in rows:
        lines.append("| " + " | ".join(_escape_cell(row[h]) for h in headers) + " |")

    return "\n".join(lines) + "\n"


def write_markdown_table(rows, output_path, caption, label):
    """Write a rendered Pandoc Markdown table to ``output_path``."""
    rendered = render_markdown_table(rows, caption, label)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
