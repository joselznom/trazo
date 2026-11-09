"""Cmd+Z and Cmd+Shift+Z inside the text, in Chrome: real keys against the real page and server.

Enter, Backspace at the start and bold or italic save at once, so the browser's
own undo has nothing of theirs to take back; the viewer's history does.
"""

import json
import time
import unittest
from unittest.mock import patch

from _preview_writing import WritingCase

from trazo import preview

META, SHIFT = 4, 8  # modifier bits of Input.dispatchKeyEvent
TEXT = "One sentence. Two sentences here.\n\nNext paragraph.\n"
PARAGRAPHS = "[...document.querySelectorAll('.pv-page p.pv-can-write')].map(p => p.textContent.replace(/¶\\d+/, '').trim())"


class WriteUndoTests(WritingCase):
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

    def settled(self):
        self.wait_for("!saves && document.querySelector('.pv-save-state').textContent !== 'Saving…'")

    def paragraphs(self):
        return self.evaluate(PARAGRAPHS)

    def broken(self):
        self.build(TEXT)
        self.write_in("sentence")
        self.caret_after("One sentence.")
        self.enter()
        self.wait_for("document.querySelector('.pv-save-state').textContent === 'Saved' && !saves")
        self.assertEqual(self.source(), "One sentence.\n\nTwo sentences here.\n\nNext paragraph.\n")

    def test_cmd_z_right_after_enter_joins_the_paragraph_again_on_the_page_and_in_the_file(self):
        self.broken()
        self.undo()
        self.wait_for(f"{PARAGRAPHS}.includes('One sentence. Two sentences here.')")
        self.settled()
        self.assertEqual(self.source(), TEXT)
        self.assertTrue(self.evaluate("document.activeElement.classList.contains('pv-writing')"))
        # The caret is back where Enter was pressed.
        self.type("X")
        self.assertIn("One sentence.X Two sentences here.", self.paragraphs())

    def test_typing_after_enter_goes_first_then_the_break(self):
        self.broken()
        self.type("Now ")
        self.undo()
        self.wait_for(f"{PARAGRAPHS}.includes('Two sentences here.')")
        self.assertEqual(self.source(), "One sentence.\n\nTwo sentences here.\n\nNext paragraph.\n")
        self.undo()
        self.wait_for(f"{PARAGRAPHS}.includes('One sentence. Two sentences here.')")
        self.settled()
        self.assertEqual(self.source(), TEXT)

    def test_text_typed_before_enter_stays_on_the_page_and_saves_on_leaving(self):
        self.build(TEXT)
        self.write_in("sentence")
        self.caret_after("One sentence.")
        self.type(" Extra.")
        self.enter()
        self.wait_for("document.querySelector('.pv-save-state').textContent === 'Saved' && !saves")
        self.undo()
        self.wait_for(f"{PARAGRAPHS}.includes('One sentence. Extra. Two sentences here.')")
        self.settled()
        self.assertEqual(self.source(), TEXT)
        self.leave()
        self.wait_for("document.querySelector('.pv-save-state').textContent === 'Saved' && !saves")
        self.assertEqual(self.source(), "One sentence. Extra. Two sentences here.\n\nNext paragraph.\n")

    def test_cmd_shift_z_breaks_it_again(self):
        self.broken()
        self.undo()
        self.wait_for(f"{PARAGRAPHS}.includes('One sentence. Two sentences here.')")
        self.settled()
        self.redo()
        self.wait_for(f"{PARAGRAPHS}.includes('Two sentences here.')")
        self.settled()
        self.assertEqual(self.source(), "One sentence.\n\nTwo sentences here.\n\nNext paragraph.\n")
        self.undo()
        self.wait_for(f"{PARAGRAPHS}.includes('One sentence. Two sentences here.')")
        self.settled()
        self.assertEqual(self.source(), TEXT)

    def test_cmd_z_removes_a_new_empty_paragraph(self):
        self.build(TEXT)
        self.write_in("sentence")
        self.caret_after("Two sentences here.")
        self.enter()
        self.wait_for("document.querySelector('.pv-writing')?.textContent === ''")
        self.undo()
        self.wait_for(f"{PARAGRAPHS}.length === 3")  # the introduction's, this one and the next
        self.type(" More.")
        self.assertIn("One sentence. Two sentences here. More.", self.paragraphs())

    def test_cmd_z_takes_bold_back(self):
        self.build(TEXT)
        self.mouse_double_click(*self.word_at("Two"))
        self.wait_for("document.querySelector('.pv-writing') && !getSelection().isCollapsed")
        self.chord("b", 66)
        self.wait_for("document.querySelector('.pv-writing strong') && !saves")
        self.assertEqual(self.source(), "One sentence. **Two** sentences here.\n\nNext paragraph.\n")
        self.undo()
        self.wait_for("!document.querySelector('.pv-writing strong')")
        self.settled()
        self.assertEqual(self.source(), TEXT)
        self.assertEqual(self.evaluate("getSelection().toString()"), "Two")

    def test_cmd_z_splits_a_joined_paragraph_again(self):
        self.build(TEXT)
        self.write_in("Next")
        self.evaluate("""(() => { const w = document.createTreeWalker(document.querySelector('.pv-writing'), NodeFilter.SHOW_TEXT);
          let n; while ((n = w.nextNode())) if (n.data.includes('Next')) { getSelection().collapse(n, n.data.indexOf('Next')); return; } })()""")
        self.key("Backspace", 8)
        self.wait_for(f"{PARAGRAPHS}.includes('One sentence. Two sentences here. Next paragraph.') && !saves")
        self.assertEqual(self.source(), "One sentence. Two sentences here. Next paragraph.\n")
        self.undo()
        self.wait_for(f"{PARAGRAPHS}.includes('Next paragraph.')")
        self.settled()
        self.assertEqual(self.source(), TEXT)

    def test_a_paste_of_two_paragraphs_goes_back_piece_by_piece(self):
        self.build(TEXT)
        self.write_in("sentence")
        self.caret_after("One sentence.")
        self.evaluate("""(() => {
          const data = new DataTransfer();
          data.setData('text/plain', ' First piece.\\n\\nSecond piece.');
          document.activeElement.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
        })()""")
        self.wait_for("document.querySelector('.pv-save-state').textContent === 'Saved' && !saves")
        self.assertIn("Second piece. Two sentences here.", self.paragraphs())
        for _ in range(3):
            self.undo()
            self.settled()
        self.wait_for(f"{PARAGRAPHS}.includes('One sentence. Two sentences here.')")
        self.assertEqual(self.source(), TEXT)

    def test_cmd_z_does_not_undo_over_a_change_on_disk(self):
        self.broken()
        changed = "One sentence.\n\nTwo sentences here.\n\nNext paragraph, edited elsewhere.\n"
        self.chapter.write_text(changed, encoding="utf-8")
        self.undo()
        self.wait_for("document.querySelector('.pv-toast')?.textContent.startsWith('Not undone')")
        self.assertEqual(self.source(), changed)
        self.assertIn("Two sentences here.", self.paragraphs())


class ReloadAfterSavesTests(WritingCase):
    def test_the_page_waits_for_the_build_of_its_last_save(self):
        watcher = patch.object(preview, "WATCHER", {"synced": 0.0, "rebuild": set(), "outputs": []})
        watcher.start()
        self.addCleanup(watcher.stop)
        self.build(TEXT)
        self.evaluate("window.samePage = true")
        self.write_in("Next")
        self.type("Edited ")
        with preview.LOCK:  # a build of an earlier save finished while the caret was in the text
            preview.STATE["manuscript"]["version"] += 1
        self.leave()
        self.wait_for("document.querySelector('.pv-save-state').textContent === 'Saved' && !saves")
        # The watcher has not caught up with this save: that build lacks it, so no reload yet.
        time.sleep(2.5)  # two polls of the page's status
        self.assertTrue(self.evaluate("window.samePage === true"))
        with preview.LOCK:
            preview.STATE["manuscript"]["version"] += 1
            preview.WATCHER["synced"] = time.time()
        self.wait_for("window.samePage === undefined")


if __name__ == "__main__":
    unittest.main()
