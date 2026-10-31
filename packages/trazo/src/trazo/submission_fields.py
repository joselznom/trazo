"""The journal form's fields of an article, as plain text, for the
submission report (submission_check.py) and the preview's Submission view.

form_fields() gives the form fields as plain text, with the key points or
highlights some journals ask for: Pandoc renders the expanded Markdown
(values and citations included) with the project's settings.
"""

import json
import re
import tempfile
from functools import lru_cache
from pathlib import Path

from . import counts, frontmatter, numbering, pandoc_run
from .expand import Values, expand_file, fenced_lines, substitute
from .project import PROJECT
from .submission_check import HEADING, read, sections

# Sections of the first document with their own form field after the Abstract.
KEY_POINTS = ["key points", "highlights", "key results"]
# Separates the parts of one Pandoc run; never in a paper.
MARK = "TrazoFormField"


@lru_cache(maxsize=16)
def _plain(text, command, flags, numbers, stamps):
    """Pandoc's plain text of Markdown `text`; `stamps` (input file times)
    only keep the cache honest."""
    with tempfile.TemporaryDirectory(prefix="trazo-form-") as folder:
        if numbers:
            path = Path(folder) / "numbering.json"
            path.write_text(numbers, encoding="utf-8")
            flags = (*flags, f"--metadata-file={path}")
        result = pandoc_run.run(["-f", "markdown", "-t", "plain", "--wrap=none", *flags],
                                input=text, text=True, command=list(command), cwd=PROJECT.root)
    if result.returncode:
        raise ValueError(result.stderr.strip() or f"Pandoc exited with {result.returncode}")
    return result.stdout


def _parts(output, count):
    """The text after each numbered MARK line, up to the next MARK line."""
    found, current = {}, None
    for line in output.splitlines():
        marker = re.fullmatch(rf"{MARK}(\d*)", line.strip())
        if marker:
            current = int(marker[1]) if marker[1] else None
        elif current is not None:
            found.setdefault(current, []).append(line.rstrip())
    return ["\n".join(found.get(index, [])).strip() for index in range(count)]


def plain(parts):
    """The plain text of standalone Markdown parts, in one Pandoc run."""
    text = "".join(f"\n\n{MARK}{index}\n\n{part}" for index, part in enumerate(parts))
    return _parts(_plain(text + f"\n\n{MARK}\n", tuple(PROJECT.command("pandoc")), (), "", ()), len(parts))


def expanded(name):
    """A document's Markdown with includes and {{v:key}} values, before the
    Word-only steps (form-only sections stay; no title block)."""
    text = "\n\n".join(expand_file(path).rstrip() for path in PROJECT.document_files(name)) + "\n"
    return substitute(text, Values(path=PROJECT.path("values")))


def plain_sections(name, text, spans):
    """The plain text of parts of a document's Markdown `text`, rendered
    within the whole document like its Word file (metadata, crossref,
    citeproc), so citation numbers match. `spans` is [(start, end, transform
    of the text)], in source order and apart."""
    from .abbreviations import render
    hidden = [section for section in sections(text) if "form-only" in section["classes"]]
    original = text
    for section in reversed(hidden):
        start, end = section["start"], section["end"]
        text = text[:start] + re.sub(r"[^\n]", " ", text[start:end]) + text[end:]
    form_spans = {index for index, (start, end, change) in enumerate(spans)
                  if any(section["start"] <= start < section["end"] for section in hidden)}
    pieces, last = [], 0
    for index, (start, end, change) in enumerate(spans):
        if index in form_spans:
            continue
        pieces += [text[last:start], f"\n\n{MARK}{index}\n\n", change(text[start:end]), f"\n\n{MARK}\n\n"]
        last = end
    pieces.append(text[last:])
    inputs = [PROJECT.path(key) for key in ("metadata", "bibliography", "csl")]
    flags = tuple(flag for flag, path in zip(("--metadata-file", "--bibliography", "--csl"), inputs)
                  for flag in [f"{flag}={path}"] if path.is_file())
    flags += ("--resource-path=.", "--filter=pandoc-crossref", "--citeproc")
    numbers = json.dumps(numbering.metadata(name))
    stamps = tuple(path.stat().st_mtime_ns if path.is_file() else 0 for path in inputs)
    output = _plain(render("".join(pieces), PROJECT.config["abbreviations"]), tuple(PROJECT.command("pandoc")), flags, numbers, stamps)
    rendered = _parts(output, len(spans))
    # Append form-only fields after Word's content: reuse its citation numbers,
    # then number form-only references without changing ordinary fields or counts.
    if form_spans:
        for index, (start, end, change) in enumerate(spans):
            if index in form_spans:
                pieces += [f"\n\n{MARK}{index}\n\n", change(original[start:end]), f"\n\n{MARK}\n\n"]
        forms = _parts(_plain(render("".join(pieces), PROJECT.config["abbreviations"]), tuple(PROJECT.command("pandoc")),
                              flags, numbers, stamps), len(spans))
        for index in form_spans:
            rendered[index] = forms[index]
    return rendered


def run_in(text):
    """Sub-headings as the start of their first paragraph ("Objectives: …")."""
    lines, lead = [], None
    for line, literal in fenced_lines(text.split("\n")):
        heading = None if literal else HEADING.fullmatch(line)
        if heading:
            title = re.sub(r"\s*\{[^{}]*\}$", "", heading[2]).strip()
            lead = title if re.search(r"[:.?!]$", title) else title + ":"
            continue
        if lead is not None and line.strip():
            if literal or re.match(r" {0,3}(?:[-*+]|\d+[.)])[ \t]|>|\||:::", line):
                lines += [lead, ""]
            else:
                line = f"{lead} {line.lstrip()}"
            lead = None
        lines.append(line)
    return "\n".join(lines + ([lead] if lead is not None else []))


def source_text(name):
    """A document's sources as written, when they cannot be expanded."""
    return "\n\n".join(read(path).rstrip() for path in PROJECT.document_files(name)) + "\n"


def form_fields(metadata=None):
    """[{label, text, form_only, error?}]: plain text for the journal's web
    form. Title, short title, abstract, each key points, highlights or key
    results section of the first document (labelled with its heading; one
    inside the Abstract is left out of the abstract), keywords, authors
    (name, affiliations, email, ORCID), numbered affiliations, the
    corresponding author, then every `decl-` section in document order.
    `metadata` defaults to the project's ({} when it cannot be read: the
    report's Metadata group says why)."""
    if metadata is None:
        try:
            metadata = frontmatter.read_metadata()
        except frontmatter.MetadataError:
            metadata = {}
    first = next(iter(PROJECT.documents))
    form = PROJECT.config["submission"]["declarations"] == "form"
    field = lambda label, text, error="", form_only=False: {  # noqa: E731
        "label": label, "text": text, "form_only": form_only, **({"error": error} if error else {})}
    numbered, people, contacts = [], [], []
    try:
        for author in frontmatter._list(metadata.get("author")):
            texts = frontmatter.affiliations(metadata, author)
            numbered += [text for text in texts if text not in numbered]
            details = author if isinstance(author, dict) else {}
            email = [details["email"]] if details.get("email") else []
            orcid = [f"ORCID {details['orcid']}"] if details.get("orcid") else []
            people.append("; ".join([frontmatter._name(author), *texts, *email, *orcid]))
            if details.get("corresponding"):
                contacts.append("; ".join([frontmatter._name(author), *email]))
    except frontmatter.MetadataError:
        pass  # the Metadata group reports it
    # One line each (hard line breaks); "1." must not start a list.
    lines = lambda values: "\\\n".join(re.sub(r"^(\d+)\.", r"\1\\.", value) for value in values)  # noqa: E731
    wanted = [("Title", str(metadata.get("title", "")))]
    if metadata.get("short-title"):
        wanted.append(("Short title", str(metadata["short-title"])))
    if metadata.get("abstract"):  # used without an Abstract section
        wanted.append(("Abstract", str(metadata["abstract"])))
    wanted += [("Keywords", "; ".join(str(term) for term in frontmatter._list(metadata.get("keywords")) if term)),
               ("Authors", lines(people)),
               ("Affiliations", lines(f"{index}. {text}" for index, text in enumerate(numbered, 1))),
               ("Corresponding author", lines(contacts))]
    try:
        texts, error = plain([text for _, text in wanted]), ""
    except (OSError, ValueError) as exc:
        texts, error = [""] * len(wanted), str(exc)
    fields = [field(label, text, error) for (label, _), text in zip(wanted, texts)]
    if not metadata.get("abstract"):
        fields.insert(len(fields) - 4, field("Abstract", ""))
    declarations, points = [], []
    for name in PROJECT.documents:
        error = ""
        try:
            text = expanded(name)
        except (OSError, ValueError, RuntimeError) as exc:  # values, a missing or circular include
            text, error = source_text(name), f"{PROJECT.titles[name]}: {exc}"
        found = sections(text)
        abstract, keys = None, []
        if name == first:
            abstract = next((section for section in found
                             if counts.excluded_heading(section["heading"], ["abstract"])), None)
            keys = [section for section in found if counts.excluded_heading(section["heading"], KEY_POINTS)]
        picked = [section for section in found
                  if section is abstract or section in keys or section["id"].startswith("decl-")]
        # A section inside another picked one is part of its text, except key
        # points inside the Abstract: their own field, not the abstract's.
        picked = [section for index, section in enumerate(picked)
                  if not any(other["start"] < section["start"] < other["end"]
                             and not (other is abstract and section in keys) for other in picked[:index])]
        if not picked:
            continue
        spans = []  # (section, start, end): the text of each field, in source order
        for section in picked:
            if section is abstract:  # around the key points inside it
                inner = [edge for other in picked if section["start"] < other["start"] < section["end"]
                         for edge in (other["start"], other["end"])]
                edges = [section["body"], *inner, section["end"]]
                spans += [(section, start, end) for start, end in zip(edges[::2], edges[1::2])]
            else:
                spans.append((section, section["body"], section["end"]))
        spans.sort(key=lambda span: span[1])
        texts = [""] * len(spans)
        if not error:
            try:
                texts = plain_sections(name, text, [(start, end, run_in if section is abstract else str)
                                                    for section, start, end in spans])
            except (OSError, ValueError) as exc:
                error = f"{PROJECT.titles[name]}: {exc}"
        summary = []
        for (section, _, _), value in zip(spans, texts):
            if section is abstract:
                summary.append(value)
            elif section in keys:
                points.append(field(section["heading"], value, error))
            else:
                declarations.append(field(section["heading"] or section["id"], value, error,
                                          form or "form-only" in section["classes"]))
        if abstract:
            fields[[entry["label"] for entry in fields].index("Abstract")] = field(
                "Abstract", "\n\n".join(value for value in summary if value), error)
    from .abbreviations import entries, inline
    try:
        abbreviation_text = expanded(first)
    except (OSError, ValueError, RuntimeError):
        abbreviation_text = source_text(first)
    found = entries(abbreviation_text, PROJECT.config["abbreviations"]["sort"])
    if found:
        from .abbreviations import lists
        spans = [(section["body"], section["end"],
                  lambda body, section=section: inline([entry for entry in found if entry["section"] == section]))
                 for section in lists(abbreviation_text)]
        try:
            rendered = plain_sections(first, abbreviation_text, spans)
            fields.append(field("Abbreviations", "\n\n".join(rendered)))
        except (OSError, ValueError) as exc:
            fields.append(field("Abbreviations", "", str(exc)))
    after = [entry["label"] for entry in fields].index("Abstract") + 1
    return fields[:after] + points + fields[after:] + declarations
