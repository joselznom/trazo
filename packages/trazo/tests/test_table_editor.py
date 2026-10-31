"""The table editor's endpoints through the preview's real HTTP handler."""

import http.client
import json
import threading
import unittest
from unittest.mock import Mock, patch

import _support  # noqa: F401  (sets TRAZO_ROOT first)
from test_table import DATA, SPECS, Restore, spec

from trazo import preview, table
from trazo.local_server import LocalServer


class EditorTests(Restore):
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

    def call(self, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": f"127.0.0.1:{self.port}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
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

    def state(self, label="results"):
        status, data = self.call(f"/__table?label={label}")
        self.assertEqual(status, 200, data)
        return data

    def test_page_and_list(self):
        status, page = self.call("/tables")
        self.assertEqual(status, 200)
        self.assertIn("/__assets/table/editor.js", page)
        self.assertEqual(self.call("/__assets/preview/table-editor.js"),
                         self.call("/__assets/table/editor.js"))
        self.assertIn("window.PREVIEW_TITLES", page)
        table.render_all()  # status "stale" until the Markdown is rendered
        status, tables = self.call("/__tables")
        kinds = {t["label"]: (t["kind"], t["status"]) for t in tables}
        self.assertEqual(kinds, {"cohorts": ("spec", "ok"), "results": ("spec", "ok")})

    def test_get_table(self):
        data = self.state()
        self.assertEqual(data["file"], "tables/specs/results.json")
        self.assertEqual(data["spec"], spec("results"))
        self.assertEqual(data["data"]["count"], 11)
        self.assertEqual(data["data"]["fields"]["metric"]["values"], ["auc", "brier"])
        self.assertIn("mean_sd", data["shared"]["formats"])
        self.assertEqual(data["model"]["header"][0][0]["id"], "cohort")
        self.assertIn("est3", data["format_usage"])
        self.assertIsNone(data["undo"])
        self.assertEqual(self.call("/__table?label=nope")[0], 404)
        self.assertEqual(self.call("/__table?label=../x")[0], 400)

    def test_preview_does_not_write(self):
        before = (SPECS / "results.json").read_bytes()
        draft = spec("results")
        draft["columns"][2]["header"] = "Method"
        status, data = self.call("/__table/preview", {"label": "results", "spec": draft})
        self.assertEqual(status, 200)
        self.assertEqual(data["model"]["header"][0][2]["text_html"], "Method")
        self.assertEqual((SPECS / "results.json").read_bytes(), before)
        draft["columns"][2]["format"] = "nope"
        status, data = self.call("/__table/preview", {"label": "results", "spec": draft})
        self.assertIn("unknown format", data["issues"][0]["message"])

    def test_save_conflict_and_invalid(self):
        base = self.state()["sha256"]
        ops = [{"op": "set", "path": "columns.method.header", "value": "Method"}]
        status, data = self.call("/__table", {"label": "results", "base_sha256": base, "ops": ops})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["spec"]["columns"][2]["header"], "Method")
        self.assertIn("| Method", table.output_path("results").read_text())
        self.assertEqual(data["undo"], "patch results: set columns.method.header")
        status, data = self.call("/__table", {"label": "results", "base_sha256": base, "ops": ops})
        self.assertEqual(status, 409)
        self.assertEqual(data["sha256"], table.sha256_of(SPECS / "results.json"))
        before = (SPECS / "results.json").read_bytes()
        status, data = self.call("/__table", {"label": "results", "base_sha256": data["sha256"],
                                              "ops": [{"op": "set", "path": "columns.n.header",
                                                       "value": "Cohort"}]})
        self.assertEqual(status, 422)
        self.assertEqual(data["issues"][0]["id"], "n")
        self.assertEqual((SPECS / "results.json").read_bytes(), before)
        status, data = self.call("/__table/undo", {"label": "results"})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["spec"]["columns"][2]["header"], "Model")
        self.assertIn("| Model", table.output_path("results").read_text())
        self.assertEqual(self.call("/__table/undo", {"label": "results"})[0], 400)
        data = self.state()
        self.call("/__table", {"label": "results", "base_sha256": data["sha256"], "ops": ops})
        (SPECS / "results.json").write_text(json.dumps(spec("results"), indent=1) + "\n")
        status, data = self.call("/__table/undo", {"label": "results"})
        self.assertEqual(status, 409, data)
        self.assertIn("changed since", data["error"])

    def test_shared(self):
        sha = self.state()["shared"]["sha256"]
        status, data = self.call("/__table/shared", {"label": "results", "base_sha256": sha, "ops": [
            {"op": "set", "path": "formats.mean_sd", "value": "{oops}"}]})
        self.assertEqual(status, 422)
        status, data = self.call("/__table/shared", {"label": "results", "base_sha256": sha, "ops": [
            {"op": "set", "path": "maps.method.forest", "value": "RF"}]})
        self.assertEqual(status, 200, data)
        self.assertIn("| RF ", table.output_path("results").read_text())
        self.assertEqual(self.call("/__table/shared", {"base_sha256": sha, "ops": []})[0], 409)
        status, data = self.call("/__table/undo", {"label": "results"})
        self.assertIn("patch shared", data["undone"])
        self.assertNotIn("RF", table.SHARED_PATH.read_text())

    def test_comments_and_status(self):
        status, before = self.call("/__table-status")
        self.assertEqual(set(before["tables"]), {"cohorts", "results"})
        status, data = self.call("/__table-comments", {"op": "add", "table": "results",
                                                       "comment": "Why bold?",
                                                       "target": {"column": "metric.auc.est"}})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["comments"][0]["target"], {"column": "metric.auc.est"})
        status, data = self.call("/__table-comments?label=results")
        self.assertEqual(len(data["comments"]), 1)
        self.assertEqual(self.call("/__table-comments", {"op": "add", "table": "results",
                                                         "comment": "x", "target": "n"})[0], 400)
        self.assertEqual(self.call("/__table-comments", {"op": "update", "id": "nope",
                                                         "status": "done"})[0], 404)
        self.assertNotEqual(self.call("/__table-status")[1]["comments"], before["comments"])
        status, tables = self.call("/__tables")
        self.assertEqual({t["label"]: t["open_comments"] for t in tables}["results"], 1)

    def test_spec_change_renders_in_process(self):
        source = SPECS / "results.json"
        polls = 0

        def tick(_):
            nonlocal polls
            polls += 1
            if polls == 1:
                data = spec("results")
                data["title"] = "Edited by hand."
                source.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            if polls > 2:
                return True  # stop the watcher

        # The fixture's [commands] tables is the project's own stage, which a spec change would rerun.
        with patch.dict(preview.PROJECT.config["commands"], {"tables": []}), \
             patch.object(preview, "regenerate_tables", side_effect=AssertionError), \
             patch.object(preview, "build") as build:
            # Through stop_event, not a patched time.sleep: another thread\'s sleep would count as a pass.
            preview.watch(stop_event=Mock(**{"wait.side_effect": tick}))
        self.assertIn("Table: [Edited by hand.]{.caption-title}", table.output_path("results").read_text())
        self.assertTrue(build.called)
        self.assertTrue((DATA / "results.json").is_file())

    def test_data_written_during_render_is_not_swallowed(self):
        polls = 0
        renders = 0
        render = preview.render_spec_tables

        def tick(_):
            nonlocal polls
            polls += 1
            if polls == 1:
                draft = spec("results")
                draft["title"] = "Changed spec."
                (SPECS / "results.json").write_text(json.dumps(draft))
            if polls > 2:
                return True  # stop the watcher

        def render_then_generate():
            nonlocal renders
            renders += 1
            render()
            if renders == 1:
                path = DATA / "results.json"
                data = json.loads(path.read_text())
                data["records"][0]["value"]["est"] = 0.123
                path.write_text(json.dumps(data))

        with patch.dict(preview.PROJECT.config["commands"], {"tables": []}), \
             patch.object(preview, "render_spec_tables", side_effect=render_then_generate), \
             patch.object(preview, "build"):
            # Through stop_event, not a patched time.sleep: another thread\'s sleep would count as a pass.
            preview.watch(stop_event=Mock(**{"wait.side_effect": tick}))
        self.assertIn("0.123", table.output_path("results").read_text())


if __name__ == "__main__":
    unittest.main()
