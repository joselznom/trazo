import { always, eventually, extract, actions, weighted } from "@antithesishq/bombadil";
import * as defaultActions from "@antithesishq/bombadil/browser/defaults/actions";
import { getFingerprint, type ActionTemplate, type State } from "@antithesishq/bombadil/browser";

export {
  noHttpErrorCodes,
  noUncaughtExceptions,
  noUnhandledPromiseRejections,
  noConsoleErrors,
  pageNeverBlank,
  shellOrEditorPresent,
} from "./shared.ts";

const diagramState = extract((state: State) => {
  const ready = state.document.readyState === "complete";
  const onDiagramPage = state.window.location.pathname === "/diagrams";
  const isLanding = state.document.body?.classList.contains("is-landing") ?? false;
  const hasOverlay = !!state.document.querySelector("#overlay");
  const hasPaperSvg = !!state.document.querySelector("#paper svg");

  const viewportEl = state.document.querySelector("#viewport");
  let viewport: { x: number; y: number; w: number; h: number } | null = null;
  if (viewportEl) {
    const r = viewportEl.getBoundingClientRect();
    if (r.width > 80 && r.height > 80) {
      viewport = { x: r.left, y: r.top, w: r.width, h: r.height };
    }
  }

  const tools = Array.from(state.document.querySelectorAll("#tools button[data-tool]"))
    .map((el) => {
      const r = el.getBoundingClientRect();
      return {
        fingerprint: getFingerprint(el),
        x: r.left + r.width / 2,
        y: r.top + r.height / 2,
        w: r.width,
        h: r.height,
      };
    })
    .filter((r) => r.w > 0 && r.h > 0);

  const historyButtons = Array.from(
    state.document.querySelectorAll('button[data-action="undo"], button[data-action="redo"]'),
  )
    .map((el) => {
      const r = el.getBoundingClientRect();
      return {
        fingerprint: getFingerprint(el),
        x: r.left + r.width / 2,
        y: r.top + r.height / 2,
        w: r.width,
        h: r.height,
      };
    })
    .filter((r) => r.w > 0 && r.h > 0);

  return { ready, onDiagramPage, isLanding, hasOverlay, hasPaperSvg, viewport, tools, historyButtons };
});

export const svgCanvasPresent = always(() => {
  const s = diagramState.current;
  if (!s.ready || !s.onDiagramPage || s.isLanding) return true;
  if (s.hasOverlay && s.hasPaperSvg) return true;
  return eventually(
    () =>
      !diagramState.current.onDiagramPage ||
      diagramState.current.isLanding ||
      (diagramState.current.hasOverlay && diagramState.current.hasPaperSvg),
  ).within(5, "seconds");
});

const switchTool = actions<ActionTemplate>(() =>
  diagramState.current.tools.map((t) => ({
    Click: {
      fingerprint: t.fingerprint,
      point: { x: t.x, y: t.y },
    },
  })),
);

const historyActions = actions<ActionTemplate>(() =>
  diagramState.current.historyButtons.map((b) => ({
    Click: {
      fingerprint: b.fingerprint,
      point: { x: b.x, y: b.y },
    },
  })),
);

const canvasDrag = actions<ActionTemplate>(() => {
  const vp = diagramState.current.viewport;
  if (!vp || diagramState.current.isLanding) return [];
  const x1 = vp.x + vp.w * 0.3;
  const y1 = vp.y + vp.h * 0.35;
  const x2 = vp.x + vp.w * 0.6;
  const y2 = vp.y + vp.h * 0.55;
  return [
    {
      MouseDrag: {
        from: { x: x1, y: y1 },
        to: { x: x2, y: y2 },
        steps: 5,
        delayMillis: 15,
      },
    },
    {
      MouseDrag: {
        from: { x: x2, y: y1 },
        to: { x: x1, y: y2 },
        steps: 5,
        delayMillis: 15,
      },
    },
  ];
});

export const explore = weighted<ActionTemplate>([
  [5, defaultActions.clicks],
  [2, defaultActions.inputs],
  [1, defaultActions.scroll],
  [1, defaultActions.navigation],
  [3, switchTool],
  [3, canvasDrag],
  [2, historyActions],
]);
