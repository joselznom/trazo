"""AST inventory of persistence, subprocess, concurrency and socket boundaries."""

from __future__ import annotations

import argparse
import ast
import re
from collections import Counter
from pathlib import Path

from common import SOURCE, dump

WRITES = {"write", "writelines", "write_text", "write_bytes", "write_atomic", "write_json", "dump",
          "replace", "rename", "copy", "copy2", "copyfile", "copytree", "copyfileobj", "extract", "extractall",
          "unlink", "rmtree", "mkdir", "makedirs", "touch", "chmod", "mkstemp", "mkdtemp", "truncate"}
THREADS = {"Thread", "Timer", "Lock", "RLock", "Event", "Condition", "Queue", "acquire", "release", "join"}
PORTS = {"bind", "server_bind", "listen", "LocalServer", "HTTPServer", "ThreadingHTTPServer", "TCPServer", "socket"}
SUBPROCESSES = {"subprocess.run", "subprocess.Popen", "subprocess.check_output", "subprocess.call",
               "subprocess.check_call", "pandoc_run.run", "os.execv", "os.execve"}

CATCHES = {
    ("background.py", 79): "Visible job error; terminates child. Cleanup wait itself has no local timeout.",
    ("browser.py", 141): "Cleanup then re-raise, no swallowed failure.",
    ("browser.py", 249): "Cleanup then re-raise, no swallowed failure.",
    ("build_graph.py", 280): "Remove changed outputs then re-raise; SIGKILL cannot run this cleanup.",
    ("diagram_editor.py", 336): "Silent close failure. Reference is dropped even if Chrome teardown failed; risk of a leaked process.",
    ("diagram_editor.py", 360): "Discard failed Chrome then re-raise to HTTP error boundary.",
    ("local_server.py", 175): "Visible HTTP 500 JSON with exception details.",
    ("mcp.py", 216): "Visible MCP isError; server stays alive.",
    ("mcp.py", 285): "Visible JSON-RPC internal error; server stays alive.",
    ("new.py", 74): "Cleanup then re-raise, no swallowed failure.",
    ("preview_edit_review.py", 247): "Silently omits broken table from output-drift checks; table editor must report it separately.",
    ("preview_locate.py", 30): "Silent matching fallback to a space. Does not write text; programming errors are also hidden.",
    ("preview_sources.py", 182): "Silent values-matching fallback. Does not save; an unrelated exception can reduce matching accuracy.",
    ("preview_watch.py", 20): "Console only, no page error state. A failed table regeneration can leave stale tables without a browser warning.",
    ("preview_watch.py", 39): "Stores error in all page states; visible banner, retries pending tables.",
    ("preview_watch.py", 99): "Stores error in the affected page state; keeps last good page.",
    ("preview_watch.py", 187): "Console only. Unexpected watcher failure is not reported to the browser and may consume the change.",
    ("review_storage.py", 52): "Remove temporary file then re-raise, no swallowed failure. Not crash durability (no fsync).",
    ("table_editor.py", 276): "Rollback new table then re-raise. A rollback failure could replace the original exception.",
}


def inventory() -> tuple[list[dict], list[dict]]:
    rows, catches = [], []
    for path in sorted((SOURCE / "trazo").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        threaded = "threading" in source
        for node in ast.walk(tree):
            context, parent = [], node
            while parent in parents:
                parent = parents[parent]
                if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    context.insert(0, parent.name)
            owner = ".".join(context) or "<module>"
            if isinstance(node, ast.ExceptHandler) and (node.type is None or ast.unparse(node.type) in ("Exception", "BaseException")):
                catches.append({"file": path.name, "line": node.lineno, "owner": owner,
                                "type": ast.unparse(node.type) if node.type else "bare",
                                "review": CATCHES.get((path.name, node.lineno), "REVIEW REQUIRED: source changed"),
                                "body": ast.get_source_segment(source, node)})
            category, name = None, ""
            if isinstance(node, ast.Call):
                name = ast.unparse(node.func)
                leaf = name.rsplit(".", 1)[-1]
                if name in SUBPROCESSES or leaf in ("communicate", "wait") and "proc" in name:
                    category = "subprocess"
                elif leaf in PORTS:
                    category = "port"
                elif leaf in THREADS and threaded:
                    category = "concurrency"
                elif leaf in WRITES and not re.search(r"stdout|stderr|stdin|wfile|writer|sys\.", name):
                    # Reviewed against the current tree: other replace/copy calls transform
                    # strings, AST nodes or dictionaries, not filesystem entries.
                    if leaf == "replace" and name not in ("os.replace", "portable.replace"):
                        if path.name == "review_storage.py" and name == "replace":
                            category = "disk mutation"
                    elif leaf == "copy" and name != "shutil.copy":
                        pass
                    else:
                        category = "disk mutation"
                elif leaf in ("open", "fdopen"):
                    modes = [n.value for n in node.args[1:] if isinstance(n, ast.Constant) and isinstance(n.value, str)]
                    modes += [k.value.value for k in node.keywords if k.arg == "mode" and isinstance(k.value, ast.Constant)]
                    pipe = path.name == "browser.py" and name == "os.fdopen"
                    stdio = "sys.stdout" in ast.unparse(node)
                    if any(re.search(r"[wax+]", str(mode)) for mode in modes) and not pipe and not stdio:
                        category = "disk mutation"
            elif threaded and isinstance(node, (ast.Assign, ast.AnnAssign)):
                name = ast.unparse(node).split("=", 1)[0].strip()
                value = node.value
                if owner == "<module>" and not isinstance(value, ast.Constant):
                    category = "shared module state candidate"
                elif "self." in name and any(term in name for term in ("state", "proc", "children", "cancel", "closed", "chrome", "reason")):
                    category = "shared instance state candidate"
            if not category:
                continue
            code = ast.get_source_segment(source, node)
            atomic = "n/a"
            if category == "disk mutation":
                atomic = ("atomic pathname replacement, no multi-file transaction or fsync"
                          if name.rsplit(".", 1)[-1] in ("write_atomic", "write_json", "replace", "rename")
                          else "not atomic at this call; inspect temp-file publication/caller")
            timeout = "n/a"
            if category in ("subprocess", "concurrency", "port"):
                timeout = next((ast.unparse(k.value) for k in getattr(node, "keywords", []) if k.arg == "timeout"),
                               "none at this call; enclosing lifecycle may bound it")
            # Show the actual error boundary instead of inferring that a log reaches the UI.
            handler, parent = "propagates to caller unless callee handles it", node
            while parent in parents:
                parent = parents[parent]
                if isinstance(parent, ast.Try) and parent.handlers:
                    handler = " | ".join(ast.get_source_segment(source, h).replace("\n", " ")[:350] for h in parent.handlers)
                    break
            rows.append({"file": path.name, "line": node.lineno, "owner": owner, "category": category,
                         "call": name, "atomic": atomic, "timeout": timeout, "failure_boundary": handler, "code": code})
    for path in sorted((SOURCE / "trazo/assets").rglob("*.js")):
        for line, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"(?:local|session)Storage\.(?:setItem|removeItem)", text):
                rows.append({"file": str(path.relative_to(SOURCE / "trazo")), "line": line,
                             "owner": "browser", "category": "browser storage", "call": text.strip(),
                             "atomic": "single key operation, no multi-key transaction", "timeout": "synchronous browser API",
                             "failure_boundary": text.strip(), "code": text.strip()})
    return rows, catches


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    rows, catches = inventory()
    dump(args.output / "inventory.json", {"sites": rows, "broad_catches": catches})
    lines = ["# Boundary inventory", "", "Static call-site inventory, not proof of runtime coverage.", "",
             "A timeout absent at a call site may be supplied by an owning job; nullable parameters are not guarantees.", "",
             "## Broad exception review", "", "| Site | Owner | Finding |", "| --- | --- | --- |"]
    lines += [f"| {c['file']}:{c['line']} | {c['owner']} | {c['review']} |" for c in catches]
    lines += ["", "## All sites", "", "| Site | Kind / owner | Atomicity | Timeout | Failure boundary |", "| --- | --- | --- | --- | --- |"]
    lines += [f"| {r['file']}:{r['line']} | {r['category']} / {r['owner']} | {r['atomic']} | {r['timeout']} | "
              + r["failure_boundary"].replace("|", "/").replace("\n", " ") + " |" for r in rows]
    (args.output / "inventory.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(dict(Counter(r["category"] for r in rows)))
    print(f"Broad catches: {len(catches)}")
