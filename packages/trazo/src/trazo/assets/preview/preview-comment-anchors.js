// Preview viewer: comments: where a comment's text is in the page.
// The parts of the viewer are classic scripts loaded in the order of
// trazo.preview.VIEWER_SCRIPTS; their top-level names are shared globals.
  if (!window.PreviewModel) throw new Error("preview-model.js did not load");  // see preview-page.js
  // ---------------------------------------------------------------- comments
  // Review comments: select text, write a note; stored by the server in
  // notes/review/comments.json for Claude to apply and answer; the author resolves them.
  const unitOf = (a, el) => a.units.find((u) => u.contains(el));
  const anchorOf = (node) => {
    const el = node.nodeType === 1 ? node : node.parentElement;
    return el ? el.closest("[data-label]") : null;
  };
  const textNodes = (el) => {
    const walker = el.ownerDocument.createTreeWalker(el, NodeFilter.SHOW_TEXT, {
      acceptNode: (t) => t.parentElement.closest("button, .pv-para, .pv-src-mark, .pv-tbl-edit, .pv-diagram, .pk-front, .pk-keywords") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    return nodes;
  };

  // Whitespace-squashed text of an element, mapped back to its text nodes.
  // The page text never changes before a reload, so it is computed once.
  const flatCache = new WeakMap();
  function flatText(el) {
    let entry = flatCache.get(el);
    if (entry) return entry;
    const nodes = textNodes(el);
    let flat = "", prevSpace = true;
    const map = [];
    nodes.forEach((t, i) => {
      for (let k = 0; k < t.data.length; k++) {
        const ch = t.data[k];
        if (/\s/.test(ch)) { if (prevSpace) continue; flat += " "; prevSpace = true; }
        else { flat += ch; prevSpace = false; }
        map.push([i, k]);
      }
    });
    entry = { nodes, flat, map };
    flatCache.set(el, entry);
    return entry;
  }

  const SURROUND = 32;
  // Characters shared at the join of `a` and `b`: the end of `a` against the end of `b` (tail) or the starts (head).
  const shared = (a, b, tail) => {
    let n = 0;
    while (n < a.length && n < b.length
      && (tail ? a[a.length - 1 - n] === b[b.length - 1 - n] : a[n] === b[n])) n++;
    return n;
  };
  // Find `want` (whitespace-squashed) inside one element: [start, end) of its
  // flat text (rangeIn makes it a Range). Where it occurs more than once,
  // `near` ({prefix, suffix}: the text around the original selection) picks
  // the occurrence; without it, the first one.
  function findIn(el, want, near) {
    if (!want) return null;
    const { flat } = flatText(el);
    let at = flat.indexOf(want);
    if (at < 0) return null;
    if (near && (near.prefix || near.suffix)) {
      let best = -1;
      for (let i = at; i >= 0; i = flat.indexOf(want, i + 1)) {
        const score = shared(flat.slice(0, i), near.prefix || "", true)
          + shared(flat.slice(i + want.length), near.suffix || "", false);
        if (score > best) { best = score; at = i; }
      }
    }
    return [at, at + want.length];
  }
  function rangeIn(el, [start, end]) {
    const { nodes, map } = flatText(el);
    const [si, so] = map[start], [ei, eo] = map[end - 1];
    const range = el.ownerDocument.createRange();
    range.setStart(nodes[si], so);
    range.setEnd(nodes[ei], eo + 1);
    return range;
  }

  // Locating every comment compares it with every labelled element, so an
  // element's context and its word sets are computed once, like flatText.
  const contextCache = new WeakMap();
  const contextOf = (a, el) => {
    let entry = contextCache.get(el);
    if (entry) return entry;
    const i = a.labelled.indexOf(el);
    const object = el.closest('figure[id], table[id]');
    const unit = unitOf(a, el);
    entry = { text: squash(flatText(el).flat).slice(0, 20000),
      before: i > 0 ? squash(flatText(a.labelled[i - 1]).flat).slice(0, 20000) : "",
      after: i + 1 < a.labelled.length ? squash(flatText(a.labelled[i + 1]).flat).slice(0, 20000) : "",
      object: object ? object.id : "", sectionId: unit ? unit.id : "" };
    contextCache.set(el, entry);
    return entry;
  };
  // The text right around a selection inside `el` (up to SURROUND characters
  // each side, squashed like findIn), so a repeated quote can be told apart.
  function surroundOf(el, range, quote) {
    const { nodes, flat, map } = flatText(el);
    // `map` runs in document order, so a test that turns true once and stays
    // true finds its first character by bisection. A character is selected
    // when it starts at or after the range start and ends (offset + 1) at or
    // before its end; comparePoint counts the end point itself as inside.
    const first = (test) => {
      let lo = 0, hi = map.length;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (test(mid)) hi = mid; else lo = mid + 1; }
      return lo;
    };
    const cmp = (k, end) => range.comparePoint(nodes[map[k][0]], map[k][1] + end);
    let s = first((k) => cmp(k, 0) >= 0), e = first((k) => cmp(k, 1) > 0) - 1;
    while (s <= e && flat[s] === " ") s++;
    while (e >= s && flat[e] === " ") e--;
    if (e < s || flat.slice(s, e + 1) !== quote) return { prefix: "", suffix: "" };
    return { prefix: flat.slice(Math.max(0, s - SURROUND), s), suffix: flat.slice(e + 1, e + 1 + SURROUND) };
  }
  const tokens = (s) => new Set((s || "").toLowerCase().match(/[\p{L}\p{N}]+/gu) || []);
  const wordCache = new WeakMap();  // context -> {text, before, after} word sets
  const wordSetsOf = (ctx) => {
    let words = wordCache.get(ctx);
    if (!words) wordCache.set(ctx, words = { text: tokens(ctx.text), before: tokens(ctx.before), after: tokens(ctx.after) });
    return words;
  };
  // Dice similarity of two word sets of sizes m and n that share `shared` words.
  const dice = (shared, m, n) => (m + n ? 2 * shared / (m + n) : 0);
  const similarity = (x, y) => {
    if (x.size > y.size) [x, y] = [y, x];
    let shared = 0;
    for (const w of x) if (y.has(w)) shared++;
    return dice(shared, x.size, y.size);
  };
  // A comment's text is compared with every element's, and most words are in
  // many elements, so looking each word up per element dominates a pass. An
  // index from each word to the elements that have it counts the shared words
  // of every element in one walk over the comment's words.
  const wordIndexCache = new WeakMap();  // analysis -> {lists: Map(word -> element indexes), sizes}
  function wordIndexOf(a) {
    let index = wordIndexCache.get(a);
    if (index) return index;
    const lists = new Map(), sizes = new Int32Array(a.labelled.length);
    a.labelled.forEach((el, i) => {
      const words = wordSetsOf(contextOf(a, el)).text;
      sizes[i] = words.size;
      for (const w of words) {
        let list = lists.get(w);
        if (!list) lists.set(w, list = []);
        list.push(i);
      }
    });
    index = { lists, sizes };
    wordIndexCache.set(a, index);
    return index;
  }
  // How many of `words` each element's text has, by index in a.labelled.
  function sharedWords(index, words) {
    const counts = new Int32Array(index.sizes.length);
    for (const w of words) {
      const list = index.lists.get(w);
      if (list) for (const i of list) counts[i]++;
    }
    return counts;
  }
  // Labels remain useful display names, but paragraph ordinals are not
  // identities. Relocate using original text and neighbours; an ambiguous
  // or deleted target remains visibly unlocated instead of jumping nearby.
  function locateIn(a, c) {
    const want = c.quote ? squash(c.quote) : "";
    if (!want) return null;
    const all = a.labelled;
    const home = a.units.find((u) => titleOf(u) === c.section);
    const anchor = c.anchor || {};
    const legacyObject = a.items.find((it) => it.num !== it.legacyNum
      && `${it.legacyNum}${it.kind === "figure" ? " caption" : ""}` === c.para);
    const oldNum = legacyObject?.legacyNum;
    const renumbered = oldNum && want.startsWith(oldNum)
      && (!want.slice(oldNum.length) || /^[.\s]/.test(want.slice(oldNum.length)))
      ? legacyObject.num + want.slice(oldNum.length) : null;
    const wanted = wordSetsOf(anchor);
    const index = wordIndexOf(a);
    const counts = anchor.text ? sharedWords(index, wanted.text) : null;
    const candidates = all.map((el, i) => {
      const span = findIn(el, want, anchor) || (renumbered && legacyObject.el.contains(el) ? findIn(el, renumbered) : null);
      const ctx = contextOf(a, el);
      const sameObject = anchor.object && anchor.object === ctx.object;
      const sameSection = anchor.sectionId && anchor.sectionId === ctx.sectionId;
      const bracketed = sameSection && anchor.before && anchor.after
        && anchor.before === ctx.before && anchor.after === ctx.after;
      // Only a possible match needs a score; most elements are not one.
      const claimed = span || sameObject || bracketed;
      const text = counts ? dice(counts[i], wanted.text.size, index.sizes[i]) : 0;
      if (!claimed && text < 0.5) return null;
      const words = wordSetsOf(ctx);
      const before = anchor.before ? similarity(wanted.before, words.before) : 0;
      const after = anchor.after ? similarity(wanted.after, words.after) : 0;
      const neighbours = before + after;
      const score = (span ? 2 : 0) + (sameObject ? 12 : 0) + text * 2 + neighbours * 3 + (sameSection ? 0.3 : 0);
      // Rewritten prose needs textual evidence plus unchanged neighbours.
      const eligible = span || sameObject || (text >= 0.5 && neighbours >= 0.9);
      return { el, span, score, eligible, bracketed };
    }).filter(Boolean);
    const uniquelyBracketed = candidates.filter((x) => x.bracketed).length === 1;
    const matches = candidates.filter((x) => x.eligible || (uniquelyBracketed && x.bracketed))
      .sort((x, y) => y.score - x.score);
    if (!matches.length) return null;
    const best = matches[0];
    if (matches[1] && best.score - matches[1].score < 0.35) return null;
    best.range = best.span ? rangeIn(best.el, best.span) : null;
    // The paragraph was rewritten: report where, but give no range (it would
    // cover the whole paragraph, and any comment made there later).
    if (!best.range) best.changed = true;
    best.moved = !!c.section && !(home && home.contains(best.el));
    return best;
  }

  // Every redraw (a card click, a view switch, each reload of the comments)
  // locates every comment against every element. A document's text never
  // changes under its analysis, so a place is kept per analysis and comment
  // until a field locateIn reads changes.
  const placeCache = new WeakMap();  // analysis -> Map(comment id -> {fields, hit})
  function locateCached(a, c) {
    let places = placeCache.get(a);
    if (!places) placeCache.set(a, places = new Map());
    const seen = places.get(c.id), fields = fieldsOf(c);
    if (seen && seen.fields === fields) return seen.hit;
    const store = storedPlaces(a);
    const stored = store && store.places.get(c.id);
    let hit = stored && stored[0] === fields ? unpack(a, stored[1]) : undefined;
    if (hit === undefined) {
      hit = locateIn(a, c);
      if (store) keep(a, store, c.id, fields, hit);
    }
    places.set(c.id, { fields, hit });
    return hit;
  }

  // Each page load would locate every comment again. So places also go to
  // sessionStorage, under the stamp the server gives each page
  // (server.page_stamp: the served page and these scripts), with a digest of
  // the fields locateIn reads for each comment: a change to the page, the
  // viewer or a comment misses, and a reload with nothing changed skips the pass.
  const storeCache = new WeakMap();  // analysis -> {key, stamp, places: Map(id -> [fields, packed hit])} or null
  const stampOf = (a) => a.root.ownerDocument.querySelector('meta[name="trazo-page"]')?.content || "";
  function storedPlaces(a) {
    if (storeCache.has(a)) return storeCache.get(a);
    const here = stampOf(model), there = stampOf(a);
    let store = null;
    if (here && there) {
      store = { key: `preview-places:${doc}:${a.doc}`, stamp: `${here}:${there}`, places: new Map(), timer: 0 };
      try {
        const saved = JSON.parse(sget(store.key));
        if (saved && saved.stamp === store.stamp) store.places = new Map(saved.places);
      } catch (e) { /* start afresh */ }
    }
    storeCache.set(a, store);
    return store;
  }
  // cyrb53: a 53-bit string hash, so a changed comment matches its stored
  // fields by chance about once in 10^16 changes.
  const hash53 = (text) => {
    let h1 = 0xdeadbeef, h2 = 0x41c6ce57;
    for (let i = 0; i < text.length; i++) {
      const ch = text.charCodeAt(i);
      h1 = Math.imul(h1 ^ ch, 2654435761);
      h2 = Math.imul(h2 ^ ch, 1597334677);
    }
    h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
    h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
    return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(36);
  };
  // The fields locateIn reads, once per comment object (a reload of the
  // comments brings new objects).
  const fieldsCache = new WeakMap();
  const fieldsOf = (c) => {
    let fields = fieldsCache.get(c);
    if (fields === undefined) fieldsCache.set(c, fields = hash53(JSON.stringify([c.quote, c.section, c.para, c.anchor])));
    return fields;
  };
  // A hit as stored: the element by its index and label, the quote by its flat-text span.
  function keep(a, store, id, fields, hit) {
    store.places.set(id, [fields, hit && { i: a.labelled.indexOf(hit.el), label: hit.el.dataset.label,
      span: hit.span, moved: hit.moved }]);
    if (!store.timer) store.timer = setTimeout(() => {
      store.timer = 0;
      sset(store.key, JSON.stringify({ stamp: store.stamp, places: [...store.places] }));
    });
  }
  // The stored hit back on this page; undefined if it does not fit (locate afresh).
  function unpack(a, packed) {
    if (packed === null) return null;
    const el = packed && a.labelled[packed.i];
    if (!el || el.dataset.label !== packed.label) return undefined;
    const { span } = packed;
    if (span && !(span[0] >= 0 && span[0] < span[1] && span[1] <= flatText(el).map.length)) return undefined;
    // As in locateIn: without a span the target was rewritten.
    return { el, range: span ? rangeIn(el, span) : null, span, changed: span ? undefined : true, moved: packed.moved };
  }
