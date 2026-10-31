"""The live preview as an MCP App (io.modelcontextprotocol/ui, SEP-1865), for hosts that draw views.

Only a client that declares the extension in initialize gets it:
preview_start then names the view (VIEW_URI) and the server lists two
app-only tools, which hosts hide from the model. `preview_request` forwards
one GET or POST to the paper's running preview on 127.0.0.1 (hosts keep
views off loopback), so the page's own requests and /__bundle reach it.
`preview_panel` is ChatGPT's thread entrypoint (`openai/ui`, ignored
elsewhere): it opens with no arguments and shows the preview this server
started last. The view shows the paper only full screen: the resource and
preview_start ask ChatGPT for that mode alone, and the view asks other hosts.

Each preview_start gives its view a token; a request with an older token of
the same paper answers `superseded`, so only the newest view in the
conversation keeps polling. Panel tokens are never superseded, and tokens
this process did not issue (a view from before a restart) are served.
"""

from __future__ import annotations

import base64
import http.client
import json
import re
import secrets
import threading
from typing import Any

from .mcp_calls import ToolError, load
from .mcp_preview import Previews
from .project import ASSETS_DIR, Project

EXTENSION = "io.modelcontextprotocol/ui"
MIME_TYPE = "text/html;profile=mcp-app"
VIEW_URI = "ui://trazo/preview"
TEXT_TYPES = ("text/", "application/json", "application/javascript", "image/svg+xml")
REQUEST_TIMEOUT = 60

RESOURCES = [{"uri": VIEW_URI, "name": "trazo preview", "mimeType": MIME_TYPE,
              "description": "The paper's live preview"}]

APP_TOOLS = [
    {"name": "preview_request",
     "description": "One request to the paper's running preview, for the preview view.",
     "inputSchema": {"type": "object", "properties": {
         "project": {"type": "string", "description": "absolute path of the paper folder"},
         "view": {"type": "string", "description": "the view's token"},
         "path": {"type": "string", "description": "the request path and query, starting with /"},
         "method": {"type": "string", "enum": ["GET", "POST"]},
         "body": {"description": "a POST's JSON body"}},
         "required": ["project", "path"], "additionalProperties": False},
     "_meta": {"ui": {"resourceUri": VIEW_URI, "visibility": ["app"]}}},
    {"name": "preview_panel", "title": "Paper preview",
     "description": "The paper preview this conversation started, in a panel.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "_meta": {"ui": {"resourceUri": VIEW_URI, "visibility": ["app"]},
               "openai/ui": {"entrypoints": [{"type": "thread"}]}}},
]
APP_TOOL_NAMES = frozenset(tool["name"] for tool in APP_TOOLS)


def load_paper(args: dict[str, Any]) -> Project:
    project = load(args)
    if project is None:  # load raises when `project` is missing; this tells the type checker
        raise ToolError("project is required: the absolute path of the paper folder")
    return project


def supports_ui(params: dict[str, Any]) -> bool:
    """Whether an initialize request's client declares MCP Apps HTML views."""
    capabilities = params.get("capabilities")
    extensions = capabilities.get("extensions") if isinstance(capabilities, dict) else None
    settings = extensions.get(EXTENSION) if isinstance(extensions, dict) else None
    types = settings.get("mimeTypes") if isinstance(settings, dict) else None
    return isinstance(types, list) and MIME_TYPE in types


def ui_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`tools` with preview_start linked to the view, then the app-only tools."""
    meta = {"ui": {"resourceUri": VIEW_URI}, "openai/ui": {"preferredModelDisplayMode": "fullscreen"}}
    listed = [{**tool, "_meta": meta} if tool["name"] == "preview_start" else tool for tool in tools]
    return listed + APP_TOOLS


def read_resource(uri: str) -> dict[str, Any]:
    """The resources/read result for VIEW_URI: view.html with view.js inlined."""
    if uri != VIEW_URI:
        raise ToolError(f"unknown resource: {uri}")
    folder = ASSETS_DIR / "mcp-app"
    script = re.sub(r"</(script)", r"<\\/\1", (folder / "view.js").read_text(encoding="utf-8"), flags=re.I)
    html = (folder / "view.html").read_text(encoding="utf-8").replace("<!-- view.js -->", f"<script>{script}</script>")
    return {"contents": [{"uri": VIEW_URI, "mimeType": MIME_TYPE, "text": html,
                          "_meta": {"ui": {"prefersBorder": False},
                                    "openai/ui": {"availableDisplayModes": ["fullscreen"],
                                                  "preferredDisplayMode": "fullscreen"}}}]}


def summary(name: str, result: dict[str, Any]) -> str:
    """The text content of an app-only tool's reply; the view reads
    structuredContent, and repeating a proxied page as text would double it."""
    if name == "preview_request" and "status" in result:
        return f"HTTP {result['status']}"
    return json.dumps(result)


class Views:
    """The tokens of the views this server opened, and their transport."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.newest: dict[str, str] = {}  # paper root -> its newest inline view's token
        self.issued: set[str] = set()
        self.panels: set[str] = set()
        self.papers: list[str] = []  # paper roots in the order preview_start served them

    def opened(self, info: dict[str, Any]) -> dict[str, Any]:
        """A preview_start result with its view's token, which supersedes the paper's older views."""
        token = secrets.token_urlsafe(12)
        with self.lock:
            self.issued.add(token)
            self.newest[info["project"]] = token
            self.papers = [root for root in self.papers if root != info["project"]] + [info["project"]]
        return {**info, "view": token}

    def panel(self, args: dict[str, Any]) -> dict[str, Any]:
        """The newest paper whose preview still runs, with a panel token; `project` null when none."""
        with self.lock:
            papers = list(reversed(self.papers))
        for root in papers:
            info = Previews.running(load_paper({"project": root}))
            if info:
                token = secrets.token_urlsafe(12)
                with self.lock:
                    self.issued.add(token)
                    self.panels.add(token)
                return {"project": root, "url": info["url"], "view": token}
        return {"project": None}

    def superseded(self, project: Project, token: Any) -> bool:
        with self.lock:
            return (isinstance(token, str) and token in self.issued and token not in self.panels
                    and self.newest.get(str(project.root)) != token)

    def request(self, args: dict[str, Any]) -> dict[str, Any]:
        """Forward one request to the paper's preview: status, type, and the
        body as `text` or `base64`, or `superseded` for an older view."""
        project = load_paper(args)
        path, method = args.get("path"), args.get("method") or "GET"
        if (not isinstance(path, str) or not path.startswith("/") or path.startswith("//")
                or method not in ("GET", "POST")):
            raise ToolError("path must start with a single / and method be GET or POST")
        if self.superseded(project, args.get("view")):
            return {"superseded": True}
        info = Previews.running(project)
        if not info:
            raise ToolError("no preview is running for this paper; ask the agent to start it")
        connection = http.client.HTTPConnection("127.0.0.1", int(info["port"]), timeout=REQUEST_TIMEOUT)
        try:
            data = None if method == "GET" else json.dumps(args.get("body")).encode()
            connection.request(method, path, body=data,
                               headers={} if data is None else {"Content-Type": "application/json"})
            reply = connection.getresponse()
            content, kind = reply.read(), reply.getheader("Content-Type") or ""
            result: dict[str, Any] = {"status": reply.status, "type": kind}
            if reply.getheader("Location"):
                result["location"] = reply.getheader("Location")
            named = re.search(r'filename="([^"]+)"', reply.getheader("Content-Disposition") or "")
            if named:  # a download, which the view hands to the host or saves in the paper
                result["filename"] = named[1]
        except (OSError, http.client.HTTPException) as exc:
            raise ToolError(f"the preview did not answer: {exc}") from None
        finally:
            connection.close()
        if kind.startswith(TEXT_TYPES):
            result["text"] = content.decode("utf-8", errors="replace")
        else:
            result["base64"] = base64.b64encode(content).decode()
        return result
