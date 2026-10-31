"""The preview's MCP App view (assets/mcp-app) in Chrome, as a
host draws it (see _mcp_app_view)."""

import base64
from pathlib import Path

import _support
from _mcp_app_view import PNG, McpAppViewTestCase

from trazo import preview


class McpAppViewTests(McpAppViewTestCase):
    def test_the_view_shows_the_page_through_the_tool(self):
        image = Path(_support.ROOT) / "figures" / "external" / "view-test.png"
        self.keep_files(image)
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(base64.b64decode(PNG))
        self.host(sandbox=False)
        self.wait_frame("d?.querySelector('.pv-bs-label')?.textContent.length > 0")
        calls = self.evaluate("window.calls")
        self.assertEqual(calls[:2], ["ui/initialize", "ui/notifications/initialized"])
        # The host started it inline; the paper shows only full screen.
        self.assertLess(calls.index("ui/request-display-mode"), calls.index("preview_request /__bundle?page=%2F"))
        self.assertEqual(self.frame("v.document.documentElement.style.height"), "100vh")
        # The page's own requests (its status poll) go through the tool.
        self.wait_for("window.calls.includes('preview_request /__workspace')")
        self.assertEqual(self.frame("v.TRAZO_VIEW.path"), "/manuscript.html")
        self.wait_frame("[...d.fonts].filter(f => f.status === 'loaded').length > 0")
        self.assertGreaterEqual(self.frame("d.fonts.size"), 16)

        # An image the page adds later becomes a data: URL.
        self.frame("(() => { const i = d.createElement('img'); i.id = 'late'; "
                   "i.setAttribute('src', 'figures/external/view-test.png'); d.body.append(i); })()")
        self.wait_frame("d.getElementById('late').getAttribute('src').startsWith('data:image/png;base64,')")

        # A local link opens the other document in the view; another site's goes to the host.
        other = list(preview.DOCS)[1]
        self.frame(f"(() => {{ const a = d.createElement('a'); a.href = '/{other}.html?view=figures'; "
                   "a.textContent = 'x'; d.body.append(a); a.click(); })()")
        self.wait_frame(f"v.TRAZO_VIEW.path === '/{other}.html' && d?.querySelector('.pv-bs-label')")
        # The page reads the query from the view; its figures view opened.
        self.assertEqual(self.frame("w.Trazo.search()"), "?view=figures")
        self.wait_frame("d.querySelector('.pv-fcard')")
        self.frame("(() => { const a = d.createElement('a'); a.href = 'https://example.org/x'; "
                   "d.body.append(a); a.click(); })()")
        self.wait_for("window.calls.includes('ui/open-link')")
        self.assertEqual(self.frame("v.TRAZO_VIEW.path"), f"/{other}.html")

        # Back inline, the view is a card that loads nothing; it reopens the same page.
        self.evaluate("sendContext({displayMode: 'inline'})")
        self.wait_frame("!w && !v.document.getElementById('trazo-view-open').hidden")
        self.assertIn("ui/notifications/size-changed", self.evaluate("window.calls"))
        polls = self.evaluate("window.calls.length")
        self.evaluate("new Promise(r => setTimeout(r, 2500))")
        self.assertLessEqual(self.evaluate("window.calls.length") - polls, 1)
        self.frame("v.document.getElementById('trazo-view-open').click()")
        self.wait_frame(f"v.TRAZO_VIEW.path === '/{other}.html' && d?.querySelector('.pv-fcard')")

        # Once a newer view of the paper is open, this one stops and says so.
        self.evaluate("window.superseded = true")
        self.wait_frame("v.document.getElementById('trazo-view-note').textContent.includes('newer preview') && !w")
        polls = self.evaluate("window.calls.length")
        self.evaluate("new Promise(r => setTimeout(r, 2500))")
        self.assertLessEqual(self.evaluate("window.calls.length") - polls, 1)

    def test_pages_keep_what_a_browser_keeps(self):
        figures = Path(_support.ROOT) / "figures" / "external"
        diagram, late, svg = figures / "example.png", figures / "view-test.png", figures / "view-test.svg"
        self.keep_files(diagram, late, svg)
        figures.mkdir(parents=True, exist_ok=True)
        diagram.write_bytes(base64.b64decode(PNG))
        late.write_bytes(b"first")
        svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>', encoding="utf-8")
        self.host(sandbox=False)
        self.wait_frame("d?.querySelector('.pv-bs-label')?.textContent.length > 0")
        # An inlined figure still links to its diagram.
        self.assertTrue(self.frame("d.querySelector('figure img[data-trazo-src=\"figures/external/example.png\"]')"
                                   ".getAttribute('src').startsWith('data:image/png;base64,')"))
        self.wait_frame("d.querySelector('.pv-diagram a')")
        add = ("(() => {{ const i = d.createElement('img'); i.className = 'late'; "
               "i.setAttribute('src', '{}'); d.body.append(i); }})()")
        # Images added later become data: URLs, SVG ones too, and a new page fetches them again.
        self.frame(add.format("figures/external/view-test.svg"))
        self.wait_frame("d.querySelector('img.late').getAttribute('src').startsWith('data:image/svg+xml')")
        self.frame(add.format("figures/external/view-test.png"))
        self.wait_frame("d.querySelectorAll('img.late')[1].getAttribute('src') === 'data:image/png;base64,' + btoa('first')")
        late.write_bytes(b"second")
        self.frame("v.TRAZO_VIEW.reload()")
        self.wait_frame("d && !d.querySelector('img.late') && d.querySelector('.pv-bs-label')?.textContent.length > 0")
        self.frame(add.format("figures/external/view-test.png"))
        self.wait_frame("d.querySelector('img.late').getAttribute('src') === 'data:image/png;base64,' + btoa('second')")

        other = list(preview.DOCS)[1]
        link = ("(() => {{ const a = d.createElement('a'); a.href = '/{}.html'; {} d.body.append(a); a.click(); }})()")
        pause = "new Promise(r => setTimeout(r, 800))"
        # A link the page handles itself stays on the page.
        self.frame(link.format(other, "a.addEventListener('click', e => e.preventDefault());"))
        self.evaluate(pause)
        self.assertEqual(self.frame("v.TRAZO_VIEW.path"), "/manuscript.html")
        # With unsaved edits the user may stay; leaving runs the page's beforeunload.
        self.frame("(() => { w.addEventListener('beforeunload', e => { v.left = true; e.preventDefault(); }); "
                   "w.confirm = () => false; })()")
        self.frame(link.format(other, ""))
        self.evaluate(pause)
        self.assertTrue(self.frame("v.left"))
        self.assertEqual(self.frame("v.TRAZO_VIEW.path"), "/manuscript.html")
        # A page that fails to load leaves the one shown, and its edits, in place.
        self.evaluate("window.failBundle = 1")
        self.frame("w.confirm = () => true")
        self.frame(link.format(other, ""))
        self.wait_frame("v.document.getElementById('trazo-view-toast')?.textContent.includes('down')")
        self.assertEqual(self.frame("[v.TRAZO_VIEW.path, !!d.querySelector('.pv-bs-label')]"), ["/manuscript.html", True])
        # Also when the view leaves full screen, keeping the page hidden, while it loads.
        self.evaluate("window.failBundle = 1500")
        self.frame(link.format(other, ""))
        self.evaluate("sendContext({displayMode: 'inline'})")
        self.wait_frame("v.document.getElementById('trazo-view-note').textContent.includes('down')")
        self.evaluate("sendContext({displayMode: 'fullscreen'})")
        self.wait_frame("w && !v.document.querySelector('iframe').hidden")
        self.assertEqual(self.frame("[v.TRAZO_VIEW.path, !!d.querySelector('.pv-bs-label')]"), ["/manuscript.html", True])
        self.evaluate("window.failBundle = 0")
        self.frame(link.format(other, ""))
        self.wait_frame(f"v.TRAZO_VIEW.path === '/{other}.html' && d?.querySelector('.pv-bs-label')")

        # A link to a place in the page moves within it: the frame's URL takes the fragment.
        self.frame("(() => { const a = d.createElement('a'); a.href = '#intro'; d.body.append(a); a.click(); })()")
        self.wait_frame("w.location.href === 'about:srcdoc#intro'")
        self.assertTrue(self.frame("!!d.querySelector('.pv-bs-label')"))

        # The table editor opens with a return address, which it resolves without an origin;
        # it moves to its first table with a fragment.
        self.frame("v.TRAZO_VIEW.assign('/tables?return=%2Fmanuscript.html')")
        self.wait_frame("w?.location.href.startsWith('about:srcdoc#') && d.querySelector('thead th')")

    def test_a_host_without_full_screen_gets_a_card_with_the_address(self):
        self.host(sandbox=False, modes=("inline",))
        self.wait_frame("v.document.getElementById('trazo-view-note')?.textContent.includes('Open it in a browser: ')")
        self.assertTrue(self.frame("v.document.getElementById('trazo-view-open').hidden"))
        self.assertFalse(any(call.startswith("preview_request") for call in self.evaluate("window.calls")))

    def test_a_frame_with_no_origin_of_its_own_says_where_to_open_the_preview(self):
        # Sandboxed without allow-same-origin, the page's frame gets yet another
        # origin, which the view cannot reach; the view says so and tells the host.
        self.host(sandbox=True)
        self.wait_for("window.calls.includes('preview_request /__bundle?page=%2F')")
        self.wait_for("window.calls.includes('notifications/message')")
        self.assertNotIn("preview_request /__workspace", self.evaluate("window.calls"))
