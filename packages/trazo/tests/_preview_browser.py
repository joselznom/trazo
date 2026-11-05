"""Shared setup for the viewer regressions in Chrome, against the real preview HTTP handler.

Each test_preview_browser_* module holds the tests of one feature in a subclass of
PreviewBrowserTestCase.
"""

import json
import sys
import tempfile
import threading
import time
import traceback
import unittest
from pathlib import Path
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)

from trazo import diagram, preview
from trazo.browser import BrowserError
from trazo.local_server import LocalServer


@unittest.skipUnless(diagram.CHROME and Path(diagram.CHROME).exists(), "needs Chrome")
class PreviewBrowserTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chrome = diagram.Chrome()
        cls.addClassCleanup(cls.chrome.close)

    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="trazo-viewer-")
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        for name, value in {
            "OUT_DIR": root,
            "COMMENTS_PATH": root / "comments.json",
            "HISTORY_PATH": root / "history.json",
            "EDITS_PATH": root / "edits.json",
            "STATE": {name: dict(state) for name, state in preview.STATE.items()},
        }.items():
            patcher = patch.object(preview, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in preview.DOCS:
            body = '''<section class="level1" id="intro"><h1>Introduction</h1>
<p>A <em>word</em>, H<sub>2</sub>O.</p><p>Another paragraph.</p>
<figure id="fig:external"><img src="figures/external/ordinary.png">
<figcaption>Figure 1. External image.</figcaption></figure>
<figure id="fig:diagram"><img src="figures/external/example.png">
<figcaption>Figure 2. Diagram caption.</figcaption></figure></section>'''
            config = {"PREVIEW_DOC": name, "PREVIEW_TITLES": preview.PROJECT.titles,
                      "PREVIEW_FILES": [], "PREVIEW_DIAGRAMS": {"names": ["example"]}}
            script = "".join(f"window.{key} = {json.dumps(value)};" for key, value in config.items())
            (root / f"{name}.html").write_text(
                '<!doctype html><html><head><meta charset="utf-8">'
                '<link rel="stylesheet" href="/__assets/preview/preview.css"></head><body>'
                + body + f"<script>{script}</script>"
                '<script src="/__assets/preview/preview-model.js"></script>'
                + "".join(f'<script src="/__assets/preview/{script}"></script>' for script in preview.VIEWER_SCRIPTS)
                + '</body></html>',
                encoding="utf-8")
        server = self.server = LocalServer(("127.0.0.1", 0), preview.Handler)
        server.daemon_threads = False  # server_close waits for the requests in flight
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        self.url = f"http://127.0.0.1:{server.server_port}/manuscript.html"
        context = self.chrome.call("Target.createBrowserContext")["browserContextId"]
        self.context = context
        self.addCleanup(self.close_page)
        target = self.chrome.call("Target.createTarget", {"url": "about:blank", "browserContextId": context})["targetId"]
        self.session = self.chrome.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        self.chrome.call("Emulation.setDeviceMetricsOverride", {
            "width": 1280, "height": 800, "deviceScaleFactor": 1, "mobile": False}, self.session)
        # The tests expect motion; a Mac with Reduce Motion on (GitHub's macOS runners) turns off view
        # transitions and smooth scrolling. Tests of reduced motion emulate it themselves.
        self.chrome.call("Emulation.setEmulatedMedia", {"features": [
            {"name": "prefers-reduced-motion", "value": "no-preference"}]}, self.session)

    def evaluate(self, expression, session=None):
        reply = self.chrome.call("Runtime.evaluate", {
            "expression": expression, "awaitPromise": True, "returnByValue": True}, session or self.session)
        self.assertNotIn("exceptionDetails", reply, reply.get("exceptionDetails"))
        return reply["result"].get("value")

    def wait_for(self, expression, session=None):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            remaining = max(1, int((deadline - time.monotonic()) * 1000))
            try:
                ready = self.evaluate(f"""(async () => {{
                  const deadline = Date.now() + {remaining};
                  while (Date.now() < deadline) {{
                    if ({expression}) return true;
                    await new Promise(resolve => setTimeout(resolve, 25));
                  }}
                  return false;
                }})()""", session)
            except BrowserError as exc:
                if "navigated or closed" not in str(exc):
                    raise
                continue  # the next check belongs to the destination document
            self.assertTrue(ready, expression)
            return
        self.fail(expression)

    def open(self, query=""):
        self.chrome.call("Page.navigate", {"url": self.url + query}, self.session)
        try:
            self.wait_for("document.querySelector('.pv-bs-label')?.textContent.length > 0")
        except AssertionError as exc:
            raise AssertionError(f"{exc}\n{self.what_the_page_waits_for()}") from None
        self.wait_for("document.readyState === 'complete'")
        # On load the page restores its reading position in the first frame
        # after document.fonts.ready; wait for that frame (callbacks run in
        # order) so it cannot undo a test's own scrolling.
        self.evaluate("document.fonts.ready.then(() => new Promise(requestAnimationFrame))")

    def what_the_page_waits_for(self):
        """The page's state and the requests still running. Opening takes well under a
        second, so one that times out is stuck, and the failure is the only trace of why."""
        page = self.evaluate("""({url: location.href, ready: document.readyState,
          shell: !!document.querySelector('.pv-build'),
          finished: performance.getEntriesByType('resource').map(e => new URL(e.name).pathname)})""")
        frames = sys._current_frames()
        running = ["".join(traceback.format_stack(frames[t.ident])) for t in threading.enumerate()
                   if "process_request_thread" in t.name and t.ident in frames]
        return f"page: {page}\nrequests still running: {len(running)}\n" + "\n".join(running)

    def fail_fetches(self, *paths):
        """In pages opened next, fetches to these paths fail like a reset
        connection until the test sets window.serverUp; window.failed counts them."""
        self.chrome.call("Page.enable", None, self.session)
        self.chrome.call("Page.addScriptToEvaluateOnNewDocument", {"source": f"""
          window.failed = 0;
          (() => {{
            const fetch = window.fetch;
            window.fetch = (url, options) => {{
              if (window.serverUp || !{json.dumps(paths)}.some(p => String(url).startsWith(p)))
                return fetch(url, options);
              window.failed += 1;
              return Promise.reject(new TypeError('Failed to fetch'));
            }};
          }})();"""}, self.session)

    def select_second_paragraph(self):
        # A scroll the test just made fires its event on the next frame, and that hides the
        # selection button; let it fire first.
        self.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        self.evaluate('''(() => {
          const p = document.querySelector('[data-label="Introduction ¶2"]');
          const range = document.createRange(); range.selectNodeContents(p);
          const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
          p.dispatchEvent(new MouseEvent('mouseup', {bubbles:true}));
        })()''')
        self.wait_for("document.querySelector('.pv-fab').hidden === false")

    def mouse(self, kind, x, y):
        self.chrome.call("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y,
            "button": "left", "buttons": 0 if kind == "mouseReleased" else 1,
            "clickCount": 1}, self.session)

    def mouse_double_click(self, x, y):
        for count in (1, 2):
            for kind in ("mousePressed", "mouseReleased"):
                self.chrome.call("Input.dispatchMouseEvent", {
                    "type": kind, "x": x, "y": y, "button": "left", "clickCount": count,
                    "buttons": 0 if kind == "mouseReleased" else 1}, self.session)

    def double_click(self, selector, text):
        """A real double click on the element with this text: the first click may redraw it."""
        self.mouse_double_click(*self.evaluate(f"""(() => {{
          const el = Array.from(document.querySelectorAll({json.dumps(selector)})).find(x => x.textContent.trim() === {json.dumps(text)});
          const box = el.getBoundingClientRect();
          return [box.left + box.width / 2, box.top + box.height / 2];
        }})()"""))

    def click_text(self, selector):
        self.evaluate("Promise.all(document.getAnimations().filter(a => a.effect.getComputedTiming().iterations !== Infinity).map(a => a.finished.catch(() => {})))")
        # A layout change (a sidebar toggled, say) refits the page from a
        # ResizeObserver in the next frame; measure after it, not before.
        self.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        point = self.evaluate(f"""(() => {{
          const range = document.createRange();
          range.selectNodeContents(document.querySelector({json.dumps(selector)}));
          const rect = range.getClientRects()[0];
          return [rect.left + rect.width / 2, rect.top + rect.height / 2];
        }})()""")
        self.mouse("mousePressed", *point)
        self.mouse("mouseReleased", *point)

    def close_page(self):
        context, self.context = self.context, None
        if context:
            self.chrome.call("Target.disposeBrowserContext", {"browserContextId": context})

    def tearDown(self):
        # Before the cleanups delete files: Windows refuses to delete one a request is reading.
        self.close_page()
        self.server.shutdown()
        self.server.server_close()

    def keep_files(self, *paths):
        """Restore these files (or remove them) after the test."""
        for path in paths:
            data = path.read_bytes() if path.exists() else None
            self.addCleanup(lambda p=path, d=data: p.write_bytes(d) if d is not None else p.unlink(missing_ok=True))

    def table_settled(self):
        """The table's comments came and redrew it: a click in that redraw would be lost."""
        self.wait_for("document.readyState === 'complete' && performance.getEntriesByType('resource')"
                      ".some(e => e.name.includes('/__review?scope=table'))")
        self.evaluate("new Promise(resolve => setTimeout(resolve, 100))")

    def open_table(self):
        self.chrome.call("Page.navigate", {"url": self.url.replace("/manuscript.html", "/tables#results")},
                         self.session)
        self.wait_for("document.querySelector('thead th[data-id=\"method\"]')")
        self.table_settled()

    # ---- Submission view (articles)
    REPORT = {
        "kind": "article", "errors": 2, "warnings": 1,
        "journal": {"journal": "", "guidelines": "", "title_page": "manuscript", "declarations": "end"},
        "placements": "Title page in the manuscript · Declarations at the end of the manuscript",
        "groups": [
            {"id": "journal", "label": "Journal", "items": [
                {"status": "warn", "label": "Journal", "value": "Not set", "detail": "Set [submission] journal"}]},
            {"id": "limits", "label": "Limits", "items": [
                {"status": "error", "label": "Main text", "value": "120 / 100 words", "detail": "20 over the limit",
                 "doc": "manuscript"},
                {"status": "ok", "label": "Figures", "value": "2 / 6", "detail": "4 left", "doc": "manuscript",
                 "view": "figures"}]},
            {"id": "metadata", "label": "Metadata", "items": [
                {"status": "ok", "label": "Authors", "value": "Ana", "detail": "Edit manuscript/metadata.yaml",
                 "doc": "manuscript", "file": "manuscript/metadata.yaml", "block": "front"},
                {"status": "ok", "label": "Keywords", "value": "term", "detail": "Edit manuscript/metadata.yaml",
                 "doc": "manuscript", "file": "manuscript/metadata.yaml", "block": "keywords"}]},
            {"id": "declarations", "label": "Declarations", "items": [
                {"status": "info", "label": "Funding", "value": "", "detail": "Type into the journal's form",
                 "doc": "manuscript", "hash": "intro"},
                {"status": "ok", "label": "Data", "value": "", "detail": "Filled", "doc": "supplementary",
                 "file": "manuscript/supplementary/S1_methods.md", "line": 3, "edit": True, "hash": "intro"}]},
            {"id": "placeholders", "label": "Placeholders", "items": [
                {"status": "error", "label": "manuscript/main/01_introduction.md:3", "value": "[TODO: x]",
                 "detail": "", "file": "manuscript/main/01_introduction.md", "line": 3, "doc": "manuscript",
                 "edit": True, "section": "Introduction"}]}],
        "fields": [{"label": "Abstract", "text": "Objectives: one “quoted”.\n\nResults: two.", "form_only": False},
                   {"label": "Funding", "text": "Grant 42.", "form_only": True},
                   {"label": "Short title", "text": "", "form_only": False}],
    }

    def serve_report(self, report):
        """GET /__submission answers `report`, or `report(fields, fresh)` if it
        is a function (without fields for ?fields=0); returns the asked fields flags."""
        asked = []

        def fake(fields=True, fresh=None):
            asked.append(fields)
            answer = report(fields, fresh) if callable(report) else report
            return {key: value for key, value in answer.items() if fields or key != "fields"}
        patcher = patch.object(preview, "submission_report", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return asked

    def key(self, key, code):
        for kind in ("keyDown", "keyUp"):
            self.chrome.call("Input.dispatchKeyEvent", {"type": kind, "key": key, "code": key,
                                                        "windowsVirtualKeyCode": code}, self.session)
