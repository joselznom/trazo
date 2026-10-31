"""Cross-scope review through the shared API and existing comment stores."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)

from trazo import diagram_editor, preview, review, table


class ReviewTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        for field in ("COMMENTS_PATH", "HISTORY_PATH"):
            stack.enter_context(patch.object(preview, field, root / ("document-" + field)))
        for kind, store in (("diagram", diagram_editor.COMMENTS), ("table", table.COMMENTS)):
            stack.enter_context(patch.object(store, "path", root / (kind + "-comments")))
            stack.enter_context(patch.object(store.history, "path", root / (kind + "-history")))

    def test_aggregate_reply_and_scoped_undo_keep_existing_stores(self):
        preview.change_comments({"op": "add", "doc": "supplementary", "comment": "Document note", "para": "Methods ¶1"})
        diagram_editor.change_comments({"op": "add", "diagram": "example", "comment": "Diagram note", "target": "a"})
        table.COMMENTS.change({"op": "add", "table": "results", "comment": "Table note"})
        items = review.payload()["comments"]
        self.assertEqual({c["scope"] for c in items}, {"document", "table", "diagram"})
        item = next(c for c in items if c["scope"] == "diagram")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(review.main(["resolve", item["key"], "Adjusted the diagram."]), 0)
        self.assertEqual(diagram_editor.COMMENTS.load()[0]["status"], "review")
        self.assertEqual(table.COMMENTS.load()[0]["status"], "open")
        self.assertEqual(preview.load_comments()[0]["para"], "Methods ¶1")
        review.change({"scope": "diagram", "op": "undo", "diagram": "example"})
        self.assertEqual(diagram_editor.COMMENTS.load()[0]["status"], "open")
        self.assertEqual(len(review.payload()["comments"]), 3)
        # Underlines reach the viewer but not the agent's list.
        preview.change_comments({"op": "add", "doc": "manuscript", "kind": "underline", "comment": "Underline"})
        self.assertEqual(len(review.payload()["comments"]), 4)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            review.main(["comments", "--all", "--json"])
        self.assertEqual(len(json.loads(out.getvalue())), 3)

    def test_listing_shows_the_selected_text_and_each_target(self):
        quote = 'The first "AI" claim.\nAcadem'
        preview.change_comments({"op": "add", "doc": "manuscript", "comment": "Shorten this",
                                 "para": "Introduction ¶1", "quote": quote})
        preview.change_comments({"op": "add", "doc": "manuscript", "comment": "Whole paragraph",
                                 "para": "Methods ¶2"})
        diagram_editor.change_comments({"op": "add", "diagram": "example", "comment": "Move it",
                                        "point": [12, 3.5]})
        table.COMMENTS.change({"op": "add", "table": "results", "comment": "Round",
                               "target": {"column": "accuracy", "row": {"model": "base"}}})
        diagram_editor.change_comments({"op": "add", "diagram": "example", "comment": "Whole diagram"})
        table.COMMENTS.change({"op": "add", "table": "results", "comment": "Whole table"})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            review.main(["comments"])
        lines = out.getvalue().splitlines()
        # The selection follows its own header verbatim, as a JSON string an agent can match.
        at = next(i for i, line in enumerate(lines) if line.endswith("| Introduction ¶1"))
        self.assertEqual(lines[at + 1], '    on: "The first \\"AI\\" claim.\\nAcadem"')
        self.assertEqual(json.loads(lines[at + 1].removeprefix("    on: ")), quote)
        self.assertEqual(lines[at + 2], "    Shorten this")
        # A comment without a selection has no "on" line.
        at = next(i for i, line in enumerate(lines) if line.endswith("| Methods ¶2"))
        self.assertEqual(lines[at + 1], "    Whole paragraph")
        self.assertEqual(sum(line.startswith("    on: ") for line in lines), 1)
        self.assertTrue(any(line.endswith("| example | point [12, 3.5]") for line in lines))
        self.assertTrue(any(line.endswith("| results | accuracy · base") for line in lines))
        # Like `trazo diagram|table comments`, a comment on the whole file shows "-".
        self.assertTrue(any(line.endswith("| example | -") for line in lines))
        self.assertTrue(any(line.endswith("| results | -") for line in lines))
