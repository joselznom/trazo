"""Viewer regressions in Chrome: the page shell (first paint, theme, views, live reload, layout)."""

import json
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)
from _preview_browser import PreviewBrowserTestCase

from trazo import preview


class ShellTests(PreviewBrowserTestCase):
    def test_viewer_scripts_load_without_errors(self):
        # The parts share their globals: one that uses a later part's name
        # while loading throws here.
        self.chrome.call("Page.enable", None, self.session)
        self.chrome.call("Page.addScriptToEvaluateOnNewDocument", {"source": """
          window.scriptErrors = [];
          addEventListener('error', e => scriptErrors.push(`${e.message} (${e.filename}:${e.lineno})`));
          addEventListener('unhandledrejection', e => scriptErrors.push(String(e.reason)));"""}, self.session)
        for query in ("", "?view=figures"):
            with self.subTest(query=query):
                self.open(query)
                self.assertEqual(self.evaluate("scriptErrors"), [])

    def test_without_the_model_script_only_the_reload_note_shows(self):
        page = preview.OUT_DIR / "manuscript.html"
        page.write_text(page.read_text().replace('<script src="/__assets/preview/preview-model.js"></script>', ""))
        self.chrome.call("Page.navigate", {"url": self.url}, self.session)
        self.wait_for("document.readyState === 'complete'")
        self.assertEqual(self.evaluate("document.body.firstElementChild.textContent"),
                         "Viewer scripts did not load; reload the page.")
        self.assertFalse(self.evaluate("!!document.querySelector('.pv-app-root')"))

    def test_initial_shell_waits_without_disabling_native_transitions(self):
        from trazo import server
        self.open()
        self.chrome.call("Page.enable", None, self.session)
        decorate = server.decorate_page
        get = preview.Handler.do_GET
        for native in (False, True):
            with self.subTest(native_render_blocking=native):
                release = threading.Event()

                def delayed_get(handler):
                    if urlsplit(handler.path).path == "/__assets/preview/" + preview.VIEWER_SCRIPTS[0]:
                        release.wait(15)
                    get(handler)

                def decorate_for_browser(page, counts=None, stamp=False):
                    rendered = decorate(page, counts, stamp)
                    return rendered if native else rendered.replace(b'rel="expect"', b'rel="unsupported-expect"')

                # Without rel=expect, layout and rendering still run in the
                # real browser while the page script is held by the server.
                script = self.chrome.call("Page.addScriptToEvaluateOnNewDocument", {"source": """
                  window.reveal = null;
                  window.addEventListener('pagereveal', e => {
                    const seen = window.reveal = {shell: !!document.getElementById('pk-shell-ready'),
                                                  transition: !!e.viewTransition};
                    if (e.viewTransition) e.viewTransition.ready.then(() => Object.assign(seen, {
                      outcome: 'ran', visible: getComputedStyle(document.body).visibility === 'visible',
                      built: !!document.querySelector('.pv-app-root')}), () => { seen.outcome = 'skipped'; });
                  });
                """}, self.session)["identifier"]
                try:
                    with patch.object(preview.Handler, "do_GET", delayed_get), \
                            patch.object(server, "decorate_page", decorate_for_browser):
                        try:
                            self.evaluate("location.href = '/supplementary.html?paint=' + Math.random()")
                            self.wait_for("location.pathname === '/supplementary.html' && window.Trazo && "
                                          "document.querySelector('#intro') && !document.querySelector('.pv-app-root')")
                            if not native:
                                self.assertEqual(self.evaluate("getComputedStyle(document.body).visibility"), "hidden")
                        finally:
                            release.set()
                        self.wait_for("document.readyState === 'complete' && document.querySelector('.pv-app-root')")
                        self.assertEqual(self.evaluate("""({visibility: getComputedStyle(document.body).visibility,
                            opacity: getComputedStyle(document.body).opacity,
                            animations: document.body.getAnimations().length})"""),
                            {"visibility": "visible", "opacity": "1", "animations": 0})
                        if native:
                            # The fade runs into the built shell. A loaded machine can reveal the
                            # page before the shell marker is parsed; app.js then skips the fade.
                            self.wait_for("window.reveal && (window.reveal.outcome || !window.reveal.transition)")
                            seen = self.evaluate("window.reveal")
                            self.assertEqual(seen, {"shell": True, "transition": True, "outcome": "ran",
                                                    "visible": True, "built": True} if seen["shell"] else
                                             {"shell": False, "transition": True, "outcome": "skipped"})
                finally:
                    self.chrome.call("Page.removeScriptToEvaluateOnNewDocument", {"identifier": script}, self.session)

    def test_theme_preferences_follow_changes_and_resets_in_other_tabs(self):
        self.open()
        peers = []
        for path, ready in (("/supplementary.html", ".pv-bs-label"),
                            ("/tables", "#te-theme"), ("/diagrams", "#home")):
            target = self.chrome.call("Target.createTarget", {
                "url": "about:blank", "browserContextId": self.context})["targetId"]
            session = self.chrome.call("Target.attachToTarget", {
                "targetId": target, "flatten": True})["sessionId"]
            self.chrome.call("Page.navigate", {"url": self.url.replace("/manuscript.html", path)}, session)
            self.wait_for(f"document.querySelector({json.dumps(ready)}) && document.readyState === 'complete'", session)
            peers.append(session)
        themed = "document.documentElement.dataset.theme === 'dark' && document.documentElement.dataset.palette === 'polar'"
        reset = "!document.documentElement.hasAttribute('data-theme') && !document.documentElement.hasAttribute('data-palette')"
        for operation in ("removeItem('trazo-preview')", "clear()"):
            self.evaluate("""document.querySelector('.pv-theme').click();
                document.querySelector('.pv-tm-item[data-theme=dark]').click();
                document.querySelector('.pv-tm-item[data-palette=polar]').click();
                document.querySelector('.pv-theme').click();""")
            for session in peers:
                self.wait_for(themed, session)
            accents = [self.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--pv-accent').trim()", session)
                       for session in (self.session, *peers)]
            self.assertEqual(len(set(accents)), 1)
            for session in (self.session, *peers):
                self.assertIn("IBM Plex Sans", self.evaluate("getComputedStyle(document.body).fontFamily", session))
            # Saving an unrelated preference must preserve the shared theme.
            self.evaluate("document.querySelector('.pv-zoom-in').click()", peers[0])
            self.assertEqual(self.evaluate("JSON.parse(localStorage.getItem('trazo-preview')).palette"), "polar")
            self.evaluate(f"localStorage.{operation}", peers[0])
            for session in (self.session, *peers[1:]):
                self.wait_for(reset, session)

    def test_shared_navigation_returns_to_the_original_figure(self):
        self.open("?view=figures")
        self.wait_for("document.querySelector('.pv-fcard a[href^=\"/diagrams?\"]')")
        link = self.evaluate("document.querySelector('.pv-fcard a[href^=\"/diagrams?\"]').href")
        self.chrome.call("Page.navigate", {"url": link}, self.session)
        self.wait_for("document.querySelector('#back')?.getAttribute('href')?.includes('fig:diagram')")
        self.assertEqual(self.evaluate("document.querySelector('#back').getAttribute('href')"),
                         "/manuscript.html?view=figures#fig:diagram")
        self.assertTrue(self.evaluate("!!document.querySelector('.pk-theme')"))

    def test_pages_have_no_workspace_menu_or_review_page(self):
        self.open()
        self.assertFalse(self.evaluate("!!document.querySelector('.pk-nav, .pk-menu')"))
        self.assertTrue(self.evaluate("!!document.querySelector('.pv-theme')"))
        self.assertEqual(self.evaluate("fetch('/review').then(r => r.status)"), 404)
        # The comment API stays for the CLI and the editors.
        self.assertEqual(self.evaluate("fetch('/__review').then(r => r.status)"), 200)

    def test_view_changes_fade_and_respect_reduced_motion(self):
        self.open()
        for motion in ("no-preference", "reduce"):
            with self.subTest(motion=motion):
                self.chrome.call("Emulation.setEmulatedMedia", {"features": [
                    {"name": "prefers-reduced-motion", "value": motion}]}, self.session)
                result = self.evaluate('''(() => {
                  document.querySelector('[data-view=figures]').click();
                  const figures = document.querySelector('.pv-figsview');
                  const animated = figures.getAnimations().some(a => a.playState === 'running');
                  const visible = !figures.hidden;
                  document.querySelector('.pv-tabs-top [data-doc=manuscript]').click();
                  const manuscript = document.querySelector('.pv-docview');
                  return {visible, returned: !manuscript.hidden,
                    animated, returnAnimated: manuscript.getAnimations().some(a => a.playState === 'running'),
                    shellVisible: getComputedStyle(document.body).visibility === 'visible'};
                })()''')
                self.assertTrue(result["visible"] and result["returned"] and result["shellVisible"])
                self.assertEqual(result["animated"], motion != "reduce")
                self.assertEqual(result["returnAnimated"], motion != "reduce")

    def test_live_reload_waits_for_a_draft_then_restores_reading_position_and_theme(self):
        page = preview.OUT_DIR / "manuscript.html"
        page.write_text(page.read_text().replace('<p>Another paragraph.</p>',
            '<p>Another paragraph.</p>' + '<p>More manuscript text for scrolling.</p>' * 80))
        self.open()
        self.evaluate("document.fonts.ready")
        self.evaluate("document.querySelector('.pv-theme').click(); "
                      "document.querySelector('.pv-tm-item[data-theme=dark]').click(); "
                      "document.querySelector('.pv-theme').click(); "
                      "document.querySelector('.pv-scroller').scrollTop = 1200")
        before = self.evaluate("({time: performance.timeOrigin, scroll: document.querySelector('.pv-scroller').scrollTop})")
        self.select_second_paragraph()
        self.evaluate("document.querySelector('.pv-fab [data-select=comment]').click()")
        self.evaluate("const area = document.querySelector('.pv-editor textarea'); "
                      "area.value = 'Keep this new draft'; "
                      "area.dispatchEvent(new Event('input', {bubbles: true}))")
        with preview.LOCK:
            preview.STATE["manuscript"]["version"] += 1
        self.wait_for("!document.querySelector('.pv-hold').hidden")
        self.assertEqual(self.evaluate("performance.timeOrigin"), before["time"])
        self.assertEqual(self.evaluate("document.querySelector('.pv-editor textarea').value"), "Keep this new draft")
        self.evaluate("document.querySelector('.pv-editor [data-op=cancel]').click()")
        self.wait_for(f"performance.timeOrigin !== {before['time']} && document.querySelector('.pv-bs-label')?.textContent.length > 0")
        self.wait_for("document.readyState === 'complete'")
        self.assertAlmostEqual(self.evaluate("document.querySelector('.pv-scroller').scrollTop"), before["scroll"], delta=2)
        self.assertEqual(self.evaluate("document.documentElement.dataset.theme"), "dark")
        # The new page shows at once: it neither hides nor fades itself in.
        self.assertEqual(self.evaluate("""({visibility: getComputedStyle(document.body).visibility,
            opacity: getComputedStyle(document.body).opacity, animations: document.body.getAnimations().length})"""),
            {"visibility": "visible", "opacity": "1", "animations": 0})

    def test_reload_shows_the_last_build_status_and_figures_count_at_once(self):
        self.open()
        self.wait_for("!document.querySelector('[data-view=figures] .pv-badge').hidden")
        shown = """({label: document.querySelector('.pv-bs-label').textContent,
            dot: document.querySelector('.pv-build .pv-dot').className,
            count: document.querySelector('[data-view=figures] .pv-badge').hidden ? null
              : document.querySelector('[data-view=figures] .pv-badge').textContent})"""
        before = self.evaluate(shown)
        self.assertTrue(before["label"] and before["count"])
        # After the reload the server does not answer yet: what shows comes
        # from the previous page.
        self.chrome.call("Page.enable", None, self.session)
        self.chrome.call("Page.addScriptToEvaluateOnNewDocument", {"source": """
          (() => {
            const fetch = window.fetch;
            window.fetch = (url, options) => /^\\/(__|supplementary)/.test(String(url))
              ? new Promise(() => {}) : fetch(url, options);
          })();"""}, self.session)
        origin = self.evaluate("performance.timeOrigin")
        self.chrome.call("Page.reload", None, self.session)
        self.wait_for(f"performance.timeOrigin !== {origin} && document.readyState === 'complete'")
        self.assertEqual(self.evaluate(shown), before)

    def test_sidebar_controls_remain_inside_tablet_viewport(self):
        self.chrome.call("Emulation.setDeviceMetricsOverride", {
            "width": 800, "height": 800, "deviceScaleFactor": 1, "mobile": False}, self.session)
        self.open()
        self.evaluate("document.fonts.ready")
        bounds = self.evaluate("""(() => {
          const r = document.querySelector('[data-side=right]').getBoundingClientRect();
          return {left: r.left, right: r.right, width: innerWidth};
        })()""")
        self.assertGreaterEqual(bounds["left"], 0)
        self.assertLessEqual(bounds["right"], bounds["width"])


if __name__ == "__main__":
    unittest.main()
