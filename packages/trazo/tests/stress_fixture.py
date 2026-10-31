"""A large, awkward test paper, generated: thesis-sized at --scale 1.

    python stress_fixture.py create DIR [--scale S] [--seed N] [--image WxH] [--no-diagrams]
    python stress_fixture.py bench DIR [same options] [--json]

`create` writes a new paper into DIR (empty or missing) with `trazo new
--no-example`, then replaces its content. At --scale 1 the manuscript has
about 86,000 words of main text in eight chapters, 133 figures (one in four
with panels, 12 of them diagrams), 102 tables (spec tables: grouped, long,
wide and with missing cells; and tables typed by hand), 150 equations and
527 cited references; the supplementary about 13,000 words, 41 figures and
51 tables of its own. Also: 80 abbreviations, 800 review comments in every
state, declarations, a cover letter, non-ASCII text throughout, and a fixed
chapter of edge cases (_stress_edges.md) that does not scale. Writing it
takes about two seconds. DIR/stress-expected.json holds what a correct build
shows (trazo never reads it): each label's number per document, the
cited keys, and the review comments' quotes.

`bench` creates the paper, then times the build, the preview and the checks
(see _stress_bench.py). Run both with this checkout's trazo, from the
repository root:

    uv run --project packages/trazo python packages/trazo/tests/stress_fixture.py bench /tmp/stress

The unit tests (test_stress_fixture.py) create a small one (--scale 0.03).
References from the manuscript to the supplementary's figures and tables use
the values workaround throughout, and the direct form once.
"""

import argparse
import json
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

import _stress_assets as assets
from _stress_project import review_comments, write_config
from _stress_text import FOREIGN, Context, Doc

EDGES = Path(__file__).resolve().parent / "_stress_edges.md"
EXPECTED = "stress-expected.json"
# Sizes at --scale 1, a thesis; about 85 words per paragraph.
SIZES = {"words": 80000, "chapters": 8, "figures": 120, "tables": 100, "equations": 150,
         "references": 600, "abbreviations": 80, "comments": 800, "diagrams": 12,
         "supp_figures": 40, "supp_tables": 50, "supp_equations": 50, "supp_words": 12000}
TABLE_KINDS = ["groups", "groups", "missing", "long", "wide"]
IGNORED = ["CI", "AUC", "SD", "CT", "MRI"]


def sizes(scale: float) -> dict[str, int]:
    return {key: max(1, round(value * scale)) for key, value in SIZES.items()}


def trazo(*args: str) -> None:
    result = subprocess.run([sys.executable, "-m", "trazo", *args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"trazo {' '.join(args)}: {result.stderr.strip()}")


def create(root: Path, scale: float = 1.0, seed: int = 1, image: tuple[int, int] = (1200, 900),
           diagrams: bool = True) -> dict:
    """Write the paper into `root` and return the expectations; without
    `diagrams` (exporting them needs Chrome) it has no diagram figures."""
    root = Path(root).resolve()
    trazo("new", "--no-example", "--title", "Stress", str(root))
    # No project scripts: values and table data are written here, so a build never syncs a venv.
    for name in ("scripts/build_values.py", "tables/scripts"):
        target = root / name
        shutil.rmtree(target) if target.is_dir() else target.unlink()
    for folder in ("manuscript/main", "manuscript/supplementary"):
        for path in (root / folder).glob("*.md"):
            path.unlink()
    size = sizes(scale)
    if not diagrams:
        size["diagrams"] = 0
    rng = random.Random(seed)
    ctx = Context(rng, size["references"], size["abbreviations"])
    assets.shared(root)
    figures = Figures(root, rng, image)
    supp = supplementary(root, ctx, size, figures)
    main = manuscript(root, ctx, size, figures, supp)
    supp.write(root)
    main.write(root)
    write_config(root, ctx, IGNORED)
    comments = review_comments(root, rng, main.paragraphs + supp.paragraphs, size["comments"], REPEATED)
    expected = {"scale": scale, "seed": seed,
                "documents": {doc.name: {"numbers": doc.numbers, "panels": doc.panels, "cited": sorted(doc.cited),
                                         "figures": doc.counters["fig"], "tables": doc.counters["tbl"]}
                              for doc in (main, supp)},
                "across": main.across, "abbreviations": sorted(ctx.abbreviations), "comments": comments}
    (root / EXPECTED).write_text(json.dumps(expected, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return expected


class Figures:
    """Figure images: one PNG per figure, and one diagram per `diagram()`."""

    def __init__(self, root: Path, rng, image: tuple[int, int]):
        self.root, self.rng, self.image, self.count = root, rng, image, 0

    def png(self, name: str) -> str:
        self.count += 1
        path = self.root / "figures/output/png" / f"{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(assets.png(*self.image, seed=self.count))
        return f"figures/output/png/{name}.png"

    def diagram(self, name: str) -> str:
        assets.diagram(self.root, name, self.rng, [self.rng.choice(FOREIGN) for _ in range(3)])
        return f"figures/external/{name}.png"


def body(doc: Doc, root: Path, figures: Figures, paragraphs: int, counts: dict, label: str,
         include: str, diagrams: int = 0) -> None:
    """Prose with figures, tables and equations spread evenly through it."""
    rng = doc.ctx.rng
    total = paragraphs
    slots = {"fig": counts["figures"], "tbl": counts["tables"], "eq": counts["equations"], "dia": diagrams}
    made = {key: 0 for key in slots}
    for index in range(total):
        if index and index % 12 == 0:
            doc.heading(rng.choice([2, 3]), rng.choice(["Results", "Methods", "Discussion", "Sensitivity análisis",
                                                        "Données *et* méthodes"]))
        doc.paragraph(rng.randint(3, 7))
        for key, wanted in slots.items():
            while made[key] < wanted * (index + 1) // total:
                made[key] += 1
                name = f"{label}{key}{made[key]:03d}"
                if key == "fig":
                    panels = rng.choice([0, 0, 0, 2, 3, 6]) if made[key] % 4 == 0 else 0
                    doc.figure(name, figures.png(name), panels, inline_panels=panels == 2)
                elif key == "dia":
                    doc.figure(name, figures.diagram(name))
                elif key == "eq":
                    doc.equation(name)
                elif made[key] % 5 == 0:
                    doc.markdown_table(name)
                else:
                    doc.spec_table(root, name, TABLE_KINDS[made[key] % len(TABLE_KINDS)], include)


def supplementary(root: Path, ctx: Context, size: dict, figures: Figures) -> Doc:
    doc = Doc(ctx, "supplementary", "S", region="supplementary")
    doc.file("manuscript/supplementary/S1_methods.md")
    doc.heading(1, "Supplementary methods", "{#supp-methods}")
    counts = {"figures": size["supp_figures"], "tables": size["supp_tables"], "equations": size["supp_equations"]}
    body(doc, root, figures, max(3, size["supp_words"] // 85), counts, "s", "../../tables/output/md")
    doc.file("manuscript/supplementary/S2_checklist.md")
    doc.heading(1, "Reporting checklist")
    doc.markdown_table("s-checklist")
    doc.paragraph(3)
    doc.figure("s-panels", figures.png("s-panels"), panels=3)  # one at any scale
    return doc


def manuscript(root: Path, ctx: Context, size: dict, figures: Figures, supp: Doc) -> Doc:
    doc = Doc(ctx, "manuscript", "", region="abstract")
    doc.file("manuscript/main/00_abstract.md")
    doc.heading(1, "Abstract")
    for part in ("Background", "Methods", "Results", "Conclusions"):
        doc.add(f"**{part}.** " + " ".join(doc.sentence() for _ in range(2)) + "\n")
    doc.paragraph(3, refs=False)
    doc.file("manuscript/main/00b_key_points.md")
    doc.heading(1, "Key points")
    doc.add("\n".join(f"- {doc.sentence((6, 12))}" for _ in range(3)) + "\n")
    doc.region = "main"
    chapters = size["chapters"]
    paragraphs = max(chapters * 3, size["words"] // 85)
    names = ["Introduction", "Background", "Methods", "Results", "Validation", "Applications",
             "Limitations", "Discussion"]
    for number in range(1, chapters + 1):
        doc.file(f"manuscript/main/{number:02d}_chapter.md")
        title = names[(number - 1) % len(names)]
        doc.heading(1, title if number <= len(names) else f"{title} {number}", f"{{#ch-{number}}}")
        share = {key: (size[key] * number // chapters) - (size[key] * (number - 1) // chapters)
                 for key in ("figures", "tables", "equations")}
        dias = (size["diagrams"] * number // chapters) - (size["diagrams"] * (number - 1) // chapters)
        body(doc, root, figures, paragraphs // chapters, share, f"c{number}", "tables/output/md", dias)
        if number == 1:
            unused = [term for term in ctx.abbreviations if term not in ctx.defined.get("main", set())]
            if unused:
                doc.add(" ".join(f"{ctx.abbreviations[term]} ({term})." for term in unused) + "\n")
                ctx.defined.setdefault("main", set()).update(unused)
    edges(doc, supp, figures)
    doc.file("manuscript/main/99_conclusion.md")
    doc.heading(1, "Conclusion")
    doc.paragraph(4)
    doc.file("manuscript/main/00c_abbreviations.md")
    doc.add("# Abbreviations {.uncounted}\n")
    doc.add("\n".join(f"{term}\n:   {meaning.capitalize()}\n" for term, meaning in ctx.abbreviations.items()))
    write_values(root, ctx)
    return doc


def edges(doc: Doc, supp: Doc, figures: Figures) -> None:
    """The fixed chapter of edge cases, with its labels numbered after the chapters'."""
    ctx = doc.ctx
    text = EDGES.read_text(encoding="utf-8")
    for label, number in supp.numbers.items():  # the workaround for manuscript -> supplementary
        kind, name = label.split(":")
        ctx.fixed_value(f"num.supp.{kind}.{name}", number)
    first_supp = next(label for label in supp.numbers if label.startswith("fig:"))
    text = text.replace("@@SUPP_FIG@@", f"{{{{v:num.supp.fig.{first_supp.split(':')[1]}}}}}")
    text = text.replace("@@SUPP_LABEL@@", first_supp)
    doc.across = {first_supp: supp.numbers[first_supp]}
    # Number the labels in reading order, includes expanded.
    nested = EDGE_INCLUDES["box.md"].replace("!include table_box.md", EDGE_INCLUDES["table_box.md"])
    expanded = text.replace("!include ../shared/box.md", nested)
    for label in sorted(re_labels(expanded), key=lambda label: expanded.index("{#" + label)):
        doc.numbers[label] = doc.number(label.split(":")[0])
    ctx.fixed_value("edge.count", "1,234")
    ctx.values["edge.score"] = {"value": {"mean": 0.5, "sd": 0.25}, "text": "0.50"}
    doc.file("manuscript/main/90_edge_cases.md")
    doc.add(text)
    figures.png("edge late figure")
    for name, content in EDGE_INCLUDES.items():
        doc.files[f"manuscript/shared/{name}"] = [content]
    doc.paragraphs.extend(EDGE_PARAGRAPHS)
    doc.cited.update(re_keys(text) | re_keys("".join(EDGE_INCLUDES.values())))


def re_labels(text: str) -> list[str]:
    return re.findall(r"\{#((?:fig|tbl|eq):[\w-]+)", text)


def re_keys(text: str) -> set[str]:
    return {key for key in re.findall(r"(?<![\w.])-?@([\w][\w:.#$%&\-+?<>~/]*\w)", text)
            if not key.startswith(("fig:", "tbl:", "eq:", "sec:"))}


# Files the edge chapter includes (nested: box.md includes a table).
EDGE_INCLUDES = {
    "box.md": "Boxed text from an included file with {{v:edge.count}} items.\n\n!include table_box.md\n",
    "table_box.md": "Table: [Nested include.]{.caption-title} A table two includes deep. {#tbl:edge-nested}\n\n"
                    "| Key | Value |\n|-----|------:|\n| ö | 1 |\n| 東京 | 2 |\n",
}
REPEATED = "The same sentence appears in several paragraphs of this chapter."
EDGE_PARAGRAPHS = [
    {"doc": "manuscript", "file": "manuscript/main/90_edge_cases.md", "section": "Edge cases",
     "section_id": "edge-cases", "para": f"Repeated text ¶{n}", "plain": [REPEATED], "repeat": n}
    for n in (1, 2, 3)]


def write_values(root: Path, ctx: Context) -> None:
    payload = {"schema_version": 1, "generator": "stress_fixture.py", "inputs": {}, "values": ctx.values}
    (root / "data/values.json").write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stress_fixture.py", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["create", "bench"])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--image", default="1200x900", help="figure size in pixels, WxH")
    parser.add_argument("--no-diagrams", action="store_true", help="no diagram figures (exporting them needs Chrome)")
    parser.add_argument("--json", action="store_true", help="bench: print the timings as JSON")
    args = parser.parse_args(argv)
    image = tuple(int(n) for n in args.image.lower().split("x"))
    if args.command == "create":
        create(args.folder, args.scale, args.seed, image, not args.no_diagrams)
        print(f"Created {args.folder} (scale {args.scale})")
        return 0
    from _stress_bench import bench
    return bench(args.folder, lambda: create(args.folder, args.scale, args.seed, image, not args.no_diagrams), args.json)


if __name__ == "__main__":
    sys.exit(main())
