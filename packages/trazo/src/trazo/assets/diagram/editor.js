/* Diagram editor: edits figures/diagrams/<name>.json in the browser.
 *
 * Served by the preview at /diagrams#<name>. Plain browser
 * JavaScript, no build step. Drawing is done by renderer.js (the same code
 * that exports), so the canvas matches the export exactly.
 *
 * State. S.diagram is the diagram JSON and the only model. Every edit is a
 * command: commit(label, mutate) clones the JSON, applies `mutate` to the
 * clone and pushes {before, after} JSON strings on the undo stack, so undo
 * and redo restore exact snapshots (nudges and repeated inspector edits
 * with the same label within MERGE_MS merge into one step). S.disk is the
 * version known to be on disk ({sha, json}); saves send base_sha256 =
 * S.disk.sha, so the server refuses (409) instead of overwriting a file
 * that changed behind the editor. A poll of GET /__diagrams/status reloads when the
 * file changes and this page is clean, and otherwise offers Keep mine /
 * Load theirs. Nothing is overwritten silently.
 *
 * Rendering loop. After each change render() calls DiagramRenderer.render
 * and swaps the SVG; its geometry (absolute millimetres) drives hit
 * testing, snapping, the inspector and the overlay (selection, handles,
 * guides, grid, panel and print-size guides), which is a second SVG with
 * the same viewBox. While dragging, a draft JSON is re-rendered once per
 * animation frame and committed on release.
 *
 * Coordinates are millimetres; a node with "panel" stores x/y relative to
 * the panel's top-left corner, and moves and resizes write them back in
 * that frame. Zoom is CSS px per mm. 100 % = physical size: 96/25.4 CSS px
 * per mm (the CSS definition of a millimetre; the browser maps CSS px to
 * device pixels with devicePixelRatio, which is close to but not exactly
 * physical), times a calibration factor kept in localStorage (set it in
 * the help popover by measuring the 50 mm ruler).
 */
(function () {
  "use strict";

  const PT = 25.4 / 72;
  const CSS_PX_PER_MM = 96 / 25.4;
  const MIN_ZOOM = 0.25;
  const MAX_ZOOM = 120;
  const SNAP_PX = 6;
  const HIT_PX = 5;
  const HANDLE_PX = 7;
  const DRAG_START_PX = 3;
  const AUTOSAVE_MS = 1500;
  const MERGE_MS = 800;
  const MIN_SIZE_MM = 1;
  const VALUE_KEY = /\{\{v:([A-Za-z0-9_.\-]+)\}\}/g;
  const TEXT_KINDS = new Set(["box", "pill", "text"]);
  const COLLECTIONS = ["panels", "nodes", "edges", "raw"];
  const SVG_NS = "http://www.w3.org/2000/svg";
  const CALIBRATION_KEY = "diagram-editor-calibration";

  const App = window.Trazo;
  const PATHS = window.TRAZO_CONFIG.paths;
  App.mountTheme(document.querySelector(".bar-right"));
  document.querySelector("#back").href = App.returnUrl || "/";
  /* Overlay colours (the editor chrome, never the figure). */
  const C = {
    sel: "#3166e8", selSoft: "#e9effd", hover: "#9db7f3", guide: "#f0447a", spacing: "#f0447a",
    flash: "#e4572e", frame: "#a3aec0", frameSoft: "#c9d0dc", hint: "#7d889c", err: "#d1242f", warn: "#c47b00",
  };

  const $ = (sel) => document.querySelector(sel);
  const viewport = $("#viewport");
  const stage = $("#stage");
  const paper = $("#paper");
  const gridLayer = $("#grid");
  const overlay = $("#overlay");

  function h(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else if (k === "dataset") Object.assign(node.dataset, v);
      else if (k === "style" && typeof v === "object") Object.assign(node.style, v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function s(name, attrs, parent) {
    const node = document.createElementNS(SVG_NS, name);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v !== null && v !== undefined) node.setAttribute(k, String(v));
    }
    if (parent) parent.appendChild(node);
    return node;
  }

  const round2 = (v) => Math.round(v * 100) / 100;
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const clone = (v) => JSON.parse(JSON.stringify(v));

  /* ---------- state ---------- */

  const S = {
    name: null,
    diagram: null,
    tokens: null,
    values: {},
    embeds: {},
    images: {},
    disk: { sha: null, json: null },
    saving: false,
    savePending: false,
    saveTimer: null,
    blocked: null,           // null | "conflict" | "invalid" | "error"
    undo: [],
    redo: [],
    sel: [],
    hover: null,
    clipboard: null,
    pasteCount: 0,
    styleClip: null,
    view: { zoom: 4, x: 40, y: 40, grid: true, guides: true },
    tool: "select",
    tab: "inspect",
    geo: null,
    boxes: {},
    issues: [],
    timing: { render: 0, total: 0 },
    drag: null,
    editing: null,
    space: false,
    calibration: (() => { try { return Number(localStorage.getItem(CALIBRATION_KEY)) || 1; } catch (err) { return 1; } })(),
    clientId: "editor-" + Math.random().toString(36).slice(2, 8),
    embedCatalog: null,
    serverIssues: null,
    serverFor: null,
    revision: null,
    inputs: null,
    exportState: null,
    checkTimer: null,
    flash: null,
    flashTimer: null,
    conflict: null,
  };

  function overlayTheme() {
    const css = getComputedStyle(document.documentElement);
    const token = name => css.getPropertyValue("--pv-" + name).trim();
    C.sel = token("accent"); C.selSoft = token("accent-soft"); C.hover = token("focus");
    C.flash = token("accent"); C.frame = token("line-strong"); C.frameSoft = token("line");
    C.hint = token("muted"); C.err = token("err"); C.warn = token("warn");
    if (S.diagram) drawOverlay();
  }
  window.addEventListener("trazo-theme", overlayTheme);
  overlayTheme();

  /* ---------- model helpers ---------- */

  function findItem(id, d = S.diagram) {
    if (!d) return null;
    for (const coll of COLLECTIONS) {
      const list = d[coll] || [];
      const index = list.findIndex((x) => x && x.id === id);
      if (index >= 0) return { coll, item: list[index], index };
    }
    return null;
  }

  const kindOf = (id, d = S.diagram) => {
    const found = findItem(id, d);
    return found ? (found.coll === "nodes" ? "node" : found.coll.replace(/s$/, "")) : null;
  };

  function usedIds(d = S.diagram) {
    const ids = new Set();
    for (const coll of COLLECTIONS) for (const x of d[coll] || []) if (x && x.id) ids.add(x.id);
    return ids;
  }

  function uniqueId(wanted, used) {
    if (!used.has(wanted)) return wanted;
    const base = wanted.replace(/-\d+$/, "") || "item";
    let n = 2;
    while (used.has(`${base}-${n}`)) n++;
    return `${base}-${n}`;
  }

  function parentMap(d = S.diagram) {
    const parent = {};
    for (const n of d.nodes || []) {
      if (n.kind === "group") for (const c of n.children || []) parent[c] = n.id;
    }
    return parent;
  }

  /* Ancestor groups of an id, outermost first, then the id itself. */
  function chainOf(id, d = S.diagram) {
    const parent = parentMap(d);
    const chain = [id];
    const seen = new Set([id]);
    while (parent[chain[0]] && !seen.has(parent[chain[0]])) {
      chain.unshift(parent[chain[0]]);
      seen.add(chain[0]);
    }
    return chain;
  }

  function leafIds(id, d = S.diagram) {
    const out = [];
    const seen = new Set();
    const stack = [id];
    while (stack.length) {
      const cur = stack.pop();
      if (seen.has(cur)) continue;
      seen.add(cur);
      const found = findItem(cur, d);
      if (!found || found.coll !== "nodes") continue;
      if (found.item.kind === "group") stack.push(...(found.item.children || []));
      else out.push(cur);
    }
    return out;
  }

  /* Every node id below a group (groups included), plus the id itself. */
  function subtreeIds(id, d = S.diagram) {
    const out = new Set();
    const stack = [id];
    while (stack.length) {
      const cur = stack.pop();
      if (out.has(cur)) continue;
      out.add(cur);
      const found = findItem(cur, d);
      if (found && found.item.kind === "group") stack.push(...(found.item.children || []));
    }
    return out;
  }

  const gridStep = () => (S.diagram && S.diagram.canvas.grid) || 1;

  function anchorId(spec) {
    return typeof spec === "string" ? spec.split(".")[0] : null;
  }

  function textKeys(node) {
    const keys = new Set();
    const visit = (v) => {
      if (typeof v === "string") for (const m of v.matchAll(VALUE_KEY)) keys.add(m[1]);
      else if (Array.isArray(v)) v.forEach(visit);
      else if (v && typeof v === "object") Object.values(v).forEach(visit);
    };
    visit(node && node.text);
    return [...keys];
  }

  function boxOf(id) {
    const b = S.boxes[id];
    return b ? b.box : null;
  }

  function unionBox(boxes) {
    const list = boxes.filter(Boolean);
    if (!list.length) return null;
    const x0 = Math.min(...list.map((b) => b[0]));
    const y0 = Math.min(...list.map((b) => b[1]));
    const x1 = Math.max(...list.map((b) => b[0] + b[2]));
    const y1 = Math.max(...list.map((b) => b[1] + b[3]));
    return [x0, y0, x1 - x0, y1 - y0];
  }

  /* Locked or hidden on the item, or inherited from a parent group or panel. */
  function flagOn(id, key, d = S.diagram) {
    const seen = new Set();
    const walk = (cur) => {
      if (!cur || seen.has(cur)) return false;
      seen.add(cur);
      const found = findItem(cur, d);
      if (!found) return false;
      if (found.item[key]) return true;
      if (found.item.panel && walk(found.item.panel)) return true;
      return walk(parentMap(d)[cur]);
    };
    return walk(id);
  }

  function isLocked(id, d = S.diagram) {
    return flagOn(id, "locked", d);
  }

  /* Matches renderer concealedIds: own flag, ancestor group, panel, or an edge into one. */
  function isHidden(id, d = S.diagram) {
    if (S.boxes[id] && S.boxes[id].hidden) return true;
    if (flagOn(id, "hidden", d)) return true;
    const found = findItem(id, d);
    if (found && found.coll === "edges") {
      return flagOn(anchorId(found.item.from), "hidden", d) || flagOn(anchorId(found.item.to), "hidden", d);
    }
    return false;
  }

  function panelAt(mm) {
    for (const p of (S.geo && S.geo.panels) || []) {
      const c = p.content;
      if (mm[0] >= c[0] && mm[0] <= c[0] + c[2] && mm[1] >= c[1] && mm[1] <= c[1] + c[3]) return p;
    }
    return null;
  }

  /* ---------- commands ---------- */

  function commit(label, mutate, opts = {}) {
    const base = opts.base || JSON.stringify(S.diagram);
    const draft = JSON.parse(base);
    if (mutate(draft) === false) return false;
    const after = JSON.stringify(draft);
    if (after === base) {
      if (opts.base) setDiagram(draft);
      return false;
    }
    const now = performance.now();
    const last = S.undo[S.undo.length - 1];
    const selAfter = opts.select || S.sel.slice();
    if (opts.merge && last && last.label === label && now - last.time < MERGE_MS && !S.redo.length) {
      last.after = after;
      last.time = now;
      last.selAfter = selAfter;
    } else {
      S.undo.push({ label, before: base, after, time: now, selBefore: opts.selBefore || S.sel.slice(), selAfter });
      if (S.undo.length > 500) S.undo.shift();
    }
    S.redo = [];
    S.sel = selAfter;
    if (S.blocked === "invalid" || S.blocked === "error") S.blocked = null;
    setDiagram(draft);
    scheduleAutosave();
    return true;
  }

  function setDiagram(d) {
    S.diagram = d;
    render();
    updateUi();
    scheduleCheck();
  }

  function undo() {
    const entry = S.undo.pop();
    if (!entry) return;
    S.redo.push(entry);
    S.sel = entry.selBefore;
    setDiagram(JSON.parse(entry.before));
    scheduleAutosave();
    toast(`Undo: ${entry.label}`);
  }

  function redo() {
    const entry = S.redo.pop();
    if (!entry) return;
    S.undo.push(entry);
    S.sel = entry.selAfter;
    setDiagram(JSON.parse(entry.after));
    scheduleAutosave();
    toast(`Redo: ${entry.label}`);
  }

  /* Move nodes (all leaves of selected groups), panels and edge waypoints by dx, dy. */
  function applyMove(d, ids, dx, dy) {
    ids = ids.filter((id) => !isLocked(id, d));
    if (!ids.length) return false;
    const panels = new Set(ids.filter((id) => kindOf(id, d) === "panel"));
    const leaves = new Set();
    for (const id of ids) {
      const k = kindOf(id, d);
      if (k === "node") leafIds(id, d).forEach((l) => leaves.add(l));
    }
    for (const p of d.panels || []) {
      if (panels.has(p.id)) {
        p.x = round2(p.x + dx);
        p.y = round2(p.y + dy);
      }
    }
    for (const n of d.nodes || []) {
      if (leaves.has(n.id) && !(n.panel && panels.has(n.panel))) {
        n.x = round2((n.x || 0) + dx);
        n.y = round2((n.y || 0) + dy);
      }
    }
    for (const e of d.edges || []) {
      if (ids.includes(e.id) && e.waypoints) e.waypoints = e.waypoints.map(([x, y]) => [round2(x + dx), round2(y + dy)]);
    }
  }

  /* Write an absolute box back to a node (panel-relative when it has a panel) or panel. */
  function applyBox(d, id, box) {
    if (isLocked(id, d)) return false;
    const found = findItem(id, d);
    if (!found) return;
    const item = found.item;
    let ox = 0;
    let oy = 0;
    if (found.coll === "nodes" && item.panel) {
      const p = (d.panels || []).find((x) => x.id === item.panel);
      if (p) [ox, oy] = [p.x, p.y];
    }
    item.x = round2(box[0] - ox);
    item.y = round2(box[1] - oy);
    item.w = round2(Math.max(MIN_SIZE_MM, box[2]));
    item.h = round2(Math.max(MIN_SIZE_MM, box[3]));
  }

  function deleteIds(d, ids) {
    ids = ids.filter((id) => !isLocked(id, d));
    if (!ids.length) return false;
    const removed = new Set();
    for (const id of ids) {
      const k = kindOf(id, d);
      if (k === "node") subtreeIds(id, d).forEach((x) => removed.add(x));
      else if (k) removed.add(id);
    }
    for (const p of d.panels || []) {
      if (!removed.has(p.id)) continue;
      for (const n of d.nodes || []) {
        if (n.panel === p.id) {
          if (n.kind !== "group") {
            n.x = round2((n.x || 0) + p.x);
            n.y = round2((n.y || 0) + p.y);
          }
          delete n.panel;
        }
      }
    }
    d.panels = (d.panels || []).filter((x) => !removed.has(x.id));
    d.nodes = (d.nodes || []).filter((x) => !removed.has(x.id));
    d.raw = (d.raw || []).filter((x) => !removed.has(x.id));
    const blocked = (d.edges || []).some((e) => e.locked && (removed.has(e.id)
      || removed.has(anchorId(e.from)) || removed.has(anchorId(e.to))));
    if (blocked) return false;
    d.edges = (d.edges || []).filter((e) => !removed.has(e.id)
      && !removed.has(anchorId(e.from)) && !removed.has(anchorId(e.to)));
    let gone = removed;
    while (gone.size) {
      for (const n of d.nodes) {
        if (n.kind === "group") n.children = (n.children || []).filter((c) => !gone.has(c));
      }
      gone = new Set(d.nodes.filter((n) => n.kind === "group" && !n.children.length).map((n) => n.id));
      d.nodes = d.nodes.filter((n) => !gone.has(n.id));
      d.edges = d.edges.filter((e) => !gone.has(anchorId(e.from)) && !gone.has(anchorId(e.to)));
    }
  }

  /* Nodes (with their subtrees) and the edges between them, as a clipboard. */
  function copyPayload(ids) {
    const nodeIds = new Set();
    for (const id of ids) if (kindOf(id) === "node") subtreeIds(id).forEach((x) => nodeIds.add(x));
    const top = ids.filter((id) => kindOf(id) === "node");
    if (!top.length) return null;
    const nodes = S.diagram.nodes.filter((n) => nodeIds.has(n.id)).map(clone);
    const edges = (S.diagram.edges || []).filter((e) => nodeIds.has(anchorId(e.from))
      && nodeIds.has(anchorId(e.to))).map(clone);
    return { nodes, edges, top };
  }

  function pasteInto(d, payload, offset) {
    const used = usedIds(d);
    const map = {};
    for (const n of payload.nodes) {
      map[n.id] = uniqueId(n.id, used);
      used.add(map[n.id]);
    }
    const panels = new Set((d.panels || []).map((p) => p.id));
    for (const src of payload.nodes) {
      const n = clone(src);
      n.id = map[src.id];
      if (n.kind === "group") n.children = n.children.map((c) => map[c] || c);
      else {
        n.x = round2((n.x || 0) + offset);
        n.y = round2((n.y || 0) + offset);
      }
      if (n.panel && !panels.has(n.panel)) delete n.panel;
      d.nodes.push(n);
    }
    d.edges = d.edges || [];
    for (const src of payload.edges) {
      const e = clone(src);
      e.id = uniqueId(e.id, used);
      used.add(e.id);
      for (const end of ["from", "to"]) {
        const id = anchorId(e[end]);
        e[end] = map[id] + e[end].slice(id.length);
      }
      if (e.waypoints) e.waypoints = e.waypoints.map(([x, y]) => [round2(x + offset), round2(y + offset)]);
      d.edges.push(e);
    }
    return payload.top.map((id) => map[id]);
  }

  const STYLE_KEYS = ["fill", "stroke", "radius", "padding", "align", "valign"];

  function styleOf(id) {
    const found = findItem(id);
    if (!found) return null;
    const item = found.item;
    if (found.coll === "edges") return { kind: "edge", stroke: item.stroke, route: item.route };
    if (found.coll !== "nodes" || item.kind === "group") return null;
    const style = { kind: "node", props: {} };
    for (const k of STYLE_KEYS) if (k in item) style.props[k] = clone(item[k]);
    if (Array.isArray(item.text)) style.textStyles = item.text.map((b) => b.style || null);
    return style;
  }

  function applyStyle(d, id, style) {
    const found = findItem(id, d);
    if (!found) return;
    const item = found.item;
    if (style.kind === "edge" && found.coll === "edges") {
      for (const k of ["stroke", "route"]) {
        if (style[k] === undefined) delete item[k];
        else item[k] = style[k];
      }
      return;
    }
    if (style.kind !== "node" || found.coll !== "nodes" || item.kind === "group") return;
    for (const k of STYLE_KEYS) {
      if (k === "radius" && item.kind !== "box") continue;
      if (k in style.props) item[k] = clone(style.props[k]);
      else delete item[k];
    }
    if (style.textStyles && style.textStyles.length && Array.isArray(item.text)) {
      item.text.forEach((block, i) => {
        const st = style.textStyles[Math.min(i, style.textStyles.length - 1)];
        if (st) block.style = st;
        else delete block.style;
      });
    }
  }

  /* ---------- rendering ---------- */

  function render(diagram = S.diagram) {
    if (!diagram || !S.tokens) return;
    const t0 = performance.now();
    let result;
    try {
      result = DiagramRenderer.render(diagram, {
        tokens: S.tokens, values: S.values, embeds: S.embeds, images: S.images });
    } catch (err) {
      showBanner("error", `The renderer failed: ${err.message}`, []);
      return;
    }
    const t1 = performance.now();
    S.geo = result.geometry;
    S.issues = result.issues;
    S.boxes = {};
    for (const p of S.geo.panels) S.boxes[p.id] = { kind: "panel", box: p.box, content: p.content, hidden: !!p.hidden };
    for (const n of S.geo.nodes) S.boxes[n.id] = { kind: n.kind, box: n.box, content: n.content, hidden: !!n.hidden };
    for (const e of S.geo.edges) {
      const pts = e.points || [];
      const box = pts.length ? unionBox(pts.map(([x, y]) => [x, y, 0, 0])) : null;
      S.boxes[e.id] = { kind: "edge", box: box && e.label ? unionBox([box, e.label.box]) : box, points: pts,
        label: e.label, hidden: !!e.hidden };
    }
    S.sel = S.sel.filter((id) => S.boxes[id] || findItem(id));
    paper.replaceChildren(result.svg);
    layoutStage();
    drawOverlay();
    if (S.editing) hideEditedText();
    S.timing = { render: t1 - t0, total: performance.now() - t0 };
    $("#timing").textContent = `render ${S.timing.render.toFixed(1)} ms (with overlay ${S.timing.total.toFixed(1)} ms)`;
  }

  function layoutStage() {
    const c = S.diagram.canvas;
    const z = S.view.zoom;
    const w = c.width_mm * z;
    const hgt = c.height_mm * z;
    stage.style.transform = `translate(${S.view.x}px, ${S.view.y}px)`;
    for (const el of [stage, paper, gridLayer]) {
      el.style.width = `${w}px`;
      el.style.height = `${hgt}px`;
    }
    const svg = paper.firstElementChild;
    if (svg) {
      svg.setAttribute("width", w);
      svg.setAttribute("height", hgt);
    }
    overlay.setAttribute("width", w);
    overlay.setAttribute("height", hgt);
    overlay.setAttribute("viewBox", `0 0 ${c.width_mm} ${c.height_mm}`);
    /* Minor lines at the snapping step (thinned out when they get dense),
       major lines every 10 minor steps. */
    let step = gridStep();
    while (step * z < 8) step *= 2;
    const major = step * 10 * z;
    const minor = step * z;
    gridLayer.hidden = !S.view.grid;
    gridLayer.style.backgroundImage =
      "linear-gradient(to right, rgba(49, 102, 232, 0.12) 1px, transparent 1px)," +
      "linear-gradient(to bottom, rgba(49, 102, 232, 0.12) 1px, transparent 1px)," +
      "linear-gradient(to right, rgba(49, 102, 232, 0.05) 1px, transparent 1px)," +
      "linear-gradient(to bottom, rgba(49, 102, 232, 0.05) 1px, transparent 1px)";
    gridLayer.style.backgroundSize = `${major}px ${major}px, ${major}px ${major}px, ${minor}px ${minor}px, ${minor}px ${minor}px`;
    const pct = (z / (CSS_PX_PER_MM * S.calibration)) * 100;
    $("#zoomlabel").textContent = `${pct >= 10 ? Math.round(pct) : pct.toFixed(1)} %`;
  }

  function drawOverlay() {
    overlay.replaceChildren();
    if (!S.diagram || !S.geo) return;
    const z = S.view.zoom;
    const px = 1 / z;
    const c = S.diagram.canvas;
    const line = (x1, y1, x2, y2, attrs) => s("line", Object.assign({ x1, y1, x2, y2,
      "vector-effect": "non-scaling-stroke" }, attrs), overlay);
    const rect = (b, attrs) => s("rect", Object.assign({ x: b[0], y: b[1], width: Math.max(b[2], 0),
      height: Math.max(b[3], 0), fill: "none", "vector-effect": "non-scaling-stroke" }, attrs), overlay);
    /* Labels outside the paper sit on the app background; labels on the
       paper get a white halo so they stay legible over the figure. */
    const label = (x, y, text, color, attrs = {}, halo = false) => {
      const t = s("text", Object.assign({ x, y, "font-size": 11 * px, fill: color, class: "ov-label" },
        halo ? { stroke: "#fff", "stroke-width": 3 * px, "paint-order": "stroke", "stroke-linejoin": "round" } : {},
        attrs), overlay);
      t.textContent = text;
      return t;
    };
    const fmt = (v) => String(round2(v));
    /* Frame title above the paper, as in design tools (hidden while an element tag sits there). */
    const tagAtTop = !S.drag && [S.hover, ...S.sel].some((id) => id && S.boxes[id] && S.boxes[id].box
      && S.boxes[id].kind !== "edge" && S.boxes[id].box[1] < 12 * px);
    if (!tagAtTop) label(0, -8 * px, `${S.name || ""}  ·  ${fmt(c.width_mm)} × ${fmt(c.height_mm)} mm`, C.hint);
    if (S.view.guides) {
      /* Print-width markers below the paper; labels alternate rows so close widths do not collide. */
      Object.entries(S.tokens.widths_mm || {}).filter(([, width]) => width < c.width_mm - 0.01)
        .sort((a, b) => a[1] - b[1]).forEach(([name, width], i) => {
          const drop = (i % 2 ? 34 : 20) * px;
          line(width, 0, width, c.height_mm, { stroke: C.frameSoft, "stroke-dasharray": "3 4" });
          line(width, c.height_mm + 3 * px, width, c.height_mm + drop - 10 * px, { stroke: C.hint });
          label(width, c.height_mm + drop, `${width} mm · ${name}`, C.hint, { "text-anchor": "middle" });
        });
      for (const p of S.geo.panels) {
        if (p.hidden) continue;
        rect(p.box, { stroke: C.frame, "stroke-dasharray": "4 3" });
        if (p.content[1] > p.box[1]) {
          line(p.box[0], p.content[1], p.box[0] + p.box[2], p.content[1], { stroke: C.frameSoft, "stroke-dasharray": "2 3" });
        }
      }
    }
    for (const [id, info] of Object.entries(S.boxes)) {
      if (!info.hidden) continue;
      if (info.kind === "edge" && info.points && info.points.length) {
        s("polyline", { points: info.points.map((pt) => pt.join(",")).join(" "), fill: "none",
          stroke: C.frame, "stroke-dasharray": "2 2", "stroke-opacity": 0.85,
          "vector-effect": "non-scaling-stroke" }, overlay);
      } else if (info.box) {
        rect(info.box, { stroke: C.frame, "stroke-dasharray": "2 2" });
        label(info.box[0], info.box[1] - 3 * px, `${id} (hidden)`, C.hint, {}, info.box[1] > 4 * px);
      }
    }
    /* Name tag of the hovered or selected element. */
    const tagId = S.drag ? null : (S.sel.length === 1 ? S.sel[0] : (S.hover && !S.sel.includes(S.hover) ? S.hover : null));
    if (tagId && !S.editing && S.boxes[tagId] && S.boxes[tagId].box && S.boxes[tagId].kind !== "edge") {
      const b = S.boxes[tagId].box;
      label(b[0], b[1] - 5 * px, S.boxes[tagId].kind === "panel" ? `Panel ${tagId}` : tagId,
        S.sel.includes(tagId) ? C.sel : C.hint, { "font-weight": 600 }, b[1] > 6 * px);
    }
    if (S.hover && !S.sel.includes(S.hover) && !S.drag) {
      const b = S.boxes[S.hover];
      if (b && b.kind === "edge") drawEdgeHighlight(b, C.hover);
      else if (b && b.box) rect(b.box, { stroke: C.hover });
    }
    for (const id of S.sel) {
      const b = S.boxes[id];
      if (!b) continue;
      if (b.kind === "edge") drawEdgeHighlight(b, C.sel);
      else rect(b.box, { stroke: C.sel, "stroke-width": b.kind === "group" ? 1 : 1.5,
        "stroke-dasharray": b.kind === "group" ? "4 2" : null });
    }
    const one = resizable();
    if (one && !S.editing) {
      const b = boxOf(one);
      const hs = HANDLE_PX * px;
      for (const [name, fx, fy] of HANDLES) {
        const r = s("rect", { x: b[0] + b[2] * fx - hs / 2, y: b[1] + b[3] * fy - hs / 2, width: hs, height: hs,
          class: `handle h-${name}`, "data-handle": name, "vector-effect": "non-scaling-stroke" }, overlay);
        r.dataset.handle = name;
      }
    }
    const drag = S.drag;
    if (drag && drag.guides) {
      for (const g of drag.guides) {
        if (g.axis === "x") line(g.at, g.from, g.at, g.to, { stroke: C.guide });
        else line(g.from, g.at, g.to, g.at, { stroke: C.guide });
      }
    }
    if (drag && drag.marks) {
      for (const m of drag.marks) {
        const tick = 3 * px;
        if (m.axis === "x") {
          line(m.from, m.cross, m.to, m.cross, { stroke: C.spacing });
          line(m.from, m.cross - tick, m.from, m.cross + tick, { stroke: C.spacing });
          line(m.to, m.cross - tick, m.to, m.cross + tick, { stroke: C.spacing });
        } else {
          line(m.cross, m.from, m.cross, m.to, { stroke: C.spacing });
          line(m.cross - tick, m.from, m.cross + tick, m.from, { stroke: C.spacing });
          line(m.cross - tick, m.to, m.cross + tick, m.to, { stroke: C.spacing });
        }
      }
    }
    if (drag && drag.type === "create" && drag.rect) {
      rect(drag.rect, { stroke: C.sel, fill: "rgba(49, 102, 232, 0.06)", "stroke-width": 1.25 });
      label(drag.rect[0] + drag.rect[2], drag.rect[1] + drag.rect[3] + 14 * px,
        `${fmt(drag.rect[2])} × ${fmt(drag.rect[3])} mm`, C.sel, { "text-anchor": "end", "font-weight": 600 }, true);
    }
    if (drag && drag.type === "marquee" && drag.rect) {
      rect(drag.rect, { stroke: C.sel, fill: "rgba(49, 102, 232, 0.07)", "stroke-dasharray": "3 2" });
    }
    drawLintMarks(rect);
    if (S.flash) {
      for (const id of S.flash) {
        const info = S.boxes[id];
        if (!info) continue;
        if (info.kind === "edge") drawEdgeHighlight(info, C.flash);
        else if (info.box) rect(info.box, { stroke: C.flash, "stroke-width": 2.5 });
      }
    }
    drawEdgeHandles();
    drawAnchorLayer();
    if (S.drawComments) S.drawComments();
    if (drag && (drag.type === "connect" || drag.type === "reconnect")) {
      const from = drag.type === "connect" ? drag.from : (drag.origin || null);
      const to = drag.target || drag.anchor;
      if (from && drag.cursor) {
        const x2 = to ? to.x : drag.cursor[0];
        const y2 = to ? to.y : drag.cursor[1];
        line(from.x, from.y, x2, y2, { stroke: C.sel, "stroke-dasharray": "3 2" });
      }
    }
  }

  const ISSUE_TOL = 0.05;

  function boxesOverlap(a, b) {
    const w = Math.min(a[0] + a[2], b[0] + b[2]) - Math.max(a[0], b[0]);
    const h = Math.min(a[1] + a[3], b[1] + b[3]) - Math.max(a[1], b[1]);
    return w > ISSUE_TOL && h > ISSUE_TOL;
  }

  function containsBox(outer, inner) {
    return inner[0] >= outer[0] - ISSUE_TOL && inner[1] >= outer[1] - ISSUE_TOL
      && inner[0] + inner[2] <= outer[0] + outer[2] + ISSUE_TOL
      && inner[1] + inner[3] <= outer[1] + outer[3] + ISSUE_TOL;
  }

  /* Layout issues from the renderer geometry (the same measurements `check` uses). */
  function lintGeometry() {
    if (!S.geo || !S.diagram) return [];
    const out = [];
    const minimum = (S.tokens.lint && S.tokens.lint.min_font_pt) || 6;
    const scaleTol = (S.tokens.lint && S.tokens.lint.embed_scale_tolerance) || 0.005;
    const canvas = [0, 0, S.geo.canvas.width_mm, S.geo.canvas.height_mm];
    const panels = {};
    for (const p of S.geo.panels) panels[p.id] = p;
    const solid = S.geo.nodes.filter((n) => n.kind !== "group" && !n.hidden);
    for (const node of solid) {
      const text = node.text;
      if (text && text.overflow_x > ISSUE_TOL) {
        out.push({ level: "error", code: "text-overflow", id: node.id,
          message: `text is ${text.overflow_x.toFixed(2)} mm wider than '${node.id}'` });
      }
      if (text && text.overflow_y > ISSUE_TOL) {
        out.push({ level: "error", code: "text-overflow", id: node.id,
          message: `text is ${text.overflow_y.toFixed(2)} mm taller than '${node.id}'` });
      }
      const small = (node.fonts_pt || []).filter((size) => size < minimum - 1e-6);
      if (small.length) {
        out.push({ level: "error", code: "font-size", id: node.id,
          message: `font size ${Math.min(...small).toFixed(2)} pt in '${node.id}' is under ${minimum} pt` });
      }
      if (node.box && !containsBox(canvas, node.box)) {
        out.push({ level: "error", code: "outside-canvas", id: node.id, message: `'${node.id}' extends past the canvas` });
      }
      const panel = node.panel && panels[node.panel];
      if (panel && !panel.hidden && node.box && !containsBox(panel.content, node.box)) {
        out.push({ level: "error", code: "outside-panel", id: node.id,
          message: `'${node.id}' extends past panel '${node.panel}'` });
      }
    }
    for (let i = 0; i < solid.length; i++) {
      for (let j = i + 1; j < solid.length; j++) {
        const a = solid[i];
        const b = solid[j];
        if (a.box && b.box && boxesOverlap(a.box, b.box) && !containsBox(a.box, b.box) && !containsBox(b.box, a.box)) {
          out.push({ level: "error", code: "overlap", id: a.id, message: `'${a.id}' and '${b.id}' overlap` });
        }
      }
    }
    for (const edge of S.geo.edges) {
      if (edge.hidden) continue;
      const pts = edge.points || [];
      const ends = new Set([edge.from_id, edge.to_id]);
      for (const node of solid) {
        if (ends.has(node.id) || !node.box) continue;
        if (pts.some((p, k) => k && segmentHits(pts[k - 1], p, node.box))) {
          out.push({ level: "error", code: "edge-crosses-node", id: edge.id,
            message: `edge '${edge.id}' crosses '${node.id}'` });
          break;
        }
      }
      const label = edge.label;
      if (label) {
        const small = (label.fonts_pt || []).filter((size) => size < minimum - 1e-6);
        if (small.length) {
          out.push({ level: "error", code: "font-size", id: edge.id,
            message: `label font ${Math.min(...small).toFixed(2)} pt is under ${minimum} pt` });
        }
      }
    }
    for (const panel of S.geo.panels) {
      if (panel.hidden || !panel.embed || panel.embed.missing || panel.embed.scale == null) continue;
      if (Math.abs(panel.embed.scale - 1) > scaleTol) {
        out.push({ level: "warning", code: "embed-scaled", id: panel.id,
          message: `embed is scaled to ${(panel.embed.scale * 100).toFixed(1)}%` });
      }
    }
    return out;
  }

  /* Liang-Barsky, matching trazo diagram segment_hits_box, over renderer points. */
  function segmentHits(p, q, box) {
    const x0 = box[0] + ISSUE_TOL;
    const y0 = box[1] + ISSUE_TOL;
    const x1 = box[0] + box[2] - ISSUE_TOL;
    const y1 = box[1] + box[3] - ISSUE_TOL;
    if (x1 <= x0 || y1 <= y0) return false;
    const dx = q[0] - p[0];
    const dy = q[1] - p[1];
    let lo = 0;
    let hi = 1;
    for (const [denom, num] of [[-dx, p[0] - x0], [dx, x1 - p[0]], [-dy, p[1] - y0], [dy, y1 - p[1]]]) {
      if (denom === 0) {
        if (num < 0) return false;
      } else {
        const r = num / denom;
        if (denom < 0) lo = Math.max(lo, r);
        else hi = Math.min(hi, r);
      }
    }
    return lo < hi;
  }

  /* Memoised per render: lintGeometry compares every pair of boxes, and the
     lint list, the canvas marks and the inspector all ask for it. */
  let issuesMemo = { geo: null, server: null, sha: null, out: [] };
  function displayedIssues() {
    if (issuesMemo.geo === S.geo && issuesMemo.server === S.serverIssues && issuesMemo.sha === S.disk.sha
        && issuesMemo.json === S.disk.json) return issuesMemo.out;
    const out = computeIssues();
    issuesMemo = { geo: S.geo, server: S.serverIssues, sha: S.disk.sha, json: S.disk.json, out };
    return out;
  }

  function computeIssues() {
    const clean = S.diagram && S.disk.json && JSON.stringify(S.diagram) === S.disk.json;
    if (clean && S.serverIssues && S.serverFor === S.revision) return S.serverIssues;
    const seen = new Set();
    const out = [];
    for (const item of [...(S.issues || []), ...lintGeometry()]) {
      const key = `${item.code}|${item.id}|${item.message}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(item);
    }
    return out;
  }

  function drawLintMarks(rect) {
    for (const item of displayedIssues()) {
      if (!item.id || isHidden(item.id)) continue;
      const info = S.boxes[item.id];
      if (!info || !info.box) continue;
      const overflow = item.code === "text-overflow";
      rect(info.box, {
        stroke: item.level === "warning" ? C.warn : C.err,
        "stroke-width": overflow ? 1.6 : 1.2,
        "stroke-dasharray": overflow ? null : "3 2",
      });
    }
  }

  function renderLint() {
    const root = $("#lint");
    const issues = S.diagram ? displayedIssues() : [];
    const errors = issues.filter((item) => item.level === "error").length;
    setTabBadge("lint", issues.length, errors ? "err" : "");
    if (!root) return;
    if (!S.diagram) {
      root.replaceChildren();
      return;
    }
    const fromServer = S.serverFor === S.revision && S.serverIssues && JSON.stringify(S.diagram) === S.disk.json;
    const order = { error: 0, warning: 1 };
    const sorted = [...issues].sort((a, b) => (order[a.level] ?? 2) - (order[b.level] ?? 2));
    root.replaceChildren(...[
      h("div", { class: "source-note" }, Icons.node(fromServer ? "check" : "refresh", 14),
        fromServer ? "Checked on disk by trazo diagram check" : "Live check in this page; re-checked on disk after saving"),
      issues.length ? null : h("div", { class: "empty" }, Icons.node("check", 28), h("b", {}, "No issues"),
        "Text fits, nothing overlaps and every token and value is known."),
      ...sorted.map((item) => h("button", { type: "button", class: `issue ${item.level}`,
        onclick: () => { if (item.id && findItem(item.id)) { select([item.id]); zoomSelection(); } } },
        Icons.node(item.level === "error" ? "error" : "warning", 16),
        h("span", {}, item.message, h("span", { class: "where" }, item.id ? h("code", {}, item.id) : null,
          item.id ? " · " : "", item.code))))].filter(Boolean));
  }

  function scheduleCheck() {
    clearTimeout(S.checkTimer);
    if (!S.name || !S.diagram || S.drag || S.editing) return;
    if (JSON.stringify(S.diagram) !== S.disk.json) return;
    if (S.serverFor === S.revision && S.serverIssues) return;
    S.checkTimer = setTimeout(runCheck, 700);
  }

  async function runCheck() {
    if (!S.name || !S.diagram || JSON.stringify(S.diagram) !== S.disk.json) return;
    const name = S.name;
    const sha = S.disk.sha;
    const revision = S.revision;
    const res = await api("GET", `check/${encodeURIComponent(name)}`);
    if (!res.ok || !res.data || S.name !== name || S.disk.sha !== sha || S.revision !== revision
        || res.data.revision !== revision) return;
    S.serverIssues = res.data.issues || [];
    S.serverFor = revision;
    drawOverlay();
    renderLint();
    renderInspector();
  }

  function elementMap(diagram) {
    const map = {};
    if (!diagram) return map;
    for (const coll of COLLECTIONS) {
      for (const item of diagram[coll] || []) if (item && item.id) map[item.id] = JSON.stringify(item);
    }
    return map;
  }

  function changedIds(before, after) {
    const a = elementMap(before);
    const b = elementMap(after);
    const ids = new Set([...Object.keys(a), ...Object.keys(b)]);
    const changed = [];
    for (const id of ids) if (a[id] !== b[id]) changed.push(id);
    return changed;
  }

  function flashIds(ids) {
    S.flash = new Set(ids);
    drawOverlay();
    clearTimeout(S.flashTimer);
    S.flashTimer = setTimeout(() => { S.flash = null; if (S.diagram) drawOverlay(); }, 1600);
  }

  async function pollStatus(workspace, offline) {
    if (offline || !workspace?.diagrams) return;
    if (!S.name || S.saving || S.drag || S.editing || S.gone === S.name) return;
    const data = workspace.diagrams;
    const res = { status: data.diagrams[S.name] ? 200 : 404, ok: true, data };
    if (res.status === 404) {
      /* The file was deleted or renamed on disk: say so once and stop polling it. */
      S.gone = S.name;
      S.blocked = "error";
      updateStatus();
      showBanner("error", `${PATHS.diagrams}/${S.name}.json no longer exists on disk.`,
        ["It was deleted or renamed outside this page. Download this version to keep it."], [
          h("button", { type: "button", onclick: downloadMine }, "Download my version"),
          h("button", { type: "button", onclick: () => { location.hash = ""; } }, "All diagrams"),
        ]);
      return;
    }
    if (!res.ok || !res.data || !res.data.diagrams) return;
    if (S.onStatus) S.onStatus(res.data);
    const state = res.data.diagrams[S.name];
    if (!state) return;
    S.exportState = state.export_state;
    if (S.revision !== state.revision || S.inputs !== workspace.inputs) {
      if (await refreshInputs()) S.inputs = workspace.inputs;
    }
    updateStatus();
    const sha = state.sha256;
    if (!sha || sha === S.disk.sha) return;
    if (S.blocked === "conflict") {
      if (!S.conflict || S.conflict.sha256 !== sha) {
        S.conflict = Object.assign({}, S.conflict, { sha256: sha });
        if (!$("#banner").hidden) showConflict();
      }
      return;
    }
    const dirty = JSON.stringify(S.diagram) !== S.disk.json;
    const got = await api("GET", `file/${encodeURIComponent(S.name)}`);
    if (!got.ok || !got.data || !got.data.diagram || S.saving || S.drag || S.editing) return;
    if (got.data.sha256 === S.disk.sha) return;
    if (!dirty) {
      const changed = changedIds(S.diagram, got.data.diagram);
      await applyDiskVersion(got.data.diagram, got.data.sha256, { resetHistory: false });
      flashIds(changed);
      toast(changed.length ? `Updated on disk: ${changed.slice(0, 4).join(", ")}` : "Reloaded from disk");
      return;
    }
    S.blocked = "conflict";
    S.conflict = { sha256: got.data.sha256, text: got.data.text };
    showConflict();
    updateStatus();
  }

  function drawEdgeHighlight(b, color) {
    if (b.points.length) {
      s("polyline", { points: b.points.map((p) => p.join(",")).join(" "), fill: "none", stroke: color,
        "stroke-width": 3, "stroke-opacity": 0.55, "vector-effect": "non-scaling-stroke" }, overlay);
    }
    if (b.label) {
      const l = b.label.box;
      s("rect", { x: l[0], y: l[1], width: l[2], height: l[3], fill: "none", stroke: color,
        "vector-effect": "non-scaling-stroke" }, overlay);
    }
  }

  const HANDLES = [["nw", 0, 0], ["n", 0.5, 0], ["ne", 1, 0], ["e", 1, 0.5],
    ["se", 1, 1], ["s", 0.5, 1], ["sw", 0, 1], ["w", 0, 0.5]];
  const ANCHOR_SIDES = ["left", "right", "top", "bottom", "center"];

  /* Anchor dots on a node or panel box: left, right, top, bottom, center. */
  function anchorPoints(id) {
    const b = boxOf(id);
    if (!b || b[2] < 0 || b[3] < 0) return [];
    const [x, y, w, h] = b;
    const at = {
      left: [x, y + h / 2], right: [x + w, y + h / 2],
      top: [x + w / 2, y], bottom: [x + w / 2, y + h],
      center: [x + w / 2, y + h / 2],
    };
    return ANCHOR_SIDES.map((side) => ({ id, side, x: at[side][0], y: at[side][1], spec: `${id}.${side}` }));
  }

  function selectedEdge() {
    if (S.sel.length !== 1) return null;
    const found = findItem(S.sel[0]);
    return found && found.coll === "edges" ? found.item : null;
  }

  /* Nearest anchor of a node or panel under p, optionally skipping one id. */
  function bestAnchor(p, exceptId) {
    const tol = 10 / S.view.zoom;
    let best = null;
    for (const [id, info] of Object.entries(S.boxes)) {
      if (id === exceptId || !info.box || info.kind === "edge" || info.hidden || isHidden(id)) continue;
      if (!inBox(info.box, p, tol)) continue;
      for (const a of anchorPoints(id)) {
        const dist = Math.hypot(p[0] - a.x, p[1] - a.y);
        if (!best || dist < best.dist) best = Object.assign({ dist }, a);
      }
    }
    return best;
  }

  function drawAnchorLayer() {
    const r = (HANDLE_PX * 0.55) / S.view.zoom;
    const hosts = new Set();
    const drag = S.drag;
    if (drag && drag.type === "connect") hosts.add(drag.from.id);
    else if (!drag && S.hover && S.boxes[S.hover] && S.boxes[S.hover].kind !== "edge") hosts.add(S.hover);
    if (!drag && S.sel.length === 1 && S.boxes[S.sel[0]] && S.boxes[S.sel[0]].kind !== "edge") hosts.add(S.sel[0]);
    if (drag && drag.target) hosts.add(drag.target.id);
    if (drag && drag.anchor) hosts.add(drag.anchor.id);
    for (const id of hosts) {
      if (isHidden(id)) continue;
      for (const a of anchorPoints(id)) {
        const hot = (drag && drag.target && drag.target.spec === a.spec)
          || (drag && drag.anchor && drag.anchor.spec === a.spec);
        s("circle", {
          cx: a.x, cy: a.y, r: hot ? r * 1.45 : r, class: "anchor",
          "data-role": "anchor", "data-id": id, "data-side": a.side,
          fill: a.side === "center" ? C.sel : "#fff", stroke: C.sel,
          "stroke-width": hot ? 2 : 1.25, "pointer-events": "all",
          "vector-effect": "non-scaling-stroke",
        }, overlay);
      }
    }
  }

  function drawEdgeHandles() {
    const edge = selectedEdge();
    if (!edge || S.editing || isHidden(edge.id) || isLocked(edge.id)) return;
    const drag = S.drag;
    if (drag && drag.type !== "waypoint" && drag.type !== "reconnect") return;
    const r = (HANDLE_PX * 0.5) / S.view.zoom;
    const pts = (S.boxes[edge.id] && S.boxes[edge.id].points) || [];
    if (pts.length >= 2) {
      for (const [end, pt] of [["from", pts[0]], ["to", pts[pts.length - 1]]]) {
        s("circle", {
          cx: pt[0], cy: pt[1], r, class: "edge-end", "data-role": "edge-end",
          "data-edge": edge.id, "data-end": end, fill: C.sel, stroke: "#fff",
          "stroke-width": 1.5, "pointer-events": "all", "vector-effect": "non-scaling-stroke",
        }, overlay);
      }
    }
    (edge.waypoints || []).forEach((pt, i) => {
      s("rect", {
        x: pt[0] - r, y: pt[1] - r, width: r * 2, height: r * 2, class: "waypoint",
        "data-role": "waypoint", "data-edge": edge.id, "data-index": i,
        fill: "#fff", stroke: C.sel, "pointer-events": "all",
        "vector-effect": "non-scaling-stroke",
      }, overlay);
    });
    if (!drag && (edge.route || "orthogonal") === "orthogonal" && pts.length >= 2) {
      let best = 0;
      let bestLen = -1;
      for (let i = 1; i < pts.length; i++) {
        const len = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
        if (len > bestLen) {
          bestLen = len;
          best = i - 1;
        }
      }
      const mid = [(pts[best][0] + pts[best + 1][0]) / 2, (pts[best][1] + pts[best + 1][1]) / 2];
      s("circle", {
        cx: mid[0], cy: mid[1], r: r * 0.9, class: "waypoint add", "data-role": "waypoint-add",
        "data-edge": edge.id, fill: C.selSoft, stroke: C.sel, "pointer-events": "all",
        "vector-effect": "non-scaling-stroke",
      }, overlay);
    }
  }

  function snapPoint(p, alt) {
    if (alt) return [round2(p[0]), round2(p[1])];
    const g = gridStep();
    return [round2(Math.round(p[0] / g) * g), round2(Math.round(p[1] / g) * g)];
  }

  function putWaypoint(edge, index, point, creating) {
    const wps = (edge.waypoints || []).map((pt) => pt.slice());
    if (creating) wps.push(point);
    else wps[index] = point;
    edge.waypoints = wps;
  }

  function resizable() {
    if (S.sel.length !== 1) return null;
    const id = S.sel[0];
    const found = findItem(id);
    if (!found) return null;
    if (isLocked(id)) return null;
    if (found.coll === "panels") return id;
    if (found.coll === "nodes" && found.item.kind !== "group" && boxOf(id)) return id;
    return null;
  }

  /* ---------- view ---------- */

  function setZoom(z, cx, cy) {
    S.view.fitted = false;
    const r = viewport.getBoundingClientRect();
    const px = cx === undefined ? r.width / 2 : cx - r.left;
    const py = cy === undefined ? r.height / 2 : cy - r.top;
    const nz = clamp(z, MIN_ZOOM, MAX_ZOOM);
    const mx = (px - S.view.x) / S.view.zoom;
    const my = (py - S.view.y) / S.view.zoom;
    S.view.zoom = nz;
    S.view.x = px - mx * nz;
    S.view.y = py - my * nz;
    layoutStage();
    drawOverlay();
    if (S.editing) positionEditor();
  }

  function zoomTo(box, margin = 30) {
    if (!box) return;
    S.view.fitted = false;
    const r = viewport.getBoundingClientRect();
    const w = Math.max(box[2], 1);
    const hh = Math.max(box[3], 1);
    const z = clamp(Math.min((r.width - 2 * margin) / w, (r.height - 2 * margin) / hh), MIN_ZOOM, MAX_ZOOM);
    S.view.zoom = z;
    S.view.x = (r.width - w * z) / 2 - box[0] * z;
    S.view.y = (r.height - hh * z) / 2 - box[1] * z;
    layoutStage();
    drawOverlay();
    if (S.editing) positionEditor();
  }

  /* Fit leaves room for the frame title above and the print-width labels below. */
  const zoomFit = () => {
    if (!S.diagram) return;
    zoomTo([0, -4, S.diagram.canvas.width_mm, S.diagram.canvas.height_mm + 10], 40);
    S.view.fitted = true;
  };
  const zoomSelection = () => zoomTo(unionBox(S.sel.map(boxOf)), 60);
  const zoomPhysical = () => setZoom(CSS_PX_PER_MM * S.calibration);

  function clientToMm(e) {
    const r = paper.getBoundingClientRect();
    return [(e.clientX - r.left) / S.view.zoom, (e.clientY - r.top) / S.view.zoom];
  }

  /* ---------- hit testing and selection ---------- */

  function inBox(b, p, tol) {
    return b && p[0] >= b[0] - tol && p[0] <= b[0] + b[2] + tol && p[1] >= b[1] - tol && p[1] <= b[1] + b[3] + tol;
  }

  function segDist(p, a, b) {
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    const len = dx * dx + dy * dy;
    const t = len ? clamp(((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len, 0, 1) : 0;
    return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
  }

  /* The leaf node, edge or panel under a point (topmost first), or null. */
  function hitTest(p) {
    const tol = HIT_PX / S.view.zoom;
    for (const e of [...S.geo.edges].reverse()) {
      if (e.hidden || isHidden(e.id)) continue;
      const pts = e.points || [];
      for (let i = 1; i < pts.length; i++) if (segDist(p, pts[i - 1], pts[i]) <= tol) return e.id;
      if (e.label && inBox(e.label.box, p, 0)) return e.id;
    }
    const leaves = S.geo.nodes.filter((n) => n.kind !== "group" && !n.hidden && !isHidden(n.id));
    for (const n of [...leaves].reverse()) if (inBox(n.box, p, 0)) return n.id;
    for (const panel of [...S.geo.panels].reverse()) {
      if (panel.hidden || isHidden(panel.id)) continue;
      if (inBox(panel.box, p, 0)) return panel.id;
    }
    return null;
  }

  function select(ids) {
    S.sel = ids;
    updateUi();
  }

  function topLevelItems() {
    const parent = parentMap();
    return [
      ...(S.diagram.nodes || []).filter((n) => !parent[n.id]).map((n) => n.id),
      ...(S.diagram.edges || []).map((e) => e.id),
    ];
  }

  /* ---------- snapping ---------- */

  const linesOf = (b, axis) => (axis === "x" ? [b[0], b[0] + b[2] / 2, b[0] + b[2]] : [b[1], b[1] + b[3] / 2, b[1] + b[3]]);

  function snapTargets(movingIds) {
    const moved = new Set();
    for (const id of movingIds) {
      subtreeIds(id).forEach((x) => moved.add(x));
      if (kindOf(id) === "panel") {
        for (const n of S.diagram.nodes || []) if (n.panel === id) moved.add(n.id);
      }
    }
    const movedLeaves = new Set([...moved].flatMap((id) => leafIds(id)));
    const out = [{ id: "@canvas", kind: "canvas", box: [0, 0, S.diagram.canvas.width_mm, S.diagram.canvas.height_mm] }];
    for (const p of S.geo.panels) {
      if (moved.has(p.id)) continue;
      out.push({ id: p.id, kind: "panel", box: p.box });
      if (p.content[1] !== p.box[1]) out.push({ id: p.id, kind: "panel", box: p.content });
    }
    for (const n of S.geo.nodes) {
      if (moved.has(n.id)) continue;
      if (n.kind === "group" && leafIds(n.id).some((l) => movedLeaves.has(l))) continue;
      out.push({ id: n.id, kind: "node", box: n.box });
    }
    return out;
  }

  function nearestLine(values, targets, axis, tol) {
    let best = null;
    for (const t of targets) {
      for (const tv of linesOf(t.box, axis)) {
        for (const mv of values) {
          const delta = tv - mv;
          if (Math.abs(delta) <= tol && (!best || Math.abs(delta) < Math.abs(best.delta) - 1e-9)) best = { delta, at: tv };
        }
      }
    }
    return best;
  }

  function guideFor(axis, at, moving, targets) {
    const q = axis === "x" ? 1 : 0;
    const spans = [[moving[q], moving[q] + moving[q + 2]]];
    for (const t of targets) {
      if (linesOf(t.box, axis).some((v) => Math.abs(v - at) < 1e-6)) spans.push([t.box[q], t.box[q] + t.box[q + 2]]);
    }
    return { axis, at, from: Math.min(...spans.map((x) => x[0])), to: Math.max(...spans.map((x) => x[1])) };
  }

  /* Equal-spacing snap of box b along an axis, against node targets in the same row or column. */
  function spacingSnap(axis, b, targets, tol) {
    const [p, q] = axis === "x" ? [0, 1] : [1, 0];
    const size = b[p + 2];
    const start = b[p];
    const end = start + size;
    const band = targets.filter((t) => t.kind === "node" && Math.min(t.box[q] + t.box[q + 2], b[q] + b[q + 2])
      - Math.max(t.box[q], b[q]) > 0).sort((u, v) => u.box[p] - v.box[p]);
    const gaps = [];
    for (let i = 1; i < band.length; i++) {
      const a = band[i - 1].box;
      const c = band[i].box;
      const g = c[p] - (a[p] + a[p + 2]);
      if (g > 0.01) gaps.push({ g, from: a[p] + a[p + 2], to: c[p], cross: (Math.max(a[q], c[q]) + Math.min(a[q] + a[q + 2], c[q] + c[q + 2])) / 2 });
    }
    let left = null;
    let right = null;
    for (const t of band) {
      const tEnd = t.box[p] + t.box[p + 2];
      if (tEnd <= start + tol && (!left || tEnd > left.box[p] + left.box[p + 2])) left = t;
      if (t.box[p] >= end - tol && (!right || t.box[p] < right.box[p])) right = t;
    }
    const cross = b[q] + b[q + 2] / 2;
    const options = [];
    if (left && right) {
      const lEnd = left.box[p] + left.box[p + 2];
      const pos = (lEnd + right.box[p] - size) / 2;
      if (pos > lEnd) options.push({ pos, marks: [{ from: lEnd, to: pos }, { from: pos + size, to: right.box[p] }] });
    }
    for (const gap of gaps) {
      if (left) {
        const lEnd = left.box[p] + left.box[p + 2];
        options.push({ pos: lEnd + gap.g, marks: [{ from: lEnd, to: lEnd + gap.g }, gap] });
      }
      if (right) {
        options.push({ pos: right.box[p] - gap.g - size, marks: [{ from: right.box[p] - gap.g, to: right.box[p] }, gap] });
      }
    }
    let best = null;
    for (const o of options) {
      const delta = o.pos - start;
      if (Math.abs(delta) <= tol && (!best || Math.abs(delta) < Math.abs(best.delta))) best = { delta, marks: o.marks };
    }
    if (!best) return null;
    best.marks = best.marks.map((m) => ({ axis, from: m.from, to: m.to, cross: m.cross !== undefined ? m.cross : cross }));
    return best;
  }

  /* Snap a moving box: guides first, then equal spacing, then the grid. */
  function snapMove(box0, dx, dy, movingIds) {
    const tol = SNAP_PX / S.view.zoom;
    const targets = snapTargets(movingIds);
    const guides = [];
    const marks = [];
    const out = [dx, dy];
    ["x", "y"].forEach((axis, i) => {
      const b = [box0[0] + out[0], box0[1] + out[1], box0[2], box0[3]];
      const near = nearestLine(linesOf(b, axis), targets, axis, tol);
      if (near) {
        out[i] += near.delta;
        return;
      }
      const spaced = spacingSnap(axis, b, targets, tol);
      if (spaced) {
        out[i] += spaced.delta;
        marks.push(...spaced.marks);
        return;
      }
      const g = gridStep();
      out[i] += Math.round(b[i] / g) * g - b[i];
    });
    const fin = [box0[0] + out[0], box0[1] + out[1], box0[2], box0[3]];
    for (const axis of ["x", "y"]) {
      for (const v of linesOf(fin, axis)) {
        const hit = targets.find((t) => linesOf(t.box, axis).some((tv) => Math.abs(tv - v) < 1e-6));
        if (hit && !guides.some((g) => g.axis === axis && Math.abs(g.at - v) < 1e-6)) guides.push(guideFor(axis, v, fin, targets));
      }
    }
    return { dx: out[0], dy: out[1], guides, marks };
  }

  function snapEdge(axis, value, targets, tol) {
    const near = nearestLine([value], targets, axis, tol);
    if (near) return { value: value + near.delta, guide: near.at };
    const g = gridStep();
    return { value: Math.round(value / g) * g, guide: null };
  }

  /* ---------- pointer interaction ---------- */

  viewport.addEventListener("pointerdown", (e) => {
    hideMenu();
    $("#tip").hidden = true;
    if (!S.diagram || S.editing) return;
    viewport.focus({ preventScroll: true });
    if (e.button === 1 || (e.button === 0 && S.space)) {
      S.drag = { type: "pan", sx: e.clientX, sy: e.clientY, vx: S.view.x, vy: S.view.y };
      viewport.classList.add("panning");
      viewport.setPointerCapture(e.pointerId);
      e.preventDefault();
      return;
    }
    if (e.button !== 0) return;
    const p = clientToMm(e);
    const role = e.target.dataset && e.target.dataset.role;
    if (role === "comment" && window.DiagramComments) {
      showTab("comments");
      DiagramComments.focus(e.target.dataset.comment);
      return;
    }
    if (S.tool === "comment") {
      e.preventDefault(); // keep the focus in the comment box that pin() focuses
      const leaf = hitTest(p);
      const target = leaf ? chainOf(leaf)[0] : null;
      if (target) select([target]);
      setTool("select");
      if (window.DiagramComments) {
        DiagramComments.pin(target ? { target } : { point: [round2(p[0]), round2(p[1])] });
      }
      return;
    }
    if (S.tool && S.tool !== "select") {
      S.drag = { type: "create", kind: S.tool, start: snapPoint(p, e.altKey), rect: null };
      viewport.setPointerCapture(e.pointerId);
      e.preventDefault();
      return;
    }
    if (role === "anchor") {
      const a = anchorPoints(e.target.dataset.id).find((x) => x.side === e.target.dataset.side);
      if (a) {
        S.drag = { type: "connect", from: a, cursor: p, target: null };
        viewport.setPointerCapture(e.pointerId);
        e.preventDefault();
        return;
      }
    }
    if ((role === "edge-end" || role === "waypoint" || role === "waypoint-add") && isLocked(e.target.dataset.edge)) {
      toast("Locked", "lock");
      return;
    }
    if (role === "edge-end") {
      const edge = findItem(e.target.dataset.edge);
      const pts = edge && S.boxes[edge.item.id] && S.boxes[edge.item.id].points;
      const origin = pts && pts.length ? (e.target.dataset.end === "from" ? pts[0] : pts[pts.length - 1]) : p;
      S.drag = { type: "reconnect", edgeId: e.target.dataset.edge, end: e.target.dataset.end,
        origin: { x: origin[0], y: origin[1] }, cursor: p, anchor: null, base: JSON.stringify(S.diagram) };
      viewport.setPointerCapture(e.pointerId);
      e.preventDefault();
      return;
    }
    if (role === "waypoint" || role === "waypoint-add") {
      S.drag = { type: "waypoint", edgeId: e.target.dataset.edge,
        index: role === "waypoint" ? Number(e.target.dataset.index) : 0,
        creating: role === "waypoint-add", base: JSON.stringify(S.diagram),
        moved: false, x: p[0], y: p[1], sx: e.clientX, sy: e.clientY };
      viewport.setPointerCapture(e.pointerId);
      e.preventDefault();
      return;
    }
    const handle = e.target.dataset && e.target.dataset.handle;
    if (handle && resizable()) {
      const id = resizable();
      S.drag = { type: "resize", id, handle, start: p, box0: boxOf(id).slice(), base: JSON.stringify(S.diagram),
        targets: snapTargets([id]), moved: false };
      viewport.setPointerCapture(e.pointerId);
      return;
    }
    const leaf = hitTest(p);
    if (!leaf) {
      S.drag = { type: "marquee", start: p, additive: e.shiftKey, before: e.shiftKey ? S.sel.slice() : [] };
      if (!e.shiftKey) select([]);
      viewport.setPointerCapture(e.pointerId);
      return;
    }
    const chain = chainOf(leaf);
    let at = -1;
    for (let i = 0; i < chain.length; i++) if (S.sel.includes(chain[i])) at = i;
    const target = at >= 0 ? chain[at] : chain[0];
    const wasSelected = at >= 0;
    if (e.shiftKey) {
      select(wasSelected ? S.sel.filter((x) => x !== target) : [...S.sel, target]);
      if (wasSelected) return;
    } else if (!wasSelected) {
      select([target]);
    }
    const ids = S.sel.filter((id) => !isLocked(id));
    if (!ids.length) return;
    S.drag = { type: "move", start: p, ids, base: JSON.stringify(S.diagram), box0: unionBox(ids.map(boxOf)),
      moved: false, sx: e.clientX, sy: e.clientY, click: { chain, at, wasSelected: wasSelected && !e.shiftKey } };
    viewport.setPointerCapture(e.pointerId);
  });

  viewport.addEventListener("pointermove", (e) => {
    if (!S.diagram || !S.geo) return;
    const p = clientToMm(e);
    $("#pos").textContent = `x ${p[0].toFixed(2)}  y ${p[1].toFixed(2)} mm`;
    const drag = S.drag;
    if (!drag) {
      if ($("#menu").hidden) hover(p, e);
      return;
    }
    if (drag.type === "pan") {
      S.view.fitted = false;
      S.view.x = drag.vx + e.clientX - drag.sx;
      S.view.y = drag.vy + e.clientY - drag.sy;
      layoutStage();
      return;
    }
    if (drag.type === "create") {
      const q = snapPoint(p, e.altKey);
      drag.rect = [Math.min(drag.start[0], q[0]), Math.min(drag.start[1], q[1]),
        Math.abs(q[0] - drag.start[0]), Math.abs(q[1] - drag.start[1])];
      drawOverlay();
      return;
    }
    if (drag.type === "marquee") {
      const r = [Math.min(drag.start[0], p[0]), Math.min(drag.start[1], p[1]),
        Math.abs(p[0] - drag.start[0]), Math.abs(p[1] - drag.start[1])];
      drag.rect = r;
      const inside = topLevelItems().filter((id) => {
        if (isHidden(id)) return false;
        const b = boxOf(id);
        return b && b[0] >= r[0] && b[1] >= r[1] && b[0] + b[2] <= r[0] + r[2] && b[1] + b[3] <= r[1] + r[3];
      });
      S.sel = drag.additive ? [...new Set([...drag.before, ...inside])] : inside;
      drawOverlay();
      updateSelInfo();
      return;
    }
    if (drag.type === "move") {
      if (!drag.moved && Math.hypot(e.clientX - drag.sx, e.clientY - drag.sy) < DRAG_START_PX) return;
      drag.moved = true;
      let dx = p[0] - drag.start[0];
      let dy = p[1] - drag.start[1];
      drag.guides = [];
      drag.marks = [];
      if (!e.altKey && drag.box0) {
        const snapped = snapMove(drag.box0, dx, dy, drag.ids);
        [dx, dy] = [snapped.dx, snapped.dy];
        drag.guides = snapped.guides;
        drag.marks = snapped.marks;
      }
      drag.dx = dx;
      drag.dy = dy;
      draftRender((d) => applyMove(d, drag.ids, dx, dy));
      return;
    }
    if (drag.type === "connect") {
      drag.cursor = p;
      drag.target = bestAnchor(p, drag.from.id);
      drawOverlay();
      return;
    }
    if (drag.type === "reconnect") {
      drag.cursor = p;
      drag.anchor = bestAnchor(p, null);
      if (drag.anchor) draftRender((d) => { findItem(drag.edgeId, d).item[drag.end] = drag.anchor.spec; });
      else drawOverlay();
      return;
    }
    if (drag.type === "waypoint") {
      if (!drag.moved && !drag.creating
          && Math.hypot(e.clientX - drag.sx, e.clientY - drag.sy) < DRAG_START_PX) return;
      drag.moved = true;
      const pt = snapPoint(p, e.altKey);
      drag.x = pt[0];
      drag.y = pt[1];
      draftRender((d) => putWaypoint(findItem(drag.edgeId, d).item, drag.index, pt, drag.creating));
      return;
    }
    if (drag.type === "resize") {
      drag.moved = true;
      const [x0, y0, w0, h0] = drag.box0;
      let l = x0;
      let t = y0;
      let r = x0 + w0;
      let b = y0 + h0;
      const dx = p[0] - drag.start[0];
      const dy = p[1] - drag.start[1];
      const hd = drag.handle;
      const tol = SNAP_PX / S.view.zoom;
      drag.guides = [];
      const edge = (axis, value) => {
        if (e.altKey) return value;
        const snapped = snapEdge(axis, value, drag.targets, tol);
        if (snapped.guide !== null) {
          drag.guides.push(guideFor(axis, snapped.guide, [l, t, r - l, b - t], drag.targets));
        }
        return snapped.value;
      };
      if (hd.includes("w")) l = Math.min(edge("x", x0 + dx), r - MIN_SIZE_MM);
      if (hd.includes("e")) r = Math.max(edge("x", x0 + w0 + dx), l + MIN_SIZE_MM);
      if (hd.includes("n")) t = Math.min(edge("y", y0 + dy), b - MIN_SIZE_MM);
      if (hd.includes("s")) b = Math.max(edge("y", y0 + h0 + dy), t + MIN_SIZE_MM);
      drag.box = [l, t, r - l, b - t];
      draftRender((d) => applyBox(d, drag.id, drag.box));
    }
  });

  let draftFrame = 0;
  function draftRender(mutate) {
    const draft = JSON.parse(S.drag.base);
    mutate(draft);
    S.diagram = draft;
    if (draftFrame) return;
    draftFrame = requestAnimationFrame(() => {
      draftFrame = 0;
      render();
    });
  }

  function endDrag(e) {
    const drag = S.drag;
    if (!drag) return;
    S.drag = null;
    viewport.classList.remove("panning");
    if (draftFrame) {
      cancelAnimationFrame(draftFrame);
      draftFrame = 0;
    }
    if (drag.type === "move") {
      if (drag.moved) {
        S.diagram = JSON.parse(drag.base);
        const label = drag.ids.length > 1 ? `move ${drag.ids.length} items` : `move ${drag.ids[0]}`;
        if (!commit(label, (d) => applyMove(d, drag.ids, drag.dx, drag.dy), { base: drag.base })) render();
      } else if (drag.click.wasSelected && drag.click.at < drag.click.chain.length - 1) {
        select([drag.click.chain[drag.click.at + 1]]);
      }
    } else if (drag.type === "resize" && drag.moved && drag.box) {
      S.diagram = JSON.parse(drag.base);
      if (!commit(`resize ${drag.id}`, (d) => applyBox(d, drag.id, drag.box), { base: drag.base })) render();
    } else if (drag.type === "marquee") {
      updateUi();
    } else if (drag.type === "create") {
      const r = drag.rect;
      const drawn = r && r[2] >= 2 && (drag.kind === "text" || r[3] >= 2);
      setTool("select");
      if (drag.kind === "panel") addPanel(drawn ? r.slice(0, 2) : drag.start, drawn ? r : null);
      else addNode(drag.kind, drawn ? r.slice(0, 2) : drag.start, drawn ? r : null, true);
    } else if (drag.type === "connect" && drag.target && drag.target.id !== drag.from.id) {
      const from = drag.from;
      const target = drag.target;
      const newId = uniqueId(`e-${from.id}-${target.id}`, usedIds());
      commit("add edge", (d) => {
        d.edges = d.edges || [];
        d.edges.push({ id: newId, from: from.spec, to: target.spec });
      }, { select: [newId] });
    } else if (drag.type === "reconnect") {
      const spec = drag.anchor && drag.anchor.spec;
      S.diagram = JSON.parse(drag.base);
      if (spec) {
        if (!commit(`reconnect ${drag.edgeId}`, (d) => { findItem(drag.edgeId, d).item[drag.end] = spec; },
          { base: drag.base })) render();
      } else render();
    } else if (drag.type === "waypoint" && drag.moved) {
      const point = [round2(drag.x), round2(drag.y)];
      S.diagram = JSON.parse(drag.base);
      if (!commit(drag.creating ? `add waypoint to ${drag.edgeId}` : `move waypoint of ${drag.edgeId}`,
        (d) => putWaypoint(findItem(drag.edgeId, d).item, drag.index, point, drag.creating),
        { base: drag.base })) render();
    }
    drawOverlay();
  }

  viewport.addEventListener("pointerup", endDrag);
  viewport.addEventListener("pointercancel", endDrag);

  function hover(p, e) {
    let id = hitTest(p);
    if (!id && S.hover && S.boxes[S.hover] && S.boxes[S.hover].kind !== "edge") {
      const tol = (HANDLE_PX + 3) / S.view.zoom;
      if (anchorPoints(S.hover).some((a) => Math.hypot(p[0] - a.x, p[1] - a.y) <= tol)) id = S.hover;
    }
    if (id !== S.hover) {
      S.hover = id;
      drawOverlay();
    }
    const overAnchor = anchorPoints(id || "").some((a) => Math.hypot(p[0] - a.x, p[1] - a.y) <= (HANDLE_PX * 0.7) / S.view.zoom);
    viewport.style.cursor = overAnchor ? "crosshair" : "";
    const found = id && findItem(id);
    const keys = found && found.coll === "nodes" ? textKeys(found.item) : [];
    const tip = $("#tip");
    if (keys.length) {
      tip.textContent = keys.map((k) => `{{v:${k}}} = ${k in S.values ? S.values[k] : "(unknown key)"}`).join("\n");
      tip.style.left = `${e.clientX + 14}px`;
      tip.style.top = `${e.clientY + 14}px`;
      tip.hidden = false;
    } else {
      tip.hidden = true;
    }
  }

  viewport.addEventListener("pointerleave", () => {
    $("#tip").hidden = true;
    if (S.hover) {
      S.hover = null;
      drawOverlay();
    }
  });

  viewport.addEventListener("dblclick", (e) => {
    if (!S.diagram || S.editing) return;
    const role = e.target.dataset && e.target.dataset.role;
    const p = clientToMm(e);
    if ((role === "waypoint" || role === "waypoint-add") && isLocked(e.target.dataset.edge)) return toast("Locked", "lock");
    if (role === "waypoint") {
      const edgeId = e.target.dataset.edge;
      const index = Number(e.target.dataset.index);
      commit(`remove waypoint of ${edgeId}`, (d) => {
        const edge = findItem(edgeId, d).item;
        edge.waypoints = (edge.waypoints || []).filter((_, i) => i !== index);
        if (!edge.waypoints.length) delete edge.waypoints;
      });
      return;
    }
    if (role === "waypoint-add") {
      const edgeId = e.target.dataset.edge;
      const point = snapPoint(p, e.altKey);
      commit(`add waypoint to ${edgeId}`, (d) => putWaypoint(findItem(edgeId, d).item, 0, point, true));
      return;
    }
    const leaf = hitTest(p);
    const found = leaf && findItem(leaf);
    if (found && found.coll === "edges") {
      if (isLocked(leaf)) return toast("Locked", "lock");
      const point = snapPoint(p, e.altKey);
      commit(`add waypoint to ${leaf}`, (d) => putWaypoint(findItem(leaf, d).item, 0, point, true));
      return;
    }
    if (found && found.coll === "nodes" && TEXT_KINDS.has(found.item.kind)) startTextEdit(leaf);
  });

  viewport.addEventListener("wheel", (e) => {
    if (!S.diagram) return;
    e.preventDefault();
    if (e.ctrlKey || e.metaKey) {
      setZoom(S.view.zoom * Math.exp(-e.deltaY * 0.01), e.clientX, e.clientY);
    } else {
      S.view.fitted = false;
      S.view.x -= e.shiftKey && !e.deltaX ? e.deltaY : e.deltaX;
      S.view.y -= e.shiftKey && !e.deltaX ? 0 : e.deltaY;
      layoutStage();
      if (S.editing) positionEditor();
    }
  }, { passive: false });

  viewport.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    $("#tip").hidden = true;
    if (!S.diagram || S.editing) return;
    const p = clientToMm(e);
    const leaf = hitTest(p);
    if (leaf) {
      const chain = chainOf(leaf);
      if (!chain.some((id) => S.sel.includes(id))) select([chain[0]]);
    }
    showMenu(e.clientX, e.clientY, p);
  });

  /* ---------- text editing ---------- */

  function blocksOf(node) {
    const text = node.text;
    if (text === undefined || text === null) return { blocks: [{ style: "node-body", runs: [] }], form: "none" };
    if (typeof text === "string") return { blocks: [{ style: "node-body", runs: [{ t: text }] }], form: "string" };
    return {
      form: "blocks",
      blocks: text.map((b) => ({ style: b.style || "node-body", hasStyle: "style" in b,
        runs: typeof b.t === "string" ? [{ t: b.t }] : (b.runs || []).map(clone),
        props: Object.fromEntries(Object.entries(b).filter(([k]) => !["style", "t", "runs"].includes(k))) })),
    };
  }

  function styleCss(styleName, z) {
    const st = S.tokens.text_styles[styleName] || S.tokens.text_styles["node-body"];
    const colors = DiagramRenderer.colorTable(S.tokens);
    return {
      fontFamily: `'${S.tokens.fonts.family}', ${S.tokens.fonts.fallback.join(", ")}`,
      fontSize: `${st.size_pt * PT * z}px`,
      fontWeight: String(st.weight),
      lineHeight: `${st.line_height_pt * PT * z}px`,
      color: colors[st.color] || "#000",
      textTransform: st.uppercase ? "uppercase" : "none",
      letterSpacing: st.letter_spacing_em ? `${st.letter_spacing_em}em` : "normal",
      fontStyle: st.italic ? "italic" : "normal",
    };
  }

  function runNodes(run) {
    const colors = DiagramRenderer.colorTable(S.tokens);
    const parts = [];
    const text = String(run.t || "");
    let last = 0;
    for (const m of text.matchAll(VALUE_KEY)) {
      if (m.index > last) parts.push(text.slice(last, m.index));
      const known = m[1] in S.values;
      parts.push(h("span", { class: "v", contenteditable: "false", "data-key": m[1],
        title: `{{v:${m[1]}}}${known ? "" : " (unknown key)"}` }, known ? S.values[m[1]] : m[0]));
      last = m.index + m[0].length;
    }
    if (last < text.length) parts.push(text.slice(last));
    let nodes = [];
    for (const part of parts) {
      if (typeof part !== "string") {
        nodes.push(part);
        continue;
      }
      part.split("\n").forEach((piece, i) => {
        if (i) nodes.push(h("br"));
        if (piece) nodes.push(document.createTextNode(piece));
      });
    }
    const wrap = (tag, attrs) => {
      const el = h(tag, attrs);
      el.append(...nodes);
      nodes = [el];
    };
    if (run.color) wrap("span", { "data-color": run.color, style: { color: colors[run.color] || "" } });
    if (run.sub) wrap("sub");
    if (run.sup) wrap("sup");
    if (run.italic) wrap("i");
    if (run.bold) wrap("b");
    return nodes;
  }

  function startTextEdit(id, selectAll) {
    const found = findItem(id);
    if (!found || !TEXT_KINDS.has(found.item.kind)) return;
    if (isLocked(id)) return toast("Locked", "lock");
    if (!S.sel.includes(id)) select([id]);
    const { blocks, form } = blocksOf(found.item);
    const ed = h("div", { class: "text-editor", contenteditable: "true", spellcheck: "false" });
    blocks.forEach((b, i) => {
      const div = h("div", { dataset: { style: b.style, index: String(i), hasStyle: b.hasStyle ? "1" : "" } });
      div.append(...b.runs.flatMap(runNodes));
      if (!div.childNodes.length) div.append(h("br"));
      ed.append(div);
    });
    stage.appendChild(ed);
    const bar = formatBar(ed);
    stage.appendChild(bar);
    S.editing = { id, el: ed, bar, form, blocks, auto: found.item.kind === "text" && found.item.w === undefined };
    positionEditor();
    hideEditedText();
    drawOverlay();
    ed.addEventListener("keydown", editorKey);
    ed.addEventListener("input", () => chipify(ed));
    ed.addEventListener("paste", (e) => {
      e.preventDefault();
      document.execCommand("insertText", false, (e.clipboardData || window.clipboardData).getData("text/plain"));
    });
    ed.addEventListener("blur", () => setTimeout(() => {
      if (S.editing && S.editing.el === ed && document.activeElement !== ed) finishTextEdit(true);
    }, 0));
    ed.focus();
    const range = document.createRange();
    range.selectNodeContents(ed);
    if (!selectAll) range.collapse(false);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    updateUi();
  }

  /* Replace a completed {{v:key}} typed into the editor with a value chip. */
  function chipify(ed) {
    const walker = document.createTreeWalker(ed, NodeFilter.SHOW_TEXT);
    const found = [];
    let n;
    while ((n = walker.nextNode())) {
      if (!n.parentElement.closest(".v") && /\{\{v:[A-Za-z0-9_.\-]+\}\}/.test(n.nodeValue)) found.push(n);
    }
    let last = null;
    for (const textNode of found) {
      const frag = document.createDocumentFragment();
      for (const part of runNodes({ t: textNode.nodeValue })) frag.append(part);
      last = frag.lastChild;
      textNode.replaceWith(frag);
    }
    if (last) {
      const range = document.createRange();
      range.setStartAfter(last);
      range.collapse(true);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    }
  }

  function positionEditor() {
    const ed = S.editing && S.editing.el;
    const b = S.editing && S.boxes[S.editing.id];
    if (!ed || !b) return;
    const z = S.view.zoom;
    const c = b.content || b.box;
    ed.style.left = `${c[0] * z}px`;
    ed.style.top = `${c[1] * z}px`;
    ed.style.minHeight = `${Math.max(c[3], 3) * z}px`;
    if (S.editing.auto) {
      ed.style.width = "";
      ed.style.minWidth = `${Math.max(c[2], 10) * z}px`;
      ed.style.whiteSpace = "pre";
    } else {
      ed.style.width = `${Math.max(c[2], 3) * z}px`;
    }
    for (const div of ed.children) {
      Object.assign(div.style, styleCss(div.dataset.style, z));
      const i = Number(div.dataset.index);
      const st = S.tokens.text_styles[div.dataset.style] || {};
      const props = (S.editing.blocks[i] && S.editing.blocks[i].props) || {};
      const before = div === ed.firstElementChild ? 0
        : (props.space_before_pt !== undefined ? props.space_before_pt : st.space_before_pt || 0);
      div.style.marginTop = `${before * PT * z}px`;
      if (props.align) div.style.textAlign = props.align;
    }
    if (S.editing.bar) {
      S.editing.bar.style.left = `${c[0] * z}px`;
      S.editing.bar.style.top = `${c[1] * z - 44}px`;
    }
  }

  /* Floating format bar over the text being edited. Buttons act on mousedown
     with preventDefault, so the text keeps the focus and the selection. */
  const FORMATS = [["bold", "B", "Bold (⌘B)"], ["italic", "I", "Italic (⌘I)"],
    ["subscript", "x₂", "Subscript (⌘,)"], ["superscript", "x²", "Superscript (⌘.)"]];

  function formatBar(ed) {
    const buttons = FORMATS.map(([cmd, label, title]) => h("button", { type: "button", class: `fmt fmt-${cmd}`, title,
      onmousedown: (e) => { e.preventDefault(); document.execCommand(cmd); refreshFormatBar(); } }, label));
    const done = h("button", { type: "button", class: "fmt done", title: "Finish editing (Esc)",
      onmousedown: (e) => { e.preventDefault(); finishTextEdit(true); } }, "Done");
    return h("div", { class: "format-bar", role: "toolbar", "aria-label": "Text format",
      onpointerdown: (e) => e.stopPropagation() }, ...buttons, h("span", { class: "fmt-sep" }), done);
  }

  function refreshFormatBar() {
    const bar = S.editing && S.editing.bar;
    if (!bar) return;
    for (const [cmd] of FORMATS) {
      let on = false;
      try { on = document.queryCommandState(cmd); } catch (err) { on = false; }
      const b = bar.querySelector(`.fmt-${cmd}`);
      if (b) b.classList.toggle("on", on);
    }
  }
  document.addEventListener("selectionchange", () => { if (S.editing) refreshFormatBar(); });

  function hideEditedText() {
    if (!S.editing) return;
    const g = paper.querySelector(`g[data-id="${CSS.escape(S.editing.id)}"]`);
    if (g) for (const t of g.querySelectorAll("text")) t.style.visibility = "hidden";
  }

  function editorKey(e) {
    e.stopPropagation();
    const mod = e.metaKey || e.ctrlKey;
    if (e.key === "Escape" || (mod && e.key === "Enter")) {
      e.preventDefault();
      finishTextEdit(true);
      return;
    }
    const commands = { KeyB: "bold", KeyI: "italic", Comma: "subscript", Period: "superscript" };
    if (mod && commands[e.code]) {
      e.preventDefault();
      document.execCommand(commands[e.code]);
      refreshFormatBar();
    } else if (mod && e.code === "KeyS") {
      e.preventDefault();
      finishTextEdit(true);
      save(false);
    }
  }

  function sameFlags(a, b) {
    return ["bold", "italic", "sub", "sup", "color"].every((k) => (a[k] || false) === (b[k] || false));
  }

  /* Rebuild text blocks from the editor DOM (inverse of runNodes). */
  function readEditor(ed) {
    const out = [];
    let current = null;
    const begin = (el) => {
      current = { el, runs: [] };
      out.push(current);
    };
    const push = (t, f) => {
      if (!t) return;
      if (!current) begin(null);
      const last = current.runs[current.runs.length - 1];
      if (last && sameFlags(last.f, f)) last.t += t;
      else current.runs.push({ t, f: Object.assign({}, f) });
    };
    const walk = (node, f, top) => {
      for (const child of node.childNodes) {
        if (child.nodeType === Node.TEXT_NODE) {
          push(child.nodeValue.replace(/\u00a0/g, " "), f);
          continue;
        }
        if (child.nodeType !== Node.ELEMENT_NODE) continue;
        const tag = child.tagName;
        if (tag === "BR") {
          push("\n", f);
          continue;
        }
        if (child.dataset.key) {
          push(`{{v:${child.dataset.key}}}`, f);
          continue;
        }
        if ((tag === "DIV" || tag === "P") && top) {
          begin(child);
          walk(child, f, false);
          if (child.lastChild && child.lastChild.nodeName === "BR") {
            const last = current.runs[current.runs.length - 1];
            if (last && last.t.endsWith("\n")) last.t = last.t.slice(0, -1);
          }
          current = null;
          continue;
        }
        const g = Object.assign({}, f);
        const weight = child.style.fontWeight;
        if (tag === "B" || tag === "STRONG" || weight === "bold" || Number(weight) >= 600) g.bold = true;
        if (weight === "normal" || (weight && Number(weight) < 600)) delete g.bold;
        if (tag === "I" || tag === "EM" || child.style.fontStyle === "italic") g.italic = true;
        if (child.style.fontStyle === "normal") delete g.italic;
        if (tag === "SUB" || child.style.verticalAlign === "sub") { g.sub = true; delete g.sup; }
        if (tag === "SUP" || child.style.verticalAlign === "super") { g.sup = true; delete g.sub; }
        if (child.dataset.color) g.color = child.dataset.color;
        if (tag === "DIV" || tag === "P") {
          push("\n", f);
          walk(child, g, false);
        } else {
          walk(child, g, false);
        }
      }
    };
    walk(ed, {}, true);
    return out.map((b) => ({ el: b.el, runs: b.runs.filter((r) => r.t) }))
      .filter((b) => b.runs.some((r) => r.t.trim()));
  }

  function editorText(ed, editing) {
    const used = new Set();
    const blocks = readEditor(ed).map((b) => {
      const ds = b.el ? b.el.dataset : {};
      const style = ds.style || (editing.blocks[0] && editing.blocks[0].style) || "node-body";
      const index = ds.index !== undefined && ds.index !== "" ? Number(ds.index) : -1;
      const original = editing.blocks[index];
      const block = {};
      const firstUse = original && !used.has(index);
      if (firstUse) used.add(index);
      if (style !== "node-body" || !original || original.hasStyle || editing.form === "blocks") block.style = style;
      const runs = b.runs.map((r) => {
        const run = { t: r.t };
        for (const k of ["bold", "italic", "sub", "sup"]) if (r.f[k]) run[k] = true;
        if (r.f.color) run.color = r.f.color;
        return run;
      });
      if (runs.length === 1 && Object.keys(runs[0]).length === 1) block.t = runs[0].t;
      else block.runs = runs;
      if (firstUse) Object.assign(block, original.props || {});
      return block;
    });
    if (!blocks.length) return undefined;
    if (editing.form === "string" && blocks.length === 1 && Object.keys(blocks[0]).length === 1
        && typeof blocks[0].t === "string") return blocks[0].t;
    if (editing.form !== "blocks") {
      for (const b of blocks) if (b.style === "node-body" && editing.form === "string") delete b.style;
    }
    return blocks;
  }

  function finishTextEdit(keep) {
    const editing = S.editing;
    if (!editing) return;
    S.editing = null;
    const text = keep ? editorText(editing.el, editing) : null;
    editing.el.remove();
    if (editing.bar) editing.bar.remove();
    if (keep && !commit(`edit text ${editing.id}`, (d) => {
      const node = findItem(editing.id, d).item;
      if (text === undefined) delete node.text;
      else node.text = text;
    })) {
      render();
    }
    updateUi();
  }

  /* ---------- actions ---------- */

  const selNodes = () => S.sel.filter((id) => kindOf(id) === "node");

  function copySelection() {
    const payload = copyPayload(S.sel);
    if (!payload) return toast("Select nodes to copy");
    S.clipboard = payload;
    S.pasteCount = 0;
    try {
      navigator.clipboard.writeText(JSON.stringify({ nodes: payload.nodes, edges: payload.edges }, null, 2)).catch(() => {});
    } catch (err) { /* no clipboard access */ }
    toast(`Copied ${payload.top.length} item(s)`);
  }

  function paste(payload = S.clipboard, label = "paste") {
    if (!payload) return;
    if (payload === S.clipboard) S.pasteCount += 1;
    const offset = Math.max(2, 2 * gridStep()) * (payload === S.clipboard ? S.pasteCount : 1);
    let added = [];
    commit(label, (d) => {
      added = pasteInto(d, payload, offset);
    });
    select(added);
  }

  function duplicate() {
    const payload = copyPayload(S.sel);
    if (payload) paste(payload, "duplicate");
  }

  function deleteSelection() {
    const ids = S.sel.filter((id) => !isLocked(id));
    if (!ids.length) return S.sel.length ? toast("Locked", "lock") : undefined;
    if (!commit(ids.length > 1 ? `delete ${ids.length} items` : `delete ${ids[0]}`,
      (d) => deleteIds(d, ids), { select: [] })) toast("A locked edge is attached; nothing was deleted");
  }

  function group() {
    const ids = selNodes();
    if (ids.length < 2) return toast("Select two or more nodes to group");
    if (ids.some((id) => isLocked(id))) return toast("Locked", "lock");
    const parent = parentMap();
    const parents = new Set(ids.map((id) => parent[id] || null));
    if (parents.size > 1) return toast("These nodes are in different groups");
    const owner = [...parents][0];
    let gid = null;
    commit("group", (d) => {
      gid = uniqueId("group", usedIds(d));
      const order = d.nodes.map((n) => n.id);
      const children = ids.slice().sort((a, b) => order.indexOf(a) - order.indexOf(b));
      d.nodes.push({ id: gid, kind: "group", children });
      if (owner) {
        const p = findItem(owner, d).item;
        const at = p.children.findIndex((c) => ids.includes(c));
        p.children = p.children.filter((c) => !ids.includes(c));
        p.children.splice(at, 0, gid);
      }
    });
    select([gid]);
  }

  function ungroup() {
    const groups = selNodes().filter((id) => findItem(id).item.kind === "group");
    if (!groups.length) return toast("Select a group to ungroup");
    if (groups.some((id) => isLocked(id))) return toast("Locked", "lock");
    let freed = [];
    commit("ungroup", (d) => {
      const parent = parentMap(d);
      for (const gid of groups) {
        const g = findItem(gid, d).item;
        freed = freed.concat(g.children);
        if (parent[gid]) {
          const p = findItem(parent[gid], d).item;
          const at = p.children.indexOf(gid);
          p.children.splice(at, 1, ...g.children);
        }
        d.nodes = d.nodes.filter((n) => n.id !== gid);
      }
    });
    select(freed);
  }

  function reorder(front) {
    const leaves = new Set(selNodes().flatMap((id) => [...subtreeIds(id)]));
    if (!leaves.size) return;
    commit(front ? "bring to front" : "send to back", (d) => {
      const moving = d.nodes.filter((n) => leaves.has(n.id));
      const rest = d.nodes.filter((n) => !leaves.has(n.id));
      d.nodes = front ? rest.concat(moving) : moving.concat(rest);
    });
  }

  function nudge(dx, dy) {
    const ids = S.sel.filter((id) => !isLocked(id));
    if (!ids.length) return S.sel.length ? toast("Locked", "lock") : undefined;
    commit("nudge", (d) => applyMove(d, ids, dx, dy), { merge: true });
  }

  function copyStyle() {
    const style = S.sel.length === 1 ? styleOf(S.sel[0]) : null;
    if (!style) return toast("Select one node or edge to copy its style");
    S.styleClip = style;
    toast(`Copied the style of ${S.sel[0]}`);
    updateUi();
  }

  function pasteStyle() {
    if (!S.styleClip || !S.sel.length) return;
    const ids = S.sel.slice();
    commit("paste style", (d) => ids.forEach((id) => applyStyle(d, id, S.styleClip)));
  }

  const PANEL_PRESETS = ["1x2", "2x1", "2x2", "1+2"];

  function presetBoxes(name, width, height) {
    const gap = 3;
    const cell = (x, y, w, h) => [round2(x), round2(y), round2(Math.max(MIN_SIZE_MM, w)), round2(Math.max(MIN_SIZE_MM, h))];
    if (name === "1x2") {
      const w = (width - gap) / 2;
      return [cell(0, 0, w, height), cell(w + gap, 0, w, height)];
    }
    if (name === "2x1") {
      const h = (height - gap) / 2;
      return [cell(0, 0, width, h), cell(0, h + gap, width, h)];
    }
    if (name === "2x2") {
      const w = (width - gap) / 2;
      const h = (height - gap) / 2;
      return [cell(0, 0, w, h), cell(w + gap, 0, w, h), cell(0, h + gap, w, h), cell(w + gap, h + gap, w, h)];
    }
    const h = (height - gap) / 2;
    const w = (width - gap) / 2;
    return [cell(0, 0, width, h), cell(0, h + gap, w, h), cell(w + gap, h + gap, w, h)];
  }

  function relabelPanels(d) {
    readingOrder(d.panels || []).forEach((p, i) => {
      p.label = i < 26 ? String.fromCharCode(65 + i) : `P${i + 1}`;
    });
  }

  function applyPreset(name) {
    let extra = 0;
    commit(`layout ${name}`, (d) => {
      const boxes = presetBoxes(name, d.canvas.width_mm, d.canvas.height_mm);
      d.panels = d.panels || [];
      const ordered = readingOrder(d.panels);
      while (ordered.length < boxes.length) {
        const id = uniqueId("panel", usedIds(d));
        const panel = { id, x: 0, y: 0, w: 10, h: 10 };
        d.panels.push(panel);
        ordered.push(panel);
      }
      extra = Math.max(0, ordered.length - boxes.length);
      boxes.forEach((box, i) => {
        const panel = ordered[i];
        [panel.x, panel.y, panel.w, panel.h] = box;
      });
      relabelPanels(d);
    });
    if (extra) toast(`${extra} extra panel${extra > 1 ? "s were" : " was"} left in place`);
  }

  function addPanel(mm, size) {
    const g = gridStep();
    const snap = (v) => round2(Math.round(v / g) * g);
    const id = uniqueId("panel", usedIds());
    commit("add panel", (d) => {
      d.panels = d.panels || [];
      d.panels.push({
        id, x: snap(mm[0]), y: snap(mm[1]),
        w: round2(size ? size[2] : Math.min(40, d.canvas.width_mm)),
        h: round2(size ? size[3] : Math.min(30, d.canvas.height_mm)),
      });
      relabelPanels(d);
    }, { select: [id] });
  }

  function addNode(kind, mm, size, editAfter) {
    const g = gridStep();
    const snap = (v) => round2(Math.round(v / g) * g);
    let id = null;
    commit(`add ${kind}`, (d) => {
      id = uniqueId(kind, usedIds(d));
      const at = { id, kind, x: snap(mm[0]), y: snap(mm[1]) };
      let node;
      if (kind === "box") {
        node = Object.assign(at, { w: 30, h: 15, fill: "field", text: [{ style: "node-title", t: "Box" }] });
      } else if (kind === "pill") {
        node = Object.assign(at, { w: 24, h: 6, fill: "field", text: [{ style: "node-body", t: "Pill" }], align: "center", valign: "middle" });
      } else {
        node = Object.assign(at, { kind: "text", text: [{ style: "body", t: "Text" }] });
      }
      if (size) {
        node.w = round2(size[2]);
        if (kind !== "text") node.h = round2(size[3]);
      }
      const panel = panelAt(mm);
      if (panel) {
        node.panel = panel.id;
        node.x = round2(node.x - panel.box[0]);
        node.y = round2(node.y - panel.box[1]);
      }
      d.nodes = d.nodes || [];
      d.nodes.push(node);
    });
    select([id]);
    if (editAfter && id && findItem(id)) startTextEdit(id, true);
  }

  /* meta.draft: linted but never exported, so the manuscript figure stays as it is. */
  const isDraft = () => !!(S.diagram && S.diagram.meta && S.diagram.meta.draft === true);

  function setDraft(on) {
    commit(on ? "mark as draft" : "mark as ready", (d) => {
      if (on) d.meta = Object.assign({}, d.meta, { draft: true });
      else if (d.meta) {
        delete d.meta.draft;
        if (!Object.keys(d.meta).length) delete d.meta;
      }
    });
  }

  const one = () => (S.sel.length === 1 ? findItem(S.sel[0]) : null);
  const canEditText = () => { const f = one(); return !!(f && f.coll === "nodes" && TEXT_KINDS.has(f.item.kind)); };
  const hasGroupSel = () => selNodes().some((id) => findItem(id).item.kind === "group");

  const ACTIONS = {
    undo: { label: "Undo", keys: "⌘Z", run: undo, can: () => S.undo.length > 0 },
    redo: { label: "Redo", keys: "⌘⇧Z", run: redo, can: () => S.redo.length > 0 },
    save: { label: "Save", keys: "⌘S", run: () => save(false) },
    copy: { label: "Copy", keys: "⌘C", run: copySelection, can: () => selNodes().length > 0 },
    paste: { label: "Paste", keys: "⌘V", run: () => paste(), can: () => !!S.clipboard },
    duplicate: { label: "Duplicate", keys: "⌘D", run: duplicate, can: () => selNodes().length > 0 },
    selectAll: { label: "Select all", keys: "⌘A", run: () => select(topLevelItems()) },
    delete: { label: "Delete", keys: "⌫", run: deleteSelection, can: () => S.sel.length > 0 },
    group: { label: "Group", keys: "⌘G", run: group, can: () => selNodes().length > 1 },
    ungroup: { label: "Ungroup", keys: "⌘⇧G", run: ungroup, can: hasGroupSel },
    front: { label: "Bring to front", keys: "⌘]", run: () => reorder(true), can: () => selNodes().length > 0 },
    back: { label: "Send to back", keys: "⌘[", run: () => reorder(false), can: () => selNodes().length > 0 },
    copyStyle: { label: "Copy style", keys: "⌘⌥C", run: copyStyle, can: () => S.sel.length === 1 },
    pasteStyle: { label: "Paste style", keys: "⌘⌥V", run: pasteStyle, can: () => !!S.styleClip && S.sel.length > 0 },
    editText: { label: "Edit text", keys: "↵ / double-click", run: () => startTextEdit(S.sel[0]), can: canEditText },
    zoomIn: { label: "Zoom in", keys: "⌘+", run: () => setZoom(S.view.zoom * 1.25) },
    zoomOut: { label: "Zoom out", keys: "⌘−", run: () => setZoom(S.view.zoom / 1.25) },
    zoomPhysical: { label: "Physical size (100 %)", keys: "⌘0", run: zoomPhysical },
    zoomFit: { label: "Zoom to fit", keys: "⇧1", run: zoomFit },
    zoomSelection: { label: "Zoom to selection", keys: "⇧2", run: zoomSelection, can: () => S.sel.length > 0 },
    help: { label: "Shortcuts and help", keys: "?", run: toggleHelp },
    exportDiagram: { label: "Export", keys: "", run: () => exportDiagram(), can: () => !!S.name && !isDraft() },
  };

  async function exportDiagram() {
    if (!S.name || S.exporting) return;
    if (S.diagram && JSON.stringify(S.diagram) !== S.disk.json) await save(false);
    if (S.blocked || (S.diagram && JSON.stringify(S.diagram) !== S.disk.json)) {
      toast("Save the diagram before exporting", "warning");
      return;
    }
    S.exporting = true;
    updateStatus();
    const res = await api("POST", `export/${encodeURIComponent(S.name)}`, {});
    if (res.status === 409) {
      S.exporting = false;
      toast("An export is already running", "info");
      pollExport();
      return;
    }
    if (!res.ok) {
      S.exporting = false;
      updateStatus();
      showBanner("error", `Export did not start: ${(res.data && res.data.error) || `HTTP ${res.status}`}`, []);
      return;
    }
    pollExport();
  }

  async function pollExport() {
    const res = await api("GET", "export");
    const st = (res.ok && res.data) || { state: "error", messages: ["No export status"] };
    const el = $("#export-state");
    if (el) {
      const running = st.state === "running";
      el.hidden = st.state === "idle";
      el.textContent = running ? `Exporting ${st.name || ""}…` : st.state === "ok" ? "Exported" : st.state === "error" ? "Export failed" : "";
      el.className = "status" + (st.state === "error" ? " error" : running ? " saving" : "");
    }
    if (st.state === "running") {
      clearTimeout(S.exportTimer);
      S.exportTimer = setTimeout(pollExport, 800);
      return;
    }
    S.exporting = false;
    updateStatus();
    if (st.state === "error") {
      const lines = Array.isArray(st.messages) ? st.messages.slice(-8) : [];
      showBanner("error", `Export failed${st.exit_code != null ? ` (exit ${st.exit_code})` : ""}.`, lines);
    } else if (st.state === "ok") {
      toast(`Exported SVG, PDF and PNG to ${PATHS.diagram_output}`);
      clearTimeout(S.exportTimer);
      S.exportTimer = setTimeout(() => { if (el && !S.exporting) el.hidden = true; }, 4000);
    }
  }

  function run(name, ...args) {
    const action = ACTIONS[name];
    if (!action || !S.diagram && !["help"].includes(name)) return;
    if (action.can && !action.can()) return;
    action.run(...args);
  }

  const TOOL_KEYS = { KeyV: "select", KeyB: "box", KeyP: "pill", KeyT: "text", KeyF: "panel", KeyC: "comment" };

  function isTyping(e) {
    const t = e.target;
    return !!(t && t.closest && t.closest("input, textarea, select, [contenteditable='true'], [contenteditable='']"));
  }

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (!$("#menu").hidden) return hideMenu();
      if (!$("#help").hidden) return toggleHelp(false);
    }
    /* Keep focus inside the help sheet without blocking its native controls. */
    if (!$("#help").hidden) {
      if (e.key === "?") toggleHelp(false);
      if (e.key === "Tab") {
        const controls = [...$("#help").querySelectorAll("button, input")];
        const next = controls.indexOf(document.activeElement) + (e.shiftKey ? -1 : 1);
        controls[(next + controls.length) % controls.length].focus();
        e.preventDefault();
      }
      return;
    }
    if (!$("#menu").hidden && !isTyping(e)) hideMenu();
    if (isTyping(e) || S.editing) return;
    if (e.code === "Space" && !e.target.closest("button, a, [role='button']")) {
      S.space = true;
      viewport.classList.add("space");
      e.preventDefault();
      return;
    }
    if (!S.diagram) return;
    const mod = e.metaKey || e.ctrlKey;
    const c = e.code;
    let action = null;
    if (mod && c === "KeyZ") action = e.shiftKey ? "redo" : "undo";
    else if (mod && c === "KeyY") action = "redo";
    else if (mod && c === "KeyS") action = "save";
    else if (mod && e.altKey && c === "KeyC") action = "copyStyle";
    else if (mod && e.altKey && c === "KeyV") action = "pasteStyle";
    else if (mod && c === "KeyC") action = "copy";
    else if (mod && c === "KeyV") action = "paste";
    else if (mod && c === "KeyD") action = "duplicate";
    else if (mod && c === "KeyA") action = "selectAll";
    else if (mod && c === "KeyG") action = e.shiftKey ? "ungroup" : "group";
    else if (mod && (c === "Equal" || c === "NumpadAdd")) action = "zoomIn";
    else if (mod && (c === "Minus" || c === "NumpadSubtract")) action = "zoomOut";
    else if (mod && c === "Digit0") action = "zoomPhysical";
    else if (mod && c === "BracketRight") action = "front";
    else if (mod && c === "BracketLeft") action = "back";
    else if (!mod && (e.key === "Delete" || e.key === "Backspace")) action = "delete";
    else if (!mod && e.shiftKey && c === "Digit1") action = "zoomFit";
    else if (!mod && e.shiftKey && c === "Digit2") action = "zoomSelection";
    else if (!mod && e.key === "?") action = "help";
    else if (!mod && e.key === "Enter") action = "editText";
    else if (mod && c === "Quote") return e.preventDefault(), toggleGrid();
    else if (!mod && !e.altKey && !e.shiftKey && c === "KeyL") return e.preventDefault(), toggleLayers();
    else if (mod && c === "Semicolon") return e.preventDefault(), toggleGuides();
    else if (!mod && !e.altKey && !e.shiftKey && TOOL_KEYS[c]) return e.preventDefault(), setTool(TOOL_KEYS[c]);
    else if (e.key === "Escape") {
      e.preventDefault();
      if (S.tool && S.tool !== "select") return setTool("select");
      return select([]);
    } else if (!mod && e.key.startsWith("Arrow")) {
      e.preventDefault();
      const step = gridStep() * (e.shiftKey ? 10 : 1);
      const d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key];
      return nudge(d[0], d[1]);
    }
    if (action) {
      e.preventDefault();
      run(action);
    }
  });

  document.addEventListener("keyup", (e) => {
    if (e.code === "Space") {
      S.space = false;
      viewport.classList.remove("space");
    }
  });

  document.addEventListener("pointerdown", (e) => {
    if (!$("#menu").hidden && !$("#menu").contains(e.target)) hideMenu();
  });

  for (const host of [$("#bar"), $("#hud-zoom")]) {
    host.addEventListener("click", (e) => {
      const b = e.target.closest("[data-action]");
      if (b) run(b.dataset.action);
    });
  }

  function setToggle(id, on) {
    const b = $(id);
    b.classList.toggle("on", on);
    b.setAttribute("aria-pressed", String(on));
  }
  function toggleGrid() {
    S.view.grid = !S.view.grid;
    setToggle("#opt-grid", S.view.grid);
    if (S.diagram) layoutStage();
  }
  function toggleGuides() {
    S.view.guides = !S.view.guides;
    setToggle("#opt-guides", S.view.guides);
    drawOverlay();
  }
  const LAYERS_KEY = "diagram-editor-layers";
  function toggleLayers(show) {
    const on = show === undefined ? document.body.classList.contains("no-layers") : show;
    document.body.classList.toggle("no-layers", !on);
    setToggle("#opt-layers", on);
    try { localStorage.setItem(LAYERS_KEY, on ? "1" : "0"); } catch (err) { /* storage blocked */ }
  }
  (() => {
    let stored = null;
    try { stored = localStorage.getItem(LAYERS_KEY); } catch (err) { /* storage blocked */ }
    const on = stored === null ? window.innerWidth >= 1100 : stored === "1";
    document.body.classList.toggle("no-layers", !on);
    setToggle("#opt-layers", on);
  })();
  $("#opt-layers").addEventListener("click", () => toggleLayers());
  $("#opt-grid").addEventListener("click", toggleGrid);
  $("#opt-guides").addEventListener("click", toggleGuides);

  /* ---------- tools ---------- */

  const TOOL_HINTS = {
    box: "<b>Box</b> · click to place, or drag to draw · Esc to cancel",
    pill: "<b>Pill</b> · click to place, or drag to draw · Esc to cancel",
    text: "<b>Text</b> · click to place, or drag to set the width · Esc to cancel",
    panel: "<b>Panel</b> · drag to draw a panel · Esc to cancel",
    comment: "<b>Comment</b> · click an element or a point · Esc to cancel",
  };

  function setTool(name) {
    S.tool = name && TOOL_HINTS[name] ? name : "select";
    for (const b of document.querySelectorAll("#tools [data-tool]")) {
      b.classList.toggle("on", b.dataset.tool === S.tool);
      b.setAttribute("aria-pressed", String(b.dataset.tool === S.tool));
    }
    const hint = $("#tool-hint");
    hint.hidden = S.tool === "select";
    hint.innerHTML = TOOL_HINTS[S.tool] || "";
    viewport.classList.toggle("placing", S.tool !== "select");
  }

  $("#tools").addEventListener("click", (e) => {
    const b = e.target.closest("[data-tool]");
    if (!b || !S.diagram) return;
    setTool(S.tool === b.dataset.tool ? "select" : b.dataset.tool);
  });
  $("#tabs").addEventListener("click", (e) => {
    const b = e.target.closest("[data-tab]");
    if (b) showTab(b.dataset.tab);
  });

  /* ---------- context menu and help ---------- */

  const ACTION_ICONS = { editText: "edit", copy: "copy", paste: "paste", duplicate: "duplicate", delete: "trash",
    group: "group", ungroup: "ungroup", front: "front", back: "back", copyStyle: "copy", pasteStyle: "brush",
    zoomFit: "fit", zoomSelection: "target", zoomPhysical: "ruler" };

  function showMenu(x, y, mm) {
    const menu = $("#menu");
    const item = (name, extra = "") => {
      const a = ACTIONS[name];
      const off = a.can && !a.can();
      return h("div", { class: `item${off ? " off" : ""}${extra}`, role: "menuitem", onclick: () => { hideMenu(); run(name); } },
        Icons.node(ACTION_ICONS[name] || "dot", 16), h("span", {}, a.label), h("kbd", {}, a.keys));
    };
    const custom = (icon, label, fn, keys = "") => h("div", { class: "item", role: "menuitem", onclick: () => { hideMenu(); fn(); } },
      Icons.node(icon, 16), h("span", {}, label), h("kbd", {}, keys));
    const sub = (text) => h("div", { class: "sub-label" }, text);
    const hasSel = S.sel.length > 0;
    menu.replaceChildren(...[
      ...(hasSel ? [item("editText"), item("copy"), item("duplicate"), h("hr"),
        item("group"), item("ungroup"), item("front"), item("back"), h("hr"),
        item("copyStyle"), item("pasteStyle"),
        S.sel.length > 1 ? custom("alignL", "Align left", () => alignSelection("left")) : null,
        S.sel.length > 1 ? custom("alignT", "Align top", () => alignSelection("top")) : null,
        h("hr")] : []),
      item("paste"),
      sub("Add here"),
      custom("box", "Box", () => addNode("box", mm, null, true), "B"),
      custom("pill", "Pill", () => addNode("pill", mm, null, true), "P"),
      custom("text", "Text", () => addNode("text", mm, null, true), "T"),
      custom("panel", "Panel", () => addPanel(mm), "F"),
      custom("comment", S.sel.length === 1 ? `Comment on ${S.sel[0]}` : "Comment at this point", () => {
        if (!window.DiagramComments) return;
        DiagramComments.pin(S.sel.length === 1 ? { target: S.sel[0] } : { point: [round2(mm[0]), round2(mm[1])] });
      }, "C"),
      h("hr"), item("zoomFit"), item("zoomSelection"), item("zoomPhysical"),
      ...(hasSel ? [h("hr"), item("delete", " danger")] : [])].filter(Boolean));
    menu.hidden = false;
    const r = menu.getBoundingClientRect();
    menu.style.left = `${Math.max(8, Math.min(x, window.innerWidth - r.width - 8))}px`;
    menu.style.top = `${Math.max(8, Math.min(y, window.innerHeight - r.height - 8))}px`;
  }

  function hideMenu() {
    $("#menu").hidden = true;
  }

  let helpFocus = null;
  function toggleHelp(show) {
    const box = $("#help");
    const open = show === undefined ? box.hidden : show;
    if (!open) {
      box.hidden = true;
      if (helpFocus && helpFocus.isConnected) helpFocus.focus();
      return;
    }
    helpFocus = document.activeElement;
    const keys = (k) => k.split(/\s*\/\s*|\s+or\s+/).flatMap((part, i) => [i ? " / " : "", h("kbd", {}, part)]);
    const kv = (pairs) => h("div", { class: "kv" }, pairs.flatMap(([k, v]) => [h("span", { class: "k" }, keys(k)), h("span", { class: "v" }, v)]));
    const cal = h("input", { type: "number", class: "input", step: "0.01", min: "0.5", max: "2", value: String(S.calibration),
      style: { width: "90px" }, "aria-label": "Calibration factor",
      onchange: (e) => {
        const v = Number(e.target.value);
        if (v >= 0.5 && v <= 2) {
          S.calibration = v;
          try { localStorage.setItem(CALIBRATION_KEY, String(v)); } catch (err) { /* storage blocked */ }
          ruler.style.width = `${50 * CSS_PX_PER_MM * v}px`;
          if (S.diagram) layoutStage();
        }
      } });
    const ruler = h("div", { class: "ruler", style: { width: `${50 * CSS_PX_PER_MM * S.calibration}px` } });
    const close = h("button", { type: "button", class: "icon-btn", title: "Close (Esc)", onclick: () => toggleHelp(false) }, Icons.node("close"));
    const sheet = h("div", { class: "sheet", onclick: (e) => e.stopPropagation() },
      h("div", { class: "sheet-head" }, Icons.node("keyboard", 20), h("h2", {}, "Shortcuts and help"), close),
      h("div", { class: "cols" },
        h("div", {},
          h("h4", {}, "Tools"),
          kv([["V", "select and move"], ["B", "box"], ["P", "pill"], ["T", "text"], ["F", "panel"], ["C", "comment"], ["L", "show or hide the layers panel"],
            ["Esc", "back to select, or clear the selection"]]),
          h("h4", {}, "Canvas"),
          kv([["Drag", "move; snaps to edges, centres, equal spacing and the grid"],
            ["Alt", "hold while dragging to move or resize without snapping"],
            ["Shift + click", "add to or remove from the selection"],
            ["Click a selected group", "select the element inside it"],
            ["Space + drag", "pan (or scroll the wheel)"], ["⌘ + wheel", "zoom at the pointer (or pinch)"],
            ["Arrows", "nudge one grid step; with Shift, ten"],
            ["Right-click", "context menu"]]),
          h("h4", {}, "Edges"),
          kv([["Hover a node", "anchors appear; drag one onto another node to connect"],
            ["Drag an end", "reconnect that end"],
            ["Drag a square", "bend an orthogonal edge; the round handle adds a bend"],
            ["Double-click", "on a square removes it; on an edge adds a bend"]])),
        h("div", {},
          h("h4", {}, "Shortcuts"),
          kv(Object.values(ACTIONS).filter((a) => a.keys).map((a) => [a.keys, a.label])
            .concat([["⌘'", "grid"], ["⌘;", "guides"]])),
          h("h4", {}, "Text"),
          kv([["Double-click / ↵", "edit text in place"], ["⌘B / ⌘I", "bold / italic"], ["⌘, / ⌘.", "subscript / superscript"],
            ["↵", "new block with its own text style"], ["⇧↵", "line break in the block"], ["Esc / ⌘↵", "finish editing"]]),
          h("p", { style: { marginTop: "8px" } }, "Type {{v:key}} to insert a value from data/values.json. The value is shown, and the placeholder is saved."),
          h("h4", {}, "Saving and export"),
          h("p", {}, `Edits autosave after 1.5 s (⌘S saves now). If the file changed on disk too, you choose keep mine or load theirs; nothing is overwritten silently. Export writes SVG, PDF and PNG to ${PATHS.diagram_output}/.`),
          h("h4", {}, "Physical size"),
          h("p", {}, "100 % zoom uses CSS millimetres times a calibration factor. Measure this 50 mm ruler on screen and set factor = 50 / measured mm."),
          ruler, h("div", { style: { display: "flex", alignItems: "center", gap: "8px" } }, h("span", { class: "v" }, "Calibration factor"), cal))));
    box.replaceChildren(sheet);
    box.onclick = () => toggleHelp(false);
    box.hidden = false;
    close.focus();
  }

  /* ---------- inspector ---------- */

  const ic = (name, size = 16) => Icons.node(name, size);
  const KIND_ICONS = { panel: "panel", box: "box", pill: "pill", text: "text", image: "image", group: "group",
    edge: "edge", raw: "file", node: "box" };

  function section(title, aside, ...children) {
    return h("div", { class: "section" },
      title ? h("div", { class: "section-head" }, h("h3", {}, title), aside ? h("div", { class: "aside" }, aside) : null) : null,
      ...children);
  }

  /* Number field with a label that can be dragged left/right to scrub the value.
     A text input (not type=number) so decimals always show with a dot; a comma
     is accepted when typing. Arrow keys step (Shift = 10 steps). */
  function numberField(label, value, onCommit, opts = {}) {
    const shown = value === undefined || value === null ? "" : String(round2(value));
    const step = Number(opts.step || 0.1);
    const input = h("input", { type: "text", inputmode: "decimal", autocomplete: "off", spellcheck: "false", value: shown,
      readonly: opts.readonly, placeholder: opts.placeholder || "", "data-field": opts.field || label,
      "aria-label": opts.title || label });
    const accept = (v) => !(!Number.isFinite(v) || (opts.positive && v <= 0) || (opts.min !== undefined && v < opts.min));
    const parse = (text) => Number(String(text).trim().replace(",", "."));
    const apply = () => {
      if (input.value === shown) return false;
      const v = parse(input.value);
      if (input.value.trim() === "" || !accept(v)) {
        input.value = shown;
        return false;
      }
      onCommit(round2(v));
      return true;
    };
    input.addEventListener("change", apply);
    input.addEventListener("focus", () => input.select());
    input.addEventListener("keydown", (e) => {
      if (opts.readonly) return;
      if (e.key === "Enter") { apply(); input.blur(); }
      else if (e.key === "Escape") { input.value = shown; input.blur(); }
      else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
        e.preventDefault();
        const base = input.value.trim() === "" ? Number(value) || 0 : parse(input.value);
        const v = round2(base + (e.key === "ArrowUp" ? 1 : -1) * step * (e.shiftKey ? 10 : 1));
        if (!accept(v)) return;
        input.value = String(v);
        S.pendingFocus = input.dataset.field;
        onCommit(v);
      }
    });
    const lab = h("span", { class: "lab", title: opts.title || label }, label);
    if (!opts.readonly && value !== undefined && value !== null) {
      lab.style.cursor = "ew-resize";
      lab.addEventListener("pointerdown", (e) => {
        e.preventDefault();
        lab.setPointerCapture(e.pointerId);
        const x0 = e.clientX;
        const v0 = Number(value);
        const scrub = step * (e.shiftKey ? 10 : 1);
        let last = v0;
        const move = (ev) => {
          const v = round2(v0 + Math.round((ev.clientX - x0) / 3) * scrub);
          if (v === last || !accept(v)) return;
          last = v;
          input.value = String(v);
          onCommit(v);
        };
        const up = () => {
          lab.removeEventListener("pointermove", move);
          lab.removeEventListener("pointerup", up);
        };
        lab.addEventListener("pointermove", move);
        lab.addEventListener("pointerup", up);
      });
    }
    return h("label", { class: `num${opts.readonly ? " ro" : ""}` }, lab, input,
      opts.unit === false ? null : h("span", { class: "unit" }, opts.unit || "mm"));
  }

  function field(label, control) {
    return h("div", { class: "field" }, h("span", { class: "lab" }, label), control);
  }

  function textInput(value, onCommit, opts = {}) {
    const input = h("input", { type: "text", class: `input${opts.mono ? " mono" : ""}`, value: value || "",
      readonly: opts.readonly, placeholder: opts.placeholder || "", "data-field": opts.field, title: opts.title || "" });
    if (!opts.readonly) {
      input.addEventListener("change", () => onCommit(input.value));
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") input.blur(); });
    }
    return input;
  }

  function segmented(options, current, onPick, field) {
    return h("div", { class: "seg", role: "radiogroup", "data-field": field },
      options.map((o) => {
        const b = h("button", { type: "button", class: (current || options[0].value) === o.value ? "on" : "",
          title: o.title || o.label || o.value, onclick: () => onPick(o.value) });
        if (o.icon) b.append(ic(o.icon));
        else b.append(o.label || o.value);
        return b;
      }));
  }

  function selectBox(options, current, onPick, fieldName) {
    return h("select", { class: "select", "data-field": fieldName, onchange: (e) => onPick(e.target.value || undefined) },
      options.map((o) => h("option", { value: o.value, selected: (current || "") === o.value }, o.label)));
  }

  function colorNames() {
    const t = S.tokens;
    const names = Object.keys(t.roles);
    for (const g of ["contenders", "datasets", "outcomes"]) names.push(...Object.keys(t[g] || {}));
    return names;
  }

  function swatches(current, onPick, allowNone = true) {
    const colors = DiagramRenderer.colorTable(S.tokens);
    const t = S.tokens;
    const groups = [["Roles", Object.keys(t.roles)], ["Contenders", Object.keys(t.contenders || {})],
      ["Datasets", Object.keys(t.datasets || {})], ["Outcomes", Object.keys(t.outcomes || {})]];
    const sw = (name) => h("button", { type: "button", class: `swatch${current === name ? " on" : ""}`,
      title: `${name}  ${colors[name]}`, "data-token": name, style: { background: colors[name] }, onclick: () => onPick(name) });
    const now = current === null
      ? h("div", { class: "token-now" }, "Mixed")
      : h("div", { class: "token-now" },
        h("span", { class: `chip${current === undefined ? " swatch none" : ""}`,
          style: current !== undefined ? { background: colors[current] || "transparent" } : {} }),
        h("code", {}, current === undefined ? "none" : current),
        current !== undefined && colors[current] ? h("span", {}, colors[current]) : null);
    return h("div", {}, now, ...groups.filter(([, names]) => names.length).map(([label, names], gi) =>
      h("div", { class: "swatch-group" }, h("span", { class: "lab" }, label),
        h("div", { class: "swatches" },
          gi === 0 && allowNone ? h("button", { type: "button", class: `swatch none${current === undefined ? " on" : ""}`,
            title: "none", onclick: () => onPick(undefined) }) : null,
          ...names.map(sw)))));
  }

  function strokeSelect(current, onPick, allowNone = true) {
    const options = allowNone ? [{ value: "", label: "None" }] : [];
    for (const [name, st] of Object.entries(S.tokens.strokes)) {
      options.push({ value: name, label: `${name} · ${st.width_pt} pt${st.dash_pt ? " · dashed" : ""}${st.marker ? " · arrow" : ""}` });
    }
    return selectBox(options, current === null ? "\u0000" : current, onPick, "stroke");
  }

  function styleSelect(current, onPick) {
    return selectBox(Object.entries(S.tokens.text_styles).map(([name, st]) => (
      { value: name, label: `${name} · ${st.size_pt} pt${st.weight && st.weight >= 600 ? " · bold" : ""}` })),
    current, (v) => v && onPick(v));
  }

  function flagButtons(item) {
    const flag = (key, onIcon, offIcon, onTitle, offTitle) => h("button", {
      type: "button", class: `icon-btn small toggle${item[key] ? " on" : ""}`, title: item[key] ? onTitle : offTitle,
      onclick: () => setProp(item.id, key, item[key] ? undefined : true, `${item[key] ? "un" : ""}${key === "locked" ? "lock" : "hide"} ${item.id}`),
    }, ic(item[key] ? onIcon : offIcon));
    return h("div", { class: "flags" },
      flag("locked", "lock", "unlock", "Unlock", "Lock"),
      flag("hidden", "eyeOff", "eye", "Show", "Hide"));
  }

  function readingOrder(items) {
    return [...items].sort((a, b) => (Math.abs((a.y || 0) - (b.y || 0)) > 2 ? (a.y || 0) - (b.y || 0) : (a.x || 0) - (b.x || 0)));
  }

  /* ---------- layers ---------- */

  function layerRows() {
    const parent = parentMap();
    const byPanel = {};
    const loose = [];
    for (const n of S.diagram.nodes || []) {
      if (parent[n.id]) continue;
      if (n.panel) (byPanel[n.panel] = byPanel[n.panel] || []).push(n);
      else loose.push(n);
    }
    const rows = [];
    const walk = (node, depth) => {
      rows.push({ id: node.id, depth, kind: node.kind || "node" });
      if (node.kind === "group") {
        for (const cid of node.children || []) {
          const child = (S.diagram.nodes || []).find((n) => n.id === cid);
          if (child) walk(child, depth + 1);
        }
      }
    };
    for (const p of readingOrder(S.diagram.panels || [])) {
      rows.push({ id: p.id, depth: 0, kind: "panel" });
      for (const n of [...(byPanel[p.id] || [])].reverse()) walk(n, 1);
    }
    for (const n of [...loose].reverse()) walk(n, 0);
    const edges = (S.diagram.edges || []).map((e) => ({ id: e.id, depth: 0, kind: "edge" }));
    const raws = (S.diagram.raw || []).map((r) => ({ id: r.id, depth: 0, kind: "raw" }));
    return { rows, edges, raws };
  }

  function renderLayers() {
    const root = $("#layers");
    if (!root) return;
    if (!S.diagram) {
      root.replaceChildren();
      $("#layer-count").textContent = "";
      return;
    }
    const scroll = root.scrollTop;
    const { rows, edges, raws } = layerRows();
    const row = (r) => {
      const found = findItem(r.id);
      if (!found) return null;
      const item = found.item;
      const flagBtn = (key, onIcon, offIcon, title) => h("button", { type: "button", class: item[key] ? "on" : "",
        title: item[key] ? `${key === "locked" ? "Unlock" : "Show"}` : title,
        onclick: (e) => {
          e.stopPropagation();
          setProp(r.id, key, item[key] ? undefined : true, `${item[key] ? "un" : ""}${key === "locked" ? "lock" : "hide"} ${r.id}`);
        } }, ic(item[key] ? onIcon : offIcon, 15));
      const name = r.kind === "panel" && item.label ? `${r.id}  ·  ${item.label}${item.title ? ` ${item.title}` : ""}` : r.id;
      return h("div", {
        class: `layer${r.kind === "panel" ? " panel-row" : ""}${S.sel.includes(r.id) ? " on" : ""}${item.hidden ? " is-hidden" : ""}${item.hidden || item.locked ? " flagged" : ""}`,
        style: { paddingLeft: `${8 + r.depth * 14}px` }, title: r.id,
        onclick: (e) => select(e.shiftKey ? (S.sel.includes(r.id) ? S.sel.filter((x) => x !== r.id) : [...S.sel, r.id]) : [r.id]),
        ondblclick: () => { select([r.id]); zoomSelection(); },
        onmouseenter: () => { if (S.hover !== r.id) { S.hover = r.id; drawOverlay(); } },
        onmouseleave: () => { if (S.hover === r.id) { S.hover = null; drawOverlay(); } },
      }, h("span", { class: "lic" }, ic(KIND_ICONS[r.kind] || "box", 15)), h("span", { class: "lid" }, name),
      h("span", { class: "lflags" }, flagBtn("locked", "lock", "unlock", "Lock"), flagBtn("hidden", "eyeOff", "eye", "Hide")));
    };
    const total = rows.length + edges.length + raws.length;
    $("#layer-count").textContent = total ? String(total) : "";
    root.replaceChildren(...[
      ...(total ? [] : [h("div", { class: "empty" }, ic("layers", 28), h("b", {}, "Nothing here yet"),
        "Pick a tool in the toolbar and click the canvas.")]),
      ...rows.map(row),
      edges.length ? h("div", { class: "layer-group-label" }, `Edges · ${edges.length}`) : null,
      ...edges.map(row),
      raws.length ? h("div", { class: "layer-group-label" }, "Raw SVG") : null,
      ...raws.map(row)].filter(Boolean));
    root.scrollTop = scroll;
    const on = root.querySelector(".layer.on");
    if (on && S.sel.length === 1) on.scrollIntoView({ block: "nearest" });
  }

  function showTab(name) {
    S.tab = name;
    const panes = { inspect: "inspector", lint: "lint", comments: "comments" };
    for (const [tab, id] of Object.entries(panes)) {
      const el = document.getElementById(id);
      if (el) el.hidden = tab !== name;
    }
    for (const b of document.querySelectorAll("#tabs button")) {
      b.classList.toggle("on", b.dataset.tab === name);
      b.setAttribute("aria-selected", String(b.dataset.tab === name));
    }
  }

  function setTabBadge(tab, count, cls) {
    const badge = document.querySelector(`#tabs [data-tab='${tab}'] .badge`);
    if (!badge) return;
    badge.hidden = !count;
    badge.textContent = count ? String(count) : "";
    badge.className = `badge${cls ? ` ${cls}` : ""}`;
  }

  function setProp(id, key, value, label) {
    if (key !== "locked" && key !== "hidden" && isLocked(id)) {
      toast("Locked", "lock");
      renderInspector();
      return;
    }
    commit(label || `set ${key} of ${id}`, (d) => {
      const item = findItem(id, d).item;
      if (value === undefined) delete item[key];
      else item[key] = value;
    }, { merge: true });
  }

  /* ---------- align and distribute ---------- */

  function alignable() {
    return S.sel.filter((id) => {
      const f = findItem(id);
      return f && f.coll !== "edges" && f.coll !== "raw" && !isLocked(id) && boxOf(id);
    });
  }

  function alignSelection(mode) {
    const ids = alignable();
    if (ids.length < 2) return;
    const all = unionBox(ids.map(boxOf));
    commit(`align ${mode}`, (d) => {
      for (const id of ids) {
        const b = boxOf(id);
        let dx = 0;
        let dy = 0;
        if (mode === "left") dx = all[0] - b[0];
        if (mode === "center") dx = all[0] + all[2] / 2 - (b[0] + b[2] / 2);
        if (mode === "right") dx = all[0] + all[2] - (b[0] + b[2]);
        if (mode === "top") dy = all[1] - b[1];
        if (mode === "middle") dy = all[1] + all[3] / 2 - (b[1] + b[3] / 2);
        if (mode === "bottom") dy = all[1] + all[3] - (b[1] + b[3]);
        if (Math.abs(dx) > 1e-6 || Math.abs(dy) > 1e-6) applyMove(d, [id], round2(dx), round2(dy));
      }
    });
  }

  function distributeSelection(axis) {
    const ids = alignable();
    if (ids.length < 3) return;
    const i = axis === "x" ? 0 : 1;
    const items = ids.map((id) => ({ id, b: boxOf(id) })).sort((a, b) => a.b[i] - b.b[i]);
    const start = items[0].b[i];
    const end = Math.max(...items.map((x) => x.b[i] + x.b[i + 2]));
    const gap = (end - start - items.reduce((sum, x) => sum + x.b[i + 2], 0)) / (items.length - 1);
    commit(`distribute ${axis === "x" ? "horizontally" : "vertically"}`, (d) => {
      let at = start;
      for (const x of items) {
        const delta = round2(at - x.b[i]);
        if (Math.abs(delta) > 1e-6) applyMove(d, [x.id], axis === "x" ? delta : 0, axis === "x" ? 0 : delta);
        at += x.b[i + 2] + gap;
      }
    });
  }

  function alignTools(count) {
    const b = (icon, title, fn, off) => h("button", { type: "button", class: "icon-btn", title, disabled: off, onclick: fn }, ic(icon));
    return section("Align", null, h("div", { class: "align-row" },
      b("alignL", "Align left", () => alignSelection("left")),
      b("alignC", "Align centres horizontally", () => alignSelection("center")),
      b("alignR", "Align right", () => alignSelection("right")),
      b("alignT", "Align top", () => alignSelection("top")),
      b("alignM", "Align middles", () => alignSelection("middle")),
      b("alignB", "Align bottom", () => alignSelection("bottom")),
      b("distH", "Distribute horizontally (3 or more)", () => distributeSelection("x"), count < 3),
      b("distV", "Distribute vertically (3 or more)", () => distributeSelection("y"), count < 3)));
  }

  /* ---------- inspector panes ---------- */

  function presetIcon(name) {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("width", "34");
    svg.setAttribute("height", "24");
    svg.setAttribute("viewBox", "0 0 34 24");
    const boxes = presetBoxes(name, 34, 24).map((b) => [b[0] + 1, b[1] + 1, b[2] - 2, b[3] - 2]);
    for (const b of boxes) {
      s("rect", { x: b[0], y: b[1], width: Math.max(b[2], 1), height: Math.max(b[3], 1), rx: 2,
        fill: "currentColor", "fill-opacity": 0.14, stroke: "currentColor", "stroke-width": 1 }, svg);
    }
    return svg;
  }

  function panelTools() {
    return section("Panel layout", null,
      h("div", { class: "preset-grid" }, PANEL_PRESETS.map((name) => h("button", { type: "button", class: "preset",
        title: `Lay out ${name} panels over the canvas (3 mm gaps)`, onclick: () => applyPreset(name) }, presetIcon(name), name))),
      h("div", { class: "row-buttons", style: { marginTop: "8px" } },
        h("button", { type: "button", class: "btn small", onclick: () => commit("label panels", relabelPanels) }, "Relabel A, B, C…")),
      h("p", { class: "note" }, "Presets fill the canvas with 3 mm gaps; 1+2 is one panel on top and two below. Labels follow reading order."));
  }

  function issuesFor(ids) {
    const issues = displayedIssues().filter((i) => ids.includes(i.id));
    if (!issues.length) return null;
    return section("Issues", null, ...issues.map((i) => h("div", { class: `callout ${i.level === "error" ? "err" : "warn"}` },
      ic(i.level === "error" ? "error" : "warning", 15), h("span", {}, i.message))));
  }

  /* Tab between inspector fields commits the field first; the re-render
     would otherwise drop the focus, so remember where it was going. */
  $("#inspector").addEventListener("keydown", (e) => {
    if (e.key !== "Tab" || !e.target.matches("input[data-field], select[data-field]")) return;
    const fields = [...$("#inspector").querySelectorAll("input[data-field]:not([readonly]), select[data-field]")];
    const at = fields.indexOf(e.target);
    const next = fields[at + (e.shiftKey ? -1 : 1)];
    if (!next) return;
    e.preventDefault();
    S.pendingFocus = next.dataset.field;
    e.target.dispatchEvent(new Event("change"));
    const target = $("#inspector").querySelector(`[data-field="${CSS.escape(S.pendingFocus || "")}"]`);
    S.pendingFocus = null;
    if (target) target.focus();
  });

  function renderInspector() {
    const box = $("#inspector");
    const tabbing = !!S.pendingFocus;
    const focused = S.pendingFocus || (box.contains(document.activeElement) ? document.activeElement.dataset.field : null);
    S.pendingFocus = null;
    const scroll = box.scrollTop;
    box.replaceChildren();
    if (!S.diagram) return;
    const items = S.sel.map((id) => findItem(id)).filter(Boolean);
    if (!items.length) inspectDocument(box);
    else if (items.length === 1) inspectOne(box, items[0]);
    else inspectMany(box, items);
    box.scrollTop = scroll;
    if (focused) {
      const el = box.querySelector(`[data-field="${CSS.escape(focused)}"]`);
      if (el && el.focus) {
        const caret = el.value !== undefined && el.setSelectionRange ? el.value.length : null;
        el.focus({ preventScroll: true });
        if (tabbing && el.select) el.select();
        else if (caret !== null && document.activeElement === el && el.type === "text") el.setSelectionRange(caret, caret);
      }
    }
  }

  function inspectDocument(box) {
    const c = S.diagram.canvas;
    const d = S.diagram;
    box.append(
      section(null, null, h("div", { class: "title-row" }, h("span", { class: "kind-ic" }, ic("file", 18)),
        h("div", { class: "names" }, h("b", {}, S.name), h("span", {}, `${PATHS.diagrams}/${S.name}.json`))),
      h("div", { class: "stats", style: { marginTop: "12px" } },
        h("div", { class: "stat" }, h("b", {}, String((d.panels || []).length)), h("span", {}, "panels")),
        h("div", { class: "stat" }, h("b", {}, String((d.nodes || []).length)), h("span", {}, "nodes")),
        h("div", { class: "stat" }, h("b", {}, String((d.edges || []).length)), h("span", {}, "edges")))),
      section("Status", null,
        segmented([{ value: "draft", label: "Draft", title: "Linted, never exported" },
          { value: "ready", label: "Ready", title: `Exported to ${PATHS.diagram_output}/` }], isDraft() ? "draft" : "ready",
        (v) => { if ((v === "draft") !== isDraft()) setDraft(v === "draft"); }, "draft"),
        h("p", { class: "note" }, isDraft()
          ? "A draft is checked but never exported, so the figure in the manuscript does not change."
          : `Export writes SVG, PDF and PNG to ${PATHS.diagram_output}/ for the manuscript.`)),
      section("Canvas", null,
        h("div", { class: "fields three" },
          numberField("W", c.width_mm, (v) => commit("canvas width", (x) => { x.canvas.width_mm = v; }), { positive: true, field: "cw", title: "Width (mm)", unit: false }),
          numberField("H", c.height_mm, (v) => commit("canvas height", (x) => { x.canvas.height_mm = v; }), { positive: true, field: "ch", title: "Height (mm)", unit: false }),
          numberField("G", c.grid, (v) => commit("canvas grid", (x) => { x.canvas.grid = v; }), { positive: true, field: "cg", title: "Grid step (mm)", unit: false })),
        h("div", { style: { marginTop: "8px" } }, segmented(Object.entries(S.tokens.widths_mm || {}).map(([name, w]) => (
          { value: String(w), label: `${w} mm`, title: `${name} column` })), String(c.width_mm),
        (v) => commit("canvas width", (x) => { x.canvas.width_mm = Number(v); }), "cwidth"))),
      panelTools(),
      section(null, null, h("p", { class: "note", style: { marginTop: 0 } },
        "Select an element to edit it. Pick a tool in the toolbar (B, P, T, F) and click the canvas to add one; right-click for more.")));
  }

  function inspectOne(box, found) {
    const { coll, item } = found;
    const id = item.id;
    const kind = coll === "nodes" ? item.kind : coll === "edges" ? "edge" : coll === "panels" ? "panel" : "raw";
    const where = coll === "nodes" && item.panel ? `${kind} · in panel ${item.panel}` : kind;
    box.append(section(null, null, h("div", { class: "title-row" },
      h("span", { class: "kind-ic" }, ic(KIND_ICONS[kind] || "box", 18)),
      h("div", { class: "names" }, h("b", { title: "ids are stable; comments and patches refer to them" }, id), h("span", {}, where)),
      flagButtons(item)),
    isLocked(id) && !item.locked ? h("div", { class: "callout info" }, ic("lock", 15), "Locked by a parent group or panel.") : null));
    if (coll === "nodes") inspectNode(box, item);
    else if (coll === "edges") inspectEdge(box, item);
    else if (coll === "panels") inspectPanel(box, item);
    if (coll !== "raw") {
      box.append(section("Style", null, h("div", { class: "row-buttons" },
        h("button", { type: "button", class: "btn small", onclick: () => run("copyStyle") }, ic("copy", 14), "Copy style"),
        h("button", { type: "button", class: "btn small", disabled: !S.styleClip, onclick: () => run("pasteStyle") }, ic("brush", 14), "Paste style"))));
    }
    const issues = issuesFor([id]);
    if (issues) box.append(issues);
  }

  function inspectMany(box, items) {
    const nodes = items.filter((x) => x.coll === "nodes" && x.item.kind !== "group").map((x) => x.item);
    box.append(section(null, null, h("div", { class: "title-row" }, h("span", { class: "kind-ic" }, ic("duplicate", 18)),
      h("div", { class: "names" }, h("b", {}, `${items.length} selected`),
        h("span", {}, items.slice(0, 3).map((x) => x.item.id).join(", ") + (items.length > 3 ? "…" : ""))))));
    box.append(alignTools(alignable().length));
    if (nodes.length) {
      const fills = new Set(nodes.map((n) => n.fill));
      box.append(section("Fill", null, swatches(fills.size === 1 ? [...fills][0] : null, (v) =>
        commit("set fill", (d) => nodes.forEach((n) => {
          const it = findItem(n.id, d).item;
          if (v === undefined) delete it.fill; else it.fill = v;
        })))));
      const strokes = new Set(nodes.map((n) => n.stroke));
      box.append(section("Stroke", null, strokeSelect(strokes.size === 1 ? [...strokes][0] : null, (v) =>
        commit("set stroke", (d) => nodes.forEach((n) => {
          const it = findItem(n.id, d).item;
          if (v === undefined) delete it.stroke; else it.stroke = v;
        })))));
    }
    box.append(section("Arrange", null, h("div", { class: "row-buttons" },
      h("button", { type: "button", class: "btn small", disabled: !ACTIONS.group.can(), onclick: () => run("group") }, ic("group", 14), "Group"),
      h("button", { type: "button", class: "btn small", disabled: !ACTIONS.pasteStyle.can(), onclick: () => run("pasteStyle") }, ic("brush", 14), "Paste style"))));
    const issues = issuesFor(items.map((x) => x.item.id));
    if (issues) box.append(issues);
  }

  function inspectNode(box, item) {
    const id = item.id;
    const geo = boxOf(id) || [0, 0, 0, 0];
    if (item.kind === "group") {
      box.append(section("Layout", null,
        h("div", { class: "fields" },
          numberField("X", geo[0], (v) => commit(`move ${id}`, (d) => applyMove(d, [id], v - geo[0], 0)), { field: "x" }),
          numberField("Y", geo[1], (v) => commit(`move ${id}`, (d) => applyMove(d, [id], 0, v - geo[1])), { field: "y" }),
          numberField("W", geo[2], () => {}, { readonly: true, field: "w" }),
          numberField("H", geo[3], () => {}, { readonly: true, field: "h" })),
        h("p", { class: "note" }, `Absolute position. Children: ${item.children.join(", ")}.`)));
      return;
    }
    const auto = item.kind === "text";
    box.append(section("Layout", item.panel ? `relative to ${item.panel}` : null,
      h("div", { class: "fields" },
        numberField("X", item.x, (v) => setProp(id, "x", v), { field: "x" }),
        numberField("Y", item.y, (v) => setProp(id, "y", v), { field: "y" }),
        numberField("W", auto && item.w === undefined ? undefined : item.w, (v) => setProp(id, "w", v),
          { positive: true, field: "w", placeholder: auto ? `auto ${round2(geo[2])}` : "" }),
        numberField("H", auto && item.h === undefined ? undefined : item.h, (v) => setProp(id, "h", v),
          { positive: true, field: "h", placeholder: auto ? `auto ${round2(geo[3])}` : "" })),
      item.kind === "box" ? h("div", { class: "fields", style: { marginTop: "6px" } },
        numberField("R", item.radius !== undefined ? item.radius : S.tokens.shapes.box.radius_mm,
          (v) => setProp(id, "radius", Math.max(0, v)), { field: "radius", title: "Corner radius", min: 0 })) : null));
    if (item.kind === "image") {
      box.append(section("Image", null, field("Source", textInput(item.src, () => {}, { readonly: true, mono: true }))));
      return;
    }
    box.append(section("Fill", null, swatches(item.fill, (v) => setProp(id, "fill", v, `set fill of ${id}`))));
    box.append(section("Stroke", null, strokeSelect(item.stroke, (v) => setProp(id, "stroke", v, `set stroke of ${id}`))));
    const { blocks, form } = blocksOf(item);
    const textSec = section("Text", h("button", { type: "button", class: "btn small ghost", onclick: () => startTextEdit(id) }, ic("edit", 14), "Edit"),
      h("div", { class: "fields", style: { marginBottom: "10px" } },
        segmented([{ value: "left", icon: "alignL", title: "Align left" }, { value: "center", icon: "alignC", title: "Centre" },
          { value: "right", icon: "alignR", title: "Align right" }], item.align || "left",
        (v) => setProp(id, "align", v === "left" ? undefined : v), "align"),
        segmented([{ value: "top", icon: "alignT", title: "Top" }, { value: "middle", icon: "alignM", title: "Middle" },
          { value: "bottom", icon: "alignB", title: "Bottom" }], item.valign || "top",
        (v) => setProp(id, "valign", v === "top" ? undefined : v), "valign")));
    blocks.forEach((b, i) => {
      if (form === "none") return;
      const preview = b.runs.map((r) => r.t).join("").replace(/\n/g, " ⏎ ");
      textSec.append(h("div", { class: "text-block" },
        styleSelect(b.style, (v) => commit(`set text style of ${id}`, (d) => {
          const n = findItem(id, d).item;
          if (typeof n.text === "string") n.text = [{ style: v, t: n.text }];
          else n.text[i].style = v;
        })),
        h("div", { class: "preview", title: preview }, preview || "(empty)")));
    });
    if (form === "none") textSec.append(h("p", { class: "note" }, "No text. Double-click the element to add some."));
    const keys = textKeys(item);
    if (keys.length) {
      textSec.append(h("div", { class: "values" }, keys.map((k) => h("div", { class: `value-chip${k in S.values ? "" : " bad"}` },
        h("code", { title: `{{v:${k}}}` }, k), h("b", {}, k in S.values ? String(S.values[k]) : "unknown key")))));
    }
    box.append(textSec);
  }

  function inspectEdge(box, item) {
    const id = item.id;
    box.append(section("Connection", null,
      field("From", textInput(item.from, () => {}, { readonly: true, mono: true })),
      field("To", textInput(item.to, () => {}, { readonly: true, mono: true })),
      field("Route", segmented([{ value: "orthogonal", icon: "orthogonal", title: "Orthogonal" },
        { value: "straight", icon: "straight", title: "Straight" }], item.route || "orthogonal",
      (v) => setProp(id, "route", v === "orthogonal" ? undefined : v), "route")),
      (item.waypoints || []).length ? h("div", { class: "row-buttons", style: { marginTop: "8px" } },
        h("button", { type: "button", class: "btn small", onclick: () => setProp(id, "waypoints", undefined, `clear waypoints of ${id}`) },
          `Clear ${item.waypoints.length} waypoint${item.waypoints.length > 1 ? "s" : ""}`)) : null,
      h("p", { class: "note" }, "Drag an end to reconnect it. Drag a square to bend the route; the round handle adds a bend, double-click a square removes it.")));
    box.append(section("Stroke", null, strokeSelect(item.stroke || "arrow",
      (v) => setProp(id, "stroke", v === "arrow" ? undefined : v, `set stroke of ${id}`), false)));
    if (typeof item.label === "string" || item.label === undefined || item.label === null) {
      box.append(section("Label", null, textInput(item.label, (v) => setProp(id, "label", v || undefined, `set label of ${id}`),
        { field: "label", placeholder: "No label" })));
    }
  }

  function inspectPanel(box, item) {
    const id = item.id;
    box.append(section("Layout", null, h("div", { class: "fields" },
      numberField("X", item.x, (v) => setProp(id, "x", v), { field: "x" }),
      numberField("Y", item.y, (v) => setProp(id, "y", v), { field: "y" }),
      numberField("W", item.w, (v) => setProp(id, "w", v), { positive: true, field: "w" }),
      numberField("H", item.h, (v) => setProp(id, "h", v), { positive: true, field: "h" }))));
    box.append(section("Heading", null,
      field("Label", textInput(item.label, (v) => setProp(id, "label", v || undefined, `set label of ${id}`), { field: "label", placeholder: "A" })),
      field("Title", textInput(item.title, (v) => setProp(id, "title", v || undefined, `set title of ${id}`), { field: "title", placeholder: "No title" }))));
    const options = [{ value: "", label: "None" }];
    for (const entry of S.embedCatalog || []) {
      const stem = entry.path.split("/").pop().replace(/\.svg$/, "");
      const size = entry.width && entry.height ? `  (${entry.width} × ${entry.height})` : "";
      options.push({ value: entry.path, label: stem + size });
    }
    if (item.embed && !(S.embedCatalog || []).some((entry) => entry.path === item.embed)) options.push({ value: item.embed, label: item.embed });
    const embedSec = section("Chart", null,
      field("Embed", selectBox(options, item.embed || "", (v) => {
        setProp(id, "embed", v || undefined, `set embed of ${id}`);
        loadAssets().then(() => { if (S.diagram) render(); });
      }, "embed")),
      item.embed ? field("Fit", segmented([{ value: "contain", label: "Contain" }, { value: "none", label: "Natural size" }],
        item.fit || "contain", (v) => setProp(id, "fit", v === "contain" ? undefined : v), "fit")) : null);
    const geo = (S.geo && S.geo.panels || []).find((p) => p.id === id);
    const placed = geo && geo.embed;
    const tolerance = (S.tokens.lint && S.tokens.lint.embed_scale_tolerance) || 0.005;
    const note = (kind, icon, ...body) => embedSec.append(h("div", { class: `callout ${kind}` }, ic(icon, 15), ...body));
    if (placed && placed.missing) {
      note("err", "error", "Missing: run scripts/trazo build --prepare=inputs.");
    } else if (placed && placed.scale != null && Math.abs(placed.scale - 1) > tolerance) {
      note("warn", "warning",
        h("span", {}, `Scaled to ${(placed.scale * 100).toFixed(1)} %, so its font sizes change. Render the chart at the panel size `
          + `(${round2(geo.content ? geo.content[2] : item.w)} × ${round2(geo.content ? geo.content[3] : item.h)} mm).`));
    } else if (placed && placed.scale != null) {
      note("info", "check", "Placed at 100 %; font sizes are as printed.");
    }
    box.append(embedSec, panelTools());
  }

  /* ---------- ui state ---------- */

  function updateSelInfo() {
    $("#selinfo").textContent = S.sel.length ? `selected: ${S.sel.slice(0, 4).join(", ")}${S.sel.length > 4 ? ` +${S.sel.length - 4}` : ""}` : "";
  }

  function updateStatus() {
    const st = $("#status");
    const dirty = S.diagram && JSON.stringify(S.diagram) !== S.disk.json;
    let text = "Saved";
    let cls = "status";
    if (S.blocked === "conflict") [text, cls] = ["Changed on disk", "status error"];
    else if (S.blocked === "invalid") [text, cls] = ["Not saved: invalid", "status error"];
    else if (S.blocked === "error") [text, cls] = ["Not saved", "status error"];
    else if (S.saving) [text, cls] = ["Saving…", "status saving"];
    else if (dirty) [text, cls] = ["Edited", "status dirty"];
    st.hidden = !S.diagram;
    st.textContent = text;
    st.className = cls;
    st.title = S.blocked === "conflict" ? "Choose keep mine or load theirs" : dirty ? "Autosaves in a moment (⌘S saves now)" : "All changes are saved to disk";
    st.onclick = () => { if (S.blocked === "conflict") showConflict(); else if (dirty) save(false); };
    $("#crumb-sep").hidden = !S.diagram;
    $("#draft-badge").hidden = !isDraft();
    if (!S.exporting) {
      const exported = $("#export-state");
      const state = dirty ? "stale" : S.exportState;
      exported.hidden = !S.diagram || !state || state === "draft";
      exported.textContent = state === "current" ? "Export current" : state === "error" ? "Export blocked" : "Export pending";
      exported.className = "status" + (state === "current" ? "" : " dirty");
    }
    const exp = document.querySelector('[data-action="exportDiagram"]');
    exp.title = isDraft() ? "Draft: not exported. Mark it ready in the Design panel to export."
      : `Write SVG, PDF and PNG to ${PATHS.diagram_output}/`;
    for (const b of document.querySelectorAll("[data-action]")) {
      const a = ACTIONS[b.dataset.action];
      b.disabled = (!S.diagram && b.dataset.action !== "help") || !!(a && a.can && !a.can()) || (b.dataset.action === "exportDiagram" && S.exporting);
    }
    for (const b of document.querySelectorAll("#tools [data-tool]")) b.disabled = !S.diagram;
  }

  function updateUi() {
    drawOverlay();
    renderInspector();
    renderLayers();
    renderLint();
    updateSelInfo();
    updateStatus();
  }

  let toastTimer = 0;
  /* A short notice; with `action` ({label, run}) it stays longer and offers
   * a button (Undo after a comment change). Hovering keeps it open. */
  function toast(text, icon = "check", action = null) {
    const t = $("#toast");
    t.replaceChildren(Icons.node(icon, 15), h("span", {}, text), action ? h("button", { type: "button",
      onclick: () => { clearTimeout(toastTimer); t.hidden = true; action.run(); } }, action.label) : null);
    t.hidden = false;
    t.style.animation = "none";
    void t.offsetWidth;
    t.style.animation = "";
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, action ? 6000 : 2000);
  }
  $("#toast").addEventListener("mouseenter", () => clearTimeout(toastTimer));
  $("#toast").addEventListener("mouseleave", () => { toastTimer = setTimeout(() => { $("#toast").hidden = true; }, 2000); });

  function showBanner(kind, message, lines, buttons = []) {
    const b = $("#banner");
    b.className = kind === "info" ? "info" : "";
    b.replaceChildren(Icons.node(kind === "info" ? "info" : "error", 18),
      h("div", {}, h("div", { class: "msg" }, message),
        lines.length ? h("ul", {}, lines.map((l) => h("li", {}, l))) : null,
        h("div", { class: "buttons" }, ...buttons,
          h("button", { type: "button", class: "btn small ghost", onclick: () => { b.hidden = true; } }, "Dismiss"))));
    for (const btn of b.querySelectorAll(".buttons button:not(.btn)")) btn.className = "btn small";
    b.hidden = false;
  }

  /* ---------- persistence ---------- */

  const API = "/__diagrams/";

  const api = (method, url, body) => App.api(API + url, body, method);

  function scheduleAutosave() {
    clearTimeout(S.saveTimer);
    updateStatus();
    if (S.blocked) return;
    S.saveTimer = setTimeout(() => save(true), AUTOSAVE_MS);
  }

  function downloadMine() {
    const blob = new Blob([JSON.stringify(S.diagram, null, 2) + "\n"], { type: "application/json" });
    const a = h("a", { href: URL.createObjectURL(blob), download: `${S.name}.mine.json` });
    document.body.append(a);
    a.click();
    a.remove();
  }

  async function save(auto) {
    if (!S.diagram) return;
    clearTimeout(S.saveTimer);
    if (S.editing || S.drag) {
      if (auto) S.saveTimer = setTimeout(() => save(true), AUTOSAVE_MS);
      return;
    }
    if (S.blocked === "conflict") {
      showConflict();
      return;
    }
    const json = JSON.stringify(S.diagram);
    if (json === S.disk.json) {
      if (!auto) toast("Nothing to save", "info");
      updateStatus();
      return;
    }
    if (S.saving) {
      S.savePending = true;
      return;
    }
    S.saving = true;
    updateStatus();
    let res;
    try {
      res = await api("POST", `file/${encodeURIComponent(S.name)}`, {
        diagram: JSON.parse(json), base_sha256: S.disk.sha, autosave: auto, client: S.clientId });
    } catch (err) {
      res = { status: 0, data: { error: String(err) } };
    }
    S.saving = false;
    if (res.status === 200) {
      S.disk = { sha: res.data.sha256, json };
      S.blocked = null;
      $("#banner").hidden = true;
      if (!auto) toast("Saved");
      scheduleCheck();
    } else if (res.status === 409) {
      S.blocked = "conflict";
      S.conflict = res.data;
      showConflict();
    } else if (res.status === 422) {
      S.blocked = "invalid";
      showBanner("error", "Not saved: the server says the diagram is invalid. Undo the last change or fix it; nothing was written.",
        (res.data.issues || []).map((i) => `${i.id ? `[${i.id}] ` : ""}${i.message}`));
    } else {
      S.blocked = "error";
      showBanner("error", `Not saved: ${(res.data && res.data.error) || `HTTP ${res.status}`}. The next edit retries.`, []);
    }
    updateStatus();
    if (S.savePending) {
      S.savePending = false;
      if (!S.blocked) save(auto);
    }
  }

  function showConflict() {
    showBanner("error",
      `${PATHS.diagrams}/${S.name}.json changed on disk while this page also has edits. `
      + "Nothing was overwritten.", [
        "Keep mine writes this page over the file, using the hash that is on disk now.",
        "Load theirs discards the unsaved edits in this page and reloads the file.",
      ], [
        h("button", { type: "button", class: "btn small primary", onclick: () => keepMine() }, "Keep mine"),
        h("button", { type: "button", onclick: () => loadTheirs() }, "Load theirs"),
        h("button", { type: "button", onclick: downloadMine }, "Download my version"),
      ]);
  }

  async function keepMine() {
    const sha = S.conflict && S.conflict.sha256;
    if (!sha || !S.diagram) return;
    const json = JSON.stringify(S.diagram);
    const res = await api("POST", `file/${encodeURIComponent(S.name)}`, {
      diagram: JSON.parse(json), base_sha256: sha, autosave: false, client: S.clientId, label: "keep mine" });
    if (res.status === 200) {
      S.disk = { sha: res.data.sha256, json };
      S.blocked = null;
      S.conflict = null;
      $("#banner").hidden = true;
      toast("Kept your version");
      updateStatus();
      scheduleCheck();
    } else if (res.status === 409) {
      S.blocked = "conflict";
      S.conflict = res.data;
      showConflict();
    } else if (res.status === 422) {
      showBanner("error", "Keep mine was refused: the diagram is invalid. Nothing was written.",
        (res.data.issues || []).map((i) => `${i.id ? `[${i.id}] ` : ""}${i.message}`));
    } else {
      showBanner("error", `Keep mine failed: ${(res.data && res.data.error) || `HTTP ${res.status}`}`, []);
    }
  }

  async function loadTheirs() {
    const res = await api("GET", `file/${encodeURIComponent(S.name)}`);
    if (res.ok && res.data && res.data.diagram) {
      S.conflict = null;
      await applyDiskVersion(res.data.diagram, res.data.sha256, { resetHistory: false });
      toast("Loaded the version on disk");
    } else {
      showBanner("error", `Could not load the file: ${(res.data && res.data.error) || res.status}`, []);
    }
  }

  async function reloadFromDisk() {
    const r = await api("GET", `file/${encodeURIComponent(S.name)}`);
    if (r.ok && r.data.diagram) applyDiskVersion(r.data.diagram, r.data.sha256, { resetHistory: false });
  }

  /* Adopt a version from disk (initial load, reload; live reload hooks in here). */
  function applyDiskVersion(diagram, sha, opts = {}) {
    S.disk = { sha, json: JSON.stringify(diagram) };
    S.blocked = null;
    $("#banner").hidden = true;
    if (opts.resetHistory !== false) {
      S.undo = [];
      S.redo = [];
      S.sel = [];
    } else if (S.diagram) {
      S.undo.push({ label: "load from disk", before: JSON.stringify(S.diagram), after: S.disk.json,
        time: performance.now(), selBefore: S.sel.slice(), selAfter: [] });
      S.redo = [];
    }
    S.diagram = diagram;
    return refreshInputs().then(() => {
      render();
      updateUi();
      scheduleCheck();
    });
  }

  async function loadAssets() {
    for (const p of S.diagram.panels || []) {
      if (!p.embed || p.embed in S.embeds) continue;
      try {
        const r = await fetch(`/${p.embed}`, { cache: "no-store" });
        S.embeds[p.embed] = r.ok ? await r.text() : null;
      } catch (err) {
        S.embeds[p.embed] = null;
      }
    }
    for (const n of S.diagram.nodes || []) {
      if (n.kind === "image" && n.src && !(n.src in S.images)) S.images[n.src] = `/${n.src}?v=${encodeURIComponent(S.revision || "")}`;
    }
  }

  let inputLoadSeq = 0;
  async function refreshInputs() {
    if (!S.name) return false;
    const name = S.name, seq = ++inputLoadSeq;
    const [r, values] = await Promise.all([api("GET", `payload/${encodeURIComponent(name)}`), api("GET", "values")]);
    if (S.name !== name || seq !== inputLoadSeq) return false;
    if (!r.ok || !r.data?.payload || !values.ok) {
      showBanner("error", "Scientific inputs could not be refreshed; showing the last usable drawing.",
        (r.data?.issues || []).map(item => item.message));
      return false;
    }
    S.tokens = r.data.payload.tokens;
    S.values = values.data.values || {};
    S.embeds = r.data.payload.embeds || {};
    S.images = r.data.payload.images || {};
    S.revision = r.data.revision;
    S.serverFor = null;
    S.serverIssues = null;
    let style = document.getElementById("pk-figure-fonts");
    if (!style) { style = document.createElement("style"); style.id = "pk-figure-fonts"; document.head.append(style); }
    const css = DiagramRenderer.fontFaceCss(S.tokens, r.data.font_url)
      .replace(/url\((['"])(.*?)\1\)/g, (_, q, url) => `url(${q}${url}?v=${encodeURIComponent(S.revision)}${q})`);
    if (style.textContent !== css) style.textContent = css;
    await DiagramRenderer.fontsReady(S.tokens);
    await loadAssets();
    if (S.name !== name || seq !== inputLoadSeq) return false;
    render();
    updateUi();
    scheduleCheck();
    return true;
  }

  // The first view arrives with the page itself; only a switch fades.
  let routed = false;
  function revealSwitch(element) {
    if (routed) App.reveal(element);
    routed = true;
  }

  async function openDiagram(name) {
    document.body.classList.remove("is-landing");
    $("#landing").hidden = true;
    $("#banner").hidden = true;
    const r = await api("GET", `file/${encodeURIComponent(name)}`);
    if (!r.ok) {
      showLanding(`Cannot open ${name}: ${(r.data && r.data.error) || r.status}`);
      return;
    }
    if (!r.data.diagram || typeof r.data.diagram !== "object") {
      S.name = null;
      S.diagram = null;
      showLanding(`${r.data.file} is not valid JSON (${r.data.parse_error}); fix the file first.`);
      return;
    }
    S.name = name;
    S.gone = null;
    document.title = `${name} · Diagram editor`;
    $("#docname").textContent = name;
    await applyDiskVersion(r.data.diagram, r.data.sha256);
    window.dispatchEvent(new Event("diagramchange"));
    zoomFit();
    revealSwitch(viewport);
  }

  async function showLanding(message) {
    document.title = "Diagram editor";
    document.body.classList.add("is-landing");
    setTool("select");
    S.name = null;
    S.diagram = null;
    window.dispatchEvent(new Event("diagramchange"));
    paper.replaceChildren();
    overlay.replaceChildren();
    $("#docname").textContent = "";
    renderInspector();
    renderLayers();
    renderLint();
    updateStatus();
    const r = await api("GET", "list");
    const list = r.ok && Array.isArray(r.data) ? r.data : [];
    const ago = (ns) => {
      if (!ns) return "";
      const sec = Math.max(0, (Date.now() - ns / 1e6) / 1000);
      if (sec < 90) return "edited just now";
      if (sec < 5400) return `edited ${Math.round(sec / 60)} min ago`;
      if (sec < 129600) return `edited ${Math.round(sec / 3600)} h ago`;
      return `edited ${Math.round(sec / 86400)} d ago`;
    };
    const card = (d) => {
      const thumb = h("div", { class: "thumb" });
      const img = h("img", { alt: "", loading: "lazy", src: `/${PATHS.diagram_output}/${encodeURIComponent(d.name)}.png?v=${d.mtime_ns || 0}` });
      img.addEventListener("error", () => img.replaceWith(Icons.node("file", 32)));
      thumb.append(img);
      return h("a", { class: "dcard", href: `#${encodeURIComponent(d.name)}` }, thumb,
        h("div", { class: "info" }, h("b", {}, d.name), h("span", {}, ago(d.mtime_ns))));
    };
    const nameInput = h("input", { type: "text", class: "input mono", placeholder: "fig3_new_diagram", pattern: "[a-z0-9][a-z0-9_\\-]*",
      required: true, title: "Lower-case letters, digits, - and _" });
    const width = segmented(Object.entries(S.tokens.widths_mm || { single: 89, double: 183 }).map(([n, w]) => (
      { value: String(w), label: `${w}`, title: `${n} column, ${w} mm` })), "183", (v) => {
      width.dataset.value = v;
      for (const b of width.querySelectorAll("button")) b.classList.toggle("on", b.title.endsWith(`${v} mm`));
    }, "new-width");
    width.dataset.value = "183";
    const height = h("input", { type: "number", class: "input", value: "60", min: "5", step: "1" });
    const form = h("form", { onsubmit: async (e) => {
      e.preventDefault();
      const name = nameInput.value.trim();
      if (list.some((d) => d.name === name)) {
        toast(`${name} already exists; pick another name`, "warning");
        nameInput.focus();
        return;
      }
      const diagram = { schema_version: 1, canvas: { width_mm: Number(width.dataset.value), height_mm: Number(height.value), grid: 1 },
        panels: [], nodes: [], edges: [], raw: [] };
      const res = await api("POST", `file/${encodeURIComponent(name)}`, { diagram, base_sha256: null });
      if (res.ok) location.hash = name;
      else if (res.status === 409) toast(`${name} already exists; pick another name`, "warning");
      else toast((res.data && res.data.error) || `HTTP ${res.status}`, "error");
    } },
    h("div", { class: "new-head" }, Icons.node("plus", 18), "New diagram"),
    h("div", {}, h("span", { class: "lab", style: { display: "block", fontSize: "11px", color: "var(--text-3)", marginBottom: "3px" } }, "Name"), nameInput),
    h("div", {}, h("span", { class: "lab", style: { display: "block", fontSize: "11px", color: "var(--text-3)", marginBottom: "3px" } }, "Width (mm)"), width),
    h("div", {}, h("span", { class: "lab", style: { display: "block", fontSize: "11px", color: "var(--text-3)", marginBottom: "3px" } }, "Height (mm)"), height),
    h("button", { type: "submit", class: "btn primary" }, "Create"));
    const landing = $("#landing");
    landing.replaceChildren(h("div", { class: "wrap" },
      h("div", { class: "lhead" }, h("div", {}, h("h1", {}, "Diagrams"),
        h("p", {}, `${list.length} in ${PATHS.diagrams} · exported to ${PATHS.diagram_output}`))),
      message ? h("div", { class: "msg" }, message) : null,
      h("div", { class: "cards" }, ...list.map(card), h("div", { class: "dcard new" }, form))));
    landing.hidden = false;
    revealSwitch(landing);
  }

  async function route() {
    const name = decodeURIComponent(location.hash.slice(1));
    if (S.editing) finishTextEdit(true);
    if (S.diagram && name !== S.name) {
      await save(false);
      // A failed or still-running save must not clear the only local copy.
      if (S.saving || S.drag || JSON.stringify(S.diagram) !== S.disk.json) {
        history.replaceState(null, "", `#${encodeURIComponent(S.name)}`);
        if (!S.blocked) toast("Finish saving before leaving this diagram", "info");
        return;
      }
    }
    if (name === S.name && S.diagram) return;
    if (!name) showLanding();
    else openDiagram(name);
  }

  window.addEventListener("beforeunload", (e) => {
    if (S.diagram && (S.editing || JSON.stringify(S.diagram) !== S.disk.json || S.saving)) {
      e.preventDefault();
      e.returnValue = "";
    }
  });
  /* On resize, refit if the view was fitted; otherwise keep the same point
     of the figure under the centre of the canvas. */
  let lastViewport = null;
  function onViewportResize() {
    const r = viewport.getBoundingClientRect();
    if (S.diagram && lastViewport && (r.width !== lastViewport.width || r.height !== lastViewport.height)) {
      if (S.view.fitted) zoomFit();
      else {
        S.view.x += (r.width - lastViewport.width) / 2;
        S.view.y += (r.height - lastViewport.height) / 2;
        layoutStage();
        if (S.editing) positionEditor();
      }
    }
    lastViewport = { width: r.width, height: r.height };
    viewport.classList.toggle("narrow", r.width < 520);
  }
  new ResizeObserver(onViewportResize).observe(viewport);
  window.addEventListener("hashchange", route);

  async function boot() {
    const [tokens, values] = await Promise.all([api("GET", "tokens"), api("GET", "values")]);
    S.tokens = tokens.data;
    S.values = (values.data && values.data.values) || {};
    const embeds = await api("GET", "embeds");
    S.embedCatalog = embeds.ok && Array.isArray(embeds.data) ? embeds.data : [];
    const style = document.createElement("style");
    style.id = "pk-figure-fonts";
    style.textContent = DiagramRenderer.fontFaceCss(S.tokens, "/" + window.TRAZO_CONFIG.paths.figure_fonts + "/");
    document.head.appendChild(style);
    await DiagramRenderer.fontsReady(S.tokens);
    await route();
    App.watch(pollStatus);
  }

  /* For scripted checks in headless Chrome. */
  window.DiagramEditor = { state: S, run, commit, render, select, save, zoomTo, setZoom, startTextEdit, finishTextEdit,
    toMm: clientToMm, applyDiskVersion, toast, showTab, setTabBadge, setTool, zoomSelection };

  boot();
})();
