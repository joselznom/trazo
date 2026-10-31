"""Every document's cross-reference labels, so one document can cite
another's figures, tables and sections.

Labels are numbered by trazo.crossref on the Pandoc AST of the document's
Word expansion (the text `trazo build` converts), so they match its
captions. The index is cached in <build>/labels.json per document, keyed by
that text and the Pandoc command: an unchanged document costs no Pandoc run,
but still one expansion of every document per call, unless within once().
numbering.metadata hands the index to every document's pass.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import __version__, crossref, pandoc_run
from .project import Project
from .review_storage import write_atomic

CACHE = "labels.json"
_ONCE = threading.local()  # .found: id(project) -> (project, index) inside once()


def cache_path(project: Project):
    return project.path("build") / CACHE


def stored(project: Project) -> dict[str, Any]:
    """The cached index as last written ({} when missing or unreadable)."""
    try:
        data = json.loads(cache_path(project).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


@contextmanager
def once() -> Iterator[None]:
    """Within the block, index() works out each project's index once on this
    thread, for work that reads the sources as they were when it started:
    the preview's pages built together. Each call otherwise expands every
    document. A change made meanwhile is not seen; the caller must notice
    it and do the work again (the preview's watcher does, for the files it
    watches)."""
    if getattr(_ONCE, "found", None) is not None:
        yield
        return
    _ONCE.found = {}
    try:
        yield
    finally:
        _ONCE.found = None


def index(project: Project) -> dict[str, dict[str, Any]]:
    """name -> {"labels": {label: "fig 2 a"}, "cites": [label, ...]} for
    every document, in [documents] order. A document that cannot be expanded
    or read has none: its own build reports why."""
    found = getattr(_ONCE, "found", None)
    if found is None:
        return _index(project)
    if id(project) not in found:
        found[id(project)] = (project, _index(project))
    return found[id(project)][1]


def _index(project: Project) -> dict[str, dict[str, Any]]:
    from .expand import expand_files
    out: dict[str, dict[str, Any]] = {}
    cached, changed = stored(project), False
    command = project.command("pandoc")
    for name in project.documents:
        try:
            source = expand_files(project.document_files(name), document=name)
        except (OSError, ValueError, RuntimeError, RecursionError):
            out[name] = {"labels": {}, "cites": []}
            continue
        key = hashlib.sha256(json.dumps([source, command, __version__]).encode("utf-8")).hexdigest()
        entry = cached.get(name)
        if not (isinstance(entry, dict) and entry.get("key") == key):
            found = read(source, command, project.root)
            if found is None:  # not cached: Pandoc may work next time
                out[name] = {"labels": {}, "cites": []}
                continue
            entry = cached[name] = {"key": key, **found}
            changed = True
        out[name] = {"labels": entry["labels"], "cites": entry["cites"]}
    if changed:
        write_atomic(cache_path(project), (json.dumps(cached, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    return out


def read(source: str, command: list[str], root: Path) -> dict[str, Any] | None:
    """The labels and cited cross-reference labels of Markdown `source`
    (None when Pandoc could not read it)."""
    try:
        result = pandoc_run.run(["--from=markdown", "--to=json"], input=source.encode("utf-8"),
                                command=command, cwd=root, timeout=120)
        doc = json.loads(result.stdout) if result.returncode == 0 else None
        if doc is None:
            return None
        crossref.check_version(doc)
        numbered = crossref.number(doc, crossref.Options(doc.get("meta", {})))
    except (OSError, subprocess.TimeoutExpired):
        return None
    except (ValueError, KeyError, TypeError):
        return {"labels": {}, "cites": []}  # a duplicate label too: the document's build says so
    labels = {label: " ".join([kind, ".".join(map(str, number)), *([letter] if letter else [])])
              for label, (kind, number, letter) in numbered.items()}
    return {"labels": labels, "cites": list(dict.fromkeys(cites(doc["blocks"])))}


def cites(value: Any):
    """The cross-reference labels cited in an AST, in document order."""
    if isinstance(value, list):
        for item in value:
            yield from cites(item)
    elif isinstance(value, dict):
        if value.get("t") == "Cite":
            for citation in value["c"][0]:
                if crossref.kind_of(citation["citationId"]) in crossref.KINDS:
                    yield crossref.label_of(citation["citationId"])
        yield from cites(value.get("c"))


def cited(project: Project, name: str) -> list[str]:
    """The other documents whose labels `name` cites."""
    found = index(project)
    wanted = set(found[name]["cites"]) - found[name]["labels"].keys()
    return [other for other, entry in found.items() if other != name and wanted & entry["labels"].keys()]


def check(project: Project, name: str) -> list[dict[str, str]]:
    """The submission check's Cross-references items of a document: what
    its pass warns about (crossref_references), from the index alone."""
    from .crossref_references import problem
    found, items = index(project), []
    own = found[name]["labels"]
    others = {other: entry["labels"] for other, entry in found.items() if other != name}
    for label in found[name]["cites"]:
        if label not in own:
            holders = [project.titles[other] for other, labels in others.items() if label in labels]
            if message := problem(label, label.partition(":")[0], holders):
                items.append({"status": "error", "label": label, "detail": message, "doc": name})
    for label in own:
        if holders := [project.titles[other] for other, labels in others.items() if label in labels]:
            names = " and ".join([project.titles[name], *holders])
            items.append({"status": "warn", "label": label, "detail": f"{label} is labelled in {names}", "doc": name})
    return items
