"""Write table data for trazo: ``tables/output/data/<name>.json``.

The generator computes the numbers; ``scripts/trazo table render``
formats them with ``tables/specs/<label>.json``. Records are flat objects
whose values are strings, ints, floats, None (missing) or objects of numbers
such as ``{"mean", "sd", "low", "high"}``. Do not round or format here; the
spec decides every decimal. The sha256 of each input is recorded, so
``scripts/trazo table check`` reports data that is older than its inputs.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "tables" / "output" / "data"
GENERATOR = "tables/scripts/generate_tables.py"


def write_data(name: str, records: Sequence[Mapping[str, object]], *,
               inputs: Sequence[str], values: Mapping[str, object] | None = None) -> Path:
    """Write <name>.json with the records, caption values and input hashes."""
    payload = {
        "schema_version": 1,
        "generator": GENERATOR,
        "inputs": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                   for path in inputs},
        "values": dict(values or {}),
        "records": [dict(record) for record in records],
    }
    path = DATA_DIR / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
