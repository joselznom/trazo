"""The diagram editor's routes, directly and through the preview's real HTTP handler."""

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import _support  # sets TRAZO_ROOT first

from trazo import diagram_editor, preview
from trazo.local_server import LocalServer, NotFound
from trazo.project import ASSETS_DIR

GET_PATHS = ("tokens", "values", "list", "status", "embeds", "export", "comments",
             "file/example", "payload/example", "check/example")
POST_PATHS = ("file/example", "export/example", "cancel-export", "comments")


class RouteTests(unittest.TestCase):
    def test_get_routes_exist_for_every_endpoint(self):
        for name in GET_PATHS:
            with self.subTest(name=name):
                self.assertTrue(callable(diagram_editor.get_route(f"/__diagrams/{name}", {})))

    def test_other_paths_are_not_diagram_routes(self):
        for path in ("/__status", "/__comments", "/__values", "/__tables", "/__diagrams",
                     "/diagrams", "/", "/figures/diagrams/example.json"):
            with self.subTest(path=path):
                self.assertIsNone(diagram_editor.get_route(path, {}))
                self.assertIsNone(diagram_editor.post_route(path, self.fail))

    def test_unknown_paths_inside_the_prefix_are_not_found(self):
        for path in ("/__diagrams/", "/__diagrams/nope", "/__diagrams/file", "/__diagrams/list/x",
                     "/__diagrams/export/example", "/__diagrams/cancel-export"):
            with self.subTest(get=path), self.assertRaises(NotFound):
                diagram_editor.get_route(path, {})()
        for path in ("/__diagrams/", "/__diagrams/nope", "/__diagrams/file", "/__diagrams/list",
                     "/__diagrams/tokens", "/__diagrams/check/example"):
            with self.subTest(post=path), self.assertRaises(NotFound):
                diagram_editor.post_route(path, self.fail)()

    def test_post_routes_read_the_body_only_when_run(self):
        reads = []
        body = lambda: reads.append(1) or {}  # noqa: E731
        for name in POST_PATHS:
            with self.subTest(name=name):
                self.assertTrue(callable(diagram_editor.post_route(f"/__diagrams/{name}", body)))
        self.assertEqual(reads, [])

    def test_names_are_percent_decoded_and_validated(self):
        route = diagram_editor.get_route("/__diagrams/file/exam%70le", {})
        self.assertEqual(route()["name"], "example")
        with self.assertRaises(diagram_editor.RequestError):
            diagram_editor.get_route("/__diagrams/file/..%2Fexample", {})()

    def test_reads_return_data(self):
        get = lambda path, query=None: diagram_editor.get_route(f"/__diagrams/{path}", query or {})()  # noqa: E731
        self.assertEqual([d["name"] for d in get("list")], ["example"])
        self.assertIn("example", get("status")["diagrams"])
        self.assertEqual(get("status", {"name": ["example"]})["assets"].keys(), set())
        self.assertEqual(get("export")["state"], "idle")
        self.assertIn("comments", get("comments"))
        self.assertIsNotNone(get("payload/example")["payload"])


class ServedAssetTests(unittest.TestCase):
    def translate(self, url):
        handler = preview.Handler.__new__(preview.Handler)
        return Path(handler.translate_path(url))

    def test_every_editor_file_is_served_by_the_preview(self):
        files = sorted(p.name for p in (ASSETS_DIR / "diagram").iterdir() if p.is_file())
        self.assertIn("editor.js", files)
        for name in files:
            with self.subTest(name=name):
                path = self.translate(f"/__assets/diagram/{name}")
                self.assertEqual(path, ASSETS_DIR / "diagram" / name)
                self.assertTrue(path.is_file())

    def test_traversal_and_unknown_files_are_missing(self):
        for bad in ("/__assets/diagram/../preview/preview.js", "/__assets/diagram/%2e%2e/x.js",
                    "/__assets/diagram/nope.js", "/__assets/diagram/sub/x.js",
                    "/__assets/diagram/.hidden", "/__assets/diagram/..%2Frenderer.js"):
            with self.subTest(bad=bad):
                self.assertFalse(self.translate(bad).is_file())


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = LocalServer(("127.0.0.1", 0), preview.Handler)
        cls.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def call(self, path, body=None, content_type="application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": f"127.0.0.1:{self.port}"}
        if body is not None:
            headers["Content-Type"] = content_type
            conn.request("POST", path, json.dumps(body), headers)
        else:
            conn.request("GET", path, headers=headers)
        res = conn.getresponse()
        data = res.read()
        conn.close()
        try:
            return res.status, json.loads(data)
        except ValueError:
            return res.status, data.decode("utf-8")

    def test_page_assets_and_lists(self):
        for path in ("/diagrams", "/diagrams/"):
            status, page = self.call(path)
            self.assertEqual(status, 200)
            self.assertIn("/__assets/diagram/editor.js", page)
            self.assertIn('id="back"', page)
        status, text = self.call("/__assets/diagram/editor.js")
        self.assertEqual(status, 200)
        self.assertIn('const API = "/__diagrams/"', text)
        status, listing = self.call("/__diagrams/list")
        self.assertEqual((status, [d["name"] for d in listing]), (200, ["example"]))
        status, state = self.call("/__diagrams/file/example")
        self.assertEqual((status, state["name"]), (200, "example"))
        status, data = self.call("/__diagrams/status?name=example")
        self.assertEqual(status, 200)
        self.assertEqual(data["export"]["state"], "idle")

    def test_preview_routes_still_work(self):
        self.assertEqual(self.call("/__tables")[0], 200)
        self.assertEqual(self.call("/__values")[0], 200)
        self.assertEqual(self.call("/__status")[0], 200)

    def test_errors_are_json(self):
        self.assertEqual(self.call("/__diagrams/nope"), (404, {"error": "not found"}))
        self.assertEqual(self.call("/__diagrams/file/missing")[0], 404)
        self.assertEqual(self.call("/__diagrams/nope", {})[0], 404)
        status, data = self.call("/__diagrams/cancel-export", {})
        self.assertEqual((status, data["error"]), (409, "no export is running"))

    def test_posts_must_be_json(self):
        self.assertEqual(self.call("/__diagrams/cancel-export", {}, "text/plain")[0], 403)

    def test_invalid_element_references_are_validation_errors_and_leave_source_unchanged(self):
        original = diagram_editor.get_diagram("example")
        for field, value in (("id", []), ("kind", []), ("panel", []), ("children", [{}])):
            with self.subTest(field=field):
                data = json.loads(original["text"])
                data["nodes"][0][field] = value
                if field == "children":
                    data["nodes"][0]["kind"] = "group"
                status, result = self.call("/__diagrams/file/example", {
                    "diagram": data, "base_sha256": original["sha256"]})
                self.assertEqual(status, 422, result)
                self.assertTrue(result["issues"])
                self.assertEqual(diagram_editor.get_diagram("example")["text"], original["text"])

    def test_public_files_reject_file_and_directory_symlinks(self):
        with tempfile.TemporaryDirectory() as folder:
            outside = Path(folder) / "image.svg"
            outside.write_text("temporary figure")
            ordinary = _support.ROOT / "figures/external/http-probe.svg"
            ordinary.parent.mkdir(parents=True, exist_ok=True)
            ordinary.write_text("ordinary figure")
            try:
                self.assertEqual(self.call("/figures/external/http-probe.svg"), (200, "ordinary figure"))
                ordinary.unlink()
                ordinary.symlink_to(outside)
                self.assertEqual(self.call("/figures/external/http-probe.svg")[0], 404)
                directory = ordinary.parent / "http-linked"
                directory.symlink_to(Path(folder), target_is_directory=True)
                try:
                    self.assertEqual(self.call("/figures/external/http-linked/image.svg")[0], 404)
                finally:
                    directory.unlink()
            finally:
                ordinary.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
