/* Icons for the diagram editor UI (not used in figures). 20 x 20 grid,
 * drawn with currentColor strokes so they follow the theme. */
(function () {
  "use strict";

  const P = {
    select: '<path d="M5 3.5l10 4.6-4.3 1.4-1.6 4.4z" fill="currentColor" stroke-linejoin="round"/><path d="M10.6 10l3.6 3.6"/>',
    box: '<rect x="3.5" y="5" width="13" height="10" rx="2"/>',
    pill: '<rect x="2.5" y="6.5" width="15" height="7" rx="3.5"/>',
    text: '<path d="M5 5.5h10M10 5.5v10M8 15.5h4"/>',
    panel: '<rect x="3" y="3.5" width="14" height="13" rx="1.5"/><path d="M3 7h14"/><path d="M5.5 5.2h2" stroke-width="1.4"/>',
    image: '<rect x="3" y="4" width="14" height="12" rx="1.5"/><circle cx="7.5" cy="8" r="1.3"/><path d="M3.5 14.5l4.2-3.8 3 2.6 2.2-1.8 3.6 3"/>',
    edge: '<path d="M3.5 15.5h6v-11h5.5"/><path d="M13 2.5l2.5 2-2.5 2"/>',
    comment: '<path d="M4 4.5h12a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H9l-3.5 3v-3H4a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1z"/>',
    undo: '<path d="M7.5 5L4 8.5 7.5 12"/><path d="M4.5 8.5H12a4 4 0 0 1 0 8H9"/>',
    redo: '<path d="M12.5 5L16 8.5 12.5 12"/><path d="M15.5 8.5H8a4 4 0 0 0 0 8h3"/>',
    grid: '<path d="M3.5 7.5h13M3.5 12.5h13M7.5 3.5v13M12.5 3.5v13"/>',
    guides: '<path d="M3.5 3.5h13v13h-13z" stroke-dasharray="2 2"/><path d="M10 1.5v17" />',
    fit: '<path d="M3.5 7.5v-4h4M16.5 7.5v-4h-4M3.5 12.5v4h4M16.5 12.5v4h-4"/>',
    target: '<rect x="6" y="6" width="8" height="8" rx="1"/><path d="M10 2.5v2M10 15.5v2M2.5 10h2M15.5 10h2"/>',
    ruler: '<rect x="2.5" y="6.5" width="15" height="7" rx="1"/><path d="M5.5 6.5v2.5M8.5 6.5v3.5M11.5 6.5v2.5M14.5 6.5v3.5"/>',
    plus: '<path d="M10 4.5v11M4.5 10h11"/>',
    minus: '<path d="M4.5 10h11"/>',
    export: '<path d="M10 3v9.5M6.5 9L10 12.5 13.5 9"/><path d="M4 13.5v2.5h12v-2.5"/>',
    help: '<circle cx="10" cy="10" r="7"/><path d="M8 8a2 2 0 1 1 2.8 1.8c-.6.3-.8.7-.8 1.3v.4"/><circle cx="10" cy="13.9" r=".6" fill="currentColor"/>',
    eye: '<path d="M2.5 10s2.8-5 7.5-5 7.5 5 7.5 5-2.8 5-7.5 5-7.5-5-7.5-5z"/><circle cx="10" cy="10" r="2.2"/>',
    eyeOff: '<path d="M2.5 10s2.8-5 7.5-5 7.5 5 7.5 5-2.8 5-7.5 5-7.5-5-7.5-5z" opacity=".45"/><path d="M4 16L16 4"/>',
    lock: '<rect x="4.5" y="9" width="11" height="8" rx="1.5"/><path d="M7 9V6.5a3 3 0 0 1 6 0V9"/>',
    unlock: '<rect x="4.5" y="9" width="11" height="8" rx="1.5"/><path d="M7 9V6.5a3 3 0 0 1 5.8-1"/>',
    chevron: '<path d="M7.5 5l5 5-5 5"/>',
    chevronDown: '<path d="M5 7.5l5 5 5-5"/>',
    close: '<path d="M5.5 5.5l9 9M14.5 5.5l-9 9"/>',
    check: '<path d="M4.5 10.5l3.5 3.5 7.5-8"/>',
    warning: '<path d="M10 3.5l7 12.5H3z" stroke-linejoin="round"/><path d="M10 8.5v3.5"/><circle cx="10" cy="14.2" r=".6" fill="currentColor"/>',
    error: '<circle cx="10" cy="10" r="7"/><path d="M10 6.5v4.5"/><circle cx="10" cy="13.6" r=".6" fill="currentColor"/>',
    info: '<circle cx="10" cy="10" r="7"/><path d="M10 9v4.5"/><circle cx="10" cy="6.6" r=".6" fill="currentColor"/>',
    trash: '<path d="M4 6h12M8 6V4.5h4V6M5.5 6l.8 10h7.4l.8-10"/>',
    group: '<rect x="3" y="3" width="14" height="14" rx="1.5" stroke-dasharray="2 2"/><rect x="6" y="6" width="5" height="4" rx=".8"/><rect x="9" y="11" width="5" height="3.5" rx=".8"/>',
    ungroup: '<rect x="3.5" y="4" width="6" height="5" rx=".8"/><rect x="10.5" y="11" width="6" height="5" rx=".8"/>',
    front: '<rect x="7" y="7" width="9.5" height="9.5" rx="1" fill="currentColor" fill-opacity=".18"/><path d="M3.5 12.5v-9h9"/>',
    back: '<rect x="3.5" y="3.5" width="9.5" height="9.5" rx="1" fill="currentColor" fill-opacity=".18"/><path d="M16.5 7.5v9h-9"/>',
    copy: '<rect x="7" y="7" width="9.5" height="9.5" rx="1.5"/><path d="M13 7V5a1.5 1.5 0 0 0-1.5-1.5h-6.5A1.5 1.5 0 0 0 3.5 5v6.5A1.5 1.5 0 0 0 5 13h2"/>',
    paste: '<rect x="4.5" y="4" width="11" height="13" rx="1.5"/><path d="M7.5 4V3h5v1"/>',
    duplicate: '<rect x="3.5" y="3.5" width="9" height="9" rx="1.5"/><rect x="7.5" y="7.5" width="9" height="9" rx="1.5"/>',
    brush: '<path d="M12.5 3.5l4 4-6.5 6.5-4-4z"/><path d="M6 10.5c-2 0-2.5 2-2.5 3.5 0 1-.5 2-1 2.5 3 0 6-1 6-4"/>',
    layers: '<path d="M10 3l7 3.5-7 3.5-7-3.5z"/><path d="M3 10l7 3.5 7-3.5"/><path d="M3 13.5L10 17l7-3.5" opacity=".6"/>',
    sliders: '<path d="M4 5.5h12M4 10h12M4 14.5h12"/><circle cx="7.5" cy="5.5" r="1.6" fill="var(--ic-fill, #fff)"/><circle cx="12.5" cy="10" r="1.6" fill="var(--ic-fill, #fff)"/><circle cx="8.5" cy="14.5" r="1.6" fill="var(--ic-fill, #fff)"/>',
    alignL: '<path d="M4 3v14"/><rect x="6" y="5.5" width="9" height="3" rx=".8"/><rect x="6" y="11.5" width="6" height="3" rx=".8"/>',
    alignC: '<path d="M10 3v14"/><rect x="4.5" y="5.5" width="11" height="3" rx=".8"/><rect x="6.5" y="11.5" width="7" height="3" rx=".8"/>',
    alignR: '<path d="M16 3v14"/><rect x="5" y="5.5" width="9" height="3" rx=".8"/><rect x="8" y="11.5" width="6" height="3" rx=".8"/>',
    alignT: '<path d="M3 4h14"/><rect x="5.5" y="6" width="3" height="9" rx=".8"/><rect x="11.5" y="6" width="3" height="6" rx=".8"/>',
    alignM: '<path d="M3 10h14"/><rect x="5.5" y="4.5" width="3" height="11" rx=".8"/><rect x="11.5" y="6.5" width="3" height="7" rx=".8"/>',
    alignB: '<path d="M3 16h14"/><rect x="5.5" y="5" width="3" height="9" rx=".8"/><rect x="11.5" y="8" width="3" height="6" rx=".8"/>',
    distH: '<path d="M3.5 3v14M16.5 3v14"/><rect x="8" y="6" width="4" height="8" rx=".8"/>',
    distV: '<path d="M3 3.5h14M3 16.5h14"/><rect x="6" y="8" width="8" height="4" rx=".8"/>',
    orthogonal: '<path d="M3.5 15.5h6.5v-11h6.5"/>',
    straight: '<path d="M3.5 16.5l13-13"/>',
    file: '<path d="M5 2.5h6.5L15.5 6.5V17a.5.5 0 0 1-.5.5H5a.5.5 0 0 1-.5-.5V3a.5.5 0 0 1 .5-.5z"/><path d="M11.5 2.5v4h4"/>',
    sparkle: '<path d="M10 2.5l1.6 4.4 4.4 1.6-4.4 1.6L10 14.5l-1.6-4.4L4 8.5l4.4-1.6z"/>',
    dot: '<circle cx="10" cy="10" r="3" fill="currentColor"/>',
    keyboard: '<rect x="2.5" y="5" width="15" height="10" rx="1.5"/><path d="M5.5 8h1M8.5 8h1M11.5 8h1M14 8h.5M6 12h8"/>',
    reply: '<path d="M8 5L3.5 9.5 8 14"/><path d="M4 9.5h7a5 5 0 0 1 5 5v1"/>',
    refresh: '<path d="M15.5 8A6 6 0 0 0 4.6 6.5M4.5 12a6 6 0 0 0 10.9 1.5"/><path d="M4.5 3.5v3h3M15.5 16.5v-3h-3"/>',
    edit: '<path d="M13 3.5l3.5 3.5L7 16.5H3.5V13z"/>',
    logo: '<rect x="2.5" y="2.5" width="6.5" height="6.5" rx="1.6" fill="currentColor"/><rect x="11" y="11" width="6.5" height="6.5" rx="1.6" fill="currentColor" fill-opacity=".35"/><path d="M5.75 9v4.25H11"/>',
  };

  function icon(name, size = 18) {
    const body = P[name] || P.dot;
    return `<svg class="ic" width="${size}" height="${size}" viewBox="0 0 20 20" fill="none" stroke="currentColor" `
      + `stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
  }

  function iconNode(name, size) {
    const t = document.createElement("template");
    t.innerHTML = icon(name, size);
    return t.content.firstChild;
  }

  window.Icons = { html: icon, node: iconNode, names: Object.keys(P) };

  /* Fill <i data-icon="name"> placeholders in the static page. */
  function hydrate(root = document) {
    for (const el of root.querySelectorAll("i[data-icon]")) {
      el.replaceWith(iconNode(el.dataset.icon, Number(el.dataset.size) || 18));
    }
  }
  window.Icons.hydrate = hydrate;
  hydrate();
})();
