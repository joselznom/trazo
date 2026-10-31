"""Document-local abbreviation lists, layouts and advisory checks.

    trazo abbreviations check [--json] [DOCUMENT ...]

Lists are Markdown definition lists under #abbreviations or a heading named
Abbreviations / List of abbreviations. Their source position is retained.
Uncaptioned Markdown tables keep cell content in Pandoc's normal pipeline.
"""

import argparse
import json
import re
from bisect import bisect_right
from html import escape
from html.parser import HTMLParser

from .project import PROJECT

TOKEN = r"[A-Za-z][A-Za-z0-9]*(?:-\d+)*"


def tokens(text, terms=()):
    return token_pattern(terms).finditer(text)


def token_pattern(terms=()):
    alternatives = "|".join(re.escape(term) + r"(?:'s|s)?" for term in sorted(terms, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alternatives + '|' if alternatives else ''}{TOKEN})(?!\w)")


def uses(text, matches):
    for match in matches:
        if (re.search(r"(?<!\w)[A-Z][A-Za-z0-9]*-$", text[max(0, match.start() - 128):match.start()])
                or re.match(r"-[A-Z][A-Za-z0-9]*", text[match.end():match.end() + 128])
                or re.search(r"[A-Z][A-Za-z0-9]*-[A-Z]", match[0])):
            continue
        yield match


def is_section(section):
    title = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", section["heading"]).casefold()
    return section["id"] == "abbreviations" or title in {"abbreviations", "list of abbreviations"}


def lists(text):
    from .submission_check import sections
    return [section for section in sections(mask(text)) if is_section(section)]


def mask(text):
    from .expand import fenced_lines
    text = re.sub(r"<!--[\s\S]*?(?:-->|$)", lambda match: re.sub(r"[^\n]", " ", match[0]), text)
    return "".join(re.sub(r"[^\n]", " ", line) if literal else line
                   for line, literal in fenced_lines(text.splitlines(keepends=True)))


def parse(text):
    found, unreadable, _ = parsed_lists(text)
    return found, unreadable


def parsed_lists(text):
    """parse(text), and lists(text) too."""
    masked = mask(text)
    found, unreadable, listed = [], [], lists(text)
    uncommented = None
    for section in listed:
        body = masked[section["body"]:section["end"]]
        lines = body.splitlines(keepends=True)
        offsets, offset = [], section["body"]
        for line in lines:
            offsets.append(offset)
            offset += len(line)
        index, invalid = 0, False
        while index < len(lines):
            if not lines[index].strip():
                index += 1
                continue
            start = index
            term = lines[index].strip()
            index += 1
            while index < len(lines) and not lines[index].strip():
                index += 1
            definitions = []
            while index < len(lines):
                marker = re.match(r"^ {0,3}[:~][ \t]+(.*)", lines[index])
                if not marker:
                    break
                paragraphs = [marker[1].strip()]
                index += 1
                while index < len(lines):
                    line = lines[index]
                    if re.match(r"^(?: {4}|\t)", line):
                        paragraphs.append(line[4:].strip() if line.startswith("    ") else line[1:].strip())
                        index += 1
                    elif not line.strip():
                        following = index + 1
                        while following < len(lines) and not lines[following].strip():
                            following += 1
                        if following < len(lines) and re.match(r"^(?: {4}|\t)", lines[following]):
                            paragraphs.append("")
                            index = following
                        else:
                            index = following
                            break
                    else:
                        break
                definitions.append("\n".join(paragraphs).strip())
            if not definitions or any(not definition for definition in definitions):
                invalid = True
                continue
            found.append({"term": term, "meaning": "; ".join(" ".join(value.split()) for value in definitions),
                          "definitions": definitions, "start": offsets[start],
                          "end": offsets[index] if index < len(lines) else section["end"], "section": section})
        if invalid:
            unreadable.append(section)
        else:
            if uncommented is None:
                uncommented = re.sub(r"<!--[\s\S]*?(?:-->|$)", lambda match: re.sub(r"[^\n]", " ", match[0]), text)
            # This list's text without its entries: copying the whole
            # document once per entry made a long list slow.
            base = section["body"]
            remaining = uncommented[base:section["end"]]
            for entry in reversed([entry for entry in found if entry["section"] == section]):
                start, end = entry["start"] - base, entry["end"] - base
                remaining = remaining[:start] + " " * (end - start) + remaining[end:]
            if remaining.strip():
                unreadable.append(section)
    return found, unreadable, listed


def entries(text, sort=True):
    found, _ = parse(text)
    return sorted(found, key=lambda entry: "".join(letter for letter in entry["term"].casefold()
                                                  if letter.isalpha())) if sort else found


def inline(found):
    return "; ".join(f'{entry["term"]}, {entry["meaning"].rstrip(".")}' for entry in found) + ("." if found else "")


def table(found):
    rows = [("Abbreviation", "Meaning"), *((entry["term"], entry["meaning"]) for entry in found)]
    lines = ["| " + " | ".join(value.replace("|", r"\|") for value in row) + " |" for row in rows]
    lines.insert(1, "|---|---|")
    marker = '<w:p><w:pPr><w:pStyle w:val="TrazoAbbreviationsMarker"/></w:pPr></w:p>'
    return '::: {.pk-abbreviations}\n\n```{=openxml}\n' + marker + '\n```\n\n' + "\n".join(lines) + '\n\n:::\n\n'


def render(text, settings):
    replacements = []
    parsed, unreadable, listed = parsed_lists(text)
    for section in listed:
        if section in unreadable:
            continue
        found = [entry for entry in parsed if entry["section"] == section]
        if settings["sort"]:
            found.sort(key=lambda entry: "".join(letter for letter in entry["term"].casefold() if letter.isalpha()))
        if not found:
            replacements.append((section["start"], section["end"], ""))
            continue
        style = settings["style"]
        body = (table(found) if style == "table" else inline(found) if style == "inline" else
                "\n\n".join(entry["term"] + "\n\n" + "\n\n".join(
                    ":   " + definition.replace("\n", "\n    ") for definition in entry["definitions"]) for entry in found))
        ordered = sorted(found, key=lambda entry: entry["start"])
        for index, entry in enumerate(ordered):
            replacements.append((entry["start"], entry["end"], body + "\n\n" if index == 0 else ""))
    replacements.sort()
    if all(before[1] <= after[0] for before, after in zip(replacements, replacements[1:])):
        pieces, last = [], 0  # one copy of a long document, not one per entry
        for start, end, body in replacements:
            pieces += [text[last:start], body]
            last = end
        return "".join(pieces) + text[last:]
    for start, end, body in reversed(replacements):  # a list inside another one's entries
        text = text[:start] + body + text[end:]
    return text


def clean(text, objects=False):
    from .submission_check import prose
    text = prose(text)
    patterns = [r"<!--[\s\S]*?-->", r"\[(?:PLACEHOLDER|TODO|TBD)\b[^\]]*\]",
                r"\$\$[\s\S]*?\$\$|\$[^$\n]+\$|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]",
                r"(?m)^(?: {4}|\t).*$", r"(?m)^ {0,3}\[[^\]]+\]:.*$",
                r"\{[^}]*\}", r"\[[^\]]*@[\s\S]*?\]",
                r"@[-\w:.]+", r"https?://[^\s<>]+", r"\]\([^)]*\)", r"<[^>]+>",
                r"\{\{v:[^}]+\}\}"]
    if objects:
        patterns = [r"!\[(?:[^\[\]]|\[[^\]]*\])*\]\([^)]*\)(?:\{[^}]*\})?",
                    r"(?ms)^:{3,}\s*\{[^}]*\.table-note[^}]*\}.*?^:{3,}\s*$",
                    r"(?m)^\s*\|.*$", r"(?m)^\s*(?:Table:?|Figure:?|:)\s+.*$", *patterns]
        from .expand import table_lines
        lines = text.splitlines(keepends=True)
        covered = table_lines(lines)
        text = "".join(re.sub(r"[^\n]", " ", line) if index in covered else line for index, line in enumerate(lines))
    for pattern in patterns:
        text = re.sub(pattern, lambda match: re.sub(r"[^\n]", " ", match[0]), text)
    return text


def acronym(token):
    return (sum(letter.isupper() for letter in token) >= 2
            and not re.fullmatch(r"[IVXLCDM]+[a-z]?", token)
            and not re.fullmatch(r"(?:[A-Z][a-z]+){2,}", token))


def variants(text):
    words = re.findall(r"[a-z0-9]+", text.casefold())
    return [word[:-1] if word.endswith("s") and len(word) > 3 else word for word in words]


def defined(text, offset, term, meaning):
    window = max(256, len(meaning) * 3)
    before, after = text[max(0, offset - window):offset], text[offset + len(term):offset + len(term) + window]
    parenthesis = re.match(r"\s*\(([^)]+)\)", after)
    if parenthesis and variants(parenthesis[1]) == variants(meaning):
        return True
    if not re.search(r"\(\s*$", before) or not re.match(r"\s*\)", after):
        return False
    wanted = variants(meaning)
    return bool(wanted) and variants(before.rsplit("(", 1)[0])[-len(wanted):] == wanted


def canonical(token, known):
    if token in known:
        return token
    for suffix in ("'s", "s"):
        if token.endswith(suffix) and token[:-len(suffix)] in known:
            return token[:-len(suffix)]
    return token


def document_sources(project, name):
    from .expand import resolve_include
    result = []

    def visit(path, stack=()):
        if path.resolve() in stack:
            raise ValueError(f"circular include: {path}")
        text = path.read_text(encoding="utf-8")
        start = 0
        from .submission_check import prose
        for match in re.finditer(r"(?m)^!include (.+)$", prose(text)):
            result.append((path, text[start:match.start()], text.count("\n", 0, start)))
            visit(resolve_include(text[match.start(1):match.end(1)], path), (*stack, path.resolve()))
            start = match.end()
        result.append((path, text[start:], text.count("\n", 0, start)))

    for path in project.document_files(name):
        visit(path)
    return result


def document_text(project, name):
    return "\n\n".join(text for _, text, _ in document_sources(project, name))


def check(project, name):
    from . import counts, frontmatter
    from .submission_check import section_place, sections
    def relative(path):
        path = path.resolve()
        return path.relative_to(project.root).as_posix() if path.is_relative_to(project.root) else str(path)
    sources = document_sources(project, name)
    text = "\n\n".join(chunk for _, chunk, _ in sources)
    spans, start = [], 0
    for path, chunk, base in sources:
        spans.append((start, start + len(chunk), path, base))
        start += len(chunk) + 2
    span_starts = [span[0] for span in spans]
    newlines = [match.start() for match in re.finditer("\n", text)]

    def location(offset, section):
        start, _, path, base = spans[bisect_right(span_starts, offset) - 1]
        return {"file": relative(path), "line": base + bisect_right(newlines, offset - 1) - bisect_right(newlines, start - 1) + 1,
                "doc": name, "edit": project.source_document([path]) is not None, **section_place(section)}

    settings = project.config["abbreviations"]
    listed, unreadable = parse(text)
    if settings["sort"]:
        listed.sort(key=lambda entry: "".join(letter for letter in entry["term"].casefold() if letter.isalpha()))
    findings = [{"status": "warn", "label": "Abbreviations list could not be read", "value": section["heading"],
                 "detail": "Abbreviations list could not be read: use Markdown definition-list entries; source left unchanged",
                 **location(section["start"], section)} for section in unreadable]
    if not listed:
        return findings
    known = {entry["term"]: entry for entry in listed}
    ignore = set(settings["ignore"])
    regions = {"abstract": [], "main": []}
    masked = clean(text)
    headings = sections(mask(text))
    heading_starts = [section["start"] for section in headings]
    ancestry, active = [], []
    for section in headings:
        while active and active[-1]["level"] >= section["level"]:
            active.pop()
        active.append(section)
        ancestry.append(tuple(active))
    for section in lists(text):
        masked = masked[:section["start"]] + re.sub(r"[^\n]", " ", masked[section["start"]:section["end"]]) + masked[section["end"]:]
    matches = list(tokens(masked, known))
    used = {canonical(match[0], known) for match in matches}
    first_use = clean(text, objects=True)
    for match in uses(masked, matches):
        token = canonical(match[0], known)
        if token not in known and (token in ignore or not acronym(token)):
            continue
        if first_use[match.start():match.end()] != match[0]:
            continue
        heading_index = bisect_right(heading_starts, match.start()) - 1
        parents = ancestry[heading_index] if heading_index >= 0 else ()
        section = parents[-1] if parents else None
        if section and match.start() < section["body"]:
            continue
        place = location(match.start(), section)
        if any(counts.excluded_heading(parent["heading"], ["abstract"]) for parent in parents):
            region = "abstract"
        elif any(counts.excluded_heading(parent["heading"], project.config["counts"]["exclude"])
                 or {"uncounted", "form-only"} & set(parent["classes"]) or parent["id"].startswith("decl-") for parent in parents):
            continue
        else:
            region = "main"
        regions[region].append((match[0], first_use, match.start(), place))
    if name == next(iter(project.documents)) and not regions["abstract"]:
        try:
            metadata = frontmatter.read_metadata()
        except frontmatter.MetadataError:
            metadata = {}
        abstract = clean(str(metadata.get("abstract", "")))
        metadata_path = project.path("metadata")
        raw_metadata = metadata_path.read_text(encoding="utf-8") if metadata_path.is_file() else ""
        abstract_key = re.search(r"(?m)^abstract\s*:", raw_metadata)
        abstract_start = abstract_key.end() if abstract_key else 0
        abstract_matches = list(tokens(abstract, known))
        for match in uses(abstract, abstract_matches):
            original = re.search(rf"(?<!\w){re.escape(match[0])}(?!\w)", raw_metadata[abstract_start:])
            line = raw_metadata.count("\n", 0, abstract_start + (original.start() if original else 0)) + 1
            regions["abstract"].append((match[0], abstract, match.start(),
                {"file": project.relative("metadata"), "line": line, "doc": name, "block": "front"}))
        used.update(canonical(match[0], known) for match in abstract_matches)

    def warn(label, term, place, detail):
        findings.append({"status": "warn", "label": label, "value": term, "detail": detail, **place})

    for entry in listed:
        if entry["term"] not in used:
            warn("Listed but unused", entry["term"], location(entry["start"], entry["section"]),
                "Not found outside the abbreviation list")
    missing = set()
    for region, occurrences in regions.items():
        seen = set()
        for token, text, offset, place in occurrences:
            spelling = token
            token = canonical(token, known)
            if acronym(token) and token not in known and token not in ignore and token not in missing:
                missing.add(token)
                warn("Used but not listed", token, place, "Acronym-like token; add it to the list or [abbreviations] ignore")
            if token in known and token not in seen:
                seen.add(token)
                if not defined(text, offset, spelling, known[token]["meaning"]):
                    warn("Define at first use", token, place, f"First use in {region}; expected {known[token]['meaning']} ({token}) or the reverse")
    return findings


def annotate(html, found):
    meanings = {entry["term"]: entry["meaning"] for entry in found}
    pattern = token_pattern(meanings)  # once: the parser hands over every run of text
    full_document = bool(re.search(r"<(?:html|head|body)\b", html, re.I))

    class Annotator(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.output, self.stack = [], []

        def handle_starttag(self, tag, attrs):
            self.output.append(self.get_starttag_text())
            attributes = dict(attrs)
            blocked = tag in {"head", "title", "script", "style", "code", "pre", "math", "a", "abbr", "table", "dl", "h1", "h2", "h3", "h4", "h5", "h6"} or bool({"pv-val", "math", "pk-abbreviations"} & set(attributes.get("class", "").split()))
            if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
                self.stack.append((tag, blocked))

        def handle_endtag(self, tag):
            self.output.append(f"</{tag}>")
            for index in range(len(self.stack) - 1, -1, -1):
                if self.stack[index][0] == tag:
                    del self.stack[index:]
                    break

        def handle_startendtag(self, tag, attrs):
            self.output.append(self.get_starttag_text())

        def handle_data(self, data):
            in_body = any(tag == "body" for tag, _ in self.stack)
            if (in_body or not full_document) and not any(blocked for _, blocked in self.stack):
                parts, start = [], 0
                for match in pattern.finditer(data):
                    term = canonical(match[0], meanings)
                    if term not in meanings:
                        continue
                    parts.extend((data[start:match.start()], f'<abbr title="{escape(meanings[term], quote=True)}">{match[0]}</abbr>'))
                    start = match.end()
                data = "".join([*parts, data[start:]])
            self.output.append(data)

        def handle_entityref(self, name):
            self.output.append(f"&{name};")

        def handle_charref(self, name):
            self.output.append(f"&#{name};")

        def handle_comment(self, data):
            self.output.append(f"<!--{data}-->")

        def handle_decl(self, decl):
            self.output.append(f"<!{decl}>")

    parser = Annotator()
    parser.feed(html)
    return "".join(parser.output)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="trazo abbreviations")
    parser.add_argument("command", choices=["check"])
    parser.add_argument("documents", nargs="*")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    findings = []
    for name in args.documents or PROJECT.documents:
        if name not in PROJECT.documents:
            parser.error(f"unknown document: {name}")
        try:
            findings.extend(check(PROJECT, name))
        except (OSError, ValueError, RuntimeError) as exc:
            parser.exit(1, f"abbreviations: {name}: {exc}\n")
    if args.json:
        print(json.dumps(findings, ensure_ascii=False, indent=2))
    else:
        for item in findings:
            print(f"WARN  {item['file']}:{item['line']}: {item['label']}: {item['value']} — {item['detail']}")
        print(f"Abbreviations: {len(findings)} warning(s)")
    return 0
