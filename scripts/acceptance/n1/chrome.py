"""Headless Chrome for the n1 scenarios, over the DevTools pipe (--remote-debugging-pipe),
with no library: JSON messages ended by a NUL byte on descriptors 3 and 4."""

from __future__ import annotations

import fcntl
import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path

from ..registry import Skip
from ..sandbox import Sandbox

CALL_TIMEOUT = 30
# Chrome wants the pipes on descriptors 3 and 4. A fresh interpreter moves them there (Popen's
# preexec_fn is unsafe next to threads, and the harness has some).
BIND_PIPES = """
import os, sys
inbound, outbound = int(sys.argv[1]), int(sys.argv[2])
os.dup2(inbound, 3)
os.dup2(outbound, 4)
os.close(inbound)
os.close(outbound)
os.execv(sys.argv[3], sys.argv[3:])
"""
KNOWN_CHROME = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)


class ChromeError(Exception):
    pass


def find_chrome(sandbox: Sandbox) -> str:
    """The Chrome the install found or downloaded (machine.json), else one at a known location."""
    try:
        recorded = json.loads((sandbox.data / "machine.json").read_text(encoding="utf-8"))["tools"]["chrome"]["path"]
    except (OSError, ValueError, KeyError, TypeError):
        recorded = ""
    for candidate in (recorded, *KNOWN_CHROME):
        if candidate and Path(candidate).exists():
            return candidate
    raise Skip("needs Chrome, Chromium or Edge, installed or downloaded by setup")


class Browser:
    def __init__(self, sandbox: Sandbox, executable: str, startup_timeout: float = CALL_TIMEOUT) -> None:
        self.next_id = 0
        self.buffer = b""
        fds: list[int] = []
        try:
            commands_in, commands_out = os.pipe()
            fds += [commands_in, commands_out]
            replies_in, replies_out = os.pipe()
            fds += [replies_in, replies_out]
            # Chrome's ends must survive the exec but not leak into other children.
            chrome_in = fcntl.fcntl(commands_in, fcntl.F_DUPFD_CLOEXEC, 10)
            chrome_out = fcntl.fcntl(replies_out, fcntl.F_DUPFD_CLOEXEC, 10)
            fds += [chrome_in, chrome_out]
            profile = sandbox.tmp / "chrome-profile"
            self.stderr = open(sandbox.tmp / "chrome.stderr", "wb")  # kept in the sandbox for --keep
            # With a throwaway HOME Chrome waits for a login keychain that does not exist and never
            # answers Page.navigate; the mock also keeps it away from the real keychain.
            flags = ["--use-mock-keychain", *(["--no-sandbox"] if os.geteuid() == 0 else [])]
            self.process = sandbox.spawn(
                [sys.executable, "-I", "-c", BIND_PIPES, str(chrome_in), str(chrome_out), executable, *flags,
                 "--headless=new", "--remote-debugging-pipe", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                 "--no-default-browser-check", "--disable-extensions", "--mute-audio", f"--user-data-dir={profile}",
                 "about:blank"],
                pass_fds=(chrome_in, chrome_out), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=self.stderr)
            self.writer = os.fdopen(commands_out, "wb")
            fds.remove(commands_out)
            self.reader = replies_in
            fds.remove(replies_in)
        finally:
            for fd in fds:
                os.close(fd)
        try:
            self.call("Browser.getVersion", timeout=startup_timeout)  # Chrome is up, or this says why not
        except ChromeError:
            self.close(graceful=False)
            raise

    def call(self, method: str, params: dict | None = None, session: str | None = None,
             timeout: float = CALL_TIMEOUT) -> dict:
        self.next_id += 1
        message: dict = {"id": self.next_id, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        try:
            self.writer.write(json.dumps(message).encode() + b"\0")
            self.writer.flush()
        except OSError as exc:
            raise ChromeError(f"Chrome exited before {method} (status {self.process.poll()})") from exc
        deadline = time.monotonic() + timeout
        while True:
            while b"\0" not in self.buffer:
                left = deadline - time.monotonic()
                if left <= 0 or not select.select([self.reader], [], [], left)[0]:
                    raise ChromeError(f"Chrome did not answer {method} in {timeout}s")
                chunk = os.read(self.reader, 1 << 20)
                if not chunk:
                    raise ChromeError(f"Chrome exited during {method} (status {self.process.poll()})")
                self.buffer += chunk
            raw, self.buffer = self.buffer.split(b"\0", 1)
            reply = json.loads(raw)
            if reply.get("id") == self.next_id:  # anything else is an event, which nothing here needs
                if "error" in reply:
                    raise ChromeError(f"{method}: {reply['error'].get('message')}")
                return reply.get("result", {})

    def page(self) -> Page:
        target = self.call("Target.createTarget", {"url": "about:blank"})["targetId"]
        session = self.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        page = Page(self, session)
        self.call("Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 800, "deviceScaleFactor": 1,
                                                         "mobile": False}, session)
        return page

    def close(self, graceful: bool = True) -> None:
        if graceful:
            try:
                self.call("Browser.close", timeout=3)
            except ChromeError:
                pass
        try:
            self.process.wait(5 if graceful else 0.1)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.writer.close()
        os.close(self.reader)
        self.stderr.close()


class Page:
    def __init__(self, browser: Browser, session: str) -> None:
        self.browser, self.session = browser, session

    def goto(self, url: str) -> None:
        self.browser.call("Page.navigate", {"url": url}, self.session)
        self.wait_for("document.readyState === 'complete'")

    def evaluate(self, expression: str) -> object:
        reply = self.browser.call("Runtime.evaluate", {"expression": expression, "awaitPromise": True,
                                                       "returnByValue": True}, self.session)
        if "exceptionDetails" in reply:
            raise ChromeError(f"{expression[:80]}: {reply['exceptionDetails'].get('text')} "
                              f"{reply['exceptionDetails'].get('exception', {}).get('description', '')}")
        return reply["result"].get("value")

    def wait_for(self, expression: str, timeout: float = 30) -> None:
        """Poll until `expression` is truthy; a page that is navigating answers with errors, which count as not yet."""
        deadline = time.monotonic() + timeout
        last = ""
        while time.monotonic() < deadline:
            try:
                if self.evaluate(expression):
                    return
            except ChromeError as exc:
                last = str(exc)
            time.sleep(0.1)
        raise ChromeError(f"timed out after {timeout}s waiting for: {expression}" + (f" ({last})" if last else ""))

    def click(self, selector: str, text: str | None = None, settle: float = 0.3) -> None:
        """A real mouse click at the middle of the first element matching `selector` (and showing `text`),
        once the element has stood still for `settle` seconds. The page moves things after it loads (the
        preview's word count shifts the update pill and its popup), and a button that moves between
        the press and the release gets no click."""
        found = json.dumps([selector, text])
        locate = f"""(() => {{
          const [selector, text] = {found};
          const element = [...document.querySelectorAll(selector)].find(e => text === null || e.textContent.trim() === text);
          if (!element) return null;
          const rect = element.getBoundingClientRect();
          return [rect.left + rect.width / 2, rect.top + rect.height / 2];
        }})()"""
        point, still_since = self.evaluate(locate), time.monotonic()
        deadline = time.monotonic() + CALL_TIMEOUT
        while point and time.monotonic() < deadline and time.monotonic() - still_since < settle:
            time.sleep(0.05)
            moved = self.evaluate(locate)
            if moved != point:
                point, still_since = moved, time.monotonic()
        if not point:
            raise ChromeError(f"nothing to click: {selector} {text or ''}")
        for kind in ("mousePressed", "mouseReleased"):
            self.browser.call("Input.dispatchMouseEvent", {"type": kind, "x": point[0], "y": point[1], "button": "left",
                                                           "buttons": 0 if kind == "mouseReleased" else 1,
                                                           "clickCount": 1}, self.session)
