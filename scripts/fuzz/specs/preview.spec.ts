import { always, extract, actions, weighted } from "@antithesishq/bombadil";
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

const previewState = extract((state: State) => {
  const ready = state.document.readyState === "complete";
  const isWriting = !!state.document.querySelector(".pv-writing");
  const strayEditable = state.document.querySelectorAll('[contenteditable="plaintext-only"]').length;

  const writableParagraphs = Array.from(state.document.querySelectorAll(".pv-page p.pv-can-write"))
    .slice(0, 8)
    .map((el) => {
      const r = el.getBoundingClientRect();
      return {
        fingerprint: getFingerprint(el),
        left: r.left,
        right: r.right,
        x: r.left + Math.min(r.width * 0.35, 140),
        y: r.top + Math.min(r.height * 0.5, 24),
        w: r.width,
        h: r.height,
      };
    })
    .filter((r) => r.w > 40 && r.h > 12 && r.y > 70 && r.y < 680);

  const blurTargets = Array.from(state.document.querySelectorAll(".pv-top, .pv-page h1"))
    .slice(0, 2)
    .map((el) => {
      const r = el.getBoundingClientRect();
      return {
        fingerprint: getFingerprint(el),
        x: r.left + Math.min(r.width * 0.5, 120),
        y: r.top + r.height / 2,
        w: r.width,
        h: r.height,
      };
    })
    .filter((r) => r.w > 10 && r.h > 10 && r.y >= 0 && r.y < 700);

  return { ready, isWriting, strayEditable, writableParagraphs, blurTargets };
});

export const noStrayContentEditable = always(
  () =>
    !previewState.current.ready ||
    previewState.current.isWriting ||
    previewState.current.strayEditable === 0,
);

const clickParagraphToWrite = actions<ActionTemplate>(() =>
  previewState.current.writableParagraphs.map((p) => ({
    Click: {
      fingerprint: p.fingerprint,
      point: { x: p.x, y: p.y },
    },
  })),
);

const dragSelectText = actions<ActionTemplate>(() =>
  previewState.current.writableParagraphs.slice(0, 4).map((p) => ({
    MouseDrag: {
      from: { x: p.left + 12, y: p.y },
      to: { x: Math.min(p.right - 12, p.left + 140), y: p.y },
      steps: 4,
      delayMillis: 15,
    },
  })),
);

const editWhileWriting = actions<ActionTemplate>(() => {
  if (!previewState.current.isWriting) return [];
  const blurClicks: ActionTemplate[] = previewState.current.blurTargets.map((t) => ({
    Click: {
      fingerprint: t.fingerprint,
      point: { x: t.x, y: t.y },
    },
  }));
  return [
    { TypeText: { text: { Regexp: "[a-zA-Z0-9 .,]{1,12}" } } },
    { PressKey: { code: 8 } },
    { PressKey: { code: 13 } },
    { PressKey: { code: 27 } },
    ...blurClicks,
  ];
});

export const explore = weighted<ActionTemplate>([
  [5, defaultActions.clicks],
  [2, defaultActions.inputs],
  [1, defaultActions.scroll],
  [1, defaultActions.navigation],
  [3, clickParagraphToWrite],
  [4, editWhileWriting],
  [2, dragSelectText],
]);
