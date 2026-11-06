"""Capture Word artifacts and compare all ZIP members with narrowly defined normalization."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from common import SOURCE, command, dump


def normalized(path: Path) -> dict[str, str]:
    result = {}
    with zipfile.ZipFile(path) as archive:
        bad = archive.testzip()
        if bad:
            raise ValueError(f"Invalid CRC in {bad}")
        for name in sorted(archive.namelist()):
            data = archive.read(name)
            if name == "docProps/core.xml":
                root = ET.fromstring(data)
                for element in root:
                    if element.tag in ("{http://purl.org/dc/terms/}created", "{http://purl.org/dc/terms/}modified"):
                        element.text = "NORMALIZED_BUILD_TIME"
                data = ET.tostring(root)
            # ZIP member timestamps/order/compression are not content. Every other byte is compared.
            result[name] = hashlib.sha256(data).hexdigest()
    return result


def capture(root: Path, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    result = {"docx": {}, "exports": {}, "renderer_sha256": hashlib.sha256(
        (SOURCE / "trazo/assets/diagram/renderer.js").read_bytes()).hexdigest()}
    for path in sorted((root / "output").glob("*.docx")):
        shutil.copy2(path, out / path.name)
        result["docx"][path.name] = normalized(path)
    for path in sorted((root / "figures/external").glob("*")):
        if path.is_file():
            result["exports"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    checked = command(root, "diagram", "check", "--all", "--exports", "--json")
    (out / "diagram-check.json").write_text(checked.stdout, encoding="utf-8")
    dump(out / "manifest.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paper", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    result = capture(args.paper.resolve(), args.output.resolve())
    if args.compare:
        previous = json.loads((args.compare / "manifest.json").read_text())
        differences = {key: result[key] for key in result if previous.get(key) != result[key]}
        dump(args.output / "comparison.json", {"equal": not differences, "different_categories": list(differences)})
        print("PASS: normalized DOCX members, exports and renderer match" if not differences else f"FAIL: {list(differences)}")
        raise SystemExit(bool(differences))
    print(f"Captured {len(result['docx'])} Word files and {len(result['exports'])} export files")
