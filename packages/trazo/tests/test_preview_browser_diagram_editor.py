"""Diagram editor regressions in Chrome: pointer events before load and undo while editing text."""

import unittest

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)
from _preview_browser import PreviewBrowserTestCase

from trazo import diagram


class DiagramEditorBrowserTests(PreviewBrowserTestCase):
    def setUp(self):
        super().setUp()
        self.keep_files(diagram.diagram_path("example"), diagram.HISTORY_PATH)

    def test_pointer_move_before_geometry_loads_does_not_throw(self):
        self.chrome.call("Page.enable", None, self.session)
        self.chrome.call("Page.addScriptToEvaluateOnNewDocument", {"source": """
          (() => {
            const origFetch = window.fetch;
            window.__releasePayload = null;
            window.__payloadHeld = false;
            window.fetch = (url, options) => {
              if (String(url).includes('/__diagrams/payload/')) {
                window.__payloadHeld = true;
                return new Promise((resolve, reject) => {
                  window.__releasePayload = () => origFetch(url, options).then(resolve, reject);
                });
              }
              return origFetch(url, options);
            };
          })();"""}, self.session)
        self.chrome.call(
            "Page.navigate",
            {"url": self.url.replace("/manuscript.html", "/diagrams#example")},
            self.session,
        )
        self.wait_for("document.readyState === 'complete' && window.__payloadHeld === true")
        box = self.evaluate("""(() => {
          const r = document.querySelector('#viewport').getBoundingClientRect();
          return [r.left + r.width / 2, r.top + r.height / 2];
        })()""")
        self.mouse("mouseMoved", *box)
        self.mouse("mousePressed", *box)
        self.mouse("mouseMoved", box[0] + 40, box[1] + 20)
        self.mouse("mouseReleased", box[0] + 40, box[1] + 20)
        self.evaluate("window.__releasePayload && window.__releasePayload()")
        self.wait_for("!!document.querySelector('#paper svg')")
        self.assertEqual(self.evaluate("window.__trazoTestErrors"), [])

    def test_undo_while_editing_new_node_text_does_not_throw(self):
        self.chrome.call(
            "Page.navigate",
            {"url": self.url.replace("/manuscript.html", "/diagrams#example")},
            self.session,
        )
        self.wait_for("document.readyState === 'complete' && !!document.querySelector('#paper svg')")
        text_tool = self.evaluate("""(() => {
          const r = document.querySelector('button[data-tool="text"]').getBoundingClientRect();
          return [r.left + r.width / 2, r.top + r.height / 2];
        })()""")
        self.mouse("mousePressed", *text_tool)
        self.mouse("mouseReleased", *text_tool)

        canvas_pt = self.evaluate("""(() => {
          const r = document.querySelector('#viewport').getBoundingClientRect();
          return [r.left + r.width * 0.45, r.top + r.height * 0.45];
        })()""")
        self.mouse("mousePressed", *canvas_pt)
        self.mouse("mouseReleased", *canvas_pt)
        self.wait_for("!!document.querySelector('.text-edit, [contenteditable=\"true\"]')")
        self.chrome.call("Input.insertText", {"text": "draft"}, self.session)

        undo_btn = self.evaluate("""(() => {
          const r = document.querySelector('button[data-action="undo"]').getBoundingClientRect();
          return [r.left + r.width / 2, r.top + r.height / 2];
        })()""")
        self.mouse("mousePressed", *undo_btn)
        self.mouse("mouseReleased", *undo_btn)
        self.wait_for("!document.querySelector('.text-edit, [contenteditable=\"true\"]')")
        self.assertEqual(self.evaluate("window.__trazoTestErrors"), [])


if __name__ == "__main__":
    unittest.main()
