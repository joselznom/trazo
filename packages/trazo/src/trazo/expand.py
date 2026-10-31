"""Concatenate Markdown files, expand '!include path' lines and value placeholders.

A placeholder ``{{v:key}}`` is replaced by the "text" of ``key`` in the
values file ([paths] values, data/values.json by default; built by the
project's own values command). ``{{v:key|name}}`` instead applies the named
format from <table_specs>/_shared.json (tables/specs/ by default) to the
key's raw "value" (a number or an object of numbers), so prose and tables
share one definition of each format. Unknown keys or formats, a value the
format does not fit, and a missing or stale values file are errors.
Placeholders inside inline code spans and fenced code blocks are left as
written.

    trazo expand FILE.md [FILE.md ...] > out.md
"""

import json
import re
import sys
from pathlib import Path

from .dependencies import file_digest
from .project import PROJECT

INCLUDE_PREFIX = "!include "
ROOT = PROJECT.root
VALUES_PATH = PROJECT.path("values")
VALUES_HINT = PROJECT.command_hint("values")
# Groups: the key and the optional format name ("" without one).
KEY_PATTERN = r"\{\{v:([A-Za-z0-9_.\-]+)(?:\|([A-Za-z0-9_.\-]+))?\}\}"
PLACEHOLDER = re.compile(KEY_PATTERN)
INLINE_CODE = re.compile(r"(`+)(?:(?!\1).)+?\1")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")


class ValuesError(RuntimeError):
    """The values file is missing, stale, or lacks a referenced key."""


def load_values(path=VALUES_PATH, check_inputs=True):
    """key -> text of every value."""
    return {key: entry["text"] for key, entry in load_entries(path, check_inputs).items()}


def load_entries(path=VALUES_PATH, check_inputs=True):
    """key -> {"value", "text", ...} from the values file (checked for staleness)."""
    path = Path(path)
    if not path.exists():
        raise ValuesError(f"values file not found: {path}; run {VALUES_HINT}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if check_inputs:
        for name, digest in payload.get("inputs", {}).items():
            source = Path(name) if Path(name).is_absolute() else ROOT / name
            if not source.exists() or file_digest(source) != digest:
                raise ValuesError(
                    f"values file {path} is stale: {name} changed; run {VALUES_HINT}"
                )
    return payload["values"]


class Values(dict):
    """key -> text, plus "key|name" -> formatted value, computed when first used."""

    def __init__(self, entries=None, *, path=VALUES_PATH, check_inputs=True):
        super().__init__({key: entry["text"] for key, entry in (entries or {}).items()})
        self.entries = entries
        self.path = path
        self.check_inputs = check_inputs

    def __missing__(self, name):
        if self.entries is None:
            self.entries = load_entries(self.path, self.check_inputs)
            self.update({key: entry["text"] for key, entry in self.entries.items()})
            if name in self:
                return self[name]
        key, _, fmt = name.partition("|")
        if not fmt or key not in self.entries:
            raise KeyError(name)
        from .table import TableError, format_value, shared_formats
        where = f"{{{{v:{name}}}}}"
        try:
            formats, source = shared_formats()
            if fmt not in formats:
                raise TableError(f"unknown format {fmt!r} (formats are in {source})")
            self[name] = format_value(formats[fmt], self.entries[key].get("value"))
        except TableError as exc:
            raise ValuesError(f"{where}: {exc}") from exc
        return self[name]


# Spans where a Pandoc bracketed span would change the meaning: inline math,
# link targets, inline HTML and attribute blocks (not the "{{v:key}}" itself).
UNMARKABLE = re.compile(r"\$[^$\n]+\$|\]\([^)]*\)|<[^>]+>|(?<!\{)\{(?!\{)(?:[^{}]|\{\{v:[^{}]*\}\})*\}")
TABLE_ROW = re.compile(r"^\s*(\||\+[-=:+])")
LINK_DEFINITION = re.compile(r"^\s{0,3}\[[^\]]+\]:")
INDENTED = re.compile(r"^( {4}|\t)")
# Column rule of a simple or multiline table: dashes with a gap ("---- ----").
TABLE_RULE = re.compile(r"^\s*-{3,}(\s+-+)+\s*$")


def table_lines(lines):
    """Indexes of lines in simple or multiline tables (conservatively)."""
    covered = set()
    for i, line in enumerate(lines):
        if i in covered or not TABLE_RULE.match(line):
            continue
        start = i
        while start > 0 and lines[start - 1].strip():
            start -= 1
        if start == i:  # multiline table: rule, header, rule, rows, rule
            rules = [j for j in range(i, len(lines)) if TABLE_RULE.match(lines[j])]
            end = rules[2] if len(rules) > 2 else len(lines) - 1
        else:           # simple table: header, rule, rows up to a blank line
            end = i
            while end + 1 < len(lines) and lines[end + 1].strip():
                end += 1
        covered.update(range(start, end + 1))
    return covered


def _replace(segment, values, mark=False):
    def lookup(match):
        key = match.group(1)
        name = f"{key}|{match.group(2)}" if match.group(2) else key
        try:
            text = values[name]
        except KeyError:
            raise ValuesError(f"unknown value key: {key}") from None
        if mark and text and not re.search(r"[\[\]\n]", text):
            return f'[{text}]{{.pv-val data-key="{key}"}}'
        return text

    return PLACEHOLDER.sub(lookup, segment)


def _replace_marked(segment, values):
    """Mark values in plain text only; protected spans get plain text."""
    pieces, last = [], 0
    for span in UNMARKABLE.finditer(segment):
        pieces.append(_replace(segment[last:span.start()], values, True))
        pieces.append(_replace(span.group(0), values))
        last = span.end()
    pieces.append(_replace(segment[last:], values, True))
    return "".join(pieces)


def _markable_line(line, state):
    """False for lines where no span may be inserted (tables, raw HTML,
    display math, YAML front matter); `state` carries the multi-line modes."""
    stripped = line.strip()
    if state["yaml"] is None:
        state["yaml"] = stripped == "---"
        if state["yaml"]:
            return False
    elif state["yaml"]:
        if stripped in ("---", "..."):
            state["yaml"] = False
        return False
    if "$$" in line:
        if line.count("$$") % 2:
            state["math"] = not state["math"]
        return False
    if state["math"]:
        return False
    return not (TABLE_ROW.match(line) or line.count("|") >= 2 or stripped.startswith("<")
                or LINK_DEFINITION.match(line) or INDENTED.match(line))


def fenced_lines(lines):
    """Yield lines with their code-block state, respecting the opening fence length."""
    fence = None
    for line in lines:
        marker = FENCE.match(line)
        if fence is not None:
            yield line, True
            if (marker and marker.group(1)[0] == fence[0]
                    and len(marker.group(1)) >= len(fence) and not marker.group(2).strip()):
                fence = None
        elif marker:
            fence = marker.group(1)
            yield line, True
        else:
            yield line, False


def substitute(text, values, mark=False):
    """Replace {{v:key}} outside inline code spans and fenced code blocks.

    With mark=True (live preview only) each value becomes the Pandoc span
    `[text]{.pv-val data-key="key"}`, except where a span could change the
    result (see _markable_line and UNMARKABLE); those get the plain text.
    """
    lines = []
    state = {"yaml": None, "math": False}  # yaml None: no line seen yet
    all_lines = text.split("\n")
    in_table = table_lines(all_lines) if mark else set()
    for number, (line, literal) in enumerate(fenced_lines(all_lines)):
        if mark and state["yaml"] is None and not line.strip():
            state["yaml"] = False  # front matter must start on the first line
        if not literal:
            do_mark = mark and _markable_line(line, state) and number not in in_table
            replace = _replace_marked if do_mark else _replace
            pieces, last = [], 0
            for code in INLINE_CODE.finditer(line):
                pieces.append(replace(line[last:code.start()], values))
                pieces.append(code.group(0))
                last = code.end()
            pieces.append(replace(line[last:], values))
            line = "".join(pieces)
        lines.append(line)
    return "\n".join(lines)


def resolve_include(path_text, source_path):
    candidate = Path(path_text)
    if candidate.exists():
        return candidate
    project_relative = ROOT / candidate
    if project_relative.exists():
        return project_relative
    relative = source_path.parent / candidate
    if relative.exists():
        return relative
    raise FileNotFoundError(f"include not found: {path_text} in {source_path}")


def include_lines(text):
    """Each line of `text` with the path its `!include` names, else None.
    An `!include` in a fenced code block is text, as Pandoc shows it."""
    for line, literal in fenced_lines(text.splitlines()):
        parts = line.split(None, 1)
        yield line, parts[1] if len(parts) == 2 and not literal and line.startswith(INCLUDE_PREFIX) else None


def expand_file(path, stack=None):
    path = Path(path)
    stack = [] if stack is None else stack
    if path in stack:
        chain = " -> ".join(str(p) for p in [*stack, path])
        raise RecursionError(f"circular include detected: {chain}")

    lines = []
    for line, target in include_lines(path.read_text(encoding="utf-8")):
        if target is not None:
            include_path = resolve_include(target, path)
            lines.append(expand_file(include_path, [*stack, path]).rstrip())
            lines.append("")
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def form_sections(text, mark=False):
    """Hide form-only ATX sections in deliverables, retain marked preview sections."""
    lines, level = [], None
    for line, literal in fenced_lines(text.splitlines(keepends=True)):
        heading = None if literal else re.match(r"^ {0,3}(#{1,6})(?:[ \t]+(.*))?$", line.rstrip("\r\n"))
        if heading and level is not None and len(heading[1]) <= level:
            if mark:
                lines.append("\n:::::::\n\n")
            level = None
        if heading and level is None and re.search(r"\{[^}]*\.form-only(?:\s|\})", heading[2] or ""):
            level = len(heading[1])
            if mark:
                lines.append('\n::::::: {.pk-form-only}\n\n')
                lines.append(line)
                lines.append('\n::: {.pk-form-badge}\n\nForm only – not in the Word file\n\n:::\n\n')
            continue
        if level is None or mark:
            lines.append(line)
    if level is not None and mark:
        lines.append("\n:::::::\n")
    return "".join(lines)


def expand_files(paths, values_path=VALUES_PATH, check_inputs=True, mark=False, document=None, with_authors=False):
    from .frontmatter import render
    paths = [Path(path) for path in paths]
    document = document or PROJECT.source_document(paths)
    if document == next(iter(PROJECT.documents)):
        appended = PROJECT.document_files(document)
        paths.extend(path for path in appended if path.resolve() not in {source.resolve() for source in paths}
                     and path.parent == PROJECT.path("declarations"))
    # Keep a blank line between source files. Without it, a trailing image can
    # merge with the next section heading and cease to be a standalone Pandoc
    # Figure, leaving its cross-reference undefined.
    text = "\n\n".join(expand_file(path).rstrip() for path in paths) + "\n"
    text = form_sections(text, mark)
    if document is not None:
        text = render(text, document, with_authors)
    if PLACEHOLDER.search(text):
        text = substitute(text, Values(path=values_path, check_inputs=check_inputs), mark)
    from .abbreviations import render as render_abbreviations
    text = render_abbreviations(text, PROJECT.config["abbreviations"])
    return text


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: trazo expand <markdown> [<markdown> ...]")
        return 0 if argv else 2
    try:
        sys.stdout.write(expand_files(argv))
    except (ValuesError, FileNotFoundError, RecursionError, ValueError) as error:
        print(f"trazo expand: {error}", file=sys.stderr)
        return 1
    return 0
