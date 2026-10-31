"""Review comments on diagrams and tables, shared by their editors and CLIs.

One JSON list per tool (notes/review/diagram_comments.json,
table_comments.json). A comment is {id, <scope>: name, target?, comment,
status, replies, created}, where <scope> is "diagram" or "table". States
follow the preview: open (the author wrote it), review (the agent replied
with `resolve`), done (only the author marks it; `resolved` holds the time);
delete moves a comment to the trash (status "deleted", the old status in
deleted_from) and restore brings it back.

Every change adds a "comments" entry {id, time, kind, <scope>, label,
changes: [{id, before, after}]} to the tool's History, so the tool's `undo`
reverts it and refuses (dropping the entry) when the comment changed since.
An entry touching several scope names (a delete with ids) has <scope> None
and belongs to each of them. Payloads name the next undo (`undo`, its
`undo_id`).

Ops of `change` (the editors' POST bodies): add {<scope>, comment,
...target fields}, update {id, status?, comment?}, reply {id, text, by?,
status?}, delete {id} or {ids} (several in one undo step), restore {id} and
undo {<scope>?, expect?} (the last comment change of that scope; 409 when
the comment changed since, or when `expect` is not that entry's undo_id).
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .local_server import Conflict, NotFound, RequestError
from .review_history import History, now
from .review_storage import read_list, review_lock, write_atomic

STATUSES = ("open", "review", "done")
AUTHORS = ("author", "agent")
OPS = ("add", "update", "reply", "delete", "restore", "undo")

Comment = dict[str, Any]


def thread_of(comment: dict[str, Any]) -> list[dict[str, Any]]:
    """Replies of a review comment; the legacy single `reply` string comes first."""
    replies = list(comment.get("replies") or [])
    if comment.get("reply"):
        replies.insert(0, {"by": "agent", "text": comment["reply"],
                           "time": comment.get("created", "")})
    return replies


def copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def upgraded(comment: Comment) -> Comment:
    """The comment with its legacy single `reply` folded into `replies`."""
    item = {k: v for k, v in comment.items() if k != "reply"}
    item["replies"] = thread_of(comment)
    return item


def text_field(request: dict[str, Any], key: str, required: bool = False) -> str:
    value = request.get(key, "")
    if not isinstance(value, str):
        raise RequestError(f"{key} must be a string")
    if required and not value.strip():
        raise RequestError(f"{key} is required")
    return value


def ids_field(request: dict[str, Any]) -> list[str]:
    """The comment ids of a request: {id} or, for delete, {ids: [...]}."""
    if "ids" not in request:
        return [request.get("id")]
    ids = request["ids"]
    if request.get("op") != "delete":
        raise RequestError("only delete accepts ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids) \
            or len(set(ids)) != len(ids):
        raise RequestError("ids must be a non-empty list of distinct comment ids")
    return ids


def set_status(item: Comment, status: str) -> None:
    """Change a comment's state, recording when it was resolved."""
    if status == "done" and item.get("status") != "done":
        item["resolved"] = time.strftime("%Y-%m-%d %H:%M:%S")  # seconds keep the order
    elif status != "done":
        item.pop("resolved", None)
    item["status"] = status


def status_field(request: dict[str, Any]) -> str:
    value = request.get("status")
    if value not in STATUSES:
        raise RequestError(f"status must be one of: {', '.join(STATUSES)}")
    return value


class CommentStore:
    """The comments of one tool.

    `targets(request)` checks an add request and returns the item's scope
    and target fields (e.g. {"diagram": name, "target": id}); `where(item)`
    names the target in history labels and listings.
    """

    def __init__(self, path: Path, scope: str, history: History,
                 targets: Callable[[dict[str, Any]], dict[str, Any]],
                 where: Callable[[Comment], str] = lambda c: str(c.get("target") or "")) -> None:
        self.path = path
        self.scope = scope
        self.history = history
        self.targets = targets
        self.where = where

    def load(self) -> list[Comment]:
        return [c for c in read_list(self.path) if isinstance(c, dict) and "id" in c]

    def save(self, comments: list[Comment]) -> None:
        write_atomic(self.path, (json.dumps(comments, ensure_ascii=False, indent=2) + "\n")
                     .encode("utf-8"))

    def history_of(self, history: list[dict[str, Any]], name: str | None) -> list[int]:
        """Indexes of the comment entries of one scope (all with None); an
        entry over several names belongs to each of them."""
        def touches(e: dict[str, Any]) -> bool:
            if e.get(self.scope) is not None:
                return e.get(self.scope) == name
            return any((ch.get("after") or ch.get("before") or {}).get(self.scope) == name
                       for ch in e.get("changes", []))
        return [i for i, e in enumerate(history)
                if e.get("kind") == "comments" and (name is None or touches(e))]

    def next_undo(self, history: list[dict[str, Any]], name: str | None) -> dict[str, Any]:
        """The label and id of the entry that `undo` of this scope reverts."""
        mine = self.history_of(history, name)
        entry = history[mine[-1]] if mine else {}
        return {"undo": entry.get("label"), "undo_id": entry.get("id")}

    def payload_unlocked(self, name: str | None) -> dict[str, Any]:
        history = self.history.load()
        return {"comments": [upgraded(c) for c in self.load()
                             if name is None or c.get(self.scope) == name],
                **self.next_undo(history, name)}

    def payload(self, name: str | None) -> dict[str, Any]:
        with review_lock():
            return self.payload_unlocked(name)

    def revert(self, entry: dict[str, Any], error: type[Exception] = Conflict) -> None:
        """Undo a "comments" history entry; raise `error` if a comment changed since."""
        comments = self.load()
        current = {c["id"]: c for c in comments}
        if any(current.get(ch["id"]) != ch["after"] for ch in entry["changes"]):
            raise error(f"undo discarded: the comment changed since "
                        f"({entry['label']}); that entry was removed")
        for change in entry["changes"]:
            at = next(i for i, c in enumerate(comments) if c["id"] == change["id"])
            if change["before"] is None:
                comments.pop(at)
            else:
                comments[at] = change["before"]
        self.save(comments)

    def new_comment(self, request: dict[str, Any]) -> Comment:
        item: Comment = {"id": uuid.uuid4().hex[:8]}
        fields = self.targets(request)
        item[self.scope] = fields.pop(self.scope)
        item.update(fields)
        item.update(comment=text_field(request, "comment", required=True).strip(),
                    status="open", replies=[], created=time.strftime("%Y-%m-%d %H:%M"))
        return item

    def change(self, request: Any) -> dict[str, Any]:
        """Apply one comment change (see the module docstring) and record it."""
        if not isinstance(request, dict):
            raise RequestError("request body must be a JSON object")
        op = request.get("op")
        if op not in OPS:
            raise RequestError(f"unknown op: {op!r}")
        scope = request.get(self.scope) if isinstance(request.get(self.scope), str) else None
        with review_lock():
            comments = self.load()
            history = self.history.load()
            if op == "undo":
                mine = self.history_of(history, scope)
                if not mine:
                    raise RequestError("nothing to undo")
                if "expect" in request and history[mine[-1]].get("id") != request["expect"]:
                    raise Conflict("not undone: the last comment change is another one")
                entry = history.pop(mine[-1])
                try:
                    self.revert(entry)
                finally:
                    self.history.save(history)
                return self.payload_unlocked(scope)
            if op == "add":
                item = self.new_comment(request)
                comments.append(item)
                changes, label = [(item, None)], "add comment"
            else:
                changes = []
                for comment_id in ids_field(request):
                    found = [i for i, c in enumerate(comments) if c.get("id") == comment_id]
                    if not found:
                        raise NotFound(f"no comment with id {comment_id}")
                    before = copy(comments[found[0]])
                    item = comments[found[0]] = upgraded(comments[found[0]])
                    label = self.apply(item, op, request)
                    changes.append((item, before))
            history.append(self.entry(changes, label))
            self.save(comments)
            self.history.save(history)
            return self.payload_unlocked(scope)

    def entry(self, changes: list[tuple[Comment, Comment | None]], label: str) -> dict[str, Any]:
        """The history entry of one change, which may touch several comments."""
        names = {item.get(self.scope) for item, _ in changes}
        return {"id": uuid.uuid4().hex[:12], "time": now(), "kind": "comments",
                self.scope: names.pop() if len(names) == 1 else None,
                "label": self.label(changes, label),
                "changes": [{"id": item["id"], "before": before, "after": copy(item)}
                            for item, before in changes]}

    def label(self, changes: list[tuple[Comment, Comment | None]], label: str) -> str:
        item = changes[0][0]
        if len(changes) > 1:
            return f"{label.replace(' comment', '')} {len(changes)} comments"
        return f"{label} ({item.get(self.scope)}: {self.where(item) or item['id']})"

    @staticmethod
    def apply(item: Comment, op: str, request: dict[str, Any]) -> str:
        """Change one comment in place; returns the history label."""
        deleted = item.get("status") == "deleted"
        if op == "restore":
            if not deleted:
                raise RequestError("comment is not deleted")
            item["status"] = item.pop("deleted_from", "open")
            return "restore comment"
        if deleted:
            raise RequestError("comment is deleted; restore it first")
        if op == "delete":
            item["deleted_from"] = item.get("status", "open")
            item["status"] = "deleted"
            return "delete comment"
        if op == "update":
            fields = [f for f in ("status", "comment") if f in request]
            if not fields:
                raise RequestError("update needs status or comment")
            if "status" in fields:
                set_status(item, status_field(request))
            if "comment" in fields:
                item["comment"] = text_field(request, "comment", required=True).strip()
            return "mark " + item["status"] if fields == ["status"] else "edit comment"
        by = request.get("by", "author")  # reply
        if by not in AUTHORS:
            raise RequestError(f"by must be one of: {', '.join(AUTHORS)}")
        text = text_field(request, "text", required=True)
        if "status" in request:
            new_status = status_field(request)
        elif by == "agent":
            new_status = "review"
        else:
            new_status = "open" if item.get("status") in ("review", "done") \
                else item.get("status", "open")
        item["replies"].append({"by": by, "text": text.strip(),
                                "time": time.strftime("%Y-%m-%d %H:%M")})
        set_status(item, new_status)
        return "reply" if by == "agent" else "follow-up"

    def resolve(self, comment_id: str, reply: str, error: type[Exception]) -> None:
        """The agent's reply from the CLI: add it and set the comment to review."""
        if not reply.strip():
            raise error("the reply text is required")
        try:
            self.change({"op": "reply", "id": comment_id, "by": "agent", "text": reply})
        except NotFound as exc:
            raise error(f"no {self.scope} comment with id {comment_id}") from exc
        except RequestError as exc:
            message = ("comment is deleted; restore it in the editor first"
                       if str(exc) == "comment is deleted; restore it first" else str(exc))
            raise error(message) from exc

    def print(self, name: str | None, show_all: bool,
              where: Callable[[Comment], str] | None = None) -> None:
        """List open comments (every state with show_all), with their threads."""
        shown = 0
        for c in self.load():
            if c.get("status") == "deleted" or (not show_all and c.get("status") != "open"):
                continue
            if name and c.get(self.scope) != name:
                continue
            shown += 1
            target = (where or self.where)(c) or "-"
            print(f"[{c['id']}] {c.get('status')} | {c.get(self.scope, '')} | {target}")
            print(f"    comment: {c.get('comment', '')}")
            for r in thread_of(c):
                print(f"    {'agent' if r.get('by') == 'agent' else 'author'}: {r.get('text', '')}")
        if not shown:
            print(f"no open {self.scope} comments" if not show_all else f"no {self.scope} comments")
