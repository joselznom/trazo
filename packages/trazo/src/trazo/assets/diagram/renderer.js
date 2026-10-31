/* Diagram renderer: figures/diagrams/<name>.json -> SVG in millimetres.
 *
 * One renderer for the browser editor and for export (headless Chrome via
 * scripts/diagram.py). Plain browser JavaScript, no build step.
 *
 *   DiagramRenderer.fontFaceCss(tokens, fontBaseUrl) -> @font-face rules
 *   await DiagramRenderer.fontsReady(tokens)
 *   DiagramRenderer.render(diagram, {tokens, values, embeds, images})
 *       -> {svg: SVGSVGElement, svgText, geometry, issues}
 *   await DiagramRenderer.renderForExport(payload) -> {svgText, geometry, issues}
 *
 * `values` maps {{v:key}} keys to text, `embeds` maps embed paths to SVG text
 * (null when missing) and `images` maps image paths to data URIs. All
 * coordinates in `geometry` are absolute canvas millimetres; lint rules use
 * it instead of estimating text widths.
 */
(function (root) {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const XLINK_NS = "http://www.w3.org/1999/xlink";
  const PT = 25.4 / 72;
  const VALUE_KEY = /\{\{v:([A-Za-z0-9_.\-]+)\}\}/g;
  const ANCHOR = /^([A-Za-z0-9][A-Za-z0-9_-]*)\.(left|right|top|bottom|center)(?:@([0-9]*\.?[0-9]+))?$/;
  const EPS = 0.01;
  const SUB_SHIFT = 0.25;
  const SUP_SHIFT = 0.35;
  const MEASURE_PX = 100;

  let measureCtx = null;

  function fmt(n) {
    return String(Math.round(n * 1000) / 1000);
  }

  function el(doc, name, attrs, parent) {
    const node = doc.createElementNS(SVG_NS, name);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined) continue;
      node.setAttribute(key, typeof value === "number" ? fmt(value) : String(value));
    }
    if (parent) parent.appendChild(node);
    return node;
  }

  function colorTable(tokens) {
    const table = Object.assign({}, tokens.roles);
    for (const group of ["contenders", "datasets", "outcomes"]) {
      for (const [name, entry] of Object.entries(tokens[group] || {})) table[name] = entry.color;
    }
    return table;
  }

  function fontFaceCss(tokens, fontBaseUrl) {
    const base = fontBaseUrl.endsWith("/") ? fontBaseUrl : fontBaseUrl + "/";
    return tokens.fonts.files.map((f) =>
      `@font-face { font-family: '${f.family}'; font-weight: ${f.weight}; ` +
      `src: url('${base}${f.file}'); }`).join("\n");
  }

  async function fontsReady(tokens) {
    const family = tokens.fonts.family;
    const weights = Object.values(tokens.fonts.weights);
    await Promise.all(weights.flatMap((w) => [
      document.fonts.load(`${w} 10px "${family}"`),
      document.fonts.load(`italic ${w} 10px "${family}"`),
    ]));
    await document.fonts.ready;
  }

  function fontFamilyAttr(tokens) {
    return [tokens.fonts.family, ...tokens.fonts.fallback]
      .map((f) => (f.includes(" ") || f.startsWith("Barlow") ? `'${f}'` : f)).join(", ");
  }

  function measure(text, font, tokens) {
    if (!measureCtx) measureCtx = document.createElement("canvas").getContext("2d");
    measureCtx.font = `${font.italic ? "italic " : ""}${font.weight} ${MEASURE_PX}px "${tokens.fonts.family}"`;
    const width = measureCtx.measureText(text).width / MEASURE_PX * font.size;
    return width + font.letterSpacing * Array.from(text).length;
  }

  /* ---------- text ---------- */

  function normalizeBlocks(text) {
    if (text === undefined || text === null) return [];
    const blocks = typeof text === "string" ? [{ t: text }] : text;
    return blocks.map((block) => ({
      style: block.style || "node-body",
      runs: typeof block.t === "string" ? [{ t: block.t }] : (block.runs || []),
      spaceBefore: block.space_before_pt,
      align: block.align,
    }));
  }

  function substitute(text, ctx, nodeId) {
    return text.replace(VALUE_KEY, (match, key) => {
      if (Object.prototype.hasOwnProperty.call(ctx.values, key)) {
        ctx.usedValues.add(key);
        return ctx.values[key];
      }
      ctx.issue("error", "unknown-value", nodeId, `unknown value key {{v:${key}}}`);
      return match;
    });
  }

  function runFont(style, run, ctx, nodeId) {
    const t = ctx.tokens;
    const base = style.size_pt * PT;
    const script = run.sub || run.sup;
    const size = script ? base * t.lint.script_scale : base;
    const weight = run.bold ? Math.max(style.weight, t.fonts.weights.semibold) : style.weight;
    let color = style.color;
    if (run.color !== undefined) color = run.color;
    if (!(color in ctx.colors)) {
      ctx.issue("error", "unknown-token", nodeId, `unknown colour token '${color}'`);
    }
    return {
      size,
      sizePt: size / PT,
      weight,
      italic: !!(run.italic || style.italic),
      fill: ctx.colors[color] || "#ff00ff",
      letterSpacing: (style.letter_spacing_em || 0) * size,
      shift: run.sub ? SUB_SHIFT * base : run.sup ? -SUP_SHIFT * base : 0,
    };
  }

  /* Split runs into words (break after a space or hyphen) and forced breaks. */
  function tokenize(block, style, ctx, nodeId) {
    const items = [];
    let word = [];
    const flush = (breakAfter) => {
      if (word.length) items.push({ chunks: word, breakAfter });
      word = [];
    };
    for (const run of block.runs) {
      let text = substitute(String(run.t === undefined ? "" : run.t), ctx, nodeId);
      if (style.uppercase) text = text.toUpperCase();
      const font = runFont(style, run, ctx, nodeId);
      let buffer = "";
      const push = () => {
        if (buffer) word.push({ text: buffer, font, value: run });
        buffer = "";
      };
      for (const ch of text) {
        if (ch === "\n") {
          push();
          flush(true);
          items.push({ forced: true });
        } else if (ch === " " || (ch === "-" && /\S/.test(buffer))) {
          buffer += ch;
          push();
          flush(true);
        } else {
          buffer += ch;
        }
      }
      push();
    }
    flush(false);
    for (const item of items) {
      if (item.forced) continue;
      for (const chunk of item.chunks) chunk.width = measure(chunk.text, chunk.font, ctx.tokens);
      item.width = item.chunks.reduce((sum, c) => sum + c.width, 0);
      const last = item.chunks[item.chunks.length - 1];
      const trimmed = last.text.replace(/ +$/, "");
      item.trailing = trimmed === last.text ? 0 : last.width - measure(trimmed, last.font, ctx.tokens);
    }
    return items;
  }

  function breakLines(items, maxWidth) {
    const lines = [];
    let line = { words: [], width: 0 };
    for (const item of items) {
      if (item.forced) {
        lines.push(line);
        line = { words: [], width: 0 };
        continue;
      }
      if (line.words.length && line.width + item.width - item.trailing > maxWidth + EPS) {
        lines.push(line);
        line = { words: [], width: 0 };
      }
      line.words.push(item);
      line.width += item.width;
    }
    lines.push(line);
    for (const l of lines) {
      const last = l.words[l.words.length - 1];
      if (last) l.width -= last.trailing;
    }
    return lines;
  }

  function layoutText(node, contentBox, autoWidth, ctx) {
    const t = ctx.tokens;
    const blocks = normalizeBlocks(node.text);
    const cap = t.fonts.cap_height_em;
    const desc = t.fonts.descender_em;
    const laid = [];
    let baseline = null;
    let bottom = contentBox.y;
    const fontsPt = new Set();
    let maxLine = 0;
    blocks.forEach((block, bi) => {
      const style = t.text_styles[block.style];
      if (!style) {
        ctx.issue("error", "unknown-token", node.id, `unknown text style '${block.style}'`);
        return;
      }
      const size = style.size_pt * PT;
      fontsPt.add(style.size_pt);
      const items = tokenize(block, style, ctx, node.id);
      for (const item of items) {
        for (const chunk of item.chunks || []) fontsPt.add(Math.round(chunk.font.sizePt * 1000) / 1000);
      }
      const lines = breakLines(items, autoWidth ? Infinity : contentBox.w);
      const spaceBefore = (block.spaceBefore !== undefined ? block.spaceBefore : style.space_before_pt || 0) * PT;
      lines.forEach((line, li) => {
        if (baseline === null) baseline = contentBox.y + cap * size;
        else baseline += style.line_height_pt * PT + (li === 0 && bi > 0 ? spaceBefore : 0);
        maxLine = Math.max(maxLine, line.width);
        laid.push({ line, baseline, size, style, align: block.align || node.align || "left" });
        bottom = baseline + desc * size;
      });
    });
    return { laid, height: bottom - contentBox.y, maxLine, fontsPt: [...fontsPt].sort((a, b) => a - b) };
  }

  function drawText(parent, layout, contentBox, node, ctx) {
    const doc = parent.ownerDocument;
    const shiftY = node.valign === "middle" ? (contentBox.h - layout.height) / 2
      : node.valign === "bottom" ? contentBox.h - layout.height : 0;
    const lines = [];
    for (const { line, baseline, size, style, align } of layout.laid) {
      const y = baseline + shiftY;
      const x = align === "center" ? contentBox.x + (contentBox.w - line.width) / 2
        : align === "right" ? contentBox.x + contentBox.w - line.width : contentBox.x;
      const chunks = line.words.flatMap((w) => w.chunks);
      if (chunks.length) {
        const last = chunks[chunks.length - 1];
        chunks[chunks.length - 1] = Object.assign({}, last, { text: last.text.replace(/ +$/, "") });
      }
      const baseFont = runFont(style, {}, ctx, node.id);
      const text = el(doc, "text", {
        x, y, "font-size": size, "font-weight": style.weight, fill: baseFont.fill,
        "letter-spacing": baseFont.letterSpacing || null, style: "white-space: pre",
      }, parent);
      let shift = 0;
      let current = null;
      for (const chunk of chunks) {
        const f = chunk.font;
        const key = [f.size, f.weight, f.italic, f.fill, f.shift].join("|");
        if (!current || current.key !== key) {
          const attrs = {};
          if (Math.abs(f.size - size) > 1e-9) attrs["font-size"] = f.size;
          if (f.weight !== style.weight) attrs["font-weight"] = f.weight;
          if (f.italic) attrs["font-style"] = "italic";
          if (f.fill !== baseFont.fill) attrs.fill = f.fill;
          if (f.shift !== shift) {
            attrs.dy = f.shift - shift;
            shift = f.shift;
          }
          if (f.letterSpacing !== baseFont.letterSpacing) attrs["letter-spacing"] = f.letterSpacing;
          current = { key, node: Object.keys(attrs).length ? el(doc, "tspan", attrs, text) : text };
        }
        current.node.appendChild(doc.createTextNode(chunk.text));
      }
      lines.push({
        x, baseline: y, width: line.width,
        top: y - ctx.tokens.fonts.cap_height_em * size,
        bottom: y + ctx.tokens.fonts.descender_em * size,
        text: chunks.map((c) => c.text).join(""),
      });
    }
    return lines;
  }

  /* ---------- geometry helpers ---------- */

  function padding(node, ctx) {
    const raw = node.padding !== undefined ? node.padding
      : (ctx.tokens.shapes[node.kind] || {}).padding_mm || 0;
    const p = Array.isArray(raw) ? raw : [raw, raw, raw, raw];
    return { top: p[0], right: p[1], bottom: p[2], left: p[3] };
  }

  function strokeAttrs(name, ctx, id) {
    if (!name) return {};
    const s = ctx.tokens.strokes[name];
    if (!s) {
      ctx.issue("error", "unknown-token", id, `unknown stroke token '${name}'`);
      return {};
    }
    return {
      stroke: ctx.colors[s.color],
      "stroke-width": s.width_pt * PT,
      "stroke-dasharray": s.dash_pt ? s.dash_pt.map((d) => fmt(d * PT)).join(" ") : null,
    };
  }

  function fillColor(name, ctx, id) {
    if (name === undefined || name === null) return null;
    if (!(name in ctx.colors)) {
      ctx.issue("error", "unknown-token", id, `unknown colour token '${name}'`);
      return "#ff00ff";
    }
    return ctx.colors[name];
  }

  function anchorPoint(spec, boxes, ctx, edgeId) {
    const m = ANCHOR.exec(spec || "");
    if (!m) {
      ctx.issue("error", "bad-anchor", edgeId, `bad anchor '${spec}'`);
      return null;
    }
    const box = boxes[m[1]];
    if (!box) {
      ctx.issue("error", "unknown-id", edgeId, `edge endpoint '${spec}' refers to unknown id '${m[1]}'`);
      return null;
    }
    const f = m[3] !== undefined ? parseFloat(m[3]) : 0.5;
    const [x, y, w, h] = box;
    switch (m[2]) {
      case "left": return { id: m[1], side: "left", x, y: y + h * f, dir: [-1, 0] };
      case "right": return { id: m[1], side: "right", x: x + w, y: y + h * f, dir: [1, 0] };
      case "top": return { id: m[1], side: "top", x: x + w * f, y, dir: [0, -1] };
      case "bottom": return { id: m[1], side: "bottom", x: x + w * f, y: y + h, dir: [0, 1] };
      default: return { id: m[1], side: "center", x: x + w / 2, y: y + h / 2, dir: null };
    }
  }

  function centerDir(from, to) {
    const dx = to.x - from.x;
    const dy = to.y - from.y;
    return Math.abs(dx) >= Math.abs(dy) ? [Math.sign(dx) || 1, 0] : [0, Math.sign(dy) || 1];
  }

  function orthogonal(points, startDir, endDir) {
    const out = [points[0]];
    let horizontal = startDir[0] !== 0;
    for (let i = 1; i < points.length; i++) {
      const p = out[out.length - 1];
      const q = points[i];
      const isLast = i === points.length - 1;
      if (Math.abs(p.x - q.x) > EPS && Math.abs(p.y - q.y) > EPS) {
        if (isLast && endDir && points.length === 2 && (startDir[0] !== 0) === (endDir[0] !== 0)) {
          if (horizontal) {
            const mx = (p.x + q.x) / 2;
            out.push({ x: mx, y: p.y }, { x: mx, y: q.y });
          } else {
            const my = (p.y + q.y) / 2;
            out.push({ x: p.x, y: my }, { x: q.x, y: my });
          }
        } else if (isLast && endDir) {
          out.push(endDir[0] !== 0 ? { x: p.x, y: q.y } : { x: q.x, y: p.y });
        } else {
          out.push(horizontal ? { x: q.x, y: p.y } : { x: p.x, y: q.y });
        }
      }
      out.push(q);
      const a = out[out.length - 2];
      horizontal = Math.abs(a.y - q.y) <= EPS ? false : true;
    }
    return out.filter((p, i) => i === 0 || Math.hypot(p.x - out[i - 1].x, p.y - out[i - 1].y) > EPS);
  }

  function shorten(points, start, end) {
    const pts = points.map((p) => ({ x: p.x, y: p.y }));
    const cut = (a, b, d) => {
      const len = Math.hypot(b.x - a.x, b.y - a.y);
      if (len <= EPS || d <= 0) return;
      const k = Math.min(d, len - EPS) / len;
      a.x += (b.x - a.x) * k;
      a.y += (b.y - a.y) * k;
    };
    if (pts.length >= 2) {
      cut(pts[0], pts[1], start);
      cut(pts[pts.length - 1], pts[pts.length - 2], end);
    }
    return pts;
  }

  /* ---------- embeds ---------- */

  const UNIT_MM = { mm: 1, cm: 10, in: 25.4, pt: PT, pc: 12 * PT, px: 25.4 / 96, "": 25.4 / 96 };

  function lengthMm(value) {
    const m = /^\s*([0-9]*\.?[0-9]+)\s*(mm|cm|in|pt|pc|px|)\s*$/.exec(value || "");
    return m ? parseFloat(m[1]) * UNIT_MM[m[2]] : null;
  }

  function prefixIds(svg, prefix) {
    const ids = new Set();
    for (const node of svg.querySelectorAll("[id]")) {
      ids.add(node.id);
      node.id = prefix + node.id;
    }
    const fix = (text) => text.replace(/url\(\s*#([^)\s]+)\s*\)/g, (m, id) =>
      ids.has(id) ? `url(#${prefix}${id})` : m);
    for (const node of svg.querySelectorAll("*")) {
      for (const attr of Array.from(node.attributes)) {
        if ((attr.localName === "href") && attr.value.startsWith("#") && ids.has(attr.value.slice(1))) {
          node.setAttributeNS(attr.namespaceURI, attr.name, "#" + prefix + attr.value.slice(1));
        } else if (attr.value.includes("url(")) {
          attr.value = fix(attr.value);
        }
      }
      if (node.localName === "style") node.textContent = fix(node.textContent);
    }
  }

  /* Rewrite each rule so it matches only inside `scope`; at-rules are dropped. */
  function scopeCss(css, scope) {
    const text = css.replace(/\/\*[\s\S]*?\*\//g, "");
    let out = "";
    let dropped = 0;
    let i = 0;
    while (i < text.length) {
      const open = text.indexOf("{", i);
      if (open < 0) break;
      let depth = 1;
      let j = open + 1;
      while (j < text.length && depth) {
        if (text[j] === "{") depth++;
        else if (text[j] === "}") depth--;
        j++;
      }
      const prelude = text.slice(i, open).trim();
      if (prelude.startsWith("@")) dropped++;
      else if (prelude) {
        const selectors = prelude.split(",").map((s) => `${scope} ${s.trim()}`).join(", ");
        out += `${selectors} {${text.slice(open + 1, j - 1)}}\n`;
      }
      i = j;
    }
    return { css: out, dropped };
  }

  function placeEmbed(parent, panel, box, ctx) {
    const doc = parent.ownerDocument;
    const info = { path: panel.embed, missing: false, natural_mm: null, placed_mm: null, scale: null };
    const text = ctx.embeds[panel.embed];
    const placeholder = (message) => {
      el(doc, "rect", { x: box.x, y: box.y, width: box.w, height: box.h, fill: "none",
        stroke: ctx.colors.signal, "stroke-width": 0.3, "stroke-dasharray": "1 1" }, parent);
      ctx.issue("error", "missing-embed", panel.id, message);
      info.missing = true;
      return info;
    };
    if (typeof text !== "string") return placeholder(`embed not found: ${panel.embed}`);
    const parsed = new DOMParser().parseFromString(text, "image/svg+xml");
    const svg = parsed.documentElement;
    if (!svg || svg.localName !== "svg" || parsed.getElementsByTagName("parsererror").length) {
      return placeholder(`embed is not valid SVG: ${panel.embed}`);
    }
    const vb = (svg.getAttribute("viewBox") || "").trim().split(/[\s,]+/).map(Number);
    let nw = lengthMm(svg.getAttribute("width"));
    let nh = lengthMm(svg.getAttribute("height"));
    if ((nw === null || nh === null) && vb.length === 4) {
      nw = vb[2] * UNIT_MM.px;
      nh = vb[3] * UNIT_MM.px;
    }
    if (!nw || !nh) return placeholder(`embed has no usable size: ${panel.embed}`);
    if (vb.length !== 4) {
      svg.setAttribute("viewBox", `0 0 ${parseFloat(svg.getAttribute("width"))} ${parseFloat(svg.getAttribute("height"))}`);
    }
    const scale = (panel.fit || "contain") === "none" ? 1 : Math.min(box.w / nw, box.h / nh);
    const w = nw * scale;
    const h = nh * scale;
    prefixIds(svg, `${panel.id}--`);
    svg.setAttribute("data-embed-scope", panel.id);
    for (const style of Array.from(svg.getElementsByTagNameNS(SVG_NS, "style"))) {
      const scoped = scopeCss(style.textContent, `[data-embed-scope="${panel.id}"]`);
      style.textContent = scoped.css;
      if (scoped.dropped) {
        ctx.issue("warning", "embed-style", panel.id,
          `dropped ${scoped.dropped} @-rule(s) from the <style> of ${panel.embed}`);
      }
    }
    const nested = doc.importNode(svg, true);
    for (const [k, v] of Object.entries({ x: box.x, y: box.y, width: w, height: h })) nested.setAttribute(k, fmt(v));
    nested.setAttribute("data-embed", panel.embed);
    parent.appendChild(nested);
    Object.assign(info, { natural_mm: [nw, nh], placed_mm: [box.x, box.y, w, h], scale });
    return info;
  }

  /* Ids left out of the SVG: hidden, inside a hidden group or panel, or an edge into one. */
  function concealedIds(diagram) {
    const hidden = new Set();
    for (const coll of ["panels", "nodes", "edges", "raw"]) {
      for (const item of diagram[coll] || []) if (item && item.hidden && item.id) hidden.add(item.id);
    }
    const nodes = diagram.nodes || [];
    let changed = true;
    while (changed) {
      changed = false;
      for (const node of nodes) {
        if (node.kind === "group" && hidden.has(node.id)) {
          for (const child of node.children || []) {
            if (!hidden.has(child)) { hidden.add(child); changed = true; }
          }
        }
        if (node.panel && hidden.has(node.panel) && !hidden.has(node.id)) {
          hidden.add(node.id);
          changed = true;
        }
      }
    }
    for (const edge of diagram.edges || []) {
      const from = String(edge.from || "").split(".")[0];
      const to = String(edge.to || "").split(".")[0];
      if (edge.hidden || hidden.has(from) || hidden.has(to)) hidden.add(edge.id);
    }
    return hidden;
  }

  /* ---------- render ---------- */

  function render(diagram, options) {
    const doc = options.document || document;
    const tokens = options.tokens;
    const issues = [];
    const ctx = {
      tokens,
      colors: colorTable(tokens),
      values: options.values || {},
      embeds: options.embeds || {},
      images: options.images || {},
      usedValues: new Set(),
      issue(level, code, id, message) { issues.push({ level, code, id: id || null, message }); },
    };
    const W = diagram.canvas.width_mm;
    const H = diagram.canvas.height_mm;
    const svg = el(doc, "svg", {
      xmlns: SVG_NS, viewBox: `0 0 ${fmt(W)} ${fmt(H)}`, width: `${fmt(W)}mm`, height: `${fmt(H)}mm`,
      "font-family": fontFamilyAttr(tokens),
    });
    const defs = el(doc, "defs", {}, svg);
    el(doc, "rect", { width: W, height: H, fill: ctx.colors.paper }, svg);
    const geometry = { canvas: { width_mm: W, height_mm: H }, panels: [], nodes: [], edges: [], raw: [] };
    const panelOrigin = {};
    const boxes = {};
    const concealed = concealedIds(diagram);
    const scratch = el(doc, "g", {});
    const mount = (id) => (concealed.has(id) ? scratch : svg);

    for (const panel of diagram.panels || []) {
      const g = el(doc, "g", { "data-id": panel.id, "data-role": "panel" }, mount(panel.id));
      panelOrigin[panel.id] = [panel.x, panel.y];
      boxes[panel.id] = [panel.x, panel.y, panel.w, panel.h];
      const hasHeader = !!(panel.label || panel.title);
      const header = hasHeader ? tokens.shapes.panel.header_mm : 0;
      if (hasHeader) {
        const label = { id: panel.id, text: [] };
        if (panel.label) label.text.push({ style: "panel-label", t: panel.label });
        const labelLayout = layoutText(label, { x: panel.x, y: panel.y, w: panel.w, h: header }, true, ctx);
        drawText(g, labelLayout, { x: panel.x, y: panel.y, w: panel.w, h: header }, label, ctx);
        if (panel.title) {
          const title = { id: panel.id, text: [{ style: "panel-title", t: panel.title }] };
          const tx = panel.x + (panel.label ? labelLayout.maxLine + 1.2 : 0);
          const labelStyle = tokens.text_styles["panel-label"];
          const titleStyle = tokens.text_styles["panel-title"];
          const dy = tokens.fonts.cap_height_em * (labelStyle.size_pt - titleStyle.size_pt) * PT;
          const box = { x: tx, y: panel.y + (panel.label ? dy : 0), w: panel.w, h: header };
          drawText(g, layoutText(title, box, true, ctx), box, title, ctx);
        }
        el(doc, "line", Object.assign({ x1: panel.x, y1: panel.y + tokens.shapes.panel.rule_offset_mm,
          x2: panel.x + panel.w, y2: panel.y + tokens.shapes.panel.rule_offset_mm },
        strokeAttrs("rule", ctx, panel.id)), g);
      }
      const content = { x: panel.x, y: panel.y + header, w: panel.w, h: panel.h - header };
      const entry = { id: panel.id, box: [panel.x, panel.y, panel.w, panel.h],
        content: [content.x, content.y, content.w, content.h], embed: null,
        hidden: concealed.has(panel.id) };
      if (panel.embed) entry.embed = placeEmbed(g, panel, content, ctx);
      geometry.panels.push(entry);
    }

    const nodes = diagram.nodes || [];
    const groups = [];
    for (const node of nodes) {
      if (node.kind === "group") {
        groups.push(node);
        continue;
      }
      const [ox, oy] = node.panel && panelOrigin[node.panel] ? panelOrigin[node.panel] : [0, 0];
      if (node.panel && !panelOrigin[node.panel]) {
        ctx.issue("error", "unknown-id", node.id, `unknown panel '${node.panel}'`);
      }
      const pad = padding(node, ctx);
      const x = ox + node.x;
      const y = oy + node.y;
      const autoW = node.kind === "text" && node.w === undefined;
      const autoH = node.kind === "text" && node.h === undefined;
      let w = autoW ? 0 : node.w;
      let h = autoH ? 0 : node.h;
      const content = { x: x + pad.left, y: y + pad.top, w: w - pad.left - pad.right, h: h - pad.top - pad.bottom };
      const layout = node.text ? layoutText(node, content, autoW, ctx) : null;
      if (layout && autoW) {
        w = layout.maxLine + pad.left + pad.right;
        content.w = layout.maxLine;
      }
      if (autoH) {
        h = (layout ? layout.height : 0) + pad.top + pad.bottom;
        content.h = layout ? layout.height : 0;
      }
      boxes[node.id] = [x, y, w, h];
      const g = el(doc, "g", { "data-id": node.id, "data-kind": node.kind }, mount(node.id));
      const fill = fillColor(node.fill, ctx, node.id);
      const stroke = strokeAttrs(node.stroke, ctx, node.id);
      const entry = { id: node.id, kind: node.kind, panel: node.panel || null, box: [x, y, w, h],
        content: [content.x, content.y, content.w, content.h], text: null, fonts_pt: [], image: null,
        hidden: concealed.has(node.id) };
      if (node.kind === "box" || node.kind === "pill" || (node.kind === "text" && (fill || node.stroke))) {
        const r = node.kind === "pill" ? h / 2
          : node.kind === "box" ? (node.radius !== undefined ? node.radius : tokens.shapes.box.radius_mm) : 0;
        el(doc, "rect", Object.assign({ x, y, width: w, height: h, rx: r || null,
          fill: fill || "none" }, stroke), g);
      } else if (node.kind === "image") {
        const href = ctx.images[node.src];
        if (typeof href === "string") {
          const image = el(doc, "image", { x, y, width: w, height: h,
            preserveAspectRatio: node.fit === "fill" ? "none" : "xMidYMid meet" }, g);
          image.setAttributeNS(XLINK_NS, "xlink:href", href);
          image.setAttribute("href", href);
        } else {
          el(doc, "rect", { x, y, width: w, height: h, fill: "none", stroke: ctx.colors.signal,
            "stroke-width": 0.3, "stroke-dasharray": "1 1" }, g);
          ctx.issue("error", "missing-image", node.id, `image not found: ${node.src}`);
        }
        entry.image = { src: node.src, missing: typeof href !== "string" };
      }
      if (layout) {
        const lines = drawText(g, layout, content, node, ctx);
        const widest = lines.reduce((m, l) => Math.max(m, l.width), 0);
        entry.text = {
          lines,
          height: layout.height,
          overflow_x: Math.max(0, widest - content.w),
          overflow_y: Math.max(0, layout.height - content.h),
        };
        entry.fonts_pt = layout.fontsPt;
      }
      geometry.nodes.push(entry);
    }
    const groupById = new Map(groups.map((g) => [g.id, g]));
    const resolving = new Set();
    const groupBox = (id) => {
      if (boxes[id]) return boxes[id];
      const group = groupById.get(id);
      if (!group || resolving.has(id)) return null;
      resolving.add(id);
      const members = (group.children || []).map((child) => boxes[child] || groupBox(child));
      resolving.delete(id);
      const found = members.filter(Boolean);
      if (found.length !== members.length) {
        ctx.issue("error", "unknown-id", id, "group has children that do not exist or form a cycle");
      }
      const x0 = Math.min(...found.map((b) => b[0]));
      const y0 = Math.min(...found.map((b) => b[1]));
      const x1 = Math.max(...found.map((b) => b[0] + b[2]));
      const y1 = Math.max(...found.map((b) => b[1] + b[3]));
      boxes[id] = found.length ? [x0, y0, x1 - x0, y1 - y0] : [0, 0, 0, 0];
      return boxes[id];
    };
    for (const group of groups) {
      geometry.nodes.push({ id: group.id, kind: "group", panel: group.panel || null,
        box: groupBox(group.id) || [0, 0, 0, 0], children: group.children || [],
        content: null, text: null, fonts_pt: [], image: null, hidden: concealed.has(group.id) });
    }

    const markers = new Set();
    for (const edge of diagram.edges || []) {
      const strokeName = edge.stroke || "arrow";
      const token = tokens.strokes[strokeName];
      const from = anchorPoint(edge.from, boxes, ctx, edge.id);
      const to = anchorPoint(edge.to, boxes, ctx, edge.id);
      if (!from || !to || !token) {
        if (!token) ctx.issue("error", "unknown-token", edge.id, `unknown stroke token '${strokeName}'`);
        geometry.edges.push({ id: edge.id, from: edge.from, to: edge.to, points: [], label: null,
          hidden: concealed.has(edge.id) });
        continue;
      }
      const startDir = from.dir || centerDir(from, to);
      const endDir = to.dir || centerDir(to, from);
      const waypoints = (edge.waypoints || []).map(([x, y]) => ({ x, y }));
      let points = [from, ...waypoints, to].map((p) => ({ x: p.x, y: p.y }));
      if ((edge.route || "orthogonal") === "orthogonal") points = orthogonal(points, startDir, endDir);
      const gap = edge.gap || token.gap_mm || [0, 0];
      points = shorten(points, gap[0], gap[1]);
      const g = el(doc, "g", { "data-id": edge.id, "data-kind": "edge" }, mount(edge.id));
      const attrs = Object.assign({ d: points.map((p, i) => `${i ? "L" : "M"}${fmt(p.x)},${fmt(p.y)}`).join(" "),
        fill: "none" }, strokeAttrs(strokeName, ctx, edge.id));
      if (token.marker === "arrow") {
        const markerId = `arrow-${token.color}`;
        if (!markers.has(markerId)) {
          markers.add(markerId);
          const marker = el(doc, "marker", { id: markerId, viewBox: "0 0 10 10", refX: 9, refY: 5,
            markerWidth: 8, markerHeight: 8, orient: "auto" }, defs);
          el(doc, "path", { d: "M0,0 L10,5 L0,10 z", fill: ctx.colors[token.color] }, marker);
        }
        attrs["marker-end"] = `url(#${markerId})`;
      }
      el(doc, "path", attrs, g);
      let labelGeom = null;
      if (edge.label) {
        let best = 0;
        for (let i = 1; i < points.length; i++) {
          const len = Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y);
          const prev = Math.hypot(points[best + 1].x - points[best].x, points[best + 1].y - points[best].y);
          if (len > prev) best = i - 1;
        }
        const a = points[best];
        const b = points[best + 1];
        const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
        const label = { id: edge.id, text: typeof edge.label === "string"
          ? [{ style: "edge-label", t: edge.label }] : edge.label, align: "center" };
        const layout = layoutText(label, { x: 0, y: 0, w: 0, h: 0 }, true, ctx);
        const horizontal = Math.abs(a.y - b.y) <= EPS;
        const lw = layout.maxLine;
        const lh = layout.height;
        const box = horizontal ? { x: mid.x - lw / 2, y: mid.y - 0.8 - lh, w: lw, h: lh }
          : { x: mid.x + 0.8, y: mid.y - lh / 2, w: lw, h: lh };
        const relaid = layoutText(label, box, true, ctx);
        drawText(g, relaid, box, label, ctx);
        labelGeom = { box: [box.x, box.y, box.w, box.h], fonts_pt: relaid.fontsPt };
      }
      geometry.edges.push({ id: edge.id, from: edge.from, to: edge.to,
        from_id: from.id, to_id: to.id, points: points.map((p) => [p.x, p.y]), label: labelGeom,
        hidden: concealed.has(edge.id) });
    }

    for (const raw of diagram.raw || []) {
      const g = el(doc, "g", { "data-id": raw.id, "data-kind": "raw" }, mount(raw.id));
      const parsed = new DOMParser().parseFromString(
        `<g xmlns="${SVG_NS}" xmlns:xlink="${XLINK_NS}">${raw.svg}</g>`, "image/svg+xml");
      if (parsed.getElementsByTagName("parsererror").length) {
        ctx.issue("error", "bad-raw", raw.id, "raw SVG fragment is not well-formed");
      } else {
        for (const child of Array.from(parsed.documentElement.childNodes)) g.appendChild(doc.importNode(child, true));
      }
      geometry.raw.push({ id: raw.id, hidden: concealed.has(raw.id) });
    }

    geometry.values_used = [...ctx.usedValues].sort();
    const svgText = new XMLSerializer().serializeToString(svg);
    const visibleIssues = issues.filter((item) => !item.id || !concealed.has(item.id));
    return { svg, svgText, geometry, issues: visibleIssues };
  }

  async function renderForExport(payload) {
    await fontsReady(payload.tokens);
    const result = render(payload.diagram, payload);
    document.body.appendChild(result.svg);
    return { svgText: result.svgText, geometry: result.geometry, issues: result.issues };
  }

  root.DiagramRenderer = { render, renderForExport, fontFaceCss, fontsReady, colorTable };
})(typeof window !== "undefined" ? window : this);
