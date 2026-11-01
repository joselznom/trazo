"""trazo mcp for a client that draws MCP Apps views (trazo.mcp_app):
the view resource, preview_start's link to it, and the app-only tools."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)
from test_mcp import Session, free_port, make_project

from trazo import mcp_app

UI = {"io.modelcontextprotocol/ui": {"mimeTypes": ["text/html;profile=mcp-app"]}}


def initialize(session, extensions=None):
    capabilities = {"extensions": extensions} if extensions is not None else {}
    return session.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": capabilities,
                                      "clientInfo": {"name": "test", "version": "0"}})["result"]


class McpAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="trazo-mcp-app-")).resolve()
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        cls.project = make_project(cls.tmp)
        cls.session = Session(cls.tmp, free_port())
        cls.addClassCleanup(cls.session.close)
        cls.init = initialize(cls.session, UI)

    def call(self, name, **arguments):
        return self.session.rpc("tools/call", {"name": name, "arguments": arguments})["result"]

    def forward(self, view, path, **extra):
        return self.call("preview_request", project=str(self.project), view=view, path=path, **extra)

    def test_the_view_and_its_tools_are_listed(self):
        self.assertIn("resources", self.init["capabilities"])
        tools = {tool["name"]: tool for tool in self.session.rpc("tools/list")["result"]["tools"]}
        self.assertEqual(tools["preview_start"]["_meta"], {"ui": {"resourceUri": "ui://trazo/preview"},
                                                          "openai/ui": {"preferredModelDisplayMode": "fullscreen"}})
        for name in ("preview_request", "preview_panel"):
            self.assertEqual(tools[name]["_meta"]["ui"], {"resourceUri": "ui://trazo/preview", "visibility": ["app"]})
        self.assertEqual(tools["preview_panel"]["_meta"]["openai/ui"], {"entrypoints": [{"type": "thread"}]})
        self.assertNotIn("_meta", tools["build"])

        listed = self.session.rpc("resources/list")["result"]["resources"]
        self.assertEqual([r["uri"] for r in listed], ["ui://trazo/preview"])
        content, = self.session.rpc("resources/read", {"uri": "ui://trazo/preview"})["result"]["contents"]
        self.assertEqual((content["uri"], content["mimeType"]), ("ui://trazo/preview", "text/html;profile=mcp-app"))
        self.assertIn("window.TRAZO_VIEW", content["text"])
        # The paper needs the whole window: ChatGPT gets no inline mode to choose.
        self.assertEqual(content["_meta"]["openai/ui"], {"availableDisplayModes": ["fullscreen"],
                                                         "preferredDisplayMode": "fullscreen"})
        self.assertNotIn("<!-- view.js -->", content["text"])
        self.assertEqual(self.session.rpc("resources/read", {"uri": "ui://other"})["error"]["code"], -32002)

    def test_views_reach_the_preview_through_the_app_tool(self):
        first = self.call("preview_start", project=str(self.project))["structuredContent"]
        self.addCleanup(self.call, "preview_stop", project=str(self.project))
        self.assertTrue(first["view"])

        status = self.forward(first["view"], "/__status")
        self.assertFalse(status["isError"], status["content"])
        reply = status["structuredContent"]
        self.assertEqual((reply["status"], reply["type"]), (200, "application/json"))
        self.assertEqual(json.loads(reply["text"])["server"]["project"], str(self.project))
        self.assertEqual(status["content"], [{"type": "text", "text": "HTTP 200"}])
        font = self.forward(first["view"], "/__assets/preview/ibm-plex-sans-latin-400-normal.woff2")["structuredContent"]
        self.assertTrue(font["base64"])
        posted = self.forward(first["view"], "/__review", method="POST", body={"scope": "nope"})["structuredContent"]
        self.assertEqual(posted["status"], 400)  # the preview's own answer: the POST reached it
        bundle = self.forward(first["view"], "/__bundle?page=/")["structuredContent"]
        self.assertEqual(bundle["status"], 200)
        for path in ("http://example.org/", "//example.org/", "__status"):
            with self.subTest(path=path):
                self.assertTrue(self.forward(first["view"], path)["isError"])

        # A newer view of the paper supersedes the older one; the panel never is.
        panel = self.call("preview_panel")["structuredContent"]
        self.assertEqual(panel["project"], str(self.project))
        second = self.call("preview_start", project=str(self.project))["structuredContent"]
        self.assertTrue(second["reused"])
        self.assertEqual(self.forward(first["view"], "/__status")["structuredContent"], {"superseded": True})
        self.assertEqual(self.forward(second["view"], "/__status")["structuredContent"]["status"], 200)
        self.assertEqual(self.forward(panel["view"], "/__status")["structuredContent"]["status"], 200)
        # A token from another server process (a view from before a restart) is served.
        self.assertEqual(self.forward("from-before", "/__status")["structuredContent"]["status"], 200)

        self.call("preview_stop", project=str(self.project))
        stopped = self.forward(second["view"], "/__status")
        self.assertTrue(stopped["isError"])
        self.assertIn("no preview is running", stopped["content"][0]["text"])
        self.assertEqual(self.call("preview_panel")["structuredContent"], {"project": None})


class WithoutViewsTests(unittest.TestCase):
    def test_a_client_without_the_extension_sees_no_view(self):
        tmp = Path(tempfile.mkdtemp(prefix="trazo-mcp-app-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        # TRAZO_MCP_VIEWS=0, set for the Claude app, which declares views but refuses their tool calls.
        for extensions, env in ((None, None), ({"io.modelcontextprotocol/ui": {"mimeTypes": ["text/x-other"]}}, None),
                                (UI, {mcp_app.VIEWS_ENV: "0"})):
            with self.subTest(extensions=extensions, env=env):
                session = Session(tmp, free_port(), env)
                self.addCleanup(session.close)
                init = initialize(session, extensions)
                self.assertNotIn("resources", init["capabilities"])
                tools = session.rpc("tools/list")["result"]["tools"]
                self.assertFalse(any("_meta" in tool for tool in tools))
                self.assertFalse({tool["name"] for tool in tools} & mcp_app.APP_TOOL_NAMES)
                self.assertEqual(session.rpc("resources/read", {"uri": "ui://trazo/preview"})["error"]["code"], -32601)
                self.assertEqual(session.rpc("tools/call", {"name": "preview_request", "arguments": {}})
                                 ["error"]["code"], -32602)


if __name__ == "__main__":
    unittest.main()
