import { always } from "@antithesishq/bombadil";
import * as defaultActions from "@antithesishq/bombadil/browser/defaults/actions";
import { actions, extract, getFingerprint, weighted } from "@antithesishq/bombadil/browser";
import { clickTarget } from "./shared.ts";

export {
  noHttpErrorCodes,
  noUncaughtExceptions,
  noUnhandledPromiseRejections,
  noConsoleErrors,
  pageNeverBlank,
  shellOrEditorPresent,
} from "./shared.ts";

const previewState = extract((state) => {
  const ready = state.document.readyState === "complete";
  const isWriting = !!state.document.querySelector(".pv-writing");
  const strayEditable = state.document.querySelectorAll('[contenteditable="plaintext-only"]').length;

  const writableParagraphs = Array.from(state.document.querySelectorAll(".pv-page p.pv-can-write"))
    .slice(0, 8)
    .flatMap((el) => {
      const r = el.getBoundingClientRect();
      const y = r.top + Math.min(r.height * 0.5, 24);
      if (!(r.width > 40 && r.height > 12 && y > 70 && y < 680)) return [];
      return [
        {
          fingerprint: getFingerprint(el),
          left: r.left,
          right: r.right,
          x: r.left + Math.min(r.width * 0.35, 140),
          y,
        },
      ];
    });

  const blurTargets = Array.from(state.document.querySelectorAll(".pv-top, .pv-page h1"))
    .slice(0, 2)
    .flatMap((el) => {
      const r = el.getBoundingClientRect();
      const y = r.top + r.height / 2;
      if (!(r.width > 10 && r.height > 10 && y >= 0 && y < 700)) return [];
      return [
        {
          fingerprint: getFingerprint(el),
          x: r.left + Math.min(r.width * 0.5, 120),
          y,
        },
      ];
    });

  return { ready, isWriting, strayEditable, writableParagraphs, blurTargets };
});

export const noStrayContentEditable = always(
  () =>
    !previewState.current.ready ||
    previewState.current.isWriting ||
    previewState.current.strayEditable === 0,
);

const clickParagraphToWrite = actions(() =>
  previewState.current.writableParagraphs.map(clickTarget),
);

const dragSelectText = actions(() =>
  previewState.current.writableParagraphs.slice(0, 4).map((p) => ({
    MouseDrag: {
      from: { x: p.left + 12, y: p.y },
      to: { x: Math.min(p.right - 12, p.left + 140), y: p.y },
      steps: 4,
      delayMillis: 15,
    },
  })),
);

const editWhileWriting = actions(() => {
  if (!previewState.current.isWriting) return [];
  return [
    { TypeText: { text: { Regexp: "[a-zA-Z0-9 .,]{1,12}" } } },
    { PressKey: { code: 8 } },
    { PressKey: { code: 13 } },
    { PressKey: { code: 27 } },
    ...previewState.current.blurTargets.map(clickTarget),
  ];
});

export const explore = weighted([
  [5, defaultActions.clicks],
  [2, defaultActions.inputs],
  [1, defaultActions.scroll],
  [1, defaultActions.navigation],
  [3, clickParagraphToWrite],
  [4, editWhileWriting],
  [2, dragSelectText],
]);
