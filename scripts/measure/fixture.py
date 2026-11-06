"""Generate an extended article from the shipped example, preserving its scripts."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import struct
import zlib
from pathlib import Path

from common import REPO, command, dump

SEED = 20261106
SENTENCES = [
    "The analysis compared the development cohort with the external validation cohort using the same prespecified outcome definition.",
    "Readers reviewed the source measurements independently and resolved disagreements before the final analysis was performed.",
    "We assessed the sensitivity of the estimates to missing measurements and reported the uncertainty for each subgroup.",
    "The protocol retained all eligible observations and recorded the reasons for exclusion in the supplementary material.",
    "Differences between sites may reflect acquisition settings, referral patterns and the distribution of baseline characteristics.",
    "The results support further prospective evaluation with a broader population and independently collected outcome measurements.",
]


def plot_png(seed: int) -> bytes:
    """Distinct deterministic raster plots, so image reuse cannot understate browser/Word work."""
    width, height = 1000, 650
    rows = []
    for y in range(height):
        row = bytearray(b"\xff" * (width * 3))
        for x in range(width):
            axis = x in (65, 66) and 35 < y < 590 or y in (589, 590) and 65 < x < 960
            curve = 380 - 150 * math.sin((x - 65) / (125 + seed * 3)) + seed * 4
            if axis:
                row[x * 3:x * 3 + 3] = b"\x30\x30\x30"
            elif 70 < x < 960 and abs(y - curve) < 2:
                row[x * 3:x * 3 + 3] = bytes((25 + seed * 6, 88, 155))
        rows.append(b"\0" + row)
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))


def create(root: Path, load: bool) -> dict:
    if root.exists():
        raise FileExistsError(f"Refusing to overwrite an existing paper: {root}")
    command(REPO, "new", "--title", "Workflow benchmark", str(root))
    if load:
        rng = random.Random(SEED)
        bib = root / "bibliography/references.bib"
        with bib.open("a", encoding="utf-8") as handle:
            for i in range(80):
                handle.write(f"\n@article{{bench{i:03d}, author={{Example, A.}}, title={{Synthetic study {i}}}, "
                             f"journal={{Benchmark fixture}}, year={{{2000 + i % 25}}}}}\n")
        text = ["# Extended validation study\n\nThis is synthetic benchmark content, not research evidence.\n"]
        words = 0
        for i in range(160):
            sentences = [rng.choice(SENTENCES) for _ in range(4)]
            paragraph = " ".join(sentences) + f" [@bench{i % 80:03d}].\n"
            text.append(paragraph)
            words += len(paragraph.split())
            if i % 16 == 0:
                text.append(f"## Validation stratum {i // 16 + 1}\n")
        # The real example's analysis scripts still run. Extra tables use their real data.
        original = json.loads((root / "tables/specs/summary.json").read_text())
        for i in range(1, 10):
            spec = {**original, "title": f"Synthetic validation stratum {i}."}
            dump(root / f"tables/specs/bench{i:02d}.json", spec)
            text.append(f"\n!include tables/output/md/bench{i:02d}.md\n")
        for i in range(1, 11):
            figure = f"figures/external/bench{i:02d}.png"
            (root / figure).write_bytes(plot_png(i))
            text.append(f"\n![Synthetic sensitivity plot {i}.]({figure}){{#fig:bench{i:02d}}}\n")
        (root / "manuscript/main/06_validation.md").write_text("\n".join(text), encoding="utf-8")
    command(root, "build")
    markdown = "\n".join(p.read_text() for p in sorted((root / "manuscript").rglob("*.md")))
    spec_files = [p for p in (root / "tables/specs").glob("*.json") if not p.name.startswith("_")]
    manifest = {"seed": SEED, "kind": "load" if load else "example", "base": "trazo new with example=true",
                "counts_report": command(root, "counts").stdout,
                "markdown_words": len(markdown.split()), "figures": len(re.findall(r"\{#fig:", markdown)),
                "table_specs": len(spec_files), "diagram_specs": len(list((root / "figures/diagrams").glob("*.json"))),
                "cited_keys": len(set(re.findall(r"(?<!\w)@((?!fig:|tbl:|eq:|sec:)[\w:-]+)", markdown))),
                "bibliography_entries": len(re.findall(r"(?m)^@", (root / "bibliography/references.bib").read_text())),
                "inputs": {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                           for folder in ("manuscript", "bibliography", "tables/specs", "figures/diagrams", "figures/external")
                           for p in sorted((root / folder).rglob("*")) if p.is_file()}}
    dump(root / "benchmark-fixture.json", manifest)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--load", action="store_true")
    args = parser.parse_args()
    print(json.dumps(create(args.folder.resolve(), args.load), indent=2))
