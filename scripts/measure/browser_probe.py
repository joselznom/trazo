"""Chrome timing and traces, using trazo's existing stdlib DevTools transport."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from common import SOURCE, dump

# This is developer tooling, not a generated paper importing trazo.
sys.path.insert(0, str(SOURCE))
from trazo.browser import BrowserError, Chrome  # noqa: E402


class Browser(Chrome):
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.event_buffer = b""
        super().__init__(timeout=45)
        target = self.call("Target.createTarget", {"url": "about:blank"})["targetId"]
        self.session = self.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        self.call("Emulation.setDeviceMetricsOverride", {
            "width": 1280, "height": 800, "deviceScaleFactor": 1, "mobile": False}, self.session)
        self.call("Performance.enable", {}, self.session)
        self.version = self.call("Browser.getVersion")

    def _read(self, timeout: float) -> bytes | None:
        chunk = super()._read(timeout)
        if chunk:
            self.event_buffer += chunk
            while b"\0" in self.event_buffer:
                raw, self.event_buffer = self.event_buffer.split(b"\0", 1)
                event = json.loads(raw)
                if event.get("method", "").startswith("Tracing."):
                    self.events.append(event)
        return chunk

    def evaluate(self, expression: str) -> object:
        reply = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True,
                                               "awaitPromise": True}, self.session)
        if "exceptionDetails" in reply:
            raise RuntimeError(reply["exceptionDetails"])
        return reply["result"].get("value")

    def wait(self, condition: str, timeout: float = 90) -> None:
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            try:
                if self.evaluate(f"Boolean({condition})"):
                    self.evaluate("document.fonts.ready.then(() => new Promise(r => "
                                  "requestAnimationFrame(() => requestAnimationFrame(r))))")
                    return
            except BrowserError as exc:
                if "navigated or closed" not in str(exc) and "context" not in str(exc):
                    raise
            time.sleep(.02)
        detail = self.evaluate("({url: location.href, text: document.body.innerText.slice(0,1500)})")
        raise TimeoutError(f"Browser condition: {condition}; {detail}")

    def navigate(self, url: str, condition: str) -> None:
        current = self.evaluate("location.href")
        self.evaluate("window.__benchmarkOldDocument = true")
        if current == url:
            self.call("Page.reload", {}, self.session)
        else:
            self.call("Page.navigate", {"url": url}, self.session)
        expected = json.dumps(url.split("#")[0])
        self.wait(f"!window.__benchmarkOldDocument && location.href.split('#')[0] === {expected} && ({condition})")

    def trace_start(self) -> None:
        self.events.clear()
        self.call("Tracing.start", {"categories": "devtools.timeline,v8,blink.user_timing,loading",
                                    "transferMode": "ReturnAsStream"})

    def trace_end(self, destination: Path) -> dict:
        self.call("Tracing.end")
        deadline = time.perf_counter() + 30
        while not any(e["method"] == "Tracing.tracingComplete" for e in self.events):
            chunk = self._read(max(.01, deadline - time.perf_counter()))
            if not chunk or time.perf_counter() > deadline:
                raise TimeoutError("Chrome did not complete the trace")
            self.buffer += chunk
        handle = next(e["params"]["stream"] for e in self.events if e["method"] == "Tracing.tracingComplete")
        pieces = []
        while True:
            part = self.call("IO.read", {"handle": handle})
            pieces.append(part["data"])
            if part.get("eof"):
                break
        self.call("IO.close", {"handle": handle})
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("".join(pieces), encoding="utf-8")
        trace = json.loads("".join(pieces))
        # Inclusive totals are diagnostic, not additive phase percentages.
        totals: dict[str, float] = {}
        for event in trace["traceEvents"]:
            if event.get("ph") == "X" and "dur" in event:
                name = event["name"]
                totals[name] = totals.get(name, 0) + event["dur"] / 1_000_000
        summary = {"inclusive_seconds": dict(sorted(totals.items(), key=lambda item: -item[1])[:30]),
                   "event_count": len(trace["traceEvents"])}
        dump(destination.with_suffix(".summary.json"), summary)
        return summary

    def metrics(self) -> dict:
        return {m["name"]: m["value"] for m in self.call("Performance.getMetrics", {}, self.session)["metrics"]}
