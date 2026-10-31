"""HTTP guards and replies shared by every trazo workspace feature.

- LocalServer: a ThreadingHTTPServer with a listen queue long enough for a
  page load, that does not report clients leaving mid-reply (see its docstring).
- Request guards: only 127.0.0.1:<port> or localhost:<port> as Host, no
  foreign Origin, no cross-site Sec-Fetch-Site (except following a link to a
  page), no framing, and POST bodies must be application/json.
- Anti-framing (X-Frame-Options: DENY, CSP frame-ancestors 'none') and
  Cache-Control: no-store on every response; no CORS headers.
- JSON replies that map RequestError (400), NotFound (404), Conflict (409),
  Unprocessable (422) and StoreError (500) to statuses.
"""

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from .review_storage import StoreError

MAX_BODY = 1_000_000


@dataclass
class Download:
    data: bytes
    content_type: str
    filename: str


class RequestError(ValueError):
    """Invalid request; the message is shown to the caller (HTTP 400)."""

    status = 400

    def __init__(self, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.extra = extra


class NotFound(RequestError):
    """The requested item does not exist (HTTP 404)."""

    status = 404


class Conflict(RequestError):
    """Stale state; `extra` is added to the JSON error, e.g. the current sha (HTTP 409)."""

    status = 409


class Unprocessable(RequestError):
    """Well-formed but invalid content, e.g. schema issues in `extra` (HTTP 422)."""

    status = 422


class LocalServer(ThreadingHTTPServer):
    """ThreadingHTTPServer with a listen queue of 128 instead of 5, quiet
    about clients that leave mid-reply.

    The handlers speak HTTP/1.0, so a page load opens a connection for every
    asset and fetch at once. When the accept thread falls behind (a busy
    machine), macOS resets loopback connections past the queue at once and
    the page's fetch fails instead of waiting.
    """

    request_queue_size = 128

    def handle_error(self, request: Any, client_address: Any) -> None:
        # A closed tab or a reload drops the connection while a reply is being
        # written; that is no server error, so it prints no traceback.
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)


class GuardedHandler(SimpleHTTPRequestHandler):
    """Request handler with the guards, anti-framing headers and JSON helpers."""

    # mimetypes does not know woff2 on every platform (e.g. minimal Linux).
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".woff2": "font/woff2"}

    def log_message(self, *args: Any) -> None:
        pass

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        # Never framed: stops a foreign page from clickjacking the buttons.
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        super().end_headers()

    def refuse(self, why: str) -> None:
        self.send_json({"error": f"forbidden: {why}"}, 403)

    def untrusted(self, post: bool) -> str | None:
        """Why this request must be refused, or None.

        Blocks DNS rebinding (Host), cross-site pages (Origin, Sec-Fetch-Site)
        (except plain page navigation) and, for POST, anything but a JSON body (a cross-site form or
        text/plain fetch cannot send application/json without a preflight,
        and there are no CORS headers, so the preflight fails).
        """
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if (self.headers.get("Host") or "").lower() not in hosts:
            return "unexpected Host header"
        origin = self.headers.get("Origin")
        if origin is not None and origin.lower() not in {f"http://{h}" for h in hosts}:
            return "unexpected Origin"
        site = (self.headers.get("Sec-Fetch-Site") or "").lower()
        path = urlparse(self.path).path
        # Following a link from another site to a page is harmless
        # (that site cannot read it); every other cross-site request is not.
        dest = (self.headers.get("Sec-Fetch-Dest") or "").lower()
        if dest in ("iframe", "frame", "embed", "object") and site != "same-origin":
            return f"framing from {site or 'unknown origin'}"
        link = (not post and site == "cross-site" and path.endswith((".html", "/"))
                and dest == "document"
                and (self.headers.get("Sec-Fetch-Mode") or "").lower() == "navigate")
        if site and not link and site not in (
                ("same-origin", "none") if post else ("same-origin", "none", "same-site")):
            return f"Sec-Fetch-Site: {site}"
        if post:
            media, *params = [p.strip() for p in (self.headers.get("Content-Type") or "").split(";")]
            if media.lower() != "application/json" or any(
                    p.lower().replace(" ", "").replace('"', "") != "charset=utf-8" for p in params):
                return "Content-Type must be application/json"
        return None

    def guard(self, post: bool) -> bool:
        """Refuse an untrusted request (403); True when it may proceed."""
        why = self.untrusted(post)
        if why:
            self.refuse(why)
            return False
        return True

    def json_body(self) -> Any:
        """The parsed JSON request body; RequestError (or ValueError) when unusable."""
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_BODY:
            raise RequestError("request body missing or too large")
        return json.loads(self.rfile.read(length))

    def reply(self, produce: Callable[[], Any]) -> None:
        """Send produce()'s result as JSON; map errors to JSON statuses."""
        try:
            payload = produce()
        except RequestError as exc:  # NotFound, Conflict, Unprocessable too
            self.send_json({"error": str(exc), **exc.extra}, exc.status)
        except StoreError as exc:
            self.send_json({"error": str(exc)}, 500)
        except ValueError as exc:  # bad JSON or encoding
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"error": f"server error: {exc!r}"}, 500)
        else:
            if isinstance(payload, Download):
                self.send_response(200)
                self.send_header("Content-Type", payload.content_type)
                self.send_header("Content-Disposition", f'attachment; filename="{payload.filename}"')
                self.send_header("Content-Length", str(len(payload.data)))
                self.end_headers()
                self.wfile.write(payload.data)
            else:
                self.send_json(payload)

    def send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
