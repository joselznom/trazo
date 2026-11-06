"""Own the real HTTP/build boundary while a supervisor injects and checks faults."""

from __future__ import annotations

import argparse
import hashlib
import json
import threading
from pathlib import Path

from common import dump, request
from fault_io import Injector


def run(root: Path, operation: str, output: Path, fault: str, site: str) -> None:
    from trazo import builds, diagram, diagram_editor, preview, table_editor
    from trazo.local_server import LocalServer
    from trazo.server import Handler

    injector = Injector(root, output.with_suffix(".sites.jsonl"), fault, site)
    if operation == "build":
        injector.install()
        code = builds.main(["--force"])
        injector.enabled = False
        dump(output, {"status": code, "hits": injector.hits})
        return
    if operation == "export":
        injector.install()
        code = diagram.main(["export", "--all"])
        injector.enabled = False
        dump(output, {"status": code, "hits": injector.hits})
        return
    if operation == "preview":
        injector.install()
        preview.build("manuscript")
        injector.enabled = False
        dump(output, {"state": preview.STATE["manuscript"], "hits": injector.hits})
        return
    source = "manuscript/main/01_introduction.md"
    if operation == "source":
        state = preview.get_source(source)
        endpoint = "/__source"
        payload = {"file": source, "base_sha256": state["sha256"],
                   "text": state["text"] + "\n\nThe browser saved a new paragraph.\n"}
    elif operation == "table":
        state = table_editor.table_state("summary")
        endpoint = "/__table"
        payload = {"label": "summary", "base_sha256": state["sha256"],
                   "ops": [{"op": "set", "path": "title", "value": "A revised table caption."}]}
    else:
        state = diagram_editor.get_diagram("fig2_workflow")
        endpoint = "/__diagrams/file/fig2_workflow"
        diagram = state["diagram"]
        diagram["nodes"][0]["text"][0]["t"] = "Revised text"
        payload = {"base_sha256": state["sha256"], "diagram": diagram}
    injector.install()
    with LocalServer(("127.0.0.1", 0), Handler) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            status, body = request(server.server_port, endpoint, payload)
            injector.enabled = False
            alive = request(server.server_port, "/__status")[0] == 200
            dump(output, {"status": status, "body": body, "alive": alive, "hits": injector.hits,
                          "payload_sha256": hashlib.sha256(json.dumps(payload).encode()).hexdigest()})
        finally:
            server.shutdown()
            worker.join(timeout=3)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paper", type=Path)
    parser.add_argument("operation", choices=("source", "table", "diagram", "build", "preview", "export"))
    parser.add_argument("output", type=Path)
    parser.add_argument("--fault", default="none", choices=("none", "kill", "full", "readonly", "race"))
    parser.add_argument("--site", default="")
    args = parser.parse_args()
    run(args.paper.resolve(), args.operation, args.output.resolve(), args.fault, args.site)
