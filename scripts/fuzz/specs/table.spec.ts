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

const tableState = extract((state: State) => {
  const ready = state.document.readyState === "complete";
  const onTablePage = state.window.location.pathname === "/tables";
  const isLoading = !!state.document.querySelector("#te-page .te-empty");
  const hasTable = !!state.document.querySelector("#te-page table");
  const saveText = (state.document.querySelector("#te-save")?.textContent ?? "").trim();
  const fatalSaveError =
    saveText.startsWith("Error:") || saveText.includes("network or server error");
  const hasCellInput = !!state.document.querySelector(".te-cellin");

  const headers = Array.from(state.document.querySelectorAll("#te-page thead th[data-id]"))
    .slice(0, 6)
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
    .filter((r) => r.w > 10 && r.h > 10 && r.y > 50 && r.y < 700);

  const cells = Array.from(state.document.querySelectorAll("#te-page tbody td[data-row]"))
    .slice(0, 8)
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
    .filter((r) => r.w > 10 && r.h > 10 && r.y > 50 && r.y < 700);

  const tableLinks = Array.from(state.document.querySelectorAll("#te-list button, #te-list a"))
    .slice(0, 6)
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
    .filter((r) => r.w > 10 && r.h > 10 && r.y > 40 && r.y < 720);

  return {
    ready,
    onTablePage,
    isLoading,
    hasTable,
    fatalSaveError,
    hasCellInput,
    headers,
    cells,
    tableLinks,
  };
});

export const tableRemainsRendered = always(() => {
  const s = tableState.current;
  if (!s.ready || !s.onTablePage || s.isLoading) return true;
  return s.hasTable;
});

export const noTableSaveError = always(() => !tableState.current.fatalSaveError);

const interactHeaders = actions<ActionTemplate>(() =>
  tableState.current.headers.flatMap((hdr) => [
    { Click: { fingerprint: hdr.fingerprint, point: { x: hdr.x, y: hdr.y } } },
    { DoubleClick: { fingerprint: hdr.fingerprint, point: { x: hdr.x, y: hdr.y } } },
  ]),
);

const interactCells = actions<ActionTemplate>(() =>
  tableState.current.cells.flatMap((cell) => [
    { Click: { fingerprint: cell.fingerprint, point: { x: cell.x, y: cell.y } } },
    { DoubleClick: { fingerprint: cell.fingerprint, point: { x: cell.x, y: cell.y } } },
  ]),
);

const editActiveCell = actions<ActionTemplate>(() => {
  if (!tableState.current.hasCellInput) return [];
  return [
    { TypeText: { text: { Regexp: "[a-zA-Z0-9 .%]{1,10}" } } },
    { PressKey: { code: 13 } },
    { PressKey: { code: 27 } },
  ];
});

const switchTables = actions<ActionTemplate>(() =>
  tableState.current.tableLinks.map((item) => ({
    Click: { fingerprint: item.fingerprint, point: { x: item.x, y: item.y } },
  })),
);

export const explore = weighted<ActionTemplate>([
  [4, defaultActions.clicks],
  [2, defaultActions.inputs],
  [1, defaultActions.scroll],
  [1, defaultActions.navigation],
  [3, interactHeaders],
  [4, interactCells],
  [4, editActiveCell],
  [2, switchTables],
]);
