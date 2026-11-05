"""The `trazo table` command line; the tables themselves are trazo.table.

`trazo table --help` prints trazo.table's docstring: the spec keys, the data
contract and the patch operations.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from . import table as _table
from . import table_hand
from .build_lock import exclusive
from .review_storage import StoreError, write_atomic
from .table import (
    COMMENTS,
    DISTINCT_SHOWN,
    HISTORY,
    NAME_RE,
    SHARED_PATH,
    SPEC_DIR,
    Checker,
    Issue,
    SpecInvalid,
    Table,
    TableError,
    all_labels,
    build,
    data_path,
    dump_spec,
    is_number,
    output_path,
    patch,
    patch_shared,
    read_json,
    rel,
    show,
    spec_path,
    stale_output,
    status_of,
    undo,
)

TABLE_DOC = _table.__doc__ or ""


def format_issue(item: Issue) -> str:
    where = f" [{item['id']}]" if item.get("id") else ""
    return f"  {item['level']}: {item['code']}{where}: {item['message']}"


def report(table: Table) -> None:
    for item in table.issues:
        print(format_issue(item))
    status = "FAIL" if table.errors else "ok"
    print(f"{table.label}: {status} ({len(table.errors)} error(s), "
          f"{len(table.warnings)} warning(s))")


def labels_of(names: list[str]) -> list[str]:
    return names or all_labels()


def cmd_check(names: list[str], as_json: bool, outputs: bool) -> int:
    tables = []
    for label in labels_of(names):
        table = build(label)
        if outputs:
            table.issues += stale_output(table)
        tables.append(table)
    if as_json:
        payload = [{"label": t.label, "file": rel(t.path), "ok": not t.errors,
                    "errors": len(t.errors), "warnings": len(t.warnings), "issues": t.issues}
                   for t in tables]
        print(json.dumps(payload[0] if len(names) == 1 else payload, indent=2, ensure_ascii=False))
    else:
        if not tables:
            print(f"no tables in {rel(SPEC_DIR)}")
        for table in tables:
            report(table)
    return 1 if any(t.errors for t in tables) else 0


@exclusive
def cmd_render(names: list[str]) -> int:
    failed = False
    for label in labels_of(names):
        table = build(label)
        if table.markdown is None:
            report(table)
            print(f"{label}: not rendered (fix the errors)")
            failed = True
            continue
        for item in table.issues:
            print(format_issue(item))
        write_atomic(output_path(label), table.markdown.encode("utf-8"))
        print(f"rendered {rel(output_path(label))}")
    return 1 if failed else 0


def cmd_new(label: str, data_name: str | None) -> None:
    path = spec_path(label)
    if path.exists():
        raise TableError(f"{rel(path)} already exists")
    if data_name is None:
        HISTORY.write_file(path, dump_spec(table_hand.new_spec(f"TODO: title of {label}.", None)), f"new {label}")
        print(f"created {rel(path)} (typed by hand: three columns, three rows)")
        print(f"next: trazo table describe {label}")
        return
    if not NAME_RE.match(data_name):
        raise TableError(f"invalid data name {data_name!r}")
    data, _ = read_json(data_path(data_name), "table data")
    problems = Checker().data(data, data_name)
    if problems:
        raise TableError(problems[0]["message"])
    fields: dict[str, list[Any]] = {}
    for record in data["records"]:
        for field, value in record.items():
            fields.setdefault(field, []).append(value)
    keys = ([f for f, vs in fields.items() if any(isinstance(v, str) for v in vs)]
            or list(fields)[:1])
    columns, formats = [], {}
    for field, values in fields.items():
        col: dict[str, Any] = {"id": re.sub(r"[^A-Za-z0-9_-]", "_", field), "field": field,
                               "header": field}
        sample = next((v for v in values if v is not None), None)
        if isinstance(sample, dict):
            col["format"] = field
            formats[field] = " / ".join(f"{{{k}:.2f}}" for k in sample)
        elif isinstance(sample, float) or any(isinstance(v, float) for v in values):
            col["format"] = "dec2"
            formats["dec2"] = "{value:.2f}"
        if is_number(sample) or isinstance(sample, dict):
            col["align"] = "right"
        columns.append(col)
    spec: dict[str, Any] = {"schema_version": 1, "data": data_name,
                            "title": f"TODO: title of {label}.", "rows": {"keys": keys},
                            "columns": columns}
    if formats:
        spec["formats"] = formats
    HISTORY.write_file(path, dump_spec(spec), f"new {label}")
    print(f"created {rel(path)} ({len(columns)} column(s), rows by {', '.join(keys)})")
    print(f"next: trazo table describe {label}")


def cmd_describe(label: str) -> int:
    table = build(label)
    spec = table.spec
    status = "FAIL" if table.errors else "ok"
    print(f"{label}: {rel(table.path)} -> {rel(output_path(label))} ({status}: "
          f"{len(table.errors)} error(s), {len(table.warnings)} warning(s))")
    if table.hand and table.filled:
        print(f"typed by hand: {len(table.rows)} row(s), leaf ids "
              + ", ".join(leaf.spec["id"] for leaf in table.all_leaves)
              + "; row ids " + ", ".join(row["key"]["row"] for row in table.rows))
        print(table.markdown or "", end="")
        return 0
    if not table.fields:
        report(table)
        return 1
    data = table.data
    stale = any(i["code"] == "stale-data" for i in table.issues)
    print(f"data {spec['data']}: {rel(data_path(spec['data']))}, {len(data['records'])} record(s), "
          f"generator {data.get('generator', '?')}, inputs {'STALE' if stale else 'current'}")
    print("fields:")
    width = max(map(len, table.fields))
    for field, values in table.fields.items():
        kinds = sorted({"null" if v is None else "string" if isinstance(v, str) else
                        "int" if isinstance(v, int) else "float" if is_number(v) else
                        "composite {" + ", ".join(v) + "}" for v in values})
        line = f"  {field:<{width}}  {' | '.join(kinds)}"
        strings = list(dict.fromkeys(v for v in values if isinstance(v, str)))
        if strings:
            more = f", ... ({len(strings)} distinct)" if len(strings) > DISTINCT_SHOWN else ""
            line += ": " + ", ".join(strings[:DISTINCT_SHOWN]) + more
        print(line)
    if data.get("values"):
        print("values (for {name} in title and legend): "
              + ", ".join(f"{k} = {show(v)}" for k, v in data["values"].items()))
    rows = spec["rows"]
    print(f"rows: {len(table.rows)}, one per {' x '.join(rows['keys'])}"
          + (f"; block rows by {rows['block']}" if rows.get("block") else ""))
    for part in ("order", "filter"):
        for field, values in rows.get(part, {}).items():
            print(f"  {part} {field}: {', '.join(map(show, values))}")
    print("columns (patch paths: columns.<id>[.<leaf id>].<key>; leaf ids for highlight "
          "and emphasis):")
    used: dict[str, str] = {}

    def leaf_line(col: dict[str, Any], full: str, indent: str) -> None:
        extras = [f"field {col.get('field')}"]
        if col.get("format"):
            extras.append(f"format {col['format']}")
            used[col["format"]] = table.formats.get(col["format"], "(unknown)")
        extras += [k for k in ("merge", "hidden") if col.get(k)]
        extras += [f"{k} {col[k]}" for k in ("align", "width") if k in col]
        print(f"{indent}{full}  {show(col.get('header'))}  ({', '.join(extras)})")

    for col in spec["columns"]:
        if "pivot" in col:
            values = col.get("values") or list(dict.fromkeys(
                v for v in table.fields.get(col["pivot"], []) if v is not None))
            print(f"  {col['id']}  pivot on {col['pivot']}: {', '.join(map(show, values))} "
                  f"(leaf ids {col['id']}.<value>.<leaf>)")
            for child in col["columns"]:
                leaf_line(child, f"{col['id']}.{child['id']}", "    ")
        elif "columns" in col:
            print(f"  {col['id']}  group {show(col['header'])}")
            for child in col["columns"]:
                leaf_line(child, f"{col['id']}.{child['id']}", "    ")
        else:
            leaf_line(col, col["id"], "  ")
    if used:
        print("formats in use: " + "; ".join(f"{k} = {show(v)}" for k, v in used.items()))
    unused = sorted(set(table.formats) - set(used))
    if unused:
        print("other formats: " + ", ".join(unused))
    for i, rule in enumerate(spec.get("highlight", [])):
        print(f"highlight.{i}: {rule['best']} of {rule['column']} within "
              f"{rule.get('within', 'table')}")
    for i, rule in enumerate(spec.get("emphasis", [])):
        print(f"emphasis.{i}: {rule['column']} in row {show(rule['row'])}")
    if table.issues:
        print("issues:")
        for item in table.issues:
            print(format_issue(item))
    if table.markdown:
        print("rendered:")
        print(table.markdown, end="")
    return 0


def cmd_patch(label: str | None, ops_file: str, shared: bool) -> int:
    text = sys.stdin.read() if ops_file == "-" else Path(ops_file).read_text(encoding="utf-8")
    try:
        ops = json.loads(text)
    except ValueError as exc:
        raise TableError(f"operations are not valid JSON: {exc}") from exc
    try:
        summary = patch_shared(ops) if shared else patch(str(label), ops)
    except SpecInvalid as exc:
        for item in exc.issues:
            print(format_issue(item))
        raise
    if not summary:
        print("no change")
        return 0
    target = rel(SHARED_PATH) if shared else rel(spec_path(str(label)))
    print(f"patched {target}: {'; '.join(summary)}")
    print("rendered every table" if shared else f"rendered {rel(output_path(str(label)))}")
    return 0



def cmd_list(as_json: bool) -> int:
    entries = []
    for label in all_labels():
        table = build(label)
        spec = table.spec if isinstance(table.spec, dict) else {}
        entries.append({"label": label, "data": spec.get("data"),
                        "status": status_of(table)})
    if as_json:
        print(json.dumps(entries, indent=2, ensure_ascii=False))
        return 0
    if not entries:
        print(f"no tables in {rel(SPEC_DIR)}")
    for entry in entries:
        print(f"{entry['label']}  data {entry['data']}  {entry['status']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="trazo table",
        description=TABLE_DOC.splitlines()[0],
        epilog="\n".join(TABLE_DOC.splitlines()[1:]),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("new", help="scaffold a spec with one column per data field")
    p.add_argument("label")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--data", help="data file name in the table data folder")
    source.add_argument("--hand", action="store_true", help="a table typed by hand (trazo.table_hand)")
    for name, about in (("render", "write the Markdown table (refuses on check errors)"),
                        ("check", "check spec, data and formats (non-zero exit on error)")):
        p = sub.add_parser(name, help=about)
        p.add_argument("labels", nargs="*", metavar="LABEL")
        p.add_argument("--all", action="store_true", help="every table")
        if name == "check":
            p.add_argument("--json", action="store_true", help="machine-readable output")
            p.add_argument("--outputs", action="store_true",
                           help="also fail when a rendered file is missing or out of date")
    p = sub.add_parser("describe", help="fields, values, columns, rows and the rendered table")
    p.add_argument("label")
    p = sub.add_parser("patch", help="apply set/add/remove/move operations")
    p.add_argument("args", nargs="+", metavar="LABEL OPS",
                   help='LABEL and a JSON file with the operations ("-" for stdin); '
                        "with --shared only the file")
    p.add_argument("--shared", action="store_true",
                   help="set formats.* and maps.* in _shared.json, then render every table")
    p = sub.add_parser("undo", help="revert the last table spec, shared or comment change")
    p.add_argument("label", nargs="?", help="only this table's last spec change")
    p = sub.add_parser("comments", help="list open table comments")
    p.add_argument("label", nargs="?", help="only this table")
    p.add_argument("--all", action="store_true", help="every state, with threads")
    p = sub.add_parser("resolve", help="reply to a table comment and set it to review")
    p.add_argument("id")
    p.add_argument("reply")
    p = sub.add_parser("list", help="every table with its data and status")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    try:
        if args.command == "new":
            cmd_new(args.label, args.data)
        elif args.command in ("render", "check"):
            if bool(args.labels) == args.all:
                parser.error(f"{args.command} needs LABEL(s) or --all")
            for label in args.labels:
                spec_path(label)
            if args.command == "render":
                return cmd_render(args.labels)
            return cmd_check(args.labels, args.json, args.outputs)
        elif args.command == "describe":
            return cmd_describe(args.label)
        elif args.command == "patch":
            if len(args.args) != (1 if args.shared else 2):
                parser.error("patch needs LABEL OPS, or --shared OPS")
            if args.shared:
                return cmd_patch(None, args.args[0], True)
            return cmd_patch(args.args[0], args.args[1], False)
        elif args.command == "undo":
            if args.label:
                spec_path(args.label)
            print(undo(args.label))
        elif args.command == "comments":
            COMMENTS.print(args.label, args.all)
        elif args.command == "resolve":
            COMMENTS.resolve(args.id, args.reply, TableError)
            print(f"[{args.id}] review: reply added")
        elif args.command == "list":
            return cmd_list(args.json)
    except (TableError, StoreError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
