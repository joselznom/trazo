"""What an article still needs before submission, and its journal form fields.

    trazo submission check [--strict] [--json]

report() builds one report for the CLI and the preview's Submission view
(GET /__submission). Every item has a status: error (fix it before
submitting), warn (check it), ok or info. The groups:

- Journal: [submission] journal and guidelines, and where the title page
  and declarations go;
- Limits: the first document's counts (counts.py) against [limits], and the
  keyword count in the metadata;
- Metadata: authors, affiliations, a corresponding author with an email,
  keywords and ORCIDs;
- Declarations: every section with a `decl-` id, in source order: empty or
  with a placeholder is an error; `.form-only` sections (all with
  declarations = "form") are typed into the journal's form;
- Placeholders: [PLACEHOLDER…], [TODO…] and [TBD…] (across lines, not in
  code) in every document source, its includes and the metadata, outside
  declarations;
- Files to upload: the Word file of each document with a full build,
  missing (error) or out of date (warn; docx_freshness.py);
- Abbreviations and Cross-references, per document.

Items also say where they are fixed, for the preview: `doc` (a document),
`file`, `line` and `edit` (a source line its Source tab opens), `hash` (a
section id) or `section` (its heading, for sections without an id),
`block` ("front" or "keywords": the title block or keywords, made from the
metadata), `view` ("figures") or `url`. Settings name trazo.toml in
their detail.

The journal form's fields are in submission_fields.py.
"""

import re
from pathlib import Path

from . import counts, frontmatter
from .expand import Values, expand_file, fenced_lines, include_lines, resolve_include, substitute
from .project import PROJECT

# A marker and its text up to the closing bracket, across lines; the text
# may hold bracketed spans such as citations.
PLACEHOLDER = re.compile(r"\[(?:PLACEHOLDER|TODO|TBD)\b(?:[^\[\]]|\[[^\[\]]*\])*\]")
HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$", re.MULTILINE)
# An inline code span: a run of backticks up to the same run, within a paragraph.
INLINE_CODE = re.compile(r"(?<!`)(`+)(?!`)(?:(?!\n[ \t]*\n)[\s\S])*?(?<!`)\1(?!`)")
COMMENT = re.compile(r"<!--[\s\S]*?(?:-->|$)")
IMAGE = re.compile(r"!\[(?:[^\[\]]|\[[^\[\]]*\])*\]\(\s*<?([^)\s>]+)")
TITLE_PAGE = {"manuscript": "In the manuscript", "separate": "Separate file"}
DECLARATIONS = {"end": "At the end of the manuscript", "separate": "Separate file",
                "form": "In the journal's form only"}
FORM = "Type into the journal's form"
SUBMISSION_SETTING = "Set in trazo.toml [submission]"
LIMIT_SETTING = "Limit set in trazo.toml [limits]"


def prose(text):
    """`text` with fenced and inline code blanked, offsets and lines kept."""
    blank = lambda value: re.sub(r"[^\n]", " ", value)  # noqa: E731
    text = "".join(blank(line) if literal else line
                   for line, literal in fenced_lines(text.splitlines(keepends=True)))
    return INLINE_CODE.sub(lambda match: blank(match[0]), text)


def sections(text):
    """The ATX sections of Markdown `text`, in order: heading, id, classes,
    level, line, and offsets (start, body: after the heading line, end: the
    next heading of the same or a higher level)."""
    heads = list(HEADING.finditer(prose(text)))
    found, line, counted = [], 1, 0
    for index, head in enumerate(heads):
        # Counted from the previous heading: from the start, a long paper's
        # headings cost quadratic time.
        line += text.count("\n", counted, head.start())
        counted = head.start()
        level = len(head[1])
        title = text[head.start(2):head.end(2)]
        attributes = re.search(r"\s*\{([^{}]*)\}$", title)
        words = attributes[1].split() if attributes else []
        found.append({"heading": (title[:attributes.start()] if attributes else title).strip(),
                      "id": next((word[1:] for word in words if word.startswith("#")), ""),
                      "classes": [word[1:] for word in words if word.startswith(".")],
                      "level": level, "line": line,
                      "start": head.start(), "body": head.end(),
                      "end": next((other.start() for other in heads[index + 1:]
                                   if len(other[1]) <= level), len(text))})
    return found


def section_at(text, offset):
    """The innermost section of Markdown `text` around `offset`, or None."""
    found = None
    for section in sections(text):
        if section["start"] <= offset < section["end"]:
            found = section
    return found


def section_place(section):
    """How the preview finds a section: its id, else its heading."""
    if not section:
        return {}
    return {"hash": section["id"]} if section["id"] else {"section": section["heading"]}


def excerpt(text):
    """`text` on one line, at most 160 characters."""
    text = " ".join(text.split())
    return text if len(text) <= 160 else text[:159] + "…"


def placeholders(text):
    """(offset, first line, last line, excerpt) of each placeholder outside code."""
    for match in PLACEHOLDER.finditer(prose(text)):
        line = text.count("\n", 0, match.start()) + 1
        yield match.start(), line, line + match[0].count("\n"), excerpt(match[0])


def relative(path):
    path, root = Path(path).resolve(), PROJECT.root.resolve()
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)


def read(path):
    return Path(path).read_text(encoding="utf-8", errors="replace")


def includes(path):
    """The files a source includes, as `trazo expand` resolves them."""
    for _, target in include_lines(read(path)):
        if target is not None:
            try:
                yield resolve_include(target, path)
            except FileNotFoundError:
                continue  # the build reports it


def sources():
    """[(path, document, editable)]: every document's Markdown files (with
    their includes, depth first) and the declarations, in document order.
    `editable`: the Source tab of the document's page opens the file."""
    found, seen = [], set()

    def visit(path, name):
        key = path.resolve()
        if key in seen or not path.is_file():
            return
        seen.add(key)
        owner = PROJECT.source_document([path])
        found.append((path, owner or name, owner is not None))
        for child in includes(path):
            visit(child, owner or name)

    for name in PROJECT.documents:
        for path in PROJECT.document_files(name):
            visit(path, name)
    for path in sorted(PROJECT.path("declarations").glob("*.md")):
        visit(path, None)
    return found




def report(manuscript_counts=None, fields=False):
    """The submission report of the project; `manuscript_counts` returns the
    first document's counts (default: build its preview page and count it);
    `fields` adds the form fields (see form_fields())."""
    result = {"kind": PROJECT.config["kind"], "groups": [], "errors": 0, "warnings": 0}
    if result["kind"] != "article":
        return {**result, "fields": []}
    settings = PROJECT.config["submission"]
    first = next(iter(PROJECT.documents))
    result["journal"] = {key: settings[key] for key in ("journal", "guidelines", "title_page", "declarations")}
    result["placements"] = " · ".join((
        "Title page in the manuscript" if settings["title_page"] == "manuscript" else "Separate title page",
        "Declarations " + {"end": "at the end of the manuscript", "separate": "in a separate file",
                           "form": "in the journal's form only"}[settings["declarations"]]))

    def group(identifier, label):
        result["groups"].append({"id": identifier, "label": label, "items": []})
        return result["groups"][-1]["items"]

    def add(items, status, label, value="", detail="", **where):
        items.append({"status": status, "label": label, "value": value, "detail": detail,
                      **{key: place for key, place in where.items() if place}})
        result["errors"] += status == "error"
        result["warnings"] += status == "warn"

    items = group("journal", "Journal")
    add(items, "ok" if settings["journal"] else "warn", "Journal", settings["journal"] or "Not set",
        SUBMISSION_SETTING if settings["journal"]
        else "Set [submission] journal in trazo.toml (the journal-submission skill does it)")
    guidelines = settings["guidelines"]
    add(items, "ok" if guidelines else "warn", "Instructions for authors", guidelines or "Not set",
        SUBMISSION_SETTING if guidelines else "Set [submission] guidelines in trazo.toml to the journal's instructions URL",
        url=guidelines if re.match(r"https?://", guidelines) else "")
    titled = "title_page" if "title_page" in PROJECT.auto_documents else first
    add(items, "info", "Title page", TITLE_PAGE[settings["title_page"]], SUBMISSION_SETTING)
    add(items, "info", "Declarations", DECLARATIONS[settings["declarations"]],
        SUBMISSION_SETTING if settings["declarations"] != "form"
        else f"Not in any Word file: type them into the journal's form · {SUBMISSION_SETTING}")

    metadata, problem = {}, ""
    try:
        metadata = frontmatter.read_metadata()
    except frontmatter.MetadataError as exc:
        problem = str(exc)
    keywords = [str(term) for term in frontmatter._list(metadata.get("keywords")) if term]
    # The metadata has no Markdown section: its rows open where it is shown,
    # the title block (on the title page when it is separate) or the keywords.
    meta = {"file": PROJECT.relative("metadata"), "doc": titled, "block": "front"}
    meta_keywords = {**meta, "doc": first, "block": "keywords"}
    edit_meta = lambda detail="": detail if meta["file"] in detail else " · ".join(  # noqa: E731
        part for part in (detail, f"Edit {meta['file']}") if part)
    files = sources()
    texts = {path: read(path) for path, _, _ in files}
    # The first document's Abstract section, for its limit's row.
    abstract = next(({"file": relative(path), "line": section["line"], "edit": editable, **section_place(section)}
                     for path, name, editable in files if name == first for section in sections(texts[path])
                     if counts.excluded_heading(section["heading"], ["abstract"])), {})

    items = group("limits", "Limits")
    limits = {key: value for key, value in counts.current_config()["limits"].items() if value}
    try:
        found = manuscript_counts() if manuscript_counts else counts.document_counts(first)
    except (RuntimeError, OSError) as exc:
        found = None
        add(items, "error", "Counts", "Not available", f"{PROJECT.titles[first]}: {exc}", doc=first)
    for key, label, unit in (*counts.ROWS, ("keywords", "Keywords", "")):
        if found is None and key != "keywords":
            continue
        value, limit = len(keywords) if key == "keywords" else found[key], limits.get(key, 0)
        shown = "None" if value is None else (
            f"{value:,}" + (f" / {limit:,}" if limit else "") + (f" {unit}" if unit else ""))
        if not limit:
            status, detail = "info", "No limit set in trazo.toml [limits]"
        elif value is None:
            status, detail = "error", f"Not found: no Abstract heading or abstract: metadata · {LIMIT_SETTING}"
        elif value > limit:
            status, detail = "error", f"{value - limit:,} over the limit · {LIMIT_SETTING}"
        else:
            status, detail = "ok", f"{f'{limit - value:,} left' if value < limit else 'At the limit'} · {LIMIT_SETTING}"
        # Each row opens what it counts.
        place = {"doc": first, "view": "figures" if key in ("figures", "tables") else "",
                 "hash": "refs" if key == "references" else ""}
        if key == "abstract":
            place.update(abstract)
        elif key == "keywords":
            place, detail = meta_keywords, edit_meta(detail)
        add(items, status, label, shown, detail, **place)

    items = group("metadata", "Metadata")
    if problem:
        add(items, "error", "Metadata", "Cannot be read", edit_meta(problem), **meta)
    authors = frontmatter._list(metadata.get("author"))
    names = [frontmatter._name(author) for author in authors]
    add(items, "ok" if authors else "error", "Authors", "; ".join(names) or "None",
        edit_meta() if authors else f"Add the authors to {meta['file']}", **meta)
    if authors:
        try:
            missing = [name for name, author in zip(names, authors) if not frontmatter.affiliations(metadata, author)]
        except frontmatter.MetadataError as exc:
            add(items, "error", "Affiliations", "Cannot be read", edit_meta(str(exc)), **meta)
        else:
            add(items, "warn" if missing else "ok", "Affiliations",
                f"Missing for {', '.join(missing)}" if missing else "Every author has one", edit_meta(), **meta)
        contacts = [(name, author) for name, author in zip(names, authors)
                    if isinstance(author, dict) and author.get("corresponding")]
        reachable = [f"{name} <{author['email']}>" for name, author in contacts if author.get("email")]
        add(items, "ok" if reachable else "warn", "Corresponding author", "; ".join(reachable) or (
            f"{contacts[0][0]} has no email" if contacts else "None"),
            edit_meta() if reachable else f"Mark one author corresponding: true, with an email, in {meta['file']}",
            **meta)
        missing = [name for name, author in zip(names, authors)
                   if not (isinstance(author, dict) and author.get("orcid"))]
        add(items, "info" if missing else "ok", "ORCID",
            f"Missing for {', '.join(missing)}" if missing else "Every author has one", edit_meta(), **meta)
    add(items, "ok" if keywords else "warn", "Keywords", "; ".join(keywords) or "None",
        edit_meta() if keywords else f"Add keywords to {meta['file']}", **meta_keywords)

    declared = {}  # path -> the (start, end) of its decl- sections
    items = group("declarations", "Declarations")
    for path, name, editable in files:
        text = texts[path]
        declarations = [section for section in sections(text) if section["id"].startswith("decl-")]
        if not declarations:
            continue
        try:
            resolved = substitute(expand_file(path), Values(path=PROJECT.path("values")))
            resolved_sections = {section["id"]: resolved[section["body"]:section["end"]]
                                 for section in sections(resolved)}
            expansion_error = ""
        except (OSError, ValueError, RuntimeError) as exc:
            resolved_sections, expansion_error = {}, f"Cannot expand: {exc}"
        for section in declarations:
            declared.setdefault(path, []).append((section["start"], section["end"]))
            body = COMMENT.sub("", prose(resolved_sections.get(section["id"], "")))
            words = " ".join(body.split())
            form = settings["declarations"] == "form" or "form-only" in section["classes"]
            na = re.fullmatch(r"not applicable\.?", words, re.IGNORECASE)
            mark = PLACEHOLDER.search(body)
            status, detail = (("error", expansion_error) if expansion_error else ("error", "Empty") if not words
                              else ("error", "Contains a placeholder") if mark
                              else ("info", FORM) if form else ("ok", "Not applicable" if na else "Filled"))
            add(items, status, section["heading"] or section["id"],
                "Not applicable" if na and form else excerpt(mark[0]) if mark else "",
                detail, file=relative(path), line=section["line"], doc=name, hash=section["id"],
                edit=editable)
    if not items:
        add(items, "warn", "Declarations", "None found",
            f"Add the journal's declarations to {PROJECT.relative('declarations')}/ (headings with a decl- id)")

    items = group("placeholders", "Placeholders")
    metadata_path = PROJECT.path("metadata")
    for path, name, editable in [*files, (metadata_path, titled, False)]:
        if path not in texts:
            if not path.is_file():
                continue
            texts[path] = read(path)
        for start, line, last, quote in placeholders(texts[path]):
            if any(begin <= start < end for begin, end in declared.get(path, [])):
                continue
            place = ({"block": "front"} if path == metadata_path
                     else section_place(section_at(texts[path], start)))
            add(items, "error", f"{relative(path)}:{line}", quote, edit_meta() if path == metadata_path else "",
                file=relative(path), line=line, end_line=last if last > line else 0, doc=name, edit=editable, **place)
    if not items:
        add(items, "ok", "Placeholders", "None found")

    from .docx_freshness import WordFiles
    items, fix, word_files = group("files", "Files to upload"), PROJECT.command_hint("submission"), WordFiles(PROJECT)
    for name in PROJECT.documents:
        output = PROJECT.document_docx(name)
        if PROJECT.document_build(name) is None or output is None:
            continue
        shown = relative(output)
        if not output.is_file():
            add(items, "error", PROJECT.titles[name], shown, f"Missing: run {fix}", doc=name)
            continue
        stale = word_files.stale(name, output)
        add(items, "warn" if stale else "ok", PROJECT.titles[name], shown,
            f"{stale}: run {fix}" if stale else "Up to date", doc=name)

    from .abbreviations import check as abbreviations
    from .labels import check as crossrefs
    for title, check in (("Abbreviations", abbreviations), ("Cross-references", crossrefs)):
        items = group(title.lower(), title)
        for name in PROJECT.documents:
            try:
                findings = check(PROJECT, name)
            except (OSError, ValueError, RuntimeError) as exc:
                add(items, "warn", f"{title} unavailable", detail=str(exc), doc=name)
                continue
            for item in findings:
                add(items, **item)
        if not items:
            add(items, "ok", title, "No issues found")

    if fields:
        from .submission_fields import form_fields
        result["fields"] = form_fields(metadata)
    return result
