import json
import unittest

from _preview_browser import PreviewBrowserTestCase

from trazo import diagram, diagram_editor


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

    def prepare_second_diagram(self):
        self.addCleanup(diagram_editor.shutdown)
        second_path = diagram.diagram_path("second")
        self.keep_files(second_path, *diagram.export_paths("example"), *diagram.export_paths("second"))
        second = {
            "schema_version": 1,
            "canvas": {"width_mm": 120, "height_mm": 60, "grid": 1},
            "panels": [],
            "nodes": [{"id": "only_second", "kind": "box", "x": 80, "y": 35, "w": 30, "h": 15, "text": "Second"}],
            "edges": [],
            "raw": [],
        }
        second_path.write_text(json.dumps(second), encoding="utf-8")

    def open_diagram(self, name: str) -> None:
        self.chrome.call("Page.navigate", {"url": self.url.replace("/manuscript.html", f"/diagrams#{name}")}, self.session)
        self.wait_for(f"window.DiagramEditor?.state?.name === {json.dumps(name)} && !!DiagramEditor.state.geo")

    def node_client_point(self, node_id: str) -> tuple[float, float]:
        x, y = self.evaluate(f"""(() => {{
          const box = DiagramEditor.state.boxes[{json.dumps(node_id)}].box;
          const rect = document.querySelector('#viewport').getBoundingClientRect();
          const v = DiagramEditor.state.view;
          return [rect.left + v.x + (box[0] + box[2] / 2) * v.zoom, rect.top + v.y + (box[1] + box[3] / 2) * v.zoom];
        }})()""")
        return float(x), float(y)

    def hold_fetch(self, needle: str, flag: str, release: str) -> None:
        self.evaluate(f"""(() => {{
          const orig = window.fetch;
          window[{json.dumps(flag)}] = false;
          let go;
          window[{json.dumps(release)}] = () => {{ if (go) go(); }};
          window.fetch = (url, options) => {{
            if (String(url).includes({json.dumps(needle)})) {{
              window[{json.dumps(flag)}] = true;
              return new Promise((resolve) => {{ go = () => resolve(orig(url, options)); }});
            }}
            return orig(url, options);
          }};
        }})()""")

    def js_errors(self):
        return self.evaluate("window.__trazoTestErrors.filter((e) => !e.resource)")

    def assert_stale_hits_blocked_while_loading_second(self, old_point: tuple[float, float], first_node: str) -> None:
        self.wait_for("window.payloadHeld && DiagramEditor.state.name === 'second' && !!DiagramEditor.state.diagram")
        self.assertIsNone(self.evaluate("DiagramEditor.state.geo"))
        self.assertEqual(self.evaluate("DiagramEditor.state.boxes"), {})
        self.mouse("mouseMoved", *old_point)
        self.mouse("mousePressed", *old_point)
        self.mouse("mouseReleased", *old_point)
        self.assertEqual(self.js_errors(), [])
        self.assertIsNone(self.evaluate("DiagramEditor.state.hover"))
        self.assertEqual(self.evaluate("DiagramEditor.state.sel"), [])
        self.evaluate("window.releasePayload()")
        self.wait_for("!!DiagramEditor.state.geo && !!DiagramEditor.state.boxes.only_second")
        self.assertNotIn(first_node, self.evaluate("Object.keys(DiagramEditor.state.boxes)"))
        self.assertEqual(self.js_errors(), [])

    def test_returning_to_landing_clears_geo_and_boxes_and_blocks_stale_hits_on_next_open(self):
        self.prepare_second_diagram()
        self.open_diagram("example")
        first_node = self.evaluate("DiagramEditor.state.diagram.nodes[0].id")
        self.assertTrue(self.evaluate("!!DiagramEditor.state.geo && Object.keys(DiagramEditor.state.boxes).length > 0"))
        old_point = self.node_client_point(first_node)

        self.evaluate("document.querySelector('#home').click()")
        self.wait_for("document.body.classList.contains('is-landing') && !document.querySelector('#landing').hidden")
        self.assertIsNone(self.evaluate("DiagramEditor.state.geo"))
        self.assertEqual(self.evaluate("DiagramEditor.state.boxes"), {})
        self.assertIsNone(self.evaluate("DiagramEditor.state.hover"))
        self.assertEqual(self.evaluate("DiagramEditor.state.sel"), [])

        self.hold_fetch("/__diagrams/payload/second", "payloadHeld", "releasePayload")
        self.evaluate("document.querySelector('#landing a.dcard[href=\"#second\"]').click()")
        self.assert_stale_hits_blocked_while_loading_second(old_point, first_node)

    def test_switching_diagrams_directly_clears_stale_geo_while_loading(self):
        self.prepare_second_diagram()
        self.open_diagram("example")
        first_node = self.evaluate("DiagramEditor.state.diagram.nodes[0].id")
        before_first = diagram.diagram_path("example").read_text(encoding="utf-8")
        old_point = self.node_client_point(first_node)

        self.hold_fetch("/__diagrams/payload/second", "payloadHeld", "releasePayload")
        self.hold_fetch("/__diagrams/file/second", "fileHeld", "releaseFile")
        self.evaluate("location.hash = 'second'")
        self.wait_for("window.fileHeld")
        self.assertIsNone(self.evaluate("DiagramEditor.state.diagram"))
        self.assertIsNone(self.evaluate("DiagramEditor.state.geo"))
        self.assertEqual(self.evaluate("DiagramEditor.state.boxes"), {})
        self.assertEqual(self.evaluate("document.querySelectorAll('#inspector button, #layers button').length"), 0)
        self.mouse("mouseMoved", *old_point)
        self.mouse("mousePressed", *old_point)
        self.mouse("mouseMoved", old_point[0] + 25, old_point[1] + 15)
        self.mouse("mouseReleased", old_point[0] + 25, old_point[1] + 15)
        self.assertEqual(self.js_errors(), [])
        self.assertIsNone(self.evaluate("DiagramEditor.state.hover"))
        self.assertEqual(self.evaluate("DiagramEditor.state.sel"), [])
        self.assertEqual(diagram.diagram_path("example").read_text(encoding="utf-8"), before_first)

        self.evaluate("window.releaseFile()")
        self.assert_stale_hits_blocked_while_loading_second(old_point, first_node)
        self.assertEqual(diagram.diagram_path("example").read_text(encoding="utf-8"), before_first)


if __name__ == "__main__":
    unittest.main()
