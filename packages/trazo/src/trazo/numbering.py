"""Shared figure/table numbering for previews, independent exports and Word builds.

`metadata_file` writes the Pandoc metadata for one document. Apply it after
the manuscript's metadata so the document's configured number and title
prefixes win. It also carries the other documents' labels (trazo.labels),
so a change in their numbering changes this file and rebuilds the Word file.
"""

import json
from pathlib import Path

from .project import PROJECT
from .review_storage import write_atomic


def reference_templates(prefix: str, title_prefix: str) -> dict:
    """How references to a document's items read: "S1", "S2 (a)", "S2.1",
    and the title prefix before the word ("Supplementary Figure S1")."""
    index = f"{prefix}$$i$$$$suf$$"
    words = f"{title_prefix} $$p$$\u00a0$$i$$" if title_prefix else "$$p$$\u00a0$$i$$"
    return {"refIndexTemplate": {"fig": index, "tbl": index, "sec": index, "default": "$$i$$$$suf$$"},
            "subfigureRefIndexTemplate": f"{prefix}$$i$$$$suf$$ ($$s$$)",
            **{f"{kind}PrefixTemplate": words for kind in ("fig", "tbl", "sec")}}


def document_templates(prefix: str, title_prefix: str) -> dict:
    """A document's captions and references; none without either prefix,
    so the metadata file's own templates apply."""
    if not prefix and not title_prefix:
        return {}
    title = f"{title_prefix} " if title_prefix else ""
    return {"figureTemplate": f"**{title}$$figureTitle$$ {prefix}$$i$$.** $$t$$",
            "tableTemplate": f"**{title}$$tableTitle$$ {prefix}$$i$$.** $$t$$",
            # Panelled figures use pandoc-crossref's caption, plus the prefixes.
            "subfigureTemplate": f"{title}$$figureTitle$$ {prefix}$$i$$$$titleDelim$$ $$t$$. $$ccs$$",
            **{key: value for key, value in reference_templates(prefix, title_prefix).items()
               if title_prefix or not key.endswith("PrefixTemplate")}}


def metadata(document: str) -> dict:
    """The document's templates, and every document's labels and reference
    templates for references between documents (crossref_references)."""
    from .labels import index
    specs, found = PROJECT.config["documents"], index(PROJECT)
    # Only what this document cites and lacks: a change elsewhere in another
    # document must not change this file, nor rebuild its Word file.
    wanted = set(found[document]["cites"]) - found[document]["labels"].keys()
    out = document_templates(specs[document]["number_prefix"], specs[document].get("title_prefix", ""))
    out["trazo-document"] = document
    out["trazo-documents"] = [
        {"name": name, "title": PROJECT.titles[name], "labels": {label: number for label, number in found[name]["labels"].items() if label in wanted},
         **reference_templates(spec["number_prefix"], spec.get("title_prefix", ""))}
        for name, spec in specs.items()]
    return out


def metadata_file(document: str, folder: Path) -> Path:
    """Write atomically beside a build, preserving the mtime when unchanged."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{document}-numbering.json"
    content = json.dumps(metadata(document), ensure_ascii=False, indent=2) + "\n"
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return path
    write_atomic(path, content.encode("utf-8"))
    return path
