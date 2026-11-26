import { always, eventually } from "@antithesishq/bombadil";
import * as defaultActions from "@antithesishq/bombadil/browser/defaults/actions";
import { actions, extract, weighted } from "@antithesishq/bombadil/browser";
import { clickTarget, queryTargets } from "./shared.ts";

export {
  noHttpErrorCodes,
  noUncaughtExceptions,
  noUnhandledPromiseRejections,
  noConsoleErrors,
  pageNeverBlank,
  shellOrEditorPresent,
} from "./shared.ts";

const diagramState = extract((state) => {
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

  const tools = queryTargets(state.document, "#tools button[data-tool]");
  const historyButtons = queryTargets(
    state.document,
    'button[data-action="undo"], button[data-action="redo"]',
  );

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

const switchTool = actions(() => diagramState.current.tools.map(clickTarget));

const historyActions = actions(() => diagramState.current.historyButtons.map(clickTarget));

const canvasDrag = actions(() => {
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

export const explore = weighted([
  [5, defaultActions.clicks],
  [2, defaultActions.inputs],
  [1, defaultActions.scroll],
  [1, defaultActions.navigation],
  [3, switchTool],
  [3, canvasDrag],
  [2, historyActions],
]);
