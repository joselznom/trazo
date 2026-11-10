"""The preview takes a new build of its document in place: in Chrome, against
the real handler and real builds."""

import base64
import unittest

import _support
from _mcp_app_view import PNG
from _preview_browser import PreviewBrowserTestCase
from _preview_writing import TEXT, WritingCase

from trazo import preview

# What a fresh load and an in-place update must agree on: the page as the viewer
# made it (white space, comments and the order of classes aside), its labels,
# index, counts and export sections.
SNAPSHOT = r"""(() => {
  const html = page.innerHTML.replace(/<!--[\s\S]*?-->/g, "").replace(/>\s+</g, "><")
    .replace(/ style="[^"]*"/g, "").replace(/ class="([^"]*)"/g, (m, c) => ` class="${c.split(" ").sort().join(" ")}"`);
  return { html, labels: model.entries.map((e) => e.label), index: leftBody.innerText,
    outline: outlineHeads.map((x) => `${x.text}:${x.words}`), counts: JSON.stringify(COUNTS),
    writable: [...blockMark.values()].filter(Boolean).length,
    exports: exportSections.map((s) => `${s.id}:${s.text}`) };
})()"""


class InPlaceTests(WritingCase):
    maxDiff = None

    def assert_same_as_a_fresh_load(self):
        shown = self.evaluate(SNAPSHOT)
        self.open()
        fresh = self.evaluate(SNAPSHOT)
        self.assertEqual({k: v for k, v in shown.items() if k != "html"}, {k: v for k, v in fresh.items() if k != "html"})
        self.assertEqual(shown["html"], fresh["html"])

    def test_changed_blocks_and_the_numbers_after_them_change_in_place(self):
        self.build()
        self.evaluate("window.samePage = true")
        start = self.evaluate("scroller.scrollTop")
        self.built_again(TEXT.replace(
            "A second paragraph with plain words in it.",
            "A new paragraph [@smith2020].\n\nA second paragraph with plain words in it."))
        self.wait_for("!!document.querySelector('[data-label=\"Writing ¶3\"]')")
        self.assertTrue(self.evaluate("window.samePage === true"))
        self.assertEqual(self.evaluate("scroller.scrollTop"), start)
        self.assert_same_as_a_fresh_load()

    def test_the_paragraph_left_takes_its_build_in_place(self):
        self.build()
        self.evaluate("window.samePage = true")
        self.write_in("plain")
        self.type("very ")
        self.leave()
        self.wait_for("saves === 0")
        version = self.built_again(self.source())
        self.wait_for(f"loaded?.manuscript === {version}")
        self.assertTrue(self.evaluate("window.samePage === true"))
        self.assertIn("very", self.evaluate("document.querySelector('.pv-page').textContent"))
        self.assert_same_as_a_fresh_load()

    def test_new_values_reach_the_page_and_its_value_tips(self):
        values = _support.ROOT / "data/values.json"
        self.keep_files(values)
        self.build()
        self.assertEqual(self.evaluate("loadValues().then((v) => v['cohort.n'].text)"), "120")
        values.write_text(values.read_text().replace('"value": 120, "text": "120"', '"value": 121, "text": "121"'))
        version = self.built_again(TEXT)
        self.wait_for(f"loaded?.manuscript === {version}")
        self.assertIn("121 fields", self.evaluate("document.querySelector('.pv-page').textContent"))
        self.assertEqual(self.evaluate("loadValues().then((v) => v['cohort.n'].text)"), "121")

    def test_a_new_section_reloads_the_page(self):
        self.build()
        self.evaluate("window.samePage = true")
        self.built_again(TEXT + "\n## Another part\n\nIts words.\n")
        self.wait_for("window.samePage === undefined")
        self.wait_for("!!document.querySelector('[data-label=\"Another part ¶1\"]')")


class ImageTests(PreviewBrowserTestCase):
    def test_an_image_rebuilt_at_the_same_path_reloads_the_page(self):
        image = _support.ROOT / "figures/external/ordinary.png"
        self.keep_files(image)
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(base64.b64decode(PNG))
        self.open()
        self.evaluate("window.samePage = true")
        image.write_bytes(base64.b64decode(PNG) + b"\0")  # a new build of it: the page's HTML is the same
        with preview.LOCK:
            preview.STATE["manuscript"]["version"] += 1
        self.wait_for("window.samePage === undefined")


if __name__ == "__main__":
    unittest.main()
