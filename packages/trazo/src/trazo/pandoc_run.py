"""Run Pandoc with trazo's own cross-references (trazo.crossref).

Call sites build a plain Pandoc argument list that names
`--filter=pandoc-crossref` before `--citeproc` where the cross-reference
pass belongs; the filter itself never runs. `run` runs two stages instead:

    pandoc <inputs> <reader flags> -t json        (no filter, no citeproc)
    -> trazo.crossref.apply
    -> pandoc -f json <writer flags> --citeproc   (and later filters)

Filters and --citeproc keep their side of the crossref filter: those
before it run in stage 1, where Pandoc's output format is JSON (a filter
there sees FORMAT=json, not the real format); those after it run in stage 2.
A call without the crossref filter runs unchanged. `--log` covers both
stages (stage 1's messages first), and `timeout` is for the whole run.
`--defaults` cannot be split, so it fails with the crossref filter.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path, PurePath

from . import crossref
from .project import PROJECT

FILTER_NAME = "pandoc-crossref"  # marks where trazo's pass goes
# Long options that take a value, and the short forms Pandoc accepts.
VALUED = {
    "--from", "--read", "--to", "--write", "--output", "--data-dir", "--metadata",
    "--metadata-file", "--defaults", "--template", "--variable", "--variable-json",
    "--wrap", "--columns", "--toc-depth", "--highlight-style",
    "--syntax-highlighting", "--include-in-header", "--include-before-body",
    "--include-after-body", "--resource-path", "--request-header", "--extract-media",
    "--filter", "--lua-filter", "--shift-heading-level-by", "--base-header-level",
    "--default-image-extension", "--indented-code-classes", "--tab-stop", "--track-changes",
    "--abbreviations", "--syntax-definition", "--reference-doc", "--css", "--epub-cover-image",
    "--epub-metadata", "--epub-embed-font", "--epub-chapter-level", "--split-level",
    "--chunk-template", "--epub-subdirectory", "--pdf-engine", "--pdf-engine-opt",
    "--bibliography", "--csl", "--citation-abbreviations", "--id-prefix",
    "--title-prefix", "--email-obfuscation", "--reference-location", "--top-level-division",
    "--number-offset", "--dpi", "--eol", "--markdown-headings", "--ipynb-output", "--log",
    "--slide-level", "--figure-caption-position", "--table-caption-position",
    "--print-default-template", "--print-default-data-file", "--print-highlight-style",
}  # checked against `pandoc --help` 3.9 (options with "=VALUE", not "[=VALUE]")
SHORT = {"-f": "--from", "-r": "--read", "-t": "--to", "-w": "--write", "-o": "--output",
         "-M": "--metadata", "-d": "--defaults", "-V": "--variable", "-H": "--include-in-header",
         "-B": "--include-before-body", "-A": "--include-after-body", "-F": "--filter",
         "-L": "--lua-filter", "-c": "--css", "-T": "--title-prefix", "-s": "--standalone",
         "-N": "--number-sections", "-C": "--citeproc", "-D": "--print-default-template",
         "-p": "--preserve-tabs", "-i": "--incremental"}
# Reader options: stage 1 only. Both stages: options either side reads.
READER = {"--from", "--read", "--metadata", "--metadata-file", "--shift-heading-level-by",
          "--base-header-level", "--default-image-extension", "--indented-code-classes", "--tab-stop",
          "--track-changes", "--abbreviations", "--file-scope", "--strip-comments",
          "--preserve-tabs"}
BOTH = {"--resource-path", "--bibliography", "--csl", "--citation-abbreviations",
        "--data-dir", "--verbose", "--quiet", "--fail-if-warnings", "--request-header",
        "--no-check-certificate"}


def options(args: list[str]) -> list[tuple[str | None, list[str]]]:
    """(long option name or None for an input file, its arguments)."""
    out, index = [], 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            out += [(None, [a]) for a in args[index + 1:]]
            break
        if arg.startswith("--"):
            name = arg.split("=", 1)[0]
            takes = name in VALUED and "=" not in arg
        elif arg.startswith("-") and len(arg) > 1:
            name = SHORT.get(arg[:2], arg[:2])
            takes = name in VALUED and len(arg) == 2
        else:
            out.append((None, [arg]))
            index += 1
            continue
        count = 2 if takes and index + 1 < len(args) else 1
        out.append((name, args[index:index + count]))
        index += count
    return out


def value(name_args: tuple[str | None, list[str]]) -> str:
    name, parts = name_args
    if len(parts) == 2:
        return parts[1]
    return parts[0].split("=", 1)[1] if "=" in parts[0] else parts[0][2:]


def is_crossref(item: tuple[str | None, list[str]]) -> bool:
    return item[0] == "--filter" and PurePath(value(item)).stem == FILTER_NAME


def split(args: list[str]) -> tuple[list[str], list[str]] | None:
    """Stage 1 and stage 2 arguments, or None without the crossref filter."""
    parsed = options(args)
    position = next((i for i, item in enumerate(parsed) if is_crossref(item)), None)
    if position is None:
        return None
    if any(name == "--defaults" for name, _ in parsed):
        raise ValueError("trazo's cross-references cannot split a --defaults file; "
                         "put its options on the command line")
    first, second = [], ["--from=json"]
    for index, (name, parts) in enumerate(parsed):
        if is_crossref((name, parts)):
            continue
        if name is None or name in READER:
            first += parts
        elif name in BOTH:
            first += parts
            second += parts
        elif name in ("--filter", "--lua-filter", "--citeproc"):
            (first if index < position else second).extend(parts)
        else:
            second += parts
    return first + ["--to=json"], second


def run(args: list[str], *, input: bytes | str | None = None, text: bool = False,
        timeout: float | None = None, command: list[str] | None = None,
        cwd=None) -> subprocess.CompletedProcess:
    """Like subprocess.run([*pandoc, *args], capture_output=True, cwd=cwd)
    (the project root by default); raises OSError or TimeoutExpired as it does,
    and ValueError for `--defaults` with the crossref filter."""
    command = list(command or PROJECT.command("pandoc"))
    cwd = cwd or PROJECT.root
    stages = split(args)
    if stages is None:
        return subprocess.run([*command, *args], input=input, cwd=cwd,
                              capture_output=True, text=text, timeout=timeout)
    first, second = stages
    deadline = None if timeout is None else time.monotonic() + timeout

    def remaining():
        if deadline is None:
            return None
        left = deadline - time.monotonic()
        if left <= 0:
            raise subprocess.TimeoutExpired([*command, *args], timeout)
        return left

    data = input.encode("utf-8") if isinstance(input, str) else input
    log = next((value(item) for item in options(args) if item[0] == "--log"), None)
    with tempfile.TemporaryDirectory(prefix="trazo-pandoc-") as folder:
        first_log = Path(folder) / "stage1-log.json"
        if log is not None:
            first = [*first, f"--log={first_log}"]
        result = subprocess.run([*command, *first], input=data, cwd=cwd,
                                capture_output=True, timeout=remaining())
        messages = result.stderr
        if result.returncode == 0:
            found: list[str] = []
            try:
                doc = crossref.apply(json.loads(result.stdout), warn=found.append, in_place=True)
            except (ValueError, KeyError, TypeError) as exc:
                result = subprocess.CompletedProcess(first, 1, b"", f"trazo crossref: {exc}\n".encode())
            else:
                messages += "".join(f"{line}\n" for line in found).encode("utf-8")
                result = second_stage(command, second, doc, inputs(args), cwd, remaining(), folder)
            messages += result.stderr
        if log is not None and first_log.exists():
            merge_logs(first_log, Path(cwd) / log)
    stdout = result.stdout.decode("utf-8", errors="replace") if text else result.stdout
    return subprocess.CompletedProcess([*command, *args], result.returncode, stdout,
                                       messages.decode("utf-8", errors="replace") if text else messages)


def merge_logs(first: Path, final: Path) -> None:
    """Stage 1's log entries, then stage 2's, in the log file asked for."""
    try:
        entries = json.loads(first.read_text(encoding="utf-8"))
        later = json.loads(final.read_text(encoding="utf-8")) if final.exists() else []
        final.write_text(json.dumps([*entries, *later], ensure_ascii=False), encoding="utf-8")
    except (OSError, ValueError):
        pass  # a log stage 2 could not write stays as Pandoc left it


def inputs(args: list[str]) -> list[str]:
    return [parts[0] for name, parts in options(args) if name is None]


def second_stage(command, args, doc, sources, cwd, timeout, folder):
    """Stage 2 reads a file named like the first input, so Pandoc's default
    HTML title (the input file name) does not change."""
    data = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    if not sources or sources[0] == "-":
        return subprocess.run([*command, *args], input=data, cwd=cwd,
                              capture_output=True, timeout=timeout)
    path = Path(folder) / f"{PurePath(sources[0]).stem}.json"
    path.write_bytes(data)
    return subprocess.run([*command, *args, str(path)], cwd=cwd,
                          capture_output=True, timeout=timeout)
