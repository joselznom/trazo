import { always, eventually } from "@antithesishq/bombadil";
import * as defaultActions from "@antithesishq/bombadil/browser/defaults/actions";
import { actions, extract, weighted } from "@antithesishq/bombadil/browser";
import { clickAndDoubleClick, clickTarget, queryTargets } from "./shared.ts";

export {
  noHttpErrorCodes,
  noUncaughtExceptions,
  noUnhandledPromiseRejections,
  noConsoleErrors,
  pageNeverBlank,
  shellOrEditorPresent,
} from "./shared.ts";

const tableState = extract((state) => {
  const ready = state.document.readyState === "complete";
  const onTablePage = state.window.location.pathname === "/tables";
  const emptyText = (state.document.querySelector("#te-page .te-empty")?.textContent ?? "").trim();
  const isLoading = emptyText === "Loading…";
  const hasTable = !!state.document.querySelector("#te-page table");
  const saveText = (state.document.querySelector("#te-save")?.textContent ?? "").trim();
  const fatalSaveError =
    saveText.startsWith("Error:") || saveText.includes("network or server error");
  const hasCellInput = !!state.document.querySelector(".te-cellin");

  const headers = queryTargets(state.document, "#te-page thead th[data-id]", {
    limit: 6,
    minSize: 10,
    minY: 50,
    maxY: 700,
  });
  const cells = queryTargets(state.document, "#te-page tbody td[data-row]", {
    limit: 8,
    minSize: 10,
    minY: 50,
    maxY: 700,
  });
  const tableLinks = queryTargets(state.document, '#te-list li[role="link"]', {
    limit: 6,
    minSize: 10,
    minY: 40,
    maxY: 720,
  });

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
  if (!s.ready || !s.onTablePage) return true;
  if (s.hasTable) return true;
  if (!s.isLoading) return false;
  return eventually(
    () => !tableState.current.onTablePage || tableState.current.hasTable,
  ).within(5, "seconds");
});

export const noTableSaveError = always(() => !tableState.current.fatalSaveError);

const interactHeaders = actions(() => tableState.current.headers.flatMap(clickAndDoubleClick));

const interactCells = actions(() => tableState.current.cells.flatMap(clickAndDoubleClick));

const editActiveCell = actions(() => {
  if (!tableState.current.hasCellInput) return [];
  return [
    { TypeText: { text: { Regexp: "[a-zA-Z0-9 .%]{1,10}" } } },
    { PressKey: { code: 13 } },
    { PressKey: { code: 27 } },
  ];
});

const switchTables = actions(() => tableState.current.tableLinks.map(clickTarget));

export const explore = weighted([
  [4, defaultActions.clicks],
  [2, defaultActions.inputs],
  [1, defaultActions.scroll],
  [1, defaultActions.navigation],
  [3, interactHeaders],
  [4, interactCells],
  [4, editActiveCell],
  [2, switchTables],
]);
