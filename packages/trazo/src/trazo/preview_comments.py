"""Review comments on the preview pages, stored in notes/review/comments.json.

The preview's paths and state stay in trazo.preview, and this code reads them from
there when it runs (preview.NAME), so patching them on trazo.preview reaches it.
"""

from .local_server import RequestError as CommentError
from .review_comments import CommentStore, text_field, thread_of
from .review_history import History
from .review_storage import read_list


class PreviewHistory(History):
    """Keep the preview's existing entries, which have no `kind` field."""

    def load(self):
        return [{**e, "kind": "comments"} for e in read_list(self.path)
                if isinstance(e, dict) and isinstance(e.get("changes"), list)
                and all(isinstance(ch, dict) and "after" in ch and "id" in ch
                        for ch in e["changes"])]

    def save(self, history):
        super().save([{k: v for k, v in e.items() if k != "kind"} for e in history])


class PreviewComments(CommentStore):
    def __init__(self):
        from . import preview
        super().__init__(preview.COMMENTS_PATH, "doc", PreviewHistory(preview.HISTORY_PATH), self.targets,
                         lambda c: c.get("para") or "")

    @staticmethod
    def targets(request):
        from . import preview
        fields = {key: text_field(request, key, required=key == "doc")
                  for key in ("doc", "section", "para", "quote")}
        if fields["doc"] not in preview.DOCS:
            raise CommentError(f"doc must be one of: {', '.join(preview.DOCS)}")
        if not fields["quote"].strip():  # a whitespace-only quote is no quote
            fields["quote"] = ""
        if "anchor" in request:
            fields["anchor"] = anchor_field(request["anchor"])
        if request.get("kind") == "underline":
            fields["kind"] = "underline"
        return fields

    def label(self, changes, label):
        item = changes[0][0]
        if all(i.get("kind") == "underline" for i, _ in changes):  # reading marks, not comments
            label = {"add comment": "underline", "delete comment": "remove underline"}.get(label, label)
            if len(changes) > 1:
                return label.replace("underline", f"{len(changes)} underlines")
        elif len(changes) > 1:
            return super().label(changes, label)
        return f"{label} ({self.where(item) or item['id']})"

    def payload_unlocked(self, name):
        # The viewer needs all documents' comments, but Undo follows its filter.
        payload = super().payload_unlocked(None)
        payload.update(self.next_undo(self.history.load(), name))
        return payload


def load_comments():
    return PreviewComments().load()


def comments_payload(doc=None):
    return PreviewComments().payload(doc)


def anchor_field(value):
    """Bounded textual context, separate from the original quoted text.

    `prefix` and `suffix` are the few characters around the quote in its
    paragraph; they pick the right one of several identical quotes.
    """
    keys = ("text", "before", "after", "object", "sectionId", "prefix", "suffix")
    if not isinstance(value, dict) or any(k not in keys for k in value):
        raise CommentError("anchor must contain textual context fields")
    if any(not isinstance(v, str) or len(v) > 20000 for v in value.values()):
        raise CommentError("anchor fields must be strings of at most 20000 characters")
    return dict(value)


def change_comments(request):
    return PreviewComments().change(request)


def comments_stamp():
    from . import preview
    return preview.COMMENTS_PATH.stat().st_mtime_ns if preview.COMMENTS_PATH.exists() else 0


def print_comments(show_all):
    for c in load_comments():
        if c.get("kind") == "underline":  # the author's reading marks
            continue
        if c.get("status") == "deleted" or (not show_all and c.get("status") != "open"):
            continue
        print(f"[{c['id']}] {c.get('status')} | {c.get('doc', '')} | "
              f"{c.get('section', '')} | {c.get('para', '')}")
        if c.get("quote"):
            print(f"    quote:   {c['quote']}")
        print(f"    comment: {c.get('comment', '')}")
        for r in thread_of(c):
            who = "agent" if r.get("by") == "agent" else "author"
            print(f"    {who}: {r.get('text', '')}")
