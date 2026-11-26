import json
import unittest

from _preview_browser import PreviewBrowserTestCase

from trazo import diagram


class DiagramEditorBrowserTests(PreviewBrowserTestCase):
    def setUp(self):
        super().setUp()
        self.keep_files(diagram.diagram_path("example"), diagram.HISTORY_PATH)

    def point(self, selector: str, fx: float = 0.5, fy: float = 0.5) -> tuple[float, float]:
        x, y = self.evaluate(
            f"(() => {{ const r = document.querySelector({json.dumps(selector)}).getBoundingClientRect();"
            f" return [r.left + r.width * {fx}, r.top + r.height * {fy}]; }})()"
        )
        return float(x), float(y)

    def click(self, selector: str, fx: float = 0.5, fy: float = 0.5) -> None:
        x, y = self.point(selector, fx, fy)
        self.mouse("mousePressed", x, y)
        self.mouse("mouseReleased", x, y)

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
        x, y = self.point("#viewport")
        self.mouse("mouseMoved", x, y)
        self.mouse("mousePressed", x, y)
        self.mouse("mouseMoved", x + 40, y + 20)
        self.mouse("mouseReleased", x + 40, y + 20)
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
        self.click('button[data-tool="text"]')
        self.click("#viewport", 0.45, 0.45)
        self.wait_for("!!document.querySelector('.text-editor')")
        self.chrome.call("Input.insertText", {"text": "draft"}, self.session)
        self.click('button[data-action="undo"]')
        self.wait_for("!document.querySelector('.text-editor')")
        self.assertEqual(self.evaluate("window.__trazoTestErrors"), [])


if __name__ == "__main__":
    unittest.main()
