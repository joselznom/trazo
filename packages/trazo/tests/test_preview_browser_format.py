"""Bold and italic while writing, in Chrome: real keys and the bar against the real page and server."""

import json
import unittest
from unittest.mock import patch

from _preview_writing import WritingCase

from trazo import preview

META = 4  # the modifier bit of Cmd in Input.dispatchKeyEvent


class FormatTests(WritingCase):
    def chord(self, key, code):
        """Cmd, or Ctrl where Chrome sends it, with a key."""
        for kind in ("keyDown", "keyUp"):
            self.chrome.call("Input.dispatchKeyEvent", {"type": kind, "key": key, "code": f"Key{key.upper()}", "modifiers": META,
                                                        "windowsVirtualKeyCode": code}, self.session)

    def select(self, word):
        self.mouse_double_click(*self.word_at(word))
        self.wait_for("document.querySelector('.pv-writing') && !getSelection().isCollapsed")

    def saved(self, text):
        self.wait_for("document.querySelector('.pv-save-state').textContent === 'Saved' && !saves")
        self.assertEqual(self.source(), text)

    def test_cmd_b_makes_the_selection_bold_and_again_takes_it_off(self):
        self.build("A quick brown fox.\n")
        self.select("quick")
        self.chord("b", 66)
        self.wait_for("document.querySelector('.pv-writing strong')?.textContent === 'quick'")
        self.saved("A **quick** brown fox.\n")
        self.assertEqual(self.evaluate("getSelection().toString()"), "quick")
        self.chord("b", 66)
        self.wait_for("!document.querySelector('.pv-writing strong')")
        self.saved("A quick brown fox.\n")

    def test_cmd_i_on_part_of_a_bold_leaves_the_rest_as_it_was(self):
        self.build("A **quick brown** fox.\n")
        self.select("quick")
        self.chord("i", 73)
        self.wait_for("document.querySelector('.pv-writing strong em')?.textContent === 'quick'")
        self.saved("A ***quick* brown** fox.\n")

    def test_the_bar_offers_bold_and_italic_for_a_selection_in_a_paragraph(self):
        self.build("A quick brown fox.\n")
        self.select("quick")
        self.wait_for("document.querySelector('.pv-fab [data-select=strong]')")
        self.click_text(".pv-fab [data-select=em]")
        self.wait_for("document.querySelector('.pv-writing em')?.textContent === 'quick'")
        self.saved("A *quick* brown fox.\n")

    def test_cmd_u_underlines_the_selection_for_word_and_again_takes_it_off(self):
        self.build("A quick brown fox.\n")
        self.select("quick")
        self.chord("u", 85)
        self.wait_for("document.querySelector('.pv-writing u')?.textContent === 'quick'")
        self.saved("A [quick]{.underline} brown fox.\n")
        self.chord("u", 85)
        self.wait_for("!document.querySelector('.pv-writing u')")
        self.saved("A quick brown fox.\n")

    def test_the_bar_sets_the_formats_apart_and_presses_the_ones_the_selection_has(self):
        self.build("A **quick** brown fox.\n")
        self.select("quick")
        self.wait_for("document.querySelector('.pv-fab [data-select=underline]')")
        bar = """Array.from(document.querySelectorAll('.pv-fab > *'),
          (el) => el.matches('.pv-fab-sep') ? '|' : el.textContent + (el.getAttribute('aria-pressed') === 'true' ? '*' : ''))"""
        self.assertEqual(self.evaluate(bar), ["Comment", "Highlight", "|", "B*", "I", "U"])
        self.click_text(".pv-fab [data-select=underline]")
        self.wait_for("document.querySelector('.pv-fab [data-select=underline]').getAttribute('aria-pressed') === 'true'")
        self.saved("A **[quick]{.underline}** brown fox.\n")
        self.assertEqual(self.evaluate(bar), ["Comment", "Highlight", "|", "B*", "I", "U*"])

    def test_what_was_typed_is_saved_with_the_format(self):
        self.build("A quick brown fox.\n")
        self.write_in("brown")
        self.type("very ")
        self.evaluate("""(() => {
          const text = document.querySelector('.pv-writing .pv-src').nextSibling;
          const range = document.createRange();
          range.setStart(text, 2); range.setEnd(text, 7);
          const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
        })()""")
        self.chord("b", 66)
        self.wait_for("document.querySelector('.pv-writing strong')")
        self.wait_for("!saves")
        self.assertIn("**quick**", self.source())
        self.assertIn("very ", self.source())

    def test_taking_bold_off_leaves_the_italic_it_had_inside(self):
        self.build("A ***quick brown*** fox.\n")
        self.select("quick")
        self.chord("b", 66)
        self.wait_for("!saves && document.querySelector('.pv-save-state').textContent === 'Saved'")
        self.assertEqual(self.source(), "A *quick* ***brown*** fox.\n")
        self.assertEqual(self.evaluate("""(() => {
          const walker = document.createTreeWalker(document.querySelector('.pv-writing'), NodeFilter.SHOW_TEXT);
          while (walker.nextNode()) if (walker.currentNode.data.includes('quick'))
            return [!!walker.currentNode.parentElement.closest('em'), !!walker.currentNode.parentElement.closest('strong')];
        })()"""), [True, False])

    def test_no_format_is_sent_after_a_save_that_failed(self):
        text = "A quick brown fox jumps.\n"
        self.build(text)
        self.write_in("jumps")
        self.type("very ")
        self.evaluate("""(() => {
          const node = document.querySelector('.pv-writing .pv-src').nextSibling;
          const range = document.createRange();
          range.setStart(node, 2); range.setEnd(node, 7);
          const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
        })()""")
        with patch("trazo.preview_block_save.check", lambda *args: "it does not read the same"):
            self.chord("b", 66)
            self.wait_for("document.querySelector('.pv-write-bar') && !saves")
        self.assertEqual(self.source(), text)
        self.assertFalse(self.evaluate("!!document.querySelector('.pv-page strong')"))

    def test_a_style_stops_at_the_word_when_the_selection_takes_a_space(self):
        self.build("A quick brown fox.\n")
        self.select("quick")
        self.evaluate("""(() => {
          const node = document.querySelector('.pv-writing .pv-src').nextSibling;
          const range = document.createRange();
          range.setStart(node, 1); range.setEnd(node, 8);  // " quick "
          const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
        })()""")
        self.chord("b", 66)
        self.wait_for("document.querySelector('.pv-writing strong')?.textContent === 'quick'")
        self.saved("A **quick** brown fox.\n")

    def test_the_selection_is_counted_in_characters(self):
        self.build("A 😀🧪 quick brown fox.\n")
        self.select("quick")
        self.chord("b", 66)
        self.wait_for("document.querySelector('.pv-writing strong')?.textContent === 'quick'")
        self.saved("A 😀🧪 **quick** brown fox.\n")

    def test_saves_in_a_row_keep_the_chips_that_stay(self):
        self.build("A[@smith2020] and {{v:cohort.n}} end.\n")
        self.write_in("end")
        self.evaluate("""(() => {
          document.querySelector('.pv-writing .pv-atom').remove();
          const word = [...document.querySelector('.pv-writing').childNodes].filter(n => n.nodeType === 3).pop();
          const range = document.createRange();
          range.setStart(word, word.data.indexOf('end')); range.setEnd(word, word.data.indexOf('end') + 3);
          const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
        })()""")
        self.chord("b", 66)
        self.type("final")
        self.leave()
        self.wait_for("!saves && !document.querySelector('.pv-writing')")
        self.assertIn("{{v:cohort.n}}", self.source())
        self.assertNotIn("smith2020", self.source())

    def test_a_correction_after_a_format_is_saved_though_the_first_save_is_on_its_way(self):
        self.build("A quick brown end.\n")
        self.write_in("quick")
        self.type("x")
        self.evaluate("""(() => {
          const fetch = window.fetch;
          window.held = [];
          window.fetch = (url, options) => {
            if (!String(url).includes('/__block') || window.released) return fetch(url, options);
            return new Promise((resolve) => window.held.push(() => resolve(fetch(url, options))));
          };
          const node = [...document.querySelector('.pv-writing').childNodes].filter(n => n.nodeType === 3).pop();
          const range = document.createRange();
          range.setStart(node, 0); range.setEnd(node, 1);
          const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
        })()""")
        self.chord("b", 66)
        self.wait_for("window.held.length === 1")
        self.evaluate("""(() => {
          const node = [...document.querySelector('.pv-writing').childNodes].filter(n => n.nodeType === 3).pop();
          const at = node.data.indexOf('end');
          const range = document.createRange();
          range.setStart(node, at); range.setEnd(node, at + 3);
          const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
        })()""")
        self.type("final")
        self.leave()
        self.evaluate("window.released = true; window.held.splice(0).forEach((go) => go())")
        self.wait_for("saves === 0")
        self.assertIn("final", self.source())
        self.assertFalse(self.evaluate("!!document.querySelector('.pv-write-bar')"))

    def test_a_refused_format_says_so_and_leaves_the_text(self):
        text = "A **quick** fox.\n"
        self.build(text)
        self.select("fox")
        self.evaluate("""(() => {
          const page = document.querySelector('.pv-writing');
          const range = document.createRange();
          range.setStart(page.querySelector('strong').firstChild, 2);
          range.setEnd(page.querySelector('strong').nextSibling, 2);
          const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
        })()""")
        self.chord("i", 73)
        self.wait_for("document.querySelector('.pv-toast')?.textContent.startsWith('Not formatted')")
        self.assertEqual(self.source(), text)

    def test_cmd_b_on_a_selection_across_paragraphs_starts_no_build(self):
        self.build()
        with patch.object(preview, "start_docx_build") as build:
            start, end = self.word_at("quick"), self.word_at("plain")
            self.mouse("mousePressed", *start)
            self.mouse("mouseMoved", *end)
            self.mouse("mouseReleased", *end)
            self.wait_for("document.querySelector('.pv-fab').hidden === false")
            self.assertFalse(self.evaluate("!!document.querySelector('.pv-fab [data-select=strong]')"))
            self.chord("b", 66)
            self.evaluate("new Promise(resolve => setTimeout(resolve, 200))")
        build.assert_not_called()

    def drag_in_writing(self, word):
        start, end = self.evaluate(f"""(() => {{
          const word = {json.dumps(word)};
          const walker = document.createTreeWalker(document.querySelector('.pv-writing'), NodeFilter.SHOW_TEXT);
          while (walker.nextNode()) {{
            const at = walker.currentNode.data.indexOf(word);
            if (at < 0) continue;
            const range = document.createRange();
            range.setStart(walker.currentNode, at); range.setEnd(walker.currentNode, at + word.length);
            const box = range.getBoundingClientRect();
            return [[box.left + 1, box.top + box.height / 2], [box.right - 1, box.top + box.height / 2]];
          }}
        }})()""")
        self.mouse("mousePressed", *start)
        self.mouse("mouseMoved", *end)
        self.mouse("mouseReleased", *end)
        self.evaluate("new Promise(resolve => setTimeout(resolve, 100))")

    def submit_fab_comment(self, text):
        self.wait_for("document.querySelector('.pv-fab').hidden === false")
        self.click_text(".pv-fab [data-select=comment]")
        self.wait_for("document.querySelector('.pv-editor').hidden === false")
        self.evaluate(f"""(() => {{
          const area = document.querySelector('.pv-editor textarea');
          area.value = {json.dumps(text)};
          area.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Enter', metaKey: true, bubbles: true}}));
        }})()""")
        self.wait_for("document.querySelectorAll('.pv-card').length === 1")

    def test_selecting_again_after_bold_in_writing_mode_uses_fresh_text_nodes(self):
        text = (
            "Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho "
            "tau upsilon phi chi psi omega one two three four five six seven eight nine ten eleven "
            "twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty sigma end.\n"
        )
        self.build(text)
        self.select("epsilon")
        self.wait_for("document.querySelector('.pv-fab [data-select=strong]')")
        self.click_text(".pv-fab [data-select=strong]")
        self.wait_for("document.querySelector('.pv-writing strong')?.textContent === 'epsilon'")
        self.saved(text.replace("epsilon", "**epsilon**"))
        self.drag_in_writing("sigma")
        self.assertEqual(self.evaluate("window.__trazoTestErrors"), [])
        self.assertEqual(self.evaluate("getSelection().toString()"), "sigma")
        self.submit_fab_comment("Check sigma")
        added = preview.load_comments()[-1]
        self.assertEqual(added["quote"], "sigma")
        self.assertTrue(added["anchor"]["prefix"].endswith("twenty "))
        self.assertTrue(added["anchor"]["suffix"].startswith(" end."))

    def test_selecting_again_after_deleting_text_in_writing_mode_uses_fresh_text_nodes(self):
        self.build("First word and several extra words before the final target word here.\n")
        self.select("several")
        self.wait_for("document.querySelector('.pv-fab').hidden === false")
        self.key("Backspace", 8)
        self.wait_for("document.querySelector('.pv-fab').hidden === true")
        self.drag_in_writing("target")
        self.assertEqual(self.evaluate("window.__trazoTestErrors"), [])
        self.submit_fab_comment("Check target")
        added = preview.load_comments()[-1]
        self.assertEqual(added["quote"], "target")
        self.assertNotIn("several", added["anchor"]["text"])
        self.assertTrue(added["anchor"]["prefix"].endswith("final "))
        self.assertTrue(added["anchor"]["suffix"].startswith(" word"))


if __name__ == "__main__":
    unittest.main()
