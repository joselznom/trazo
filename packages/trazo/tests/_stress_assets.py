"""Binary and JSON assets of the stress paper: PNG figures, table specs and data, diagrams.

Used by stress_fixture.py; every function is deterministic for a given
random.Random, so two runs with one seed write the same bytes.
"""

import json
import struct
import zlib
from pathlib import Path

# Map values and headers with non-ASCII text, Markdown and characters that
# need escaping in a grid table (|, *, _).
SITES = {"internal": "Internal (València)", "external": "Zürich (external)", "saopaulo": "São Paulo",
         "tokyo": "東京 (Tokyo)"}
GROUPS = {"A": "Group A", "B": "Group B (*n* ≥ 30)", "C": "Group C | pooled"}
VISITS = {"baseline": "Baseline", "follow-up": "Follow-up", "month_12": "Month 12"}
METHODS = {"logistic": "Logistic", "forest": "Random forest", "boost": "Gradient_boosting", "naive": "Naïve Bayes"}
COHORTS = ["c1", "c2", "c3", "c4", "c5", "c6"]
SHARED = {
    "schema_version": 1,
    "formats": {
        "mean_sd": "{mean:.2f} ({sd:.2f})",
        "ci": "{low:.2f}–{high:.2f}",
        "est_ci": "{est:.3f} ({low:.3f}–{high:.3f})",
        "pct": "{k} ({pct:.1f}%)",
        "p": "{value:.3f}",
        "dec1": "{value:.1f}",
    },
    "maps": {"site": SITES, "group": GROUPS, "visit": VISITS, "method": METHODS},
}


def png(width: int, height: int, seed: int) -> bytes:
    """An RGB PNG of `width` x `height`: eight distinct noisy rows, repeated,
    so files compress like real plots (tens of kilobytes) and build fast."""
    rows = []
    state = seed * 2654435761 % 2**32 or 1
    for _ in range(8):
        row = bytearray(width * 3)
        for i in range(0, len(row), 3):
            state = (state * 1103515245 + 12345) % 2**31
            shade = 160 + (state >> 16) % 96
            row[i:i + 3] = bytes((shade, (shade + seed * 37) % 256, (shade + seed * 91) % 256))
        rows.append(b"\0" + bytes(row))
    raw = b"".join(rows[(y * 5) % 8] for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def _ci(rng, centre: float, spread: float) -> dict:
    est = round(centre + rng.uniform(-spread, spread), 4)
    half = round(rng.uniform(0.01, 0.08), 4)
    return {"est": est, "low": round(est - half, 4), "high": round(est + half, 4)}


def _groups(rng, n: int) -> tuple[list, dict]:
    records = []
    for site in ("internal", "external", "saopaulo", "tokyo"):
        for group in GROUPS:
            for visit in VISITS:
                records.append({"site": site, "group": group, "visit": visit,
                                "patients": rng.randint(12, 400),
                                "score": {"mean": round(rng.uniform(0.2, 0.9), 4), "sd": round(rng.uniform(0.02, 0.2), 4)},
                                "ci": {"low": round(rng.uniform(0.1, 0.4), 4), "high": round(rng.uniform(0.5, 0.95), 4)}})
    spec = {"rows": {"keys": ["site", "group", "visit"], "order": {"site": list(SITES), "visit": list(VISITS)},
                     "block": "site"},
            "columns": [{"id": "group", "field": "group", "header": "Group", "merge": True},
                        {"id": "visit", "field": "visit", "header": "Visit"},
                        {"id": "patients", "field": "patients", "header": "*n*", "align": "right"},
                        {"id": "score", "header": "Score", "columns": [
                            {"id": "mean", "field": "score", "header": "Mean (SD)", "format": "mean_sd", "align": "right"},
                            {"id": "ci", "field": "ci", "header": "95% CI", "format": "ci", "align": "right"}]}],
            "highlight": [{"column": "score.mean", "best": "max", "within": "block"}]}
    return records, spec


def _long(rng, n: int, words) -> tuple[list, dict]:
    records = []
    for item in range(1, n + 1):
        name = " ".join(rng.choice(words) for _ in range(rng.randint(2, 9))).capitalize()
        k = rng.randint(0, 300)
        records.append({"item": f"{item:03d}", "name": name, "n": 300, "share": {"k": k, "n": 300},
                        "effect": _ci(rng, 0.5, 0.3)})
    spec = {"rows": {"keys": ["item"]},
            "columns": [{"id": "item", "field": "item", "header": "No."},
                        {"id": "name", "field": "name", "header": "Item", "width": 4},
                        {"id": "share", "field": "share", "header": "Present, *n* (%)", "format": "pct", "align": "right"},
                        {"id": "effect", "field": "effect", "header": "Effect (95% CI)", "format": "est_ci", "align": "right"}]}
    return records, spec


def _wide(rng, n: int) -> tuple[list, dict]:
    records = [{"method": method, "cohort": cohort, "auc": _ci(rng, 0.75, 0.15), "brier": round(rng.uniform(0.05, 0.3), 4)}
               for method in METHODS for cohort in COHORTS]
    spec = {"rows": {"keys": ["method"], "order": {"method": list(METHODS)}},
            "columns": [{"id": "method", "field": "method", "header": "Method"},
                        {"id": "by", "pivot": "cohort", "values": COHORTS, "columns": [
                            {"id": "auc", "field": "auc", "header": "AUC", "format": "est_ci", "align": "center"},
                            {"id": "brier", "field": "brier", "header": "Brier", "format": "p", "align": "center"}]}],
            "maps": {"cohort": {c: f"Cohort {c[1]}" for c in COHORTS}},
            "highlight": [{"column": "by.c1.auc", "best": "max", "within": "table"}]}
    return records, spec


def _missing(rng, n: int) -> tuple[list, dict]:
    records = []
    for method in METHODS:
        for visit in VISITS:
            value = None if rng.random() < 0.3 else round(rng.uniform(1, 99), 2)
            records.append({"method": method, "visit": visit, "value": value, "hidden": "x", "keep": "no" if visit == "month_12" else "yes"})
    spec = {"missing": "n/a",
            "rows": {"keys": ["method", "visit"], "filter": {"keep": ["yes"]}},
            "columns": [{"id": "method", "field": "method", "header": "Method", "merge": True},
                        {"id": "visit", "field": "visit", "header": "Visit"},
                        {"id": "value", "field": "value", "header": "Value (µg/m³)", "format": "dec1", "align": "right"},
                        {"id": "hidden", "field": "hidden", "header": "Hidden", "hidden": True}]}
    return records, spec


KINDS = {"groups": _groups, "long": _long, "wide": _wide, "missing": _missing}


def table(root: Path, label: str, kind: str, rng, words, title: str, legend: str, note: str,
          rows: int = 200) -> None:
    """Write tables/specs/<label>.json and tables/output/data/<label>.json."""
    maker = KINDS[kind]
    records, spec = (maker(rng, rows, words) if kind == "long" else maker(rng, rows))
    data = {"schema_version": 1, "generator": "stress_fixture.py", "inputs": {},
            "values": {"n": sum(r.get("patients", 0) for r in records) or len(records)}, "records": records}
    spec = {"schema_version": 1, "data": label, "title": title, "legend": legend, **spec}
    if note:
        spec["note"] = note
    _write_json(root / "tables/specs" / f"{label}.json", spec)
    _write_json(root / "tables/output/data" / f"{label}.json", data)


def diagram(root: Path, name: str, rng, labels: list[str]) -> None:
    """A row of boxes joined by arrows, with non-ASCII labels."""
    nodes, edges = [], []
    for i, text in enumerate(labels):
        nodes.append({"id": f"n{i}", "kind": "box", "x": 3 + i * 45, "y": 4, "w": 38, "h": 14,
                      "fill": rng.choice(["field", "signal-tint"]),
                      "text": [{"style": "node-title", "t": text}, {"style": "node-body", "t": f"step {i + 1}"}]})
        if i:
            edges.append({"id": f"e{i}", "from": f"n{i - 1}.right", "to": f"n{i}.left"})
    spec = {"schema_version": 1, "canvas": {"width_mm": 3 + 45 * len(labels), "height_mm": 22, "grid": 1},
            "panels": [], "nodes": nodes, "edges": edges, "raw": [], "meta": {"about": f"Stress diagram {name}."}}
    _write_json(root / "figures/diagrams" / f"{name}.json", spec)


def shared(root: Path) -> None:
    _write_json(root / "tables/specs/_shared.json", SHARED)


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
