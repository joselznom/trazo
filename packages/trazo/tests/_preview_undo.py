"""What the tests of Cmd+Z while writing share: real keys, and saves whose answers wait."""

import json
import threading
from contextlib import contextmanager
from unittest.mock import patch

from _preview_writing import WritingCase

from trazo import preview

META, SHIFT = 4, 8  # modifier bits of Input.dispatchKeyEvent
TEXT = "One sentence. Two sentences here.\n\nNext paragraph.\n"
PARAGRAPHS = "[...document.querySelectorAll('.pv-page p.pv-can-write')].map(p => p.textContent.replace(/¶\\d+/, '').trim())"
BOLD = "[...document.querySelectorAll('.pv-page p strong')].map(e => e.textContent)"


class UndoCase(WritingCase):
    def chord(self, key, code, modifiers=META, command=None):
        """Cmd with a key; `command` is what the browser runs for it when the page lets it."""
        for kind in ("keyDown", "keyUp"):
            event = {"type": kind, "key": key, "code": f"Key{key.upper()}", "modifiers": modifiers,
                     "windowsVirtualKeyCode": code}
            if kind == "keyDown" and command:
                event["commands"] = [command]
            self.chrome.call("Input.dispatchKeyEvent", event, self.session)

    def undo(self):
        self.chord("z", 90, command="undo")

    def redo(self):
        self.chord("z", 90, META | SHIFT, command="redo")

    def enter(self):
        for kind in ("keyDown", "keyUp"):
            self.chrome.call("Input.dispatchKeyEvent", {"type": kind, "key": "Enter", "code": "Enter",
                                                        "windowsVirtualKeyCode": 13, **({"text": "\r"} if kind == "keyDown" else {})},
                             self.session)

    def caret_after(self, word):
        self.evaluate(f"""(() => {{
          const walker = document.createTreeWalker(document.querySelector('.pv-writing'), NodeFilter.SHOW_TEXT);
          while (walker.nextNode()) {{
            const at = walker.currentNode.data.indexOf({json.dumps(word)});
            if (at >= 0) {{ getSelection().collapse(walker.currentNode, at + {len(word)}); return; }}
          }}
        }})()""")

    def caret_before_next(self):
        self.evaluate("""(() => { const w = document.createTreeWalker(document.querySelector('.pv-writing'), NodeFilter.SHOW_TEXT);
          let n; while ((n = w.nextNode())) if (n.data.includes('Next')) { getSelection().collapse(n, n.data.indexOf('Next')); return; } })()""")

    def settled(self):
        self.wait_for("!saves && document.querySelector('.pv-save-state').textContent !== 'Saving…'")

    def saved_now(self):
        self.wait_for("document.querySelector('.pv-save-state').textContent === 'Saved' && !saves")

    @contextmanager
    def held(self, route):
        """The answers of `route` (a function of trazo.preview) wait until the block ends."""
        gate, real = threading.Event(), getattr(preview, route)
        def later(request):
            gate.wait(10)
            return real(request)
        with patch.object(preview, route, later):
            try:
                yield
            finally:
                gate.set()
                self.settled()

    def select_word(self, word):
        self.mouse_double_click(*self.word_at(word))
        self.wait_for("document.querySelector('.pv-writing') && !getSelection().isCollapsed")

    def paragraphs(self):
        return self.evaluate(PARAGRAPHS)
