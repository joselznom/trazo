"""Edit caption for figures and tables, saved to their original source.

The preview's paths and state stay in trazo.preview, and this code reads them from
there when it runs (preview.NAME), so patching them on trazo.preview reaches it.
"""

import re

from . import preview_edits, preview_tables, table, table_editor
from .local_server import Conflict
from .local_server import RequestError as CommentError
from .preview_sources import change_source, get_source, read_source, source_path
from .review_comments import text_field


def caption_source(doc, identifier, kind):
    from . import preview
    prefix = "fig:" if kind == "figure" else "tbl:"
    if doc not in preview.DOCS or not isinstance(identifier, str) or not identifier.startswith(prefix):
        raise CommentError(f"choose a {kind} in a document")
    captions = preview_edits.image_captions if kind == "figure" else preview_edits.table_captions
    found = []
    for candidate in preview.PROJECT.document_files(doc):
        path, file = source_path(candidate.relative_to(preview.ROOT).as_posix())
        source = read_source(path)[1]
        for start, end in captions(source, identifier):
            found.append((file, source, start, end))
    if len(found) != 1:
        raise CommentError(f"{kind} caption must appear once in the original Markdown")
    return found[0]


def get_figure_text(doc, identifier):
    file, text, start, end = caption_source(doc, identifier, "figure")
    state = get_source(file)
    # The file may have changed between locating and reading it.
    spans = list(preview_edits.image_captions(state["text"], identifier))
    if len(spans) != 1:
        raise Conflict("the figure changed on disk; reload its caption")
    start, end = spans[0]
    return {**state, "doc": doc, "id": identifier, "caption": state["text"][start:end]}


def change_figure_text(request):
    if not isinstance(request, dict) or request.get("op", "save") not in ("save", "undo"):
        raise CommentError("expected a caption save or undo request")
    doc, identifier = request.get("doc"), request.get("id")
    state = get_figure_text(doc, identifier)
    if request.get("op") == "undo":
        change_source({"op": "undo", "file": state["file"]})
    else:
        caption = text_field(request, "caption")
        if "\n\n" in caption or "\r" in caption:
            raise CommentError("the caption must be one Markdown paragraph")
        # Validate balanced brackets before inserting into ![...].
        if preview_edits.closing("[" + caption + "]", 0, "[", "]") != len(caption) + 1:
            raise CommentError("escape unmatched brackets in the caption")
        start, end = next(preview_edits.image_captions(state["text"], identifier))
        text = state["text"][:start] + caption + state["text"][end:]
        change_source({"file": state["file"], "text": text,
                       "base_sha256": request.get("base_sha256")})
    return get_figure_text(doc, identifier)


def get_table_caption(doc, identifier):
    """One caption editor payload, backed by the table's original source."""
    from . import preview
    if doc not in preview.DOCS or not isinstance(identifier, str) or not identifier.startswith("tbl:"):
        raise CommentError("choose a table in a document")
    label = identifier[4:]
    # Markdown IDs may contain dots and other characters that generated table
    # labels do not support. Gate path construction, then fall back to source.
    if table.LABEL_RE.fullmatch(label) and table.spec_path(label).is_file():
        state = table_editor.get_table({"label": [label]})
        values = state["data"]["values"]
        state = {"kind": "spec", "file": state["file"], "sha256": state["sha256"],
            "can_undo": bool(state["undo"]), "fields": [
                {"key": key, "segments": preview_tables.segments(str(state["spec"].get(key, "")), values, key),
                 "values": [preview_tables.segments("{" + name + "}", values, key)[0] for name in sorted(values)]}
                for key in (*preview_tables.CAPTION_KEYS, "note")]}
    else:
        file, _, _, _ = caption_source(doc, identifier, "table")
        state = get_source(file)
        spans = list(preview_edits.table_captions(state["text"], identifier))
        if len(spans) != 1:
            raise Conflict("the table changed on disk; reload its caption")
        start, end = spans[0]
        located = preview_edits.table_note(state["text"], identifier)
        note = located[0] if located else None
        state = {"kind": "markdown", "file": file, "sha256": state["sha256"],
            "can_undo": state["can_undo"], "fields": [
                {"key": "caption", "segments": [{"text": state["text"][start:end]}], "values": []},
                {"key": "note", "segments": [{"text": state["text"][slice(*note)] if note else ""}], "values": []}]}
    return {**state, "doc": doc, "id": identifier}


def with_table_note(text, identifier, note):
    """Markdown with the table's ::: table-note set to `note` (added after the
    table and caption when missing, removed when empty)."""
    if not isinstance(note, str) or "\r" in note or re.search(r"(?m)^ {0,3}:{3,}", note):
        raise CommentError("the note must be Markdown paragraphs without ::: fences")
    located = preview_edits.table_note(text, identifier)
    if located is None:
        raise Conflict("the table changed on disk; reload its caption")
    span, end = located
    body = "\n\n".join(" ".join(p.split()) for p in re.split(r"\n\s*\n", note) if p.strip())
    if span:
        if body:
            return text[:span[0]] + body + text[span[1]:]
        # Remove the whole block: its blank line, fences and text.
        close = re.compile(r"(?m)^ {0,3}:{3,}[ \t]*$").search(text, span[1])
        return text[:end] + text[close.end():]
    if not body:
        return text
    return text[:end] + f"\n\n{table.NOTE_OPEN}\n{body}\n:::" + text[end:]


def change_table_caption(request):
    """Save or undo through each source format's existing validation/history."""
    if not isinstance(request, dict) or request.get("op", "save") not in ("save", "undo"):
        raise CommentError("expected a caption save or undo request")
    doc, identifier = request.get("doc"), request.get("id")
    state = get_table_caption(doc, identifier)
    label, undo = identifier[4:], request.get("op") == "undo"
    changes = request.get("changes")
    if not undo and (not isinstance(changes, dict) or not changes or
                    set(changes) - {field["key"] for field in state["fields"]}):
        raise CommentError("changes must contain only this table's caption fields")
    if state["kind"] == "spec":
        if undo:
            table_editor.undo_table({"label": label})
        else:
            table_editor.save_table({"label": label, "base_sha256": request.get("base_sha256"),
                "ops": [{"op": "set", "path": key, "value": None if key == "note" and not str(value).strip() else value}
                        for key, value in changes.items()]})
    elif undo:
        change_source({"op": "undo", "file": state["file"]})
    else:
        source = get_source(state["file"])
        text = source["text"]
        if "caption" in changes:
            caption = changes["caption"]
            if not isinstance(caption, str) or not caption.strip() or "\n\n" in caption or "\r" in caption:
                raise CommentError("the caption must be one non-empty Markdown paragraph")
            spans = list(preview_edits.table_captions(text, identifier))
            if len(spans) != 1:
                raise Conflict("the table changed on disk; reload its caption")
            start, end = spans[0]
            text = text[:start] + caption + text[end:]
            if list(preview_edits.table_captions(text, identifier)) != [(start, start + len(caption))]:
                raise CommentError("the caption must preserve its Markdown table identifier")
        if "note" in changes:
            text = with_table_note(text, identifier, changes["note"])
        change_source({"file": state["file"], "text": text, "base_sha256": request.get("base_sha256")})
    return get_table_caption(doc, identifier)
