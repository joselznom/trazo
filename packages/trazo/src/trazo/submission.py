"""Check what an article still needs before submission (trazo.submission_check).
`trazo build --submission` builds the separate submission documents.

    trazo submission check [--strict] [--json]
"""

import argparse
import json
import sys


def check(strict=False, as_json=False):
    """Print the submission report (see trazo.submission_check); 1 with
    `strict` when it has an error."""
    from .labels import once
    from .submission_check import report
    with once():
        result = report(fields=as_json)
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result["kind"] != "article":
        print("Reports have no submission layer: nothing to check.")
    else:
        tags = {"error": "ERROR", "warn": "WARN", "ok": "ok", "info": "info"}
        for group in result["groups"]:
            print(group["label"])
            for item in group["items"]:
                text = " — ".join(part for part in (item["value"], item["detail"]) if part)
                print(f"  {tags[item['status']]:<5}  {item['label']}" + (f": {text}" if text else ""))
        plural = lambda count, word: f"{count} {word}{'' if count == 1 else 's'}"  # noqa: E731
        print(f"\n{plural(result['errors'], 'error')}, {plural(result['warnings'], 'warning')}")
    return int(strict and result["errors"] > 0)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="trazo submission", description=__doc__)
    parser.add_argument("action", choices=["check"], help="what is missing or over the limits")
    parser.add_argument("--strict", action="store_true", help="exit 1 when there is an error")
    parser.add_argument("--json", action="store_true", help="print the report, with the form fields, as JSON")
    args = parser.parse_args(argv)
    try:
        return check(args.strict, args.json)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"trazo submission: {exc}", file=sys.stderr)
        return 1
