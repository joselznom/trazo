"""Review all workspace comments while retaining the existing scoped stores.

Public ids are scope:id (document, table or diagram). Existing CLI commands
and JSON stores retain their original ids, anchors, replies and undo scope.
"""

import argparse
import json
from urllib.parse import quote

from .dependencies import fingerprint
from .local_server import RequestError
from .review_comments import thread_of
from .review_storage import StoreError, review_lock


def stores():
    from . import diagram_editor, preview, table
    return {"document": preview.PreviewComments(), "table": table.COMMENTS,
            "diagram": diagram_editor.COMMENTS}


def revision():
    return fingerprint(store.path for store in stores().values())


def store_for(scope):
    if scope not in stores():
        raise RequestError("scope must be document, table or diagram")
    return stores()[scope]


def payload(scope=None, name=None):
    if scope:
        return store_for(scope).payload(name)
    with review_lock():
        comments = []
        for kind, store in stores().items():
            for item in store.payload_unlocked(None)["comments"]:
                target = str(item.get(store.scope) or "")
                href = (f"/{quote(target)}.html?at={quote(item.get('para') or '')}" if kind == "document"
                        else f"/{'tables' if kind == 'table' else 'diagrams'}#{quote(target)}")
                comments.append({**item, "scope": kind, "key": f"{kind}:{item['id']}",
                                 "name": target, "href": href})
        return {"comments": comments, "revision": revision()}


def change(request):
    if not isinstance(request, dict):
        raise RequestError("request body must be a JSON object")
    return store_for(request.get("scope")).change({k: v for k, v in request.items() if k != "scope"})


def get_route(path, query):
    return (lambda: payload(query.get("scope", [None])[0], query.get("name", [None])[0])) if path == "/__review" else None


def post_route(path, body):
    return (lambda: change(body())) if path == "/__review" else None


def main(argv=None):
    parser = argparse.ArgumentParser(prog="trazo review", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    comments = sub.add_parser("comments")
    comments.add_argument("--all", action="store_true")
    comments.add_argument("--json", action="store_true")
    reply = sub.add_parser("resolve")
    reply.add_argument("id", help="scope:id printed by review comments")
    reply.add_argument("reply")
    undo = sub.add_parser("undo")
    undo.add_argument("scope", choices=("document", "table", "diagram"))
    undo.add_argument("name", nargs="?")
    args = parser.parse_args(argv)
    try:
        if args.command == "comments":
            # Underlines are the author's reading marks, not comments.
            items = [c for c in payload()["comments"] if c.get("kind") != "underline"
                     and (args.all or c.get("status") == "open")]
            if args.json:
                print(json.dumps(items, indent=2, ensure_ascii=False))
            else:
                from . import diagram, table
                # The places `trazo diagram|table comments` print, not the raw target.
                places = {"table": table.COMMENTS.where, "diagram": diagram.comment_place}
                for c in items:
                    place = c.get("para") if c["scope"] == "document" else (places[c["scope"]](c) or "-")
                    print(f"[{c['key']}] {c.get('status')} | {c['name']} | {place or ''}")
                    if c.get("quote"):
                        # JSON quoting keeps the whole selection on one line and shows
                        # where it starts and ends, even mid-word.
                        print(f"    on: {json.dumps(c['quote'], ensure_ascii=False)}")
                    print(f"    {c.get('comment', '')}")
                    for r in thread_of(c):
                        print(f"    {r.get('by')}: {r.get('text')}")
        elif args.command == "resolve":
            scope, sep, identifier = args.id.partition(":")
            if not sep:
                raise RequestError("use scope:id from trazo review comments")
            change({"scope": scope, "op": "reply", "id": identifier, "by": "agent", "text": args.reply})
            print(f"[{args.id}] review: reply added")
        else:
            store = store_for(args.scope)
            result = change({"scope": args.scope, "op": "undo", **({store.scope: args.name} if args.name else {})})
            print("undone; next undo: " + (result["undo"] or "nothing"))
    except (RequestError, StoreError, OSError) as exc:
        parser.exit(1, f"review: {exc}\n")
    return 0
