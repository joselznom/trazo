// Preview viewer: the shell (top bar, sidebars, views), the viewer's state, toast and tooltip.
// The parts of the viewer are classic scripts loaded in the order of
// trazo.preview.VIEWER_SCRIPTS; their top-level names are shared globals.
  if (!window.PreviewModel) throw new Error("preview-model.js did not load");  // see preview-page.js
  // ---------------------------------------------------------------- shell
  const ICON = (d, extra) => `<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="${extra || 1.4}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
  const ICONS = {
    left: ICON('<rect x="2" y="3" width="12" height="10" rx="1.6"/><path d="M6 3v10"/>'),
    right: ICON('<rect x="2" y="3" width="12" height="10" rx="1.6"/><path d="M10 3v10"/>'),
    theme: ICON('<circle cx="8" cy="8" r="5.2"/><path d="M8 2.8a5.2 5.2 0 0 1 0 10.4z" fill="currentColor"/>'),
    up: ICON('<path d="M4 10l4-4 4 4"/>', 1.6),
    down: ICON('<path d="M4 6l4 4 4-4"/>', 1.6),
    prev: ICON('<path d="M10 3L5 8l5 5"/>', 1.6),
    next: ICON('<path d="M6 3l5 5-5 5"/>', 1.6),
    close: ICON('<path d="M4 4l8 8M12 4l-8 8"/>', 1.6),
    download: ICON('<path d="M8 2.5v8M4.5 7.5 8 11l3.5-3.5M3 13.5h10"/>', 1.6),
    file: ICON('<path d="M4 2h5l3 3v9H4z"/><path d="M9 2v3h3"/>'),
    lines: ICON('<path d="M3 4h10M3 8h6M3 12h8"/>', 1.6),
    pick: ICON('<rect x="2.5" y="2.5" width="4" height="4" rx="1"/><rect x="2.5" y="9.5" width="4" height="4" rx="1"/><path d="M9 4.5h4.5M9 11.5h4.5"/>'),
    trash: ICON('<path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.5h5.8l.6-8.5"/>'),
    comment: ICON('<path d="M3 3.5h10v7H7l-3 2.5v-2.5H3z"/>'),
    underline: ICON('<path d="M5 2.5v5a3 3 0 0 0 6 0v-5M4 13.5h8"/>'),
  };
  const MOD = /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘" : "Ctrl+";
  const app = h("div", { class: "pv-app-root" });
  app.innerHTML =
    `<header class="pv-topbar">` +
    `<button type="button" class="pv-icon-btn" data-side="left" aria-label="Toggle index sidebar" aria-pressed="true" title="Toggle index sidebar ( [ )">${ICONS.left}</button>` +
    `<div class="pv-brand" role="img" aria-label="trazo">${WORDMARK}</div>` +
    `<nav class="pv-tabs-top" aria-label="Views"></nav>` +
    `<div class="pv-spacer"></div>` +
    `<span class="pv-hold" hidden>reload paused: unsaved changes</span>` +
    `<span class="pv-words" aria-live="polite"></span>` +
    `<button type="button" class="pv-build" aria-label="Build status"><span class="pv-dot off"></span><span class="pv-bs-label"></span></button>` +
    `<div class="pv-export">` +
    `<button type="button" class="pv-btn primary pv-export-toggle" aria-haspopup="menu" aria-expanded="false" aria-controls="pv-export-menu">${ICONS.download}<span>Export</span>${ICONS.down}</button>` +
    `<div class="pv-export-menu" id="pv-export-menu" role="menu" aria-label="Export" hidden>` +
    `<button type="button" class="pv-mi pv-docx" role="menuitem" data-act="build">` +
    `<span class="pv-mi-ic">${ICONS.file}</span><span class="pv-mi-t"></span><kbd>${MOD}B</kbd><span class="pv-mi-s"></span></button>` +
    `<button type="button" class="pv-mi" role="menuitem" data-act="current">` +
    `<span class="pv-mi-ic">${ICONS.lines}</span><span class="pv-mi-t">Current section</span><span></span><span class="pv-mi-s"></span></button>` +
    `<button type="button" class="pv-mi" role="menuitem" data-act="choose" aria-haspopup="dialog" aria-controls="pv-export-panel">` +
    `<span class="pv-mi-ic">${ICONS.pick}</span><span class="pv-mi-t">Choose sections…</span><kbd>${MOD}E</kbd><span class="pv-mi-s">Any combination, as one DOCX</span></button>` +
    `<div class="pv-mi-foot" hidden></div></div>` +
    `<div class="pv-export-panel" id="pv-export-panel" role="dialog" aria-labelledby="pv-export-title" hidden></div></div>` +
    `<div class="pv-themebox"><button type="button" class="pv-icon-btn pv-theme" aria-haspopup="menu" aria-expanded="false" aria-controls="pv-theme-menu">${ICONS.theme}</button>` +
    `<div class="pv-theme-menu" id="pv-theme-menu" role="menu" aria-label="Theme" hidden></div></div>` +
    `<button type="button" class="pv-icon-btn" data-side="right" aria-label="Toggle review sidebar" aria-pressed="true" title="Toggle review sidebar ( ] )">${ICONS.right}</button>` +
    `</header>` +
    `<div class="pv-banners"></div>` +
    `<div class="pv-cols">` +
    `<aside class="pv-side left" aria-label="Index and filters">` +
    `<div class="pv-side-head"><div class="pv-side-title"></div><div class="pv-side-sub"></div></div>` +
    `<div class="pv-side-scroll"></div>` +
    `<div class="pv-resizer" role="separator" aria-orientation="vertical" aria-label="Resize index sidebar" tabindex="0"></div></aside>` +
    `<main class="pv-center"><div class="pv-viewbar" role="group" aria-label="Document display">` +
    `<details class="pv-type"><summary aria-label="Text size" title="Text size">Aa</summary>` +
    `<div class="pv-type-menu" role="group" aria-label="Text size">` +
    `<span>Text size</span><div class="pv-type-controls">` +
    `<button type="button" class="pv-icon-btn pv-font-down" aria-label="Decrease text size" title="Decrease text size">A−</button>` +
    `<output class="pv-font-value" aria-live="polite">14 px</output>` +
    `<button type="button" class="pv-icon-btn pv-font-up" aria-label="Increase text size" title="Increase text size">A+</button></div>` +
    `<button type="button" class="pv-btn sm ghost pv-font-reset">Reset to 14 px</button>` +
    `</div></details>` +
    `<button type="button" class="pv-icon-btn pv-zoom-out" aria-label="Zoom out" title="Zoom out">−</button>` +
    `<button type="button" class="pv-zoom-reset" title="Reset zoom to 100%" aria-live="polite">100%</button>` +
    `<button type="button" class="pv-icon-btn pv-zoom-in" aria-label="Zoom in" title="Zoom in">+</button>` +
    `<button type="button" class="pv-btn sm ghost pv-zoom-fit" aria-pressed="false">Fit width</button>` +
    `</div><div class="pv-scroller"></div><div class="pv-scrim" hidden></div></main>` +
    `<aside class="pv-side right" aria-label="Review panel">` +
    `<div class="pv-resizer" role="separator" aria-orientation="vertical" aria-label="Resize review sidebar" tabindex="0"></div>` +
    `<div class="pv-rtabs" role="tablist" aria-label="Review panel">` +
    `<button type="button" role="tab" data-tab="comments" aria-selected="true">Comments <span class="pv-badge" hidden></span></button>` +
    `<button type="button" role="tab" data-tab="source" aria-selected="false" tabindex="-1">Source <span class="dirty" hidden aria-label="unsaved changes">●</span></button>` +
    `<button type="button" role="tab" data-tab="diff" aria-selected="false" tabindex="-1">Diff</button></div>` +
    `<section class="pv-pane" data-pane="comments">` +
    `<div class="pv-csum"><div class="pv-cfilter" role="group" aria-label="Show comments">` +
    `<button type="button" data-filter="active" aria-pressed="true" title="Open and to check">Active <span class="n"></span></button>` +
    `<button type="button" data-filter="resolved" aria-pressed="false">Resolved <span class="n"></span></button></div><div class="pv-stepper">` +
    `<button type="button" class="pv-icon-btn" data-step="-1" aria-label="Previous open comment" title="Previous open comment (k or ⌥↑)">${ICONS.up}</button>` +
    `<button type="button" class="pv-icon-btn" data-step="1" aria-label="Next open comment" title="Next open comment (j or ⌥↓)">${ICONS.down}</button></div></div>` +
    `<div class="pv-csum-text" aria-live="polite"></div><div class="pv-pane-scroll"></div></section>` +
    `<section class="pv-pane pv-source" data-pane="source" hidden></section>` +
    `<section class="pv-pane pv-diffbox" data-pane="diff" hidden></section>` +
    `</aside></div>`;
  document.body.insertBefore(app, document.body.firstChild);

  const bar = $(".pv-topbar", app);
  const tabsTop = $(".pv-tabs-top", app);
  const wordsEl = $(".pv-words", app);
  const buildBtn = $(".pv-build", app);
  const docxBtn = $(".pv-docx", app);
  const themeBtn = $(".pv-theme", app);
  const hold = $(".pv-hold", app);
  const banners = $(".pv-banners", app);
  const left = $(".pv-side.left", app), right = $(".pv-side.right", app);
  const leftTitle = $(".pv-side-title", left), leftSub = $(".pv-side-sub", left), leftBody = $(".pv-side-scroll", left);
  const scroller = $(".pv-scroller", app);
  const zoomBar = $(".pv-viewbar", app);
  const zoomOut = $(".pv-zoom-out", zoomBar), zoomIn = $(".pv-zoom-in", zoomBar);
  const zoomReset = $(".pv-zoom-reset", zoomBar), zoomFit = $(".pv-zoom-fit", zoomBar);
  const typeMenu = $(".pv-type", zoomBar);
  const fontDown = $(".pv-font-down", typeMenu), fontUp = $(".pv-font-up", typeMenu);
  const fontValue = $(".pv-font-value", typeMenu), fontReset = $(".pv-font-reset", typeMenu);
  const scrim = $(".pv-scrim", app);
  const rtabs = $(".pv-rtabs", right);
  const panel = $('[data-pane="comments"] .pv-pane-scroll', right);
  const summaryEl = $(".pv-csum-text", right);
  const srcBox = $('[data-pane="source"]', right);
  const diffBox = $('[data-pane="diff"]', right);
  const paneEls = $$("[data-pane]", right);

  const docView = h("article", { class: "pv-doc-wrap pv-docview" }, page);
  const figsView = h("section", { class: "pv-figs pv-figsview", "aria-label": "Figures & Tables", hidden: true });
  const submissionView = h("section", { class: "pv-subm", "aria-label": "Submission", hidden: true });
  scroller.append(docView, figsView, submissionView);
  const banner = h("div", { class: "pv-banner" });
  const docxBox = h("div", { class: "pv-banner" });
  banners.append(banner, docxBox);

  // Short names and colours (by index) tell the documents apart in the
  // Figures & Tables view: "Manuscript" is Main, "Supplementary" Supp.
  const KNOWN_CHIPS = { manuscript: "Main", supplementary: "Supp." };
  const chips = {};
  docNames.forEach((name, i) => {
    const title = String(DOCS[name] || name).trim();
    const first = title.split(/\s+/)[0] || name;
    let text = KNOWN_CHIPS[first.toLowerCase()]
      || (/\s/.test(title) ? title.split(/\s+/).map((w) => w[0]).join("").toUpperCase().slice(0, 4)
        : first.length > 6 ? first.slice(0, 4) + "." : first);
    if (Object.values(chips).some((c) => c.text === text)) text += ` ${i + 1}`;
    chips[name] = { text, cls: "d" + (i % 4), index: i };
  });
  const docIndex = (name) => (chips[name] ? chips[name].index : docNames.length);
  const chipEl = (name) => h("span", { class: `pv-chip ${chips[name].cls}`, text: chips[name].text, title: DOCS[name] });

  // Articles only: a Submission menu with the checklist (?view=submission)
  // and the submission documents (declarations, title page, cover letter;
  // trazo.project.SUBMISSION_PARTS) instead of their own tabs. Its
  // button's badge counts the checklist's errors.
  const isArticle = window.TRAZO_CONFIG?.kind === "article";
  const SUBMISSION_PARTS = isArticle ? (window.TRAZO_CONFIG.submission_parts || []).filter((name) => DOCS[name]) : [];
  const docTab = (name, attrs) => h("a", { href: `/${name}.html`, "data-doc": name, text: DOCS[name], ...attrs });
  for (const name of docNames) if (!SUBMISSION_PARTS.includes(name)) tabsTop.append(docTab(name));
  const figsTab = h("a", { href: "?view=figures", "data-view": "figures", text: "Figures & Tables " }, h("span", { class: "pv-badge review", hidden: true }));
  tabsTop.append(figsTab);
  const submissionTab = isArticle ? h("button", { type: "button", class: "pv-subm-toggle", "aria-haspopup": "menu",
    "aria-expanded": "false", "aria-controls": "pv-subm-menu" }, "Submission", h("span", { class: "pv-badge", hidden: true })) : null;
  const checklistItem = isArticle
    ? h("a", { href: "?view=submission", "data-view": "submission", role: "menuitem", text: "Checklist" }) : null;
  const submissionMenu = isArticle ? h("div", { class: "pv-subm-menu", id: "pv-subm-menu", role: "menu", "aria-label": "Submission", hidden: true },
    checklistItem, SUBMISSION_PARTS.length ? h("div", { class: "pv-subm-sep", role: "separator" }) : null,
    ...SUBMISSION_PARTS.map((name) => docTab(name, { role: "menuitem" }))) : null;
  if (submissionTab) {
    submissionTab.insertAdjacentHTML("beforeend", ICONS.down);
    tabsTop.append(submissionTab, submissionMenu);
  }

  // ---------------------------------------------------------------- state
  const viewFromUrl = () => {
    const requested = new URLSearchParams(Trazo.search()).get("view");
    return requested === "figures" || (requested === "submission" && isArticle) ? requested : "doc";
  };
  let view = viewFromUrl();
  const pos = { doc: 0, figures: 0, submission: 0 };
  const scrollKey = (v) => "preview-scroll:" + Trazo.path() + (v !== "doc" ? `?view=${v}` : "");
  const mqNarrow = window.matchMedia("(max-width: 899px)");
  const S = {
    left: prefs.left !== false, right: prefs.right !== false, narrow: mqNarrow.matches, drawer: null,
    lw: clamp(+prefs.lw || 290, 200, 460), rw: clamp(+prefs.rw || 360, 280, 600),
  };
  let tab = ["source", "diff"].includes(prefs.tab) ? prefs.tab : "comments";
  const rightOpen = () => (S.narrow ? S.drawer === "right" : S.right);
  let comments = [];
  var commentsReady = false, sourceReady = false;  // set later; read by early callers
  // The last submission report (see the Submission view), kept for the tab.
  let submission = null, submissionError = "", submissionSeq = 0;
  // Fresh checks (see loadSubmission()): how many full ones are on their way
  // and when the last of any kind was asked for.
  let submissionChecking = 0, submissionAsked = Date.now();
  try { submission = JSON.parse(sget("preview-submission")); } catch (e) { submission = null; }
  const tallyOf = (items) => ({ errors: items.filter((i) => i.status === "error").length,
    warnings: items.filter((i) => i.status === "warn").length });
  const todoText = (k) => (!k.errors && !k.warnings ? "Nothing to fix"
    : [k.errors ? `${k.errors} to fix` : "", k.warnings ? `${k.warnings} to check` : ""].filter(Boolean).join(" · "));

  // ---------------------------------------------------------------- toast, tooltip
  let toastTimer = null;
  // A short notice; with `action` ({label, run}) it stays longer and offers
  // a button (Undo after a comment change). Hovering keeps it open.
  function toast(text, action) {
    let el = $(".pv-toast");
    if (!el) {
      el = h("div", { class: "pv-toast", role: "status" });
      el.addEventListener("mouseenter", () => clearTimeout(toastTimer));
      el.addEventListener("mouseleave", () => { toastTimer = setTimeout(() => el.remove(), 2400); });
      document.body.append(el);
    }
    el.textContent = text;
    if (action) {
      const b = h("button", { type: "button", text: action.label });
      b.addEventListener("click", () => { clearTimeout(toastTimer); el.remove(); action.run(); });
      el.append(b);
    }
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.remove(), action ? 6000 : 2400);
  }
  const tip = h("div", { class: "pv-tip", role: "tooltip", hidden: true });
  document.body.append(tip);
  let tipFor = null;
  function placeTip(target) {
    tip.hidden = false;
    const box = target.getBoundingClientRect(), width = tip.offsetWidth, height = tip.offsetHeight;
    tip.style.left = `${Math.max(8, Math.min(box.left, window.innerWidth - width - 8))}px`;
    tip.style.top = `${box.bottom + height + 12 > window.innerHeight ? Math.max(8, box.top - height - 8) : box.bottom + 6}px`;
  }
  const hideTip = () => { tipFor = null; tip.hidden = true; };
