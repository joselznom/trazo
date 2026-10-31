"""A host for the preview's MCP App view (assets/mcp-app) in
Chrome: a srcdoc frame with SEP-1865's default CSP, a page that answers the
view's JSON-RPC and serves preview_request by fetching the real preview
handler, which trazo.mcp_app does over 127.0.0.1."""

import json

import _support
from _preview_browser import PreviewBrowserTestCase

from trazo import mcp_app

# What a host must use when a view declares no domains (SEP-1865, "Host Behavior").
CSP = ("default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; media-src 'self' data:; connect-src 'none';")
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="

HOST = """
window.calls = []; window.downloads = []; window.superseded = false;
const frame = document.createElement('iframe');
if (SANDBOX) frame.sandbox = 'allow-scripts';
frame.style.cssText = 'width:900px;height:700px;border:0';
frame.srcdoc = VIEW.replace('<head>', `<head><meta http-equiv="Content-Security-Policy" content="${CSP}">`);
const send = (m) => frame.contentWindow.postMessage({jsonrpc: '2.0', ...m}, '*');
const base64 = (buffer) => { let s = ''; for (const b of new Uint8Array(buffer)) s += String.fromCharCode(b); return btoa(s); };
async function forward(args) {
  if (window.superseded && args.view === 'first') return {superseded: true};
  if (window.failBundle && args.path.startsWith('/__bundle')) {
    await new Promise((r) => setTimeout(r, window.failBundle));
    return {status: 503, type: 'application/json', text: '{"error": "down"}'};
  }
  const r = await fetch(args.path, {method: args.method || 'GET', cache: 'no-store',
    headers: args.body === undefined ? {} : {'Content-Type': 'application/json'},
    body: args.body === undefined ? undefined : JSON.stringify(args.body)});
  const type = r.headers.get('Content-Type') || '';
  const text = /^(text\\/|application\\/json|application\\/javascript|image\\/svg)/.test(type);
  const filename = (r.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/)?.[1];
  return {status: r.status, type, ...(filename ? {filename} : {}),
    ...(text ? {text: await r.text()} : {base64: base64(await r.arrayBuffer())})};
}
window.addEventListener('message', async (e) => {
  if (e.source !== frame.contentWindow) return;
  const m = e.data; if (!m || m.jsonrpc !== '2.0' || !m.method) return;
  window.calls.push(m.method === 'tools/call' ? `${m.params.name} ${m.params.arguments.path || ''}` : m.method);
  const reply = (result) => send({id: m.id, result});
  if (m.method === 'ui/initialize') reply({protocolVersion: '2026-01-26', hostInfo: {name: 'test', version: '0'},
      hostCapabilities: {serverTools: {}, openLinks: {}, ...CAPABILITIES},
      hostContext: {displayMode: 'inline', availableDisplayModes: MODES, ...CONTEXT}});
  else if (m.method === 'ui/notifications/initialized') {
    send({method: 'ui/notifications/tool-input', params: {arguments: {project: PROJECT}}});
    send({method: 'ui/notifications/tool-result', params: {content: [], structuredContent: {project: PROJECT, view: 'first', url: location.origin}}});
  }
  else if (m.method === 'tools/call') reply({content: [], structuredContent: await forward(m.params.arguments), isError: false});
  else if (m.method === 'ui/download-file') { window.downloads.push(m.params); reply(window.downloadResult || {}); }
  else if (m.method === 'ui/request-display-mode') reply({mode: MODES.includes(m.params.mode) ? m.params.mode : 'inline'});
  else if (m.id !== undefined) reply({});
});
window.sendContext = (context) => send({method: 'ui/notifications/host-context-changed', params: context});
document.body.append(frame);
"""



class McpAppViewTestCase(PreviewBrowserTestCase):
    def host(self, sandbox, modes=("inline", "fullscreen"), capabilities=None, context=None):
        # A page of the preview's origin, so the host can fetch it like trazo.mcp_app does.
        self.chrome.call("Page.navigate", {"url": self.url.replace("/manuscript.html", "/__status")}, self.session)
        self.wait_for("document.readyState === 'complete'")
        view = mcp_app.read_resource(mcp_app.VIEW_URI)["contents"][0]["text"]
        consts = {"VIEW": view, "CSP": CSP, "SANDBOX": sandbox, "PROJECT": str(_support.ROOT), "MODES": list(modes),
                  "CAPABILITIES": capabilities or {}, "CONTEXT": context or {}}
        self.evaluate("document.body.replaceChildren();"
                      + "".join(f"var {key} = {json.dumps(value)};" for key, value in consts.items())
                      + HOST)

    # In expressions: v is the view's window, w and d the page's window and document.
    SCOPE = ("const v = document.querySelector('iframe').contentWindow, "
             "w = v.document.querySelector('iframe')?.contentWindow, d = w?.document;")

    def frame(self, expression):
        return self.evaluate(f"(() => {{ {self.SCOPE} return {expression}; }})()")

    def wait_frame(self, expression):
        self.wait_for(f"(() => {{ {self.SCOPE} return {expression}; }})()")
