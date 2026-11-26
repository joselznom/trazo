import { always } from "@antithesishq/bombadil";
import * as defaultProperties from "@antithesishq/bombadil/browser/defaults/properties";
import {
  extract,
  getFingerprint,
  type ActionTemplate,
  type Fingerprint,
} from "@antithesishq/bombadil/browser";

export interface Target {
  fingerprint: Fingerprint;
  x: number;
  y: number;
}

export function queryTargets(
  doc: Document,
  selector: string,
  opts: { limit?: number; minSize?: number; minY?: number; maxY?: number } = {},
): Target[] {
  const { limit, minSize = 0, minY = -Infinity, maxY = Infinity } = opts;
  const elements = Array.from(doc.querySelectorAll(selector));
  const slice = limit === undefined ? elements : elements.slice(0, limit);
  const targets: Target[] = [];
  for (const el of slice) {
    const r = el.getBoundingClientRect();
    const y = r.top + r.height / 2;
    if (r.width > minSize && r.height > minSize && y > minY && y < maxY) {
      targets.push({ fingerprint: getFingerprint(el), x: r.left + r.width / 2, y });
    }
  }
  return targets;
}

export function clickTarget(target: Target): ActionTemplate {
  return { Click: { fingerprint: target.fingerprint, point: { x: target.x, y: target.y } } };
}

export function clickAndDoubleClick(target: Target): ActionTemplate[] {
  const point = { x: target.x, y: target.y };
  return [
    { Click: { fingerprint: target.fingerprint, point } },
    { DoubleClick: { fingerprint: target.fingerprint, point } },
  ];
}

export const {
  noHttpErrorCodes,
  noUncaughtExceptions,
  noConsoleErrors,
} = defaultProperties;

const unhandledRejections = extract((state) =>
  state.errors.uncaughtExceptions
    .map((exc) => {
      const desc = exc.remote_object?.description ?? "";
      return desc && !exc.text.includes(desc) ? `${exc.text}: ${desc}` : exc.text;
    })
    .filter(
      (msg) =>
        msg.startsWith("Uncaught (in promise)") &&
        !msg.includes("Transition was skipped"),
    ),
);

export const noUnhandledPromiseRejections = always(
  () => unhandledRejections.current.length === 0,
);

const coreState = extract((state) => {
  const ready = state.document.readyState === "complete";
  const textLen = (state.document.body?.innerText ?? "").trim().length;
  const hasShellOrEditor = !!state.document.querySelector(".pv-build, #docname, #te-save");
  return { ready, textLen, hasShellOrEditor };
});

export const pageNeverBlank = always(
  () => !coreState.current.ready || coreState.current.textLen > 0,
);

export const shellOrEditorPresent = always(
  () => !coreState.current.ready || coreState.current.hasShellOrEditor,
);
