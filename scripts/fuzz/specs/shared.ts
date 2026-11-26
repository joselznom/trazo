import { always, extract } from "@antithesishq/bombadil";
import * as defaultProperties from "@antithesishq/bombadil/browser/defaults/properties";
import type { State } from "@antithesishq/bombadil/browser";

export const {
  noHttpErrorCodes,
  noUncaughtExceptions,
  noConsoleErrors,
} = defaultProperties;

const unhandledRejections = extract((state: State) =>
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

const coreState = extract((state: State) => {
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
