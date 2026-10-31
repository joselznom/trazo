"""Project files of the stress paper: trazo.toml, metadata, bibliography,
declarations, cover letter and review comments (used by stress_fixture.py)."""

import json
from pathlib import Path

from _stress_text import Context


def write_config(root: Path, ctx: Context, ignored: list[str]) -> None:
    version = next(line for line in (root / "trazo.toml").read_text().splitlines() if line.startswith("version"))
    (root / "trazo.toml").write_text(f"""{version}
kind = "article"

[submission]
journal = "Journal of Stress Testing"
guidelines = "https://example.org/authors"
title_page = "separate"
declarations = "separate"

[abbreviations]
style = "list"
ignore = {json.dumps(ignored)}

[limits]
keywords = 8
""", encoding="utf-8")
    authors = "\n".join(f'  - name: "{name}"\n    affiliations: [{{ref: a{i % 5}}}]' + (
        '\n    email: "first@example.org"\n    orcid: "0000-0002-1825-0097"\n    corresponding: true' if i == 0 else "")
        for i, name in enumerate(["Zoë Ångström", "José Núñez", "Łukasz Wróbel", "Ingrid Ødegaard", "李 明",
                                  "Amélie Côté", "Søren Kierkegård", "Nguyễn Văn An", "Ana-María O'Neil",
                                  "Björn Müller", "Fatma Yılmaz", "Kofi Mensah"]))
    affiliations = "\n".join(f'  - id: a{i}\n    department: "Department {i}"\n    name: "Universität {city}"\n'
                             f'    city: "{city}"\n    country: "Country {i}"'
                             for i, city in enumerate(["Zürich", "São Paulo", "Kraków", "Malmö", "東京"]))
    (root / "manuscript/metadata.yaml").write_text(f"""---
title: "Stress: a thesis-sized paper with ünïcödé, “quotes” and C:\\\\paths"
short-title: "Stress paper"
author:
{authors}
affiliations:
{affiliations}
keywords:
  - "stress testing"
  - "señal"
  - "reproducible manuscripts"
  - "東京"
lang: en-US
link-citations: true
reference-section-title: "References"
figureTitle: "Figure"
tableTitle: "Table"
figPrefix: "Figure"
tblPrefix: "Table"
secPrefix: "Section"
figureTemplate: "**$$figureTitle$$ $$i$$.** $$t$$"
tableTemplate: "**$$tableTitle$$ $$i$$.** $$t$$"
numbersections: false
---
""", encoding="utf-8")
    bib = []
    for i, key in enumerate(ctx.keys):
        surname = ["M{\\\"u}ller", "Núñez", "{van der Berg}", "O'Brien", "Smith", "Ødegaard"][i % 6]
        bib.append(f"@article{{{key},\n  author = {{{surname}, A. and Other, B.}},\n"
                   f"  title = {{A study of {{MRI}} number {i}: señal and noise}},\n"
                   f"  journal = {{Journal {i % 17}}},\n  year = {{{1990 + i % 35}}},\n  volume = {{{i % 40 + 1}}},\n"
                   f"  pages = {{{i}--{i + 9}}},\n  doi = {{10.1000/stress_{i}}}\n}}\n")
    (root / "bibliography/references.bib").write_text("\n".join(bib), encoding="utf-8")
    declarations = ["Ethics approval and consent to participate|decl-ethics", "Consent for publication|decl-consent-publication",
                    "Availability of data and materials|decl-data", "Code availability|decl-code",
                    "Competing interests|decl-competing-interests", "Funding|decl-funding",
                    "Authors' contributions|decl-contributions", "Use of generative AI|decl-ai",
                    "Acknowledgements|decl-acknowledgements"]
    (root / "manuscript/declarations/declarations.md").write_text("# Declarations\n\n" + "\n".join(
        f"## {title} {{#{ident}}}\n\nNot applicable for this synthetic paper (Zürich, 東京).\n"
        for title, ident in (item.split("|") for item in declarations)), encoding="utf-8")
    (root / "manuscript/cover-letter/cover_letter.md").write_text(
        "Dear Editor,\n\nWe submit the manuscript \"Stress\" as an Original Article in the Journal of Stress "
        "Testing.\n\nIt is a synthetic paper that exercises every part of the build.\n\nYours sincerely,\n\n"
        "Zoë Ångström, Universität Zürich, first@example.org\n", encoding="utf-8")


def review_comments(root: Path, rng, paragraphs: list[dict], count: int, repeated_quote: str) -> dict:
    """notes/review/comments.json: comments on plain sentences in every state,
    a quote repeated across paragraphs, and stale ones whose quote is gone."""
    candidates = [p for p in paragraphs if p["plain"] and "repeat" not in p]
    comments, quotes = [], []
    statuses = ["open"] * 3 + ["review"] * 2 + ["done"] * 4 + ["deleted"]
    for index in range(count):
        para = rng.choice(candidates)
        quote = rng.choice(para["plain"])
        status = rng.choice(statuses)
        comment = {"id": f"{rng.getrandbits(32):08x}", "doc": para["doc"], "section": para["section"],
                   "para": para["para"], "quote": quote,
                   "anchor": {"text": " ".join(para["plain"]), "before": "", "after": "", "object": "",
                              "sectionId": para["section_id"]},
                   "comment": rng.choice(["Rephrase this.", "¿Fuente? Añade la cita.", "Check the number 😀.",
                                          "Too long; split it."]),
                   "status": "deleted" if status == "deleted" else status,
                   "replies": [{"by": "agent", "text": "Done: rewritten.", "time": "2026-10-20 10:00"}]
                   if status in ("review", "done") else [],
                   "created": f"2026-10-{1 + index % 27:02d} {index % 24:02d}:{index % 60:02d}"}
        if status == "deleted":
            comment["deleted_from"] = "open"
        if index % 20 == 0:
            comment["kind"] = "underline"
        quotes.append({"id": comment["id"], "doc": para["doc"], "file": para["file"], "quote": quote,
                       "status": comment["status"]})
        comments.append(comment)
    stale = {**comments[0], "id": "5ta1e000", "quote": "This sentence was deleted long ago.", "status": "open"}
    stale.pop("kind", None)
    repeated = {**stale, "id": "4e9ea700", "quote": repeated_quote, "para": "Repeated text ¶2", "section": "Edge cases",
                "anchor": {"text": repeated_quote, "before": "", "after": "", "object": "", "sectionId": "edge-cases",
                           "prefix": "", "suffix": ""}}
    comments += [stale, repeated]
    path = root / "notes/review/comments.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(comments, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"quotes": quotes, "stale": [stale["id"]], "repeated": repeated["id"]}
