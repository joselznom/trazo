"""The preview's update notice (trazo.preview_updates): GET /__updates
and POST /__update, against a fake plugin root and a data folder whose
venv is this test's own Python, so the preview "runs the install"."""

import http.client
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo is imported)
from test_mcp import Session, free_port, make_project, wait_until

import trazo
from trazo import manifest, new, preview_updates
from trazo.local_server import Conflict
from trazo.project import Project

FAKE_SETUP = """#!/bin/sh
echo "$*" >> "{log}"
[ "${{FAKE_SETUP_EXIT:-0}}" = 3 ] && echo "setup.sh: offline: uv 9.9.9 must be downloaded; nothing was changed" >&2
exit "${{FAKE_SETUP_EXIT:-0}}"
"""


@unittest.skipIf(os.name == "nt", "the fake setup.sh is a shell script")
class UpdateNoticeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="trazo-updates-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.data = self.tmp / "data"
        self.data.mkdir()
        self.plugin = self.tmp / "plugin root"
        (self.plugin / ".claude-plugin").mkdir(parents=True)
        self.log = self.tmp / "setup.log"
        script = self.plugin / preview_updates.SETUP_SCRIPT
        script.parent.mkdir(parents=True)
        script.write_text(FAKE_SETUP.format(log=self.log))
        self.set_plugin(trazo.__version__)
        for patcher in (patch.dict(os.environ, {"TRAZO_DATA_DIR": str(self.data),
                                                "TRAZO_PLUGIN_ROOT": str(self.plugin), "CLAUDE_PLUGIN_ROOT": ""}),
                        patch.object(preview_updates, "RESTART", None)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def set_plugin(self, version):
        (self.plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "trazo",
                                                                                "version": version}))
        index = self.plugin / preview_updates.PLUGIN_PACKAGE / preview_updates.TEMPLATE_INDEX
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_text(json.dumps({"template": f"v{version}"}))

    def run_the_install(self):
        """The data folder's venv is this Python's: its trazo is installed there."""
        (self.data / "venv").symlink_to(sys.prefix)

    def paper(self, template=None) -> Project:
        root = self.tmp / "paper"
        new.create(root, kind="article", example=False, title="A paper", project_name="paper")
        if template:
            data = manifest.read(root)
            manifest.write(root, {**data, "template": template})
        with patch.dict(os.environ, {"TRAZO_ROOT": str(root)}):
            return Project.load(root)

    def setup_calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_nothing_to_update(self):
        self.run_the_install()
        project = self.paper()
        plan = preview_updates.plan(project)
        self.assertEqual((plan["action"], plan["version"]), (None, trazo.__version__))
        self.assertEqual(plan["versions"]["installed"], trazo.__version__)
        with self.assertRaises(Conflict):
            preview_updates.apply(project)

    def test_paper_behind(self):
        project = self.paper("v0.1.0")
        plan = preview_updates.plan(project)
        self.assertEqual((plan["action"], plan["install"], plan["paper"], plan["restart"]),
                         ("update", False, True, False))
        reply = preview_updates.apply(project)
        self.assertEqual((reply["ok"], reply["restart"], reply["needs_merge"]), (True, False, []))
        self.assertEqual(manifest.read(project.root)["template"], f"v{trazo.__version__}")
        self.assertEqual(self.setup_calls(), [])
        self.assertIsNone(preview_updates.plan(project)["action"])

    def test_newer_plugin_reinstalls_offline_then_restarts(self):
        self.run_the_install()
        self.set_plugin("99.0.0")
        project = self.paper()
        plan = preview_updates.plan(project)
        self.assertEqual((plan["action"], plan["version"], plan["install"], plan["paper"]),
                         ("update", "99.0.0", True, True))
        restarts = []
        with patch.object(preview_updates, "RESTART", lambda: restarts.append(1)):
            reply = preview_updates.apply(project)
        self.assertEqual((reply["ok"], reply["version"], reply["restart"], reply["new_chat"]),
                         (True, "99.0.0", True, True))
        self.assertEqual(self.setup_calls(), [f"--offline {self.plugin}"])
        self.assertTrue(wait_until(lambda: restarts == [1], 5))

    def test_the_paper_follows_the_shipped_template_not_the_version(self):
        # A trazo release whose template is the paper's own: only trazo updates.
        self.run_the_install()
        self.set_plugin("99.0.0")
        project = self.paper()
        index = self.plugin / preview_updates.PLUGIN_PACKAGE / preview_updates.TEMPLATE_INDEX
        index.write_text(json.dumps({"template": manifest.read(project.root)["template"]}))
        plan = preview_updates.plan(project)
        self.assertEqual((plan["action"], plan["install"], plan["paper"]), ("update", True, False))

    def test_a_download_is_left_to_the_chat(self):
        self.run_the_install()
        self.set_plugin("99.0.0")
        project = self.paper("v0.1.0")
        with patch.dict(os.environ, {"FAKE_SETUP_EXIT": "3"}), \
                patch.object(preview_updates, "RESTART", lambda: self.fail("restarted")):
            reply = preview_updates.apply(project)
        self.assertEqual(reply, {"ok": False, "download": True,
                                 "error": "setup.sh: offline: uv 9.9.9 must be downloaded; nothing was changed"})
        self.assertEqual(manifest.read(project.root)["template"], "v0.1.0")
        time.sleep(preview_updates.RESTART_DELAY + 0.2)

    def test_an_older_plugin_offers_nothing(self):
        # Installed by the other app's newer plugin, or a paper updated by a newer trazo.
        self.run_the_install()
        self.set_plugin("0.1.0")
        self.assertEqual(preview_updates.plan(self.paper())["action"], "plugin")
        shutil.rmtree(self.tmp / "paper")
        (self.data / "venv").unlink()
        self.set_plugin(trazo.__version__)
        project = self.paper("v99.0.0")
        self.assertEqual(preview_updates.plan(project)["action"], "plugin")
        with self.assertRaisesRegex(Conflict, "update the plugin in the app"):
            preview_updates.apply(project)

    def test_not_running_the_install(self):
        # A development venv or a terminal: no reinstall over the data folder.
        self.set_plugin("99.0.0")
        plan = preview_updates.plan(self.paper())
        self.assertEqual((plan["action"], plan["install"], plan["versions"]["installed"]), (None, False, None))

    def test_one_update_at_a_time(self):
        project = self.paper("v0.1.0")
        with preview_updates.LOCK, self.assertRaisesRegex(Conflict, "already running"):
            preview_updates.apply(project)


@unittest.skipIf(os.name == "nt", "the fake setup.sh is a shell script")
class RestartTests(unittest.TestCase):
    """The preview restarts in place after trazo was reinstalled."""

    def test_the_preview_restarts_in_place(self):
        tmp = Path(tempfile.mkdtemp(prefix="trazo-mcp-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        project = make_project(tmp)
        plugin = tmp / "plugin"
        (plugin / ".claude-plugin").mkdir(parents=True)
        (plugin / ".claude-plugin" / "plugin.json").write_text('{"name": "trazo", "version": "99.0.0"}')
        script = plugin / preview_updates.SETUP_SCRIPT
        script.parent.mkdir(parents=True)
        script.write_text(FAKE_SETUP.format(log=tmp / "setup.log"))
        (tmp / "data").mkdir()
        (tmp / "data" / "venv").symlink_to(sys.prefix)
        port = free_port()
        session = Session(tmp, port, {"TRAZO_DATA_DIR": str(tmp / "data"), "TRAZO_PLUGIN_ROOT": str(plugin)})
        self.addCleanup(session.close)
        info = session.tool("preview_start", project=str(project))["structuredContent"]

        connection = http.client.HTTPConnection("127.0.0.1", info["port"], timeout=30)
        connection.request("POST", "/__update", body="{}", headers={
            "Host": f"127.0.0.1:{info['port']}", "Content-Type": "application/json"})
        reply = json.loads(connection.getresponse().read())
        connection.close()
        self.assertEqual((reply["ok"], reply["restart"]), (True, True), reply)

        log = project / ".build" / "preview.log"
        bound = f"[trazo] http://127.0.0.1:{info['port']}/"
        self.assertTrue(wait_until(lambda: log.read_text().count(bound) == 2, 30), log.read_text())
        self.assertTrue(wait_until(lambda: json.loads((project / ".build" / "preview.json").read_text()) == {
            "url": info["url"], "port": info["port"], "pid": info["pid"], "ppid": info["ppid"], "project": str(project)}))
        again = session.tool("preview_start", project=str(project))["structuredContent"]
        self.assertEqual((again["pid"], again["reused"]), (info["pid"], True))
        stop = session.tool("preview_stop", project=str(project))["structuredContent"]
        self.assertEqual(stop, {"stopped": True, "pid": info["pid"], "started_here": True})


if __name__ == "__main__":
    unittest.main()
