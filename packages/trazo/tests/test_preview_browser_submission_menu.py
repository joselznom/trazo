"""Viewer regressions in Chrome: the Submission menu and its error badge (articles)."""

import json
import unittest
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)
from _preview_browser import PreviewBrowserTestCase

from trazo import preview


class SubmissionMenuTests(PreviewBrowserTestCase):
    def submission_parts(self):
        """Serve an article with a declarations, title page and cover letter page."""
        from trazo import counts, server
        from trazo.project import DEFAULTS, Project, merge
        root = preview.OUT_DIR
        for name in ("declarations", "cover-letter"):
            folder = root / "manuscript" / name
            folder.mkdir(parents=True)
            (folder / "source.md").write_text("Submission source.")
        project = Project(root, merge(DEFAULTS, {"submission": {
            "title_page": "separate", "declarations": "separate"}}), True)
        original = (root / "manuscript.html").read_text()
        for name in project.documents:
            preview.STATE.setdefault(name, dict(preview.STATE["manuscript"]))
            (root / f"{name}.html").write_text(original.replace(
                'window.PREVIEW_DOC = "manuscript";', f'window.PREVIEW_DOC = "{name}";').replace(
                json.dumps(preview.PROJECT.titles), json.dumps(project.titles)))
        for module in (counts, preview, server):
            patcher = patch.object(module, "PROJECT", project)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(preview, "DOCS", project.documents)
        patcher.start()
        self.addCleanup(patcher.stop)
        return project

    def test_submission_menu_and_error_badge_only_for_articles(self):
        asked = self.serve_report(self.REPORT)
        self.open()
        self.wait_for("!document.querySelector('.pv-subm-toggle .pv-badge').hidden")
        # The fixture has no submission documents: the menu holds the checklist only.
        self.assertEqual(self.evaluate("[...document.querySelectorAll('.pv-tabs-top > *')].map(a => a.dataset.doc || a.dataset.view || a.className)"),
                         ["manuscript", "supplementary", "figures", "pv-subm-toggle", "pv-subm-menu"])
        self.assertEqual(self.evaluate("[...document.querySelectorAll('.pv-subm-menu [role=menuitem]')].map(a => a.textContent)"),
                         ["Checklist"])
        self.assertEqual(self.evaluate("document.querySelector('.pv-subm-toggle .pv-badge').textContent"), "2")
        self.assertEqual(asked, [False])  # the badge needs no form fields
        self.serve_report({**self.REPORT, "errors": 0})
        self.open()
        self.evaluate("new Promise(resolve => setTimeout(resolve, 300))")
        self.assertTrue(self.evaluate("document.querySelector('.pv-subm-toggle .pv-badge').hidden"))
        from trazo import server
        from trazo.project import DEFAULTS, Project, merge
        report = Project(preview.PROJECT.root, merge(DEFAULTS, {"kind": "report"}), True)
        with patch.object(server, "PROJECT", report):
            self.open("?view=submission")
        self.assertFalse(self.evaluate("!!document.querySelector('.pv-subm-toggle, .pv-subm-menu, [data-view=submission]')"))
        self.assertEqual(self.evaluate("[document.querySelector('.pv-docview').hidden, document.querySelector('.pv-subm').hidden]"),
                         [False, True])

    def test_submission_menu_holds_the_checklist_and_submission_documents(self):
        self.serve_report(self.REPORT)
        self.submission_parts()
        self.open()
        self.wait_for("!document.querySelector('.pv-subm-toggle .pv-badge').hidden")
        tabs = "[...document.querySelectorAll('.pv-tabs-top > a')].map(a => a.dataset.doc || a.dataset.view)"
        self.assertEqual(self.evaluate(tabs), ["manuscript", "supplementary", "figures"])
        self.assertEqual(self.evaluate("""[...document.querySelectorAll('.pv-subm-menu [role=menuitem]')].map(a =>
            [a.firstChild.textContent, a.getAttribute('href')])"""),
            [["Checklist", "?view=submission"], ["Declarations", "/declarations.html"],
             ["Title page", "/title_page.html"], ["Cover letter", "/cover_letter.html"]])
        state = """[document.querySelector('.pv-subm-toggle').classList.contains('on'),
            [...document.querySelectorAll('.pv-tabs-top [aria-current=page]')].map(a => a.dataset.doc || a.dataset.view)]"""
        self.assertEqual(self.evaluate(state), [False, ["manuscript"]])
        # Opened from the keyboard: arrows move, Escape closes and gives the focus back.
        self.evaluate("document.querySelector('.pv-subm-toggle').focus()")
        self.key("ArrowDown", 40)
        self.wait_for("!document.querySelector('.pv-subm-menu').hidden")
        self.assertEqual(self.evaluate("[document.querySelector('.pv-subm-toggle').getAttribute('aria-expanded'), document.activeElement.textContent]"),
                         ["true", "Checklist"])
        self.key("ArrowDown", 40)
        self.assertEqual(self.evaluate("document.activeElement.dataset.doc"), "declarations")
        self.key("End", 35)
        self.assertEqual(self.evaluate("document.activeElement.dataset.doc"), "cover_letter")
        self.key("ArrowDown", 40)
        self.assertEqual(self.evaluate("document.activeElement.textContent"), "Checklist")
        self.key("Escape", 27)
        self.assertEqual(self.evaluate("[document.querySelector('.pv-subm-menu').hidden, document.activeElement.className, document.querySelector('.pv-subm-toggle').getAttribute('aria-expanded')]"),
                         [True, "pv-subm-toggle", "false"])
        # A click outside closes it; the checklist opens in this page.
        self.evaluate("document.querySelector('.pv-subm-toggle').click()")
        self.assertFalse(self.evaluate("document.querySelector('.pv-subm-menu').hidden"))
        self.evaluate("document.querySelector('.pv-scroller').click()")
        self.assertTrue(self.evaluate("document.querySelector('.pv-subm-menu').hidden"))
        self.evaluate("document.querySelector('.pv-subm-toggle').click(); document.querySelector('[data-view=submission]').click()")
        self.wait_for("location.search === '?view=submission' && !document.querySelector('.pv-subm').hidden")
        self.assertEqual(self.evaluate(state), [True, ["submission"]])
        self.assertTrue(self.evaluate("document.querySelector('.pv-subm-menu').hidden"))
        # A submission document's page selects the button and its item.
        self.evaluate("document.querySelector('.pv-subm-toggle').click(); document.querySelector('.pv-subm-menu [data-doc=cover_letter]').click()")
        # The shell's buttons exist before the viewer script marks the current tab; it has run once loaded.
        self.wait_for("location.pathname === '/cover_letter.html' && document.readyState === 'complete'")
        self.assertEqual(self.evaluate(state), [True, ["cover_letter"]])
        self.assertEqual(self.evaluate("document.querySelector('.pv-subm-toggle').getAttribute('aria-current')"), "true")
        self.evaluate("document.querySelector('[data-view=figures]').click()")
        self.wait_for("location.search === '?view=figures'")
        self.assertEqual(self.evaluate(state), [False, ["figures"]])
        # Narrow: the tabs scroll sideways; the menu stays on screen, under its button.
        self.chrome.call("Emulation.setDeviceMetricsOverride", {
            "width": 420, "height": 800, "deviceScaleFactor": 1, "mobile": False}, self.session)
        self.open()
        self.evaluate("document.querySelector('.pv-subm-toggle').scrollIntoView({inline: 'end'})")
        self.evaluate("new Promise(resolve => setTimeout(resolve, 200))")  # its scroll closes the menu
        self.evaluate("document.querySelector('.pv-subm-toggle').click()")
        self.assertFalse(self.evaluate("document.querySelector('.pv-subm-menu').hidden"))
        box = self.evaluate("""(() => { const m = document.querySelector('.pv-subm-menu').getBoundingClientRect(),
            b = document.querySelector('.pv-subm-toggle').getBoundingClientRect(),
            hit = document.elementFromPoint(m.left + m.width / 2, m.bottom - 10);
            return [m.left >= 0 && m.right <= innerWidth, Math.round(m.top - b.bottom), hit.closest('a').dataset.doc]; })()""")
        self.assertEqual(box, [True, 6, "cover_letter"])

    def test_index_nested_lists_keep_their_spacing_in_articles(self):
        # The Submission view once shared the index's ul.pv-sub class.
        page = preview.OUT_DIR / "manuscript.html"
        page.write_text(page.read_text().replace('<p>Another paragraph.</p>',
            '<section class="level2" id="sub"><h2>Subsection</h2><p>Another paragraph.</p></section>', 1))
        self.serve_report(self.REPORT)
        self.open()
        self.assertEqual(self.evaluate("""(s => [s.paddingTop, s.paddingLeft, s.maxWidth, s.marginLeft])(
            getComputedStyle(document.querySelector('.pv-tree ul.pv-sub')))"""), ["0px", "0px", "none", "13px"])
        self.assertTrue(self.evaluate("!!document.querySelector('.pv-subm[aria-label=Submission]')"))


if __name__ == "__main__":
    unittest.main()
