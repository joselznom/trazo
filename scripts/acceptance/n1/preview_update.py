"""The preview's update notice, in a browser (docs/plugin.md check 9)."""

import json

from ..context import Context
from ..drive import run_hook
from ..registry import scenario
from .chrome import Browser, ChromeError, find_chrome
from .support import http_get, installed_version, mcp_session, new_paper, preview_record, serving, wait_until

PILL = "document.querySelector('.pv-update-pill')"
POPUP = "document.querySelector('.pv-update-pop')"
UPDATE_WAIT = 120  # seconds: setup.sh --offline reinstalls trazo, then the preview restarts


# Runs in every document the page opens. The "Updated to" card lives in one document, so a poll can
# miss it: an observer records that it was shown. It also counts loads and keeps the requests that failed.
PAGE_HOOK = """
window.__errs = [];
addEventListener('error', e => __errs.push((e.target.src || e.target.href || 'js') + ' ' + (e.message || '')), true);
sessionStorage.setItem('__loads', String(Number(sessionStorage.getItem('__loads') || 0) + 1));
new MutationObserver(() => {
  const pop = document.querySelector('.pv-update-pop');
  if (pop && !pop.hidden && pop.textContent.startsWith('Updated to')) sessionStorage.setItem('__shown', pop.textContent);
}).observe(document, {subtree: true, childList: true, characterData: true, attributes: true});
"""
SHOWN = "sessionStorage.getItem('__shown') || ''"
LOADS = "sessionStorage.getItem('__loads')"
PAGE_STATE = f"""JSON.stringify({{href: location.href, notReloaded: window.notReloaded, loads: {LOADS},
  navigation: performance.getEntriesByType('navigation').map(n => n.type + ':' + n.responseStatus).join(),
  viewerLoaded: typeof window.PreviewModel, loadErrors: window.__errs, stored: sessionStorage.getItem('trazo-updated'),
  shown: {SHOWN}, popup: {POPUP} && {POPUP}.innerText, popupHidden: {POPUP} && {POPUP}.hidden}})"""


def paper_template(paper) -> str:
    return json.loads((paper / ".trazo" / "manifest.json").read_text(encoding="utf-8"))["template"]


@scenario("n1-preview-update", needs=("darwin", "seed"))
def preview_update(ctx: Context) -> None:
    sb, last, head = ctx.sandbox, ctx.last_release(), ctx.checkout()
    sb.install(last)
    old = mcp_session(sb, sb.plugin(last))
    paper = new_paper(old, sb.work / "paper")
    assert old.close()[0] == 0
    assert paper_template(paper) == f"v{last.version}", paper_template(paper)

    # The app updated the plugin; the install and the paper are still the last release's.
    mcp = mcp_session(sb, sb.plugin(head))
    started = mcp.call("preview_start", project=str(paper))
    assert not started.is_error, started.text
    info = started.data
    chrome = Browser(sb, find_chrome(sb))
    try:
        page = chrome.page()
        chrome.call("Page.enable", {}, page.session)  # the script below is ignored without it
        chrome.call("Page.addScriptToEvaluateOnNewDocument", {"source": PAGE_HOOK}, page.session)
        page.goto(info["url"] + "/")
        page.wait_for(f"{PILL} && !{PILL}.hidden", 60)
        assert page.evaluate(f"{PILL}.textContent") == head.version
        ctx.evidence.note(f"the pill shows {head.version} on {info['url']} (install {last.version})")

        page.click(".pv-update-pill")
        page.wait_for(f"{POPUP} && !{POPUP}.hidden")
        assert f"trazo {head.version}" in page.evaluate(f"{POPUP}.innerText"), page.evaluate(f"{POPUP}.innerText")
        page.evaluate("window.notReloaded = true")  # gone after the page reloads
        page.click(".pv-update-pop button", "Update")
        try:
            page.wait_for(f"window.notReloaded === undefined || ({POPUP} && /^Updat/.test({POPUP}.innerText))", 10)
        except ChromeError as exc:
            raise ChromeError(f"the click on Update did not start the update; page: {page.evaluate(PAGE_STATE)}") from exc
        try:
            page.wait_for(f"window.notReloaded === undefined && ({SHOWN}).startsWith('Updated to {head.version}.')", UPDATE_WAIT)
        except ChromeError as exc:
            asked = page.evaluate("fetch('/__updates', {signal: AbortSignal.timeout(5000)}).then(r => r.status, e => String(e))")
            raise ChromeError(f"{exc}\npage: {page.evaluate(PAGE_STATE)}\n"
                              f"/__updates from the page: {asked}, from the harness: {http_get(info['url'] + '/__updates')[0]}") from exc
        assert page.evaluate("location.origin") == info["url"], f"the page moved to {page.evaluate('location.origin')}"
        ctx.evidence.note(f"after Update the page reloaded on {info['url']}: "
                          f"{page.evaluate(SHOWN)!r}, loads: {page.evaluate(LOADS)}")
    finally:
        chrome.close()

    assert installed_version(sb) == head.version, f"trazo {installed_version(sb)} is installed"
    assert paper_template(paper) == f"v{head.version}", f"the paper is at {paper_template(paper)}"
    record = preview_record(paper)
    assert record["port"] == info["port"], f"the preview moved from port {info['port']} to {record['port']}"
    # The page can show the result before the server restarts (a live reload after the paper's rebuild
    # does), and the restart keeps the pid, so wait for the new trazo to answer rather than read it once.
    def status() -> dict:
        try:
            return json.loads(http_get(info["url"] + "/__updates")[1])
        except ValueError:
            return {}
    assert wait_until(lambda: status().get("versions", {}).get("running") == head.version, 30), (
        f"the preview still runs trazo {status().get('versions', {}).get('running')} at {record['url']}; "
        f"preview.log ends:\n{(paper / '.build' / 'preview.log').read_text(errors='replace')[-800:]}")
    assert wait_until(lambda: serving(record), 30), f"the restarted preview does not answer at {record['url']}"
    assert status()["action"] is None, status()
    assert run_hook(sb, sb.plugin(head)).context is None, "the hook still asks for an update"
    assert mcp.close()[0] == 0
