"""Building one document's preview page with Pandoc.

The preview's paths and state stay in trazo.preview, and this code reads them from
there when it runs (preview.NAME), so patching them on trazo.preview reaches it.
"""

import json
import os
import re

from . import pandoc_run, table
from .expand import FENCE, ValuesError, expand_files
from .numbering import metadata_file
from .pandoc_options import mathml_flag


def strip_html_comments(line, inside):
    """Remove <!-- ... --> text from a line; returns (line, still inside)."""
    out = ""
    while line:
        if inside:
            end = line.find("-->")
            if end < 0:
                return out, True
            line, inside = line[end + 3:], False
        else:
            start = line.find("<!--")
            if start < 0:
                return out + line, False
            out, line, inside = out + line[:start], line[start + 4:], True
    return out, inside


def doc_files(name):
    """[{file, title}] for each source file of a document, in build order.

    The title is the file's single level-1 heading ("" if it has none).
    """
    from . import preview
    files = []
    for path in preview.PROJECT.document_files(name):
        titles, fence, comment = [], None, False
        for line in path.read_text(encoding="utf-8").split("\n"):
            if fence is None:
                line, comment = strip_html_comments(line, comment)
            marker = FENCE.match(line)
            if fence is None and marker:
                fence = marker.group(1)
            elif fence is not None:
                if (marker and marker.group(1)[0] == fence[0]
                        and len(marker.group(1)) >= len(fence) and not marker.group(2).strip()):
                    fence = None
            elif re.match(r"#\s+\S", line):
                titles.append(re.sub(r"\s*\{[^}]*\}\s*$", "", line[1:]).strip())
        rel = path.relative_to(preview.ROOT).as_posix()
        if len(titles) > 1:
            raise ValueError(f"{rel} has {len(titles)} level-1 headings "
                             "(one per file is required by the preview)")
        files.append({"file": rel, "title": titles[0] if titles else ""})
    return files


def diagram_links():
    """The diagrams with an exported figures/external/<name>.png."""
    from .workspace import diagram_links as links
    return links()


def table_spec_labels():
    """Tables that "Edit table" opens in the table editor: tables/specs/<label>.json."""
    return table.all_labels()


def title_prefixes(project):
    """Document name -> title prefix ("Supplementary"), for the viewer's numbers."""
    return {name: doc.get("title_prefix", "") for name, doc in project.config["documents"].items()}


def render(name, out_dir, *, for_counts=False):
    """Build a document's preview page as <out_dir>/<name>.html; returns
    (error, pandoc warnings), error "" on success. The served pages are
    built in the preview folder; `trazo counts` uses one of its own."""
    from . import preview
    sections = preview.PROJECT.document_files(name)
    out_dir.mkdir(parents=True, exist_ok=True)
    error, warnings = "", []
    try:
        files = doc_files(name)
        text = expand_files(sections, mark=not for_counts, document=name)
    except (ValuesError, FileNotFoundError, RecursionError, ValueError) as exc:
        error = str(exc)
    else:
        source = out_dir / f"{name}.md"
        source.write_text(text, encoding="utf-8")
        after_body = out_dir / "after_body.html"
        after_body.write_text(
            f'<script>window.PREVIEW_DOC = "{name}";\n'
            f'window.PREVIEW_FILES = {json.dumps(files).replace("</", "<" + chr(92) + "/")};\n'
            f'window.PREVIEW_TITLES = {json.dumps(preview.PROJECT.titles).replace("</", "<" + chr(92) + "/")};\n'
            f'window.PREVIEW_NUMBER_PREFIXES = {json.dumps(preview.PROJECT.number_prefixes).replace("</", "<" + chr(92) + "/")};\n'
            f'window.PREVIEW_TITLE_PREFIXES = {json.dumps(title_prefixes(preview.PROJECT)).replace("</", "<" + chr(92) + "/")};\n'
            f'window.PREVIEW_DIAGRAMS = {json.dumps(diagram_links())};\n'
            f'window.PREVIEW_TABLE_SPECS = {json.dumps(table_spec_labels())};</script>\n'
            '<script src="/__assets/app/app.js"></script>\n'
            f'<script src="/{preview.ASSET_URL}preview-model.js"></script>\n'
            + "".join(f'<script src="/{preview.ASSET_URL}{script}"></script>\n' for script in preview.VIEWER_SCRIPTS),
            encoding="utf-8",
        )
        # Write next to the page and rename, so a request never gets half a page.
        page, partial = out_dir / f"{name}.html", out_dir / f"{name}.html.tmp"
        try:
            command = tuple(preview.PROJECT.command("pandoc"))
            result = pandoc_run.run(
                [str(source), *preview.PANDOC_FLAGS,
                 f"--metadata-file={metadata_file(name, out_dir)}", mathml_flag(command),
                 f"--include-after-body={after_body}", "-o", str(partial)],
                command=list(command), cwd=preview.ROOT, text=True,
            )
        except OSError as exc:  # pandoc missing or not executable
            error = f"could not run pandoc: {exc}"
        else:
            lines = [line for line in result.stderr.splitlines() if line.strip()]
            if result.returncode != 0:
                error = "\n".join(lines) or f"pandoc exited with {result.returncode}"
            else:
                warnings = lines
                if not for_counts:
                    from .abbreviations import annotate, document_text, entries
                    found = entries(document_text(preview.PROJECT, name))
                    if found:
                        partial.write_text(annotate(partial.read_text(encoding="utf-8"), found), encoding="utf-8")
                    content = partial.read_text(encoding="utf-8")
                    if re.search(r'class="[^"]*\bpk-form-only\b', content):
                        from .counts import ROWS, document_counts
                        try:
                            word = document_counts(name)
                        except (OSError, ValueError, RuntimeError) as exc:
                            error = str(exc)
                        else:
                            snapshot = json.dumps({key: word[key] for key, _, _ in ROWS})
                            content = content.replace('</body>', '<script type="application/json" id="pk-word-counts">' + snapshot + '</script></body>')
                            partial.write_text(content, encoding="utf-8")
                if not error:
                    os.replace(partial, page)
        partial.unlink(missing_ok=True)
    return error, warnings
