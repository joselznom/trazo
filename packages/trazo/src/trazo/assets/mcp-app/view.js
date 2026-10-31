// trazo's live preview as an MCP App view (trazo.mcp_app).
// The view cannot reach the preview over the network: it talks JSON-RPC to
// the host over postMessage (SEP-1865), and every request goes through the
// app-only tool preview_request. Each page, from /__bundle, runs in a srcdoc
// frame of its own: the viewer scripts declare globals, so a page needs a
// fresh window, and a srcdoc frame shares the view's origin and CSP. Its
// first script calls TRAZO_VIEW.adopt(window), which points fetch at the
// tool and keeps the page's path and query (a srcdoc location holds
// neither) for Trazo.path() and Trazo.search(). The page navigates with
// TRAZO_VIEW.reload() and assign(url). A srcdoc frame resolves a bare
// "#fragment" against the view's URL, which would navigate it away, so
// fragments are set as absolute about:srcdoc URLs. Only stable SEP-1865
// messages are used.
(() => {
  "use strict";
  const ORIGIN = "http://trazo.invalid";  // resolves the page's relative URLs
  const CARD_HEIGHT = 56;
  const PANEL_POLL_MS = 3000;
  const pending = new Map();
  let images = new Map();  // path and query -> Promise of a data: URL, for the page shown
  const storage = {};  // in-memory stand-ins, kept across pages, when the frame has none
  let nextId = 1, host = { hostContext: {} }, target = null, frame = null, page = null, rendering = false;
  let shownRef = "/";  // the page to show again when the view returns to full screen

  // --- JSON-RPC with the host ---------------------------------------------
  const post = (message) => window.parent.postMessage({ jsonrpc: "2.0", ...message }, "*");
  function request(method, params) {
    const id = nextId++;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      post({ id, method, params });
    });
  }
  const notify = (method, params) => post({ method, params });
  const handlers = {
    "ui/notifications/tool-result": (result) => start(result),
    "ui/notifications/tool-cancelled": () => { if (!target) note("The preview was not started."); },
    "ui/notifications/host-context-changed": (context) => {
      host.hostContext = { ...host.hostContext, ...context };
      if (context.theme) { view.theme = context.theme; frame?.contentWindow?.Trazo?.applyTheme(); }
      layout();
    },
  };
  window.addEventListener("message", (event) => {
    if (event.source !== window.parent) return;
    const m = event.data;
    if (!m || m.jsonrpc !== "2.0") return;
    if (m.method === undefined) {
      const call = pending.get(m.id);
      if (!call) return;
      pending.delete(m.id);
      if (m.error) call.reject(new Error(m.error.message || "request failed"));
      else call.resolve(m.result);
      return;
    }
    if (m.id !== undefined) {
      // Teardown needs no saving: the page keeps its state on the server.
      post(m.method === "ui/resource-teardown" ? { id: m.id, result: {} }
        : { id: m.id, error: { code: -32601, message: `Method not found: ${m.method}` } });
    }
    handlers[m.method]?.(m.params || {});
  });

  async function tool(name, args) {
    const result = await request("tools/call", { name, arguments: args });
    if (result.isError) throw new Error(result.content?.[0]?.text || `${name} failed`);
    return result.structuredContent;
  }

  // --- Transport: the page's requests as preview_request calls -----------
  async function forward(path, method = "GET", body) {
    const reply = await tool("preview_request", { project: target.project, view: target.view, path, method,
      ...(body === undefined ? {} : { body }) });
    if (reply.superseded) {
      superseded();
      return new Promise(() => {});  // nothing more reaches the page
    }
    return reply;
  }
  const bytes = (base64) => Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
  const local = (url) => url.origin === ORIGIN;
  const resolve = (ref) => new URL(ref, ORIGIN + view.path + view.search);

  function pageFetch(win) {
    const nativeFetch = win.fetch.bind(win);
    return async (input, init = {}) => {
      const url = resolve(typeof input === "string" ? input : input.url ?? String(input));
      if (!local(url)) return nativeFetch(input, init);
      const body = init.body === undefined || init.body === null ? undefined : JSON.parse(init.body);
      const reply = await forward(url.pathname + url.search, (init.method || "GET").toUpperCase(), body);
      const content = reply.base64 !== undefined ? bytes(reply.base64) : reply.text;
      return new win.Response(reply.status === 204 || reply.status === 304 ? null : content,
        { status: reply.status, headers: { "Content-Type": reply.type || "application/octet-stream" } });
    };
  }

  // --- Pages ---------------------------------------------------------------
  const view = window.TRAZO_VIEW = {
    path: "/", search: "", theme: null,  // the host's light or dark, for the page's "auto" theme
    reload: () => render(view.path + view.search + (frame?.contentWindow.location.hash || "")),
    assign: (ref) => {
      const url = resolve(String(ref));
      if (!local(url)) { openLink(url.href); return; }
      if (url.pathname === view.path && url.search === view.search && url.hash) {
        frame.contentWindow.location.hash = url.hash;
        return;
      }
      render(url.pathname + url.search + url.hash);
    },
    adopt,
    // A view cannot download: the host downloads the export when it offers
    // ui/download-file (SEP-1865 draft), else the preview saves it in the paper.
    download: async (ref) => {
      const url = resolve(ref);
      if (host.hostCapabilities?.downloadFile) {
        const r = await forward(url.pathname + url.search);
        if (r.status !== 200) throw new Error(JSON.parse(r.text || "{}").error || `HTTP ${r.status}`);
        const name = r.filename || url.pathname.split("/").pop();
        try {
          const result = await request("ui/download-file", { contents: [{ type: "resource", resource: {
            uri: `file:///${encodeURIComponent(name)}`, mimeType: r.type,
            ...(r.base64 !== undefined ? { blob: r.base64 } : { text: r.text }) } }] });
          if (!result?.isError) return;
        } catch (e) { /* refused */ }
        // Refused or failed: save it in the paper instead.
      }
      url.searchParams.set("save", "1");
      const r = await forward(url.pathname + url.search);
      const data = JSON.parse(r.text || "{}");
      if (r.status !== 200) throw new Error(data.error || `HTTP ${r.status}`);
      toast(`Saved in the paper: ${data.path}`);
    },
  };

  async function render(ref) {
    const url = resolve(ref);
    let bundle;
    rendering = true;
    try {
      const reply = await forward(`/__bundle?page=${encodeURIComponent(url.pathname)}`);
      if (reply.status !== 200) throw new Error(JSON.parse(reply.text || "{}").error || `HTTP ${reply.status}`);
      bundle = JSON.parse(reply.text);
    } catch (e) {
      const text = `The preview could not be shown: ${e.message}`;
      // The page there stays, with any unsaved edits, also one kept hidden.
      if (frame?.hidden) showNote(text, canFullscreen());
      else if (frame) toast(text);
      else note(text);
      return;
    } finally {
      rendering = false;
    }
    if (mode() !== "fullscreen") return;  // left full screen meanwhile; layout() shows the card
    if (!leave(true)) return;
    shownRef = url.pathname + url.search + url.hash;
    images = new Map();
    Object.assign(view, { path: bundle.path, search: url.search });
    page = { fonts: bundle.fonts.map((f) => ({ ...f, data: bytes(f.data) })), hash: url.hash };
    const adoptScript = "<script>parent.TRAZO_VIEW.adopt(window)<\/script>";
    const next = document.createElement("iframe");
    next.title = "Paper preview";
    next.style.cssText = "position:absolute;inset:0;width:100%;height:100%;border:0";
    next.srcdoc = /<head[^>]*>/i.test(bundle.html) ? bundle.html.replace(/<head[^>]*>/i, (h) => h + adoptScript)
      : adoptScript + bundle.html;
    // A view without an origin of its own (sandboxed without allow-same-origin)
    // gives the frame another one, so adopt() never runs there.
    next.addEventListener("load", () => { if (frame === next && !page.adopted) unsupported(); }, { once: true });
    document.getElementById("trazo-view-note").hidden = true;
    document.getElementById("trazo-view-open").hidden = true;
    frame?.remove();
    frame = next;
    document.body.append(next);
    layout();
  }

  // What a browser does before leaving a page: beforeunload (the viewer saves
  // its place there, and refuses while edits are unsaved) and pagehide (the
  // table editor sends pending edits). False when the page stays: it has
  // unsaved edits and the user, when `ask`ed, keeps them.
  function leave(ask) {
    const win = frame?.contentWindow;
    if (!win || frame.hidden) return true;
    const event = new win.Event("beforeunload", { cancelable: true });
    win.dispatchEvent(event);
    if (event.defaultPrevented && !(ask && win.confirm("Leave this page? Changes you made may not be saved."))) return false;
    win.dispatchEvent(new win.PageTransitionEvent("pagehide"));
    return true;
  }

  // Runs in the page's frame before its own scripts.
  function adopt(win) {
    page.adopted = true;
    win.TRAZO_VIEW = view;
    win.fetch = pageFetch(win);
    for (const name of ["localStorage", "sessionStorage"]) {
      try { win[name].getItem("trazo"); } catch (e) {
        storage[name] ??= memoryStorage();
        try { Object.defineProperty(win, name, { value: storage[name], configurable: true }); } catch (e2) { /* left */ }
      }
    }
    const here = (hash) => win.location.href.split("#")[0] + hash;
    if (page.hash) win.history.replaceState(null, "", here(page.hash));
    // The page's history keeps its query here; the frame's URL takes only the fragment.
    for (const method of ["pushState", "replaceState"]) {
      const native = win.history[method].bind(win.history);
      win.history[method] = (state, title, ref) => {
        if (ref === undefined || ref === null) { native({ trazo: view.search, state }, title); return; }
        const url = resolve(String(ref));
        Object.assign(view, { path: url.pathname, search: url.search });
        native({ trazo: view.search, state }, title, here(url.hash));
      };
    }
    win.addEventListener("popstate", (event) => { view.search = event.state?.trazo ?? ""; });
    for (const f of page.fonts) win.document.fonts.add(new win.FontFace(f.family, f.data, f.descriptors));
    // The diagram editor declares the figure fonts by URL, which the CSP blocks and
    // whose failure fails its fontsReady(); /__bundle sends them above instead.
    // renderer.js stays as it is: its object is changed once it is set.
    let renderer;
    Object.defineProperty(win, "DiagramRenderer", { configurable: true, get: () => renderer,
      set: (value) => { renderer = value; if (value) value.fontFaceCss = () => ""; } });
    watchImages(win);
    win.addEventListener("click", onClick);  // after the page's own handlers
  }
  function memoryStorage() {
    const items = new Map();
    return {
      getItem: (k) => (items.has(String(k)) ? items.get(String(k)) : null),
      setItem: (k, v) => { items.set(String(k), String(v)); },
      removeItem: (k) => { items.delete(String(k)); },
      clear: () => items.clear(),
      key: (i) => [...items.keys()][i] ?? null,
      get length() { return items.size; },
    };
  }

  // Images the page adds or changes later (Figures & Tables, other documents' figures).
  function watchImages(win) {
    const fix = (el) => {
      const attr = el.localName === "img" ? "src" : "href";
      const ref = el.getAttribute(attr);
      if (!ref || /^(data|blob):|^#/.test(ref)) return;
      const url = resolve(ref);
      if (!local(url)) return;
      const key = url.pathname + url.search, cache = images;
      if (!cache.has(key)) {
        cache.set(key, forward(key).then((r) => {
          if (r.status !== 200) throw new Error(`HTTP ${r.status}`);
          const type = r.type.split(";")[0];
          return r.base64 !== undefined ? `data:${type};base64,${r.base64}`
            : `data:${type};charset=utf-8,${encodeURIComponent(r.text)}`;
        }).catch(() => { cache.delete(key); return null; }));
      }
      cache.get(key).then((data) => {
        if (!data || el.getAttribute(attr) !== ref) return;
        if (!el.dataset.trazoSrc) el.dataset.trazoSrc = ref;
        el.setAttribute(attr, data);
      });
    };
    const scan = (node) => {
      if (node.nodeType !== 1) return;
      if (node.matches("img[src], image[href]")) fix(node);
      node.querySelectorAll("img[src], image[href]").forEach(fix);
    };
    new win.MutationObserver((records) => {
      for (const r of records) {
        if (r.type === "attributes") { if (r.target.matches("img[src], image[href]")) fix(r.target); }
        else r.addedNodes.forEach(scan);
      }
    }).observe(win.document, { subtree: true, childList: true, attributes: true, attributeFilter: ["src", "href"] });
  }

  function onClick(event) {
    const a = event.target.closest?.("a[href]");
    if (!a || event.defaultPrevented || a.hasAttribute("download") || event.button !== 0
        || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const ref = a.getAttribute("href");
    if (/^(javascript|data|blob|mailto):/i.test(ref)) return;
    event.preventDefault();
    if (ref.startsWith("#")) a.ownerDocument.defaultView.location.hash = ref;  // the frame's own URL
    else view.assign(ref);
  }

  function openLink(href) {
    if (host.hostCapabilities?.openLinks) request("ui/open-link", { url: href }).catch(() => {});
  }

  // --- Full screen only ----------------------------------------------------
  // The paper needs the whole window, so the page shows only in full screen;
  // the resource offers ChatGPT no other mode. Shown inline anyway (a view
  // reopened from history, a host without full screen), the view is a
  // one-line card that opens it, and loads and polls nothing; only a page
  // with unsaved edits stays, hidden, until the view returns to full screen.
  const mode = () => host.hostContext.displayMode || "inline";
  const canFullscreen = () => (host.hostContext.availableDisplayModes || []).includes("fullscreen");
  function layout() {
    const full = mode() === "fullscreen";
    document.documentElement.style.height = full ? "100vh" : `${CARD_HEIGHT}px`;
    document.body.style.cssText = "position:relative;height:100%;overflow:hidden";
    if (!full) notify("ui/notifications/size-changed", { width: document.documentElement.clientWidth, height: CARD_HEIGHT });
    if (!target || target.superseded || target.unsupported) return;
    if (full && frame?.hidden) { frame.hidden = false; showNote(false); }
    else if (full && !frame && !rendering) render(shownRef);
    else if (!full) card();
  }
  function card() {
    if (frame && !frame.hidden) {
      shownRef = view.path + view.search + frame.contentWindow.location.hash;
      if (!leave(false)) {
        frame.hidden = true;  // unsaved edits wait, hidden, for the view to return to full screen
        showNote("The paper's preview has unsaved changes.", true);
        return;
      }
    }
    if (frame?.hidden) return;
    if (canFullscreen()) note("The paper's preview.", true);
    else note(`This app cannot show the preview full screen. Open it in a browser: ${target.url}`);
  }
  async function openFullscreen() {
    try {
      const result = await request("ui/request-display-mode", { mode: "fullscreen" });
      host.hostContext.displayMode = result?.mode || mode();
    } catch (e) { /* the host keeps its mode */ }
    layout();
  }
  document.getElementById("trazo-view-open").addEventListener("click", openFullscreen);

  // --- Lifecycle -----------------------------------------------------------
  function note(text, open = false) {
    frame?.remove();
    frame = null;
    showNote(text, open);
  }
  function toast(text) {
    let t = document.getElementById("trazo-view-toast");
    if (!t) {
      t = document.createElement("p");
      t.id = "trazo-view-toast";
      t.style.cssText = "position:fixed;left:12px;bottom:12px;z-index:1;margin:0;padding:6px 10px;border-radius:6px;"
        + "border:1px solid rgba(127,127,127,.5);background:Canvas;color:CanvasText;font:12px system-ui";
      document.body.append(t);
    }
    t.textContent = text;
    clearTimeout(t.timer);
    t.timer = setTimeout(() => t.remove(), 6000);
  }
  function showNote(text, open = false) {
    const p = document.getElementById("trazo-view-note");
    if (text !== false) p.textContent = text;
    p.hidden = text === false;
    document.getElementById("trazo-view-open").hidden = text === false || !open;
  }
  function unsupported() {
    const text = "This app shows the view without an origin of its own, where the preview cannot run. "
      + `Open it in a browser instead: ${target.url}`;
    target.unsupported = true;
    note(text);
    notify("notifications/message", { level: "warning", logger: "trazo", data: text });
  }
  function superseded() {
    if (target.superseded) return;
    target.superseded = true;
    note("A newer preview of this paper is open further down the conversation.");
  }

  function start(result) {
    if (target) return;
    const info = result.structuredContent || {};
    if (!info.project) {
      // The panel, before any preview_start in this conversation.
      note("No preview is open in this conversation yet: ask the agent to open the paper's preview.");
      setTimeout(async () => {
        let next = result;
        try { next = { structuredContent: await tool("preview_panel", {}) }; } catch (e) { /* try again */ }
        start(next);
      }, PANEL_POLL_MS);
      return;
    }
    target = { project: info.project, view: info.view, url: info.url };
    if (mode() !== "fullscreen" && canFullscreen()) openFullscreen();
    else layout();
  }

  request("ui/initialize", {
    protocolVersion: "2026-01-26",
    appInfo: { name: "trazo-preview", version: "1" },
    appCapabilities: { availableDisplayModes: ["fullscreen"] },
  }).then((result) => {
    host = { ...result, hostContext: result.hostContext || {} };
    view.theme = host.hostContext.theme || null;
    notify("ui/notifications/initialized", {});
    layout();
  }).catch((e) => note(`The host did not start the view: ${e.message}`));
})();
