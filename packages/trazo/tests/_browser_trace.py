"""Experiment-only tracing for the viewer tests in Chrome: what the server and the page
did around a failed wait.

The CI failures on Windows leave a page whose status label never fills. Whether a
viewer script failed to load, threw, or a request never reached the server cannot be
told from the pathnames of finished requests, so this records each side's timeline:
- the server: every accepted connection, its request line, status, bytes sent, the
  time it took and any error (including the ConnectionErrors LocalServer hides);
- the page: every fetch with its start, end, status or error, and the Resource
  Timing entries with their status and sizes (a failed script is listed too, with
  status 0 or a body size of 0).
"""

import http.client
import threading
import time
from collections import deque

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)

from trazo import preview
from trazo.local_server import LocalServer

T0 = time.monotonic()
_LOG: deque = deque(maxlen=400)
_LOCK = threading.Lock()


def now() -> float:
    return round(time.monotonic() - T0, 3)


def record(**entry) -> dict:
    entry.setdefault("t", now())
    with _LOCK:
        _LOG.append(entry)
    return entry


def since(start: float) -> list[dict]:
    with _LOCK:
        return [e for e in _LOG if e["t"] >= start]


POLLS = ("GET /__status ", "GET /__workspace ")


def report(start: float) -> str:
    """The server log since `start`; answered polls are counted, not listed."""
    lines, polls = [], []
    for entry in since(start):
        if entry.get("status") == 200 and entry.get("line", "").startswith(POLLS) and "error" not in entry:
            polls.append(entry["t"])
            continue
        if polls:
            lines.append(f"  ... {len(polls)} answered polls from {polls[0]} to {polls[-1]} s")
            polls = []
        lines.append(f"  {entry}")
    if polls:
        lines.append(f"  ... {len(polls)} answered polls from {polls[0]} to {polls[-1]} s")
    return "\n".join(lines)


class _CountingWriter:
    def __init__(self, raw, entry):
        self.raw, self.entry = raw, entry

    def write(self, data):
        written = self.raw.write(data)
        self.entry["bytes"] = self.entry.get("bytes", 0) + len(data)
        return written

    def __getattr__(self, name):
        return getattr(self.raw, name)


class TracedHandler(preview.Handler):
    def setup(self):
        self.trace = record(event="request", port=self.client_address[1],
                            thread=threading.current_thread().name)
        super().setup()
        self.wfile = _CountingWriter(self.wfile, self.trace)

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except BaseException as exc:
            self.trace["error"] = repr(exc)
            raise
        finally:
            self.trace.setdefault("line", (getattr(self, "requestline", "") or "")[:120])
            self.trace["took"] = round(now() - self.trace["t"], 3)

    def parse_request(self):
        self.trace["read"] = round(now() - self.trace["t"], 3)  # request line and headers arrived
        return super().parse_request()

    def send_response(self, code, message=None):
        self.trace["status"] = int(code)
        super().send_response(code, message)


class TracedServer(LocalServer):
    def handle_error(self, request, client_address):
        import sys
        record(event="server-error", port=client_address[1], error=repr(sys.exc_info()[1]))
        super().handle_error(request, client_address)


def probe(port: int) -> dict:
    """GET /__status from Python: whether the server answers at all at the failure."""
    start = time.monotonic()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        connection.request("GET", "/__status", headers={"Host": f"127.0.0.1:{port}"})
        status = connection.getresponse().status
        return {"status": status, "took": round(time.monotonic() - start, 3)}
    except OSError as exc:
        return {"error": repr(exc), "took": round(time.monotonic() - start, 3)}


# Runs in every new document before its scripts: fetch start/end, script load
# results and the time origin, so a failure report can show what never answered.
PAGE_SCRIPT = """
  window.__trazoFetches = [];
  (() => {
    const fetch = window.fetch;
    window.fetch = function (url, options) {
      const entry = {url: String(url).slice(0, 80), method: options?.method || 'GET', start: Math.round(performance.now())};
      window.__trazoFetches.push(entry);
      if (window.__trazoFetches.length > 300) window.__trazoFetches.splice(0, 100);
      return fetch.apply(this, arguments).then(r => { entry.end = Math.round(performance.now()); entry.status = r.status; return r; },
        e => { entry.end = Math.round(performance.now()); entry.error = String(e); throw e; });
    };
  })();
"""

# What the page shows at a failed wait, beyond the original diagnostics.
PAGE_STATE = """({
  now: Math.round(performance.now()),
  visibility: document.visibilityState,
  navigation: (() => { const n = performance.getEntriesByType('navigation')[0];
    return n && {type: n.type, status: n.responseStatus, responseEnd: Math.round(n.responseEnd),
      domContentLoaded: Math.round(n.domContentLoadedEventEnd), load: Math.round(n.loadEventEnd)}; })(),
  viewer: (() => { const t = (f) => { try { return f(); } catch (e) { return 'unset: ' + e.name; } };
    return {started: t(() => started), polling: t(() => polling), showBuild: t(() => typeof showBuild),
      watching: t(() => typeof Trazo === 'object')}; })(),
  failedResources: performance.getEntriesByType('resource')
    .filter(e => e.responseStatus !== 200 || (e.initiatorType === 'script' && e.decodedBodySize === 0))
    .map(e => ({path: new URL(e.name).pathname, status: e.responseStatus, transfer: e.transferSize,
      body: e.decodedBodySize, start: Math.round(e.startTime), end: Math.round(e.responseEnd)})),
  scripts: performance.getEntriesByType('resource').filter(e => e.initiatorType === 'script')
    .map(e => [new URL(e.name).pathname.split('/').pop(), e.responseStatus, e.decodedBodySize,
      Math.round(e.startTime), Math.round(e.responseEnd)]),
  fetches: (window.__trazoFetches || []).filter(f => f.end === undefined || f.error || f.status !== 200)
    .concat((window.__trazoFetches || []).slice(0, 6)),
  fetchCount: (window.__trazoFetches || []).length,
})"""
