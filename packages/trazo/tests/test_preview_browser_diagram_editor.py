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

    def test_inspector_align_while_editing_text_keeps_both_changes(self):
        self.chrome.call(
            "Page.navigate",
            {"url": self.url.replace("/manuscript.html", "/diagrams#example")},
            self.session,
        )
        self.wait_for("document.readyState === 'complete' && !!document.querySelector('#paper svg')")
        self.evaluate("DiagramEditor.startTextEdit('a', true)")
        self.wait_for("!!document.querySelector('.text-editor')")
        self.chrome.call("Input.insertText", {"text": "DRAFT"}, self.session)
        self.click('button[title="Centre"]')
        self.wait_for("!document.querySelector('.text-editor')")
        node = self.evaluate("DiagramEditor.state.diagram.nodes.find((n) => n.id === 'a')")
        self.assertEqual(node["text"], "DRAFT")
        self.assertEqual(node["align"], "center")
        self.assertEqual(self.evaluate("window.__trazoTestErrors"), [])

    def test_switching_diagrams_while_load_is_in_flight_does_not_leak_text_edit(self):
        second_path = diagram.diagram_path("example2")
        second_spec = json.loads(diagram.diagram_path("example").read_text(encoding="utf-8"))
        second_spec["nodes"][0]["text"] = "Second"
        second_path.write_text(json.dumps(second_spec, indent=2) + "\n", encoding="utf-8")
        self.addCleanup(second_path.unlink, missing_ok=True)

        self.chrome.call("Page.enable", None, self.session)
        self.chrome.call("Page.addScriptToEvaluateOnNewDocument", {"source": """
          (() => {
            const origFetch = window.fetch;
            window.__releaseSecond = null;
            window.__secondHeld = false;
            window.fetch = (url, options) => {
              if (String(url).includes('/__diagrams/file/example2')) {
                window.__secondHeld = true;
                return new Promise((resolve, reject) => {
                  window.__releaseSecond = () => origFetch(url, options).then(resolve, reject);
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
        self.wait_for("document.readyState === 'complete' && !!document.querySelector('#paper svg')")
        self.evaluate("location.hash = 'example2'")
        self.wait_for("window.__secondHeld === true")

        self.evaluate("DiagramEditor.startTextEdit('a', true)")
        if self.evaluate("!!document.querySelector('.text-editor')"):
            self.chrome.call("Input.insertText", {"text": "LEAKED"}, self.session)

        self.evaluate("window.__releaseSecond && window.__releaseSecond()")
        self.wait_for(
            "DiagramEditor.state.name === 'example2' && !!document.querySelector('#paper svg')"
        )
        self.click("#docname")
        self.wait_for("!document.querySelector('.text-editor')")

        node_second = self.evaluate("DiagramEditor.state.diagram.nodes.find((n) => n.id === 'a')")
        disk_first = json.loads(diagram.diagram_path("example").read_text(encoding="utf-8"))
        self.assertEqual(node_second["text"], "Second")
        self.assertEqual(disk_first["nodes"][0]["text"], "Input")
        self.assertEqual(self.evaluate("window.__trazoTestErrors"), [])


if __name__ == "__main__":
    unittest.main()
