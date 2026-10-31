// Shared workspace services. No build step or external browser dependencies.
(function () {
  "use strict";
  if (window.Trazo) return;
  const KEY = "trazo-preview";
  const modes = ["auto", "light", "dark"];
  const palettes = [
    { id: "coral", name: "Ink & coral", colors: ["#e3e8f0", "#17233b", "#d14a22"] },
    { id: "classic", name: "Classic", colors: ["#e4e7ec", "#1f2430", "#2a58c8"] },
    { id: "paper", name: "Paper", colors: ["#e8e4db", "#26231f", "#0f6e66"] },
    { id: "indigo", name: "Indigo", colors: ["#dcdcec", "#1d1c36", "#4f46e5"] },
    { id: "polar", name: "Polar", colors: ["#d6e0e8", "#0f2533", "#0e7490"] },
  ];
  function readPrefs(fallback = {}) {
    try {
      const value = JSON.parse(localStorage.getItem(KEY) || "{}");
      return value && typeof value === "object" && !Array.isArray(value) ? value : {};
    } catch (e) { return fallback; }
  }
  let memory = readPrefs();
  function theme() {
    return { mode: modes.includes(memory.theme) ? memory.theme : "auto",
      palette: palettes.some(p => p.id === memory.palette) ? memory.palette : "coral" };
  }
  function applyTheme() {
    const root = document.documentElement, chosen = theme();
    // "auto" follows the host app's theme in an MCP App view, else the system's.
    const mode = chosen.mode === "auto" && window.TRAZO_VIEW?.theme ? window.TRAZO_VIEW.theme : chosen.mode;
    for (const [key, value, fallback] of [["theme", mode, "auto"], ["palette", chosen.palette, "coral"]]) {
      if (value === fallback) root.removeAttribute("data-" + key); else root.setAttribute("data-" + key, value);
    }
    window.dispatchEvent(new Event("trazo-theme"));
  }
  function savePrefs(extra) {
    memory = { ...readPrefs(memory), ...extra };
    try { localStorage.setItem(KEY, JSON.stringify(memory)); } catch (e) { /* private storage */ }
    applyTheme();
    return memory;
  }
  window.addEventListener("storage", e => {
    if (e.key === KEY || e.key === null) { memory = readPrefs(memory); applyTheme(); }
  });
  applyTheme();

  // Page changes use the browser's own cross-document view transition (see
  // app.css) and reloads keep the old page painted until the new one is
  // ready. The shell marker in app.css also guards browsers without native
  // render blocking. reveal() is only for switching views inside a shown page.
  const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const entrances = new WeakMap();
  let reloadPending = false;
  function reveal(element) {
    if (!element) return;
    entrances.get(element)?.cancel();
    if (reducedMotion() || !element.animate) return;
    const animation = element.animate([{ opacity: 0 }, { opacity: 1 }],
      { duration: 180, easing: "ease-out" });
    entrances.set(element, animation);
    animation.finished.then(() => {
      if (entrances.get(element) === animation) entrances.delete(element);
    }).catch(() => {});
  }
  window.addEventListener("pageshow", () => { reloadPending = false; });
  function reload(canReload = () => true) {
    if (reloadPending || !canReload()) return false;
    reloadPending = true;
    (window.TRAZO_VIEW || location).reload();
    return true;
  }

  async function api(url, body, method) {
    const response = await fetch(url, { method: method || (body === undefined ? "GET" : "POST"),
      cache: "no-store", headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body) });
    const data = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
    return { ok: response.ok, status: response.status, data };
  }

  // One non-overlapping status request per page; consumers share the result.
  const listeners = new Set();
  let timer = null, polling = false, latest = null;
  async function poll() {
    if (polling || !listeners.size) return;
    clearTimeout(timer);
    polling = true;
    try {
      const result = await api("/__workspace");
      if (!result.ok) throw new Error(result.data.error || "Workspace unavailable");
      latest = result.data;
      await Promise.allSettled([...listeners].map(fn => fn(latest, null)));
    } catch (error) {
      await Promise.allSettled([...listeners].map(fn => fn(null, error)));
    } finally {
      polling = false;
      if (listeners.size) timer = setTimeout(poll, document.hidden ? 5000 : 1000);
    }
  }
  function watch(fn) {
    listeners.add(fn);
    if (latest) Promise.resolve().then(() => fn(latest, null));
    if (!polling) { clearTimeout(timer); timer = setTimeout(poll, 0); }
    return () => { listeners.delete(fn); if (!listeners.size) clearTimeout(timer); };
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value == null || value === false) continue;
      if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat()) if (child != null && child !== false) node.append(child);
    return node;
  }

  function themeMenu(button, existing) {
    const menu = existing || el("div", { class: "pv-theme-menu", role: "menu", "aria-label": "Theme", hidden: true });
    if (!existing) document.body.append(menu);
    button.setAttribute("aria-haspopup", "menu");
    button.setAttribute("aria-expanded", "false");
    const make = (key, value, label, colors) => el("button", { type: "button", class: "pv-tm-item", role: "menuitemradio", ["data-" + key]: value },
      el("span", { class: "pv-tm-check", "aria-hidden": "true" }), el("span", { text: label }),
      colors ? el("span", { class: "pv-tm-swatch", "aria-hidden": "true", style: `background:linear-gradient(90deg,${colors[0]} 0 34%,${colors[1]} 34% 67%,${colors[2]} 67%)` }) : null);
    menu.replaceChildren(el("div", { class: "pv-tm-head", text: "Mode" }),
      ...modes.map(mode => make("theme", mode, mode === "auto" ? "Auto (system)" : mode === "dark" ? "Dark" : "Light")),
      el("div", { class: "pv-tm-head", text: "Theme" }), ...palettes.map(p => make("palette", p.id, p.name, p.colors)));
    const items = [...menu.querySelectorAll("button")];
    function update() {
      const chosen = theme(), name = palettes.find(p => p.id === chosen.palette).name;
      button.title = `Theme: ${name}, ${chosen.mode}`;
      button.setAttribute("aria-label", button.title);
      for (const item of items) item.setAttribute("aria-checked", String(item.dataset.theme ? item.dataset.theme === chosen.mode : item.dataset.palette === chosen.palette));
    }
    function close(focus) { menu.hidden = true; button.setAttribute("aria-expanded", "false"); if (focus) button.focus(); }
    function open() {
      menu.hidden = false; button.setAttribute("aria-expanded", "true");
      const rect = button.getBoundingClientRect();
      menu.style.left = `${Math.max(8, Math.min(rect.right - menu.offsetWidth, innerWidth - menu.offsetWidth - 8))}px`;
      menu.style.top = `${rect.bottom + 6}px`;
      (items.find(item => item.getAttribute("aria-checked") === "true") || items[0]).focus();
    }
    button.addEventListener("click", () => menu.hidden ? open() : close());
    button.addEventListener("keydown", e => { if (e.key === "ArrowDown") { e.preventDefault(); open(); } });
    menu.addEventListener("click", e => {
      const item = e.target.closest(".pv-tm-item");
      if (item) savePrefs(item.dataset.theme ? { theme: item.dataset.theme } : { palette: item.dataset.palette });
    });
    menu.addEventListener("keydown", e => {
      const index = items.indexOf(document.activeElement), step = { ArrowDown: 1, ArrowUp: -1 }[e.key];
      if (step) { e.preventDefault(); items[(index + step + items.length) % items.length].focus(); }
      else if (["Home", "End"].includes(e.key)) { e.preventDefault(); items[e.key === "Home" ? 0 : items.length - 1].focus(); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(true); }
    });
    document.addEventListener("click", e => { if (!menu.contains(e.target) && !button.contains(e.target)) close(); });
    window.addEventListener("trazo-theme", update);
    update();
  }

  function safeReturn(value) {
    if (!value || !value.startsWith("/") || value.startsWith("//")) return null;
    // A preview MCP App view has no origin of its own to resolve against.
    const origin = window.TRAZO_VIEW ? "http://trazo.invalid" : location.origin;
    try { const url = new URL(value, origin); return url.origin === origin ? url.pathname + url.search + url.hash : null; }
    catch (e) { return null; }
  }
  // The page's path and query: in an MCP App view the page
  // lives in a srcdoc frame, whose location cannot hold them.
  const pagePath = () => window.TRAZO_VIEW?.path ?? location.pathname;
  const pageSearch = () => window.TRAZO_VIEW?.search ?? location.search;
  const returnUrl = safeReturn(new URLSearchParams(pageSearch()).get("return"));
  // Moves to a fragment without a history entry; absolute, since a srcdoc
  // frame would resolve a bare "#x" against the view's URL.
  const replaceHash = (hash) => location.replace(`${location.href.split("#")[0]}#${hash}`);
  function editorLink(path, name, from = pagePath() + pageSearch() + location.hash) {
    return `${path}?return=${encodeURIComponent(safeReturn(from) || "/")}#${encodeURIComponent(name)}`;
  }
  // The theme button: the page's own, or a new one appended to `host`.
  function mountTheme(host, options = {}) {
    const themeButton = options.themeButton || el("button", { type: "button", class: "pk-button pk-theme", text: "◐" });
    if (!options.themeButton) host.append(themeButton);
    themeMenu(themeButton, options.themeMenu);
    return themeButton;
  }
  const review = {
    load: (scope, name) => api(`/__review?scope=${scope}${name == null ? "" : "&name=" + encodeURIComponent(name)}`),
    change: (scope, body) => api("/__review", { ...body, scope }),
  };
  window.Trazo = { api, watch, el, readPrefs, savePrefs, theme, mountTheme, editorLink, returnUrl, review, reveal, reload, path: pagePath, search: pageSearch, replaceHash, applyTheme };
})();
