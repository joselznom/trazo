"""Render structured authors and visible keywords from Pandoc metadata."""

import json
import re
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from .portable import no_window
from .project import PROJECT


class MetadataError(ValueError):
    """The project's manuscript metadata cannot be read or resolved."""


@lru_cache(maxsize=8)
def _read(path, mtime_ns, size, command):
    with tempfile.TemporaryDirectory(prefix="trazo-metadata-") as folder:
        template = Path(folder) / "metadata.template"
        template.write_text("$meta-json$", encoding="utf-8")
        try:
            # An empty document on stdin: Windows has no /dev/null.
            result = subprocess.run(
                [*command, "-f", "markdown", f"--metadata-file={path}",
                 "-t", "markdown", f"--template={template}"],
                input="", capture_output=True, encoding="utf-8", check=True, **no_window())
            return json.loads(result.stdout)
        except (OSError, subprocess.CalledProcessError, ValueError) as exc:
            detail = exc.stderr if isinstance(exc, subprocess.CalledProcessError) else str(exc)
            raise MetadataError(f"cannot read metadata {path}: {detail.strip()}") from exc


def read_metadata():
    path = PROJECT.path("metadata")
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {}
    return _read(str(path), stat.st_mtime_ns, stat.st_size, tuple(PROJECT.command("pandoc")))


def _list(value):
    return value if isinstance(value, list) else ([] if value is None else [value])


def _name(author):
    name = author.get("name", "") if isinstance(author, dict) else author
    if isinstance(name, dict):
        return " ".join(str(name[key]) for key in ("given", "family") if name.get(key))
    return str(name)


def _affiliation(value):
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        raise MetadataError("affiliation must be a name, a ref, or a map")
    return ", ".join(str(value[key]) for key in
                     ("department", "name", "address", "city", "region", "postal-code", "country")
                     if value.get(key))


def _styled(style, text):
    return f'::: {{custom-style="{style}"}}\n\n{text}\n\n:::\n'


def _metadata_block(values):
    yaml = "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in values.items())
    return f"---\n{yaml}\n---\n\n"


def _front(metadata, authors, overrides=None):
    known = {str(entry["id"]): entry for entry in _list(metadata.get("affiliations"))
             if isinstance(entry, dict) and "id" in entry}
    affiliations, numbers = [], []
    for author in authors:
        author_numbers = []
        for value in _list(author.get("affiliations")) if isinstance(author, dict) else []:
            if isinstance(value, dict) and "ref" in value:
                reference = str(value["ref"])
                if reference not in known:
                    raise MetadataError(f"unknown affiliation ref: {reference}")
                value = known[reference]
            elif isinstance(value, str) and value in known:
                value = known[value]
            text = _affiliation(value)
            if not text:
                continue
            if text not in affiliations:
                affiliations.append(text)
            number = str(affiliations.index(text) + 1)
            if number not in author_numbers:
                author_numbers.append(number)
        numbers.append(author_numbers)
    shared = bool(affiliations) and all(number == ["1"] for number in numbers)
    names, corresponding = [], []
    equal = False
    for author, author_numbers in zip(authors, numbers):
        marks = [] if shared else list(author_numbers)
        if isinstance(author, dict):
            if author.get("corresponding"):
                marks.append(r"\*")
                contact = _name(author)
                if author.get("email"):
                    contact += f' (<{author["email"]}>)'
                corresponding.append(contact)
            if author.get("equal-contributor"):
                marks.append("†")
                equal = True
        names.append(_name(author) + ("^" + ",".join(marks) + "^" if marks else ""))
    content = [_styled("Author", ", ".join(names))]
    content.extend(_styled("Affiliation", ("" if shared else f"^{index}^ ") + text)
                   for index, text in enumerate(affiliations, 1))
    if corresponding:
        content.append(_styled("Correspondence", r"\* Corresponding author: " + "; ".join(corresponding)))
    if equal:
        content.append(_styled("Correspondence", "† These authors contributed equally."))
    if metadata.get("date"):
        content.append(_styled("Date", str(metadata["date"])))
    overrides = {**(overrides or {}), "author": [], "affiliations": []}
    if "date" in metadata:
        overrides["date"] = ""
    return _metadata_block(overrides) + '::::: {.pk-front}\n\n' + "\n".join(content) + "\n:::::\n\n"


def separate_title_page(project):
    """An article whose authors go on a separate title page, not under the title."""
    return project.config["kind"] == "article" and project.config["submission"]["title_page"] == "separate"


def render(text, document=None, with_authors=False):
    """`with_authors` keeps the authors under the title even with a separate
    title page (a copy for coauthors; the title page still has them)."""
    from .expand import fenced_lines

    metadata = read_metadata()
    separate = separate_title_page(PROJECT)
    if PROJECT.config["kind"] == "article" and document in ("cover_letter", "declarations"):
        overrides = {key: "" for key in ("title", "subtitle", "short-title", "title-short", "date",
                                         "abstract", "abstract-title", "keywords-title")}
        overrides.update(author=[], affiliations=[], institute=[], keywords=[])
        return _metadata_block(overrides) + text
    if document == "title_page" and separate:
        authors = _list(metadata.get("author"))
        front = _front(metadata, authors, {"abstract": "", "abstract-title": "", "keywords": []})
        orcids = []
        for author in authors:
            if isinstance(author, dict) and author.get("orcid"):
                identifier = str(author["orcid"])
                url = identifier if identifier.startswith("https://") else f"https://orcid.org/{identifier}"
                orcids.append(_styled("Correspondence", f"ORCID: {_name(author)} <{url}>"))
        return front.removesuffix("\n:::::\n\n") + "\n" + "\n".join(orcids) + "\n:::::\n\n" + text
    keywords = [str(term) for term in _list(metadata.get("keywords")) if term]
    if keywords and (document is None or document == next(iter(PROJECT.documents))):
        lines = text.splitlines(keepends=True)
        abstract, end = False, None
        for index, (line, literal) in enumerate(fenced_lines(lines)):
            if literal:
                continue
            heading = re.match(r"^ {0,3}#\s+(.+?)\s*$", line)
            if not heading:
                continue
            if abstract:
                end = index
                break
            title = re.sub(r"\s*\{[^{}]*\}\s*$", "", heading[1])
            title = re.sub(r"\s+#+\s*$", "", title).strip()
            abstract = title.casefold() == "abstract"
        if abstract:
            label = metadata.get("keywords-title", "Keywords")
            block = f'\n::: {{.pk-keywords custom-style="Keywords"}}\n\n**{label}:** ' + "; ".join(keywords) + "\n\n:::\n\n"
            lines.insert(len(lines) if end is None else end, block)
            text = "".join(lines)
    authors = _list(metadata.get("author"))
    if separate and not with_authors:
        text = '---\nauthor: []\naffiliations: []\ndate: ""\n---\n\n' + text
    elif any(isinstance(author, dict) for author in authors):
        text = _front(metadata, authors) + text
    return text


def affiliations(metadata, author):
    """The affiliation texts of an author, references resolved."""
    known = {str(entry["id"]): entry for entry in _list(metadata.get("affiliations"))
             if isinstance(entry, dict) and "id" in entry}
    texts = []
    for value in _list(author.get("affiliations")) if isinstance(author, dict) else []:
        if isinstance(value, dict) and "ref" in value:
            value = known.get(str(value["ref"]), {})
        elif isinstance(value, str) and value in known:
            value = known[value]
        text = _affiliation(value)
        if text and text not in texts:
            texts.append(text)
    return texts
