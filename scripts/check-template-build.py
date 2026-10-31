#!/usr/bin/env python3
"""Exercise a rendered project's builds through scripts/trazo, as the
desktop apps run them, and a content-independent verify.

The Word files come from `trazo build` (one document with `--doc`), and
values, tables and figures from `trazo build --prepare=inputs`, which the
preview and the checks run; trazo decides what is fresh. The counts below
come from trazo's `[build] ...` lines."""

from pathlib import Path
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile


def check_layout(project: Path) -> None:
    readme = (project / "README.md").read_text()
    first = readme.index("| Skill |")
    last = readme.index('| `audit-ai-writing`', first)
    assert all(line.startswith("|") for line in readme[first:last].splitlines()), "split skills table"
    tree = readme.split("```text\n", 1)[1].split("```", 1)[0]
    assert all(tree.splitlines()), "blank line in structure tree"
    for name in ("README.md", "trazo.toml", "manuscript/metadata.yaml"):
        text = (project / name).read_text()
        assert "\n\n\n" not in text, f"extra blank lines in {name}"
    metadata = (project / "manuscript/metadata.yaml").read_text()
    abbreviations = sorted((project / "manuscript").rglob("*abbreviations*.md"))
    report = '\nkind = "report"\n' in (project / "trazo.toml").read_text()
    expected = [] if report else [project / "manuscript/main/00c_abbreviations.md"]
    assert abbreviations == expected, abbreviations
    author = metadata.split("author:\n", 1)[1].split("affiliations:\n", 1)[0]
    assert "\n\n    " not in author, "blank line inside author map"
    print(f"Template layout checks passed: {project}")


def check_title(project: Path, title: str) -> None:
    for name in ("manuscript", "cover_letter"):
        with zipfile.ZipFile(project / f"output/{name}.docx") as archive:
            text = "".join(ET.fromstring(archive.read("word/document.xml")).itertext())
        text = text.translate(str.maketrans({"“": '"', "”": '"'}))
        assert title in text, f"title not preserved in {name}: {text}"
    print("Literal title preserved in manuscript and cover letter Word output")
    check_abbreviations(project, example=False)


def check_abbreviations(project: Path, example: bool) -> None:
    with zipfile.ZipFile(project / "output/manuscript.docx") as archive:
        document = ET.fromstring(archive.read("word/document.xml"))
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    headings = ["".join(paragraph.itertext()) for paragraph in document.findall(".//w:body/w:p", namespace)
                if paragraph.find("w:pPr/w:pStyle", namespace) is not None]
    tables = [table for table in document.findall(".//w:tbl", namespace)
              if table.find('w:tblPr/w:tblStyle[@w:val="Abbreviations"]', namespace) is not None]
    if example:
        assert "Abbreviations" in headings, "example article has no Abbreviations heading"
        assert len(tables) == 1, "example article must have one abbreviations table"
        cells = ["".join(cell.itertext()) for cell in tables[0].findall("w:tr/w:tc", namespace)]
        assert cells == ["Abbreviation", "Meaning", "AI", "Artificial intelligence", "SD", "Standard deviation"], cells
    else:
        assert "Abbreviations" not in headings, "blank article has an empty Abbreviations heading"
        assert not tables, "blank article has an empty abbreviations table"
    print(f"Abbreviations Word output verified: {'example table' if example else 'blank section omitted'}")


def check(project: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="trazo-build-") as folder:
        root = Path(folder) / "project"
        shutil.copytree(project, root, ignore=shutil.ignore_patterns(".git", ".venv", ".build", "output"))

        def run(command: list, env=None) -> str:
            result = subprocess.run(command, cwd=root, env={**os.environ, **(env or {})},
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            if result.returncode:
                raise AssertionError(result.stdout)
            return result.stdout

        logged = {"text": ""}

        def build(*argv: str, env=None) -> None:
            logged["text"] = run(["scripts/trazo", *argv], env)

        def ran(label: str) -> int:
            return re.findall(r"^\[build\] (.+)$", logged["text"], re.M).count(label)

        def producers(generate: int, render: int) -> None:
            assert (ran("tables: generate"), ran("tables: render")) == (generate, render), logged["text"]

        def tables(generate: int, render: int) -> None:
            build("build", "--prepare=inputs")
            producers(generate, render)

        second = root / "tables/specs/second.json"
        spec = json.loads((root / "tables/specs/summary.json").read_text())
        spec["title"] = "Second presentation of the same data."
        second.write_text(json.dumps(spec))
        outputs = [root / f"tables/output/md/{name}.md" for name in ("summary", "second")]
        for path in outputs:
            path.unlink(missing_ok=True)
        tables(1, 1)
        before = [path.stat().st_mtime_ns for path in outputs]
        tables(0, 0)
        assert before == [path.stat().st_mtime_ns for path in outputs]

        # Missing presentation outputs rerender together, without regenerating data.
        outputs[0].unlink()
        tables(0, 1)
        assert outputs[0].is_file()
        tables(0, 0)

        # Presentation edits do not run the numerical generator.
        spec["title"] = "Revised presentation."
        second.write_text(json.dumps(spec))
        tables(0, 1)
        assert "Revised presentation." in outputs[1].read_text()

        # Missing data is regenerated; the same numbers need no new render.
        # Changed inputs run both producers exactly once.
        (root / "tables/output/data/summary.json").unlink()
        tables(1, 0)
        assert (root / "tables/output/data/summary.json").is_file()
        data = root / "data/example.csv"
        scores = data.read_text()
        data.write_text(scores.replace("P16,external,B,follow-up,0.72", "P16,external,B,follow-up,0.74"))
        tables(1, 1)
        tables(0, 0)
        os.utime(data, None)  # hashes, not timestamps: a touched input runs nothing
        tables(0, 0)

        # Both documents depend on table outputs; supplementary numbering also
        # comes from the same trazo metadata used by previews and exports.
        supplementary_source = root / "manuscript/supplementary/S9_build_check.md"
        supplementary_source.write_text(
            "# Supplementary build check\n\n"
            "![Supplementary figure.](figures/output/png/fig1_trend.png){#fig:supp-build}\n\n"
            "!include tables/output/md/summary.md\n\n"
            "See [@fig:supp-build] and [@tbl:summary].\n")
        build("build")
        check_abbreviations(root, example=True)
        supplementary = root / ".build/supplementary.md"
        supplementary_docx = root / "output/supplementary.docx"
        previous_supplementary = supplementary.stat().st_mtime_ns
        previous_supplementary_docx = supplementary_docx.stat().st_mtime_ns
        with zipfile.ZipFile(supplementary_docx) as archive:
            text = "".join(ET.fromstring(archive.read("word/document.xml")).itertext())
        assert "Figure S1" in text and "Table S1" in text, text

        metadata = root / "manuscript/metadata.yaml"
        metadata.write_text(metadata.read_text().replace("First Author", "Revised Author"))
        build("build", "--doc", "manuscript")
        with zipfile.ZipFile(root / "output/manuscript.docx") as archive:
            document = ET.fromstring(archive.read("word/document.xml"))
        namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        authors = ["".join(paragraph.itertext()) for paragraph in document.findall(".//w:p", namespace)
                   if paragraph.find('w:pPr/w:pStyle[@w:val="Author"]', namespace) is not None]
        assert any("Revised Author" in author for author in authors), authors

        declaration_source = root / "manuscript/declarations/declarations.md"
        declaration_source.write_text(declaration_source.read_text() + "\nUpdated declaration for the journal.\n")
        build("build", "--doc", "manuscript")
        with zipfile.ZipFile(root / "output/manuscript.docx") as archive:
            text = "".join(ET.fromstring(archive.read("word/document.xml")).itertext())
        assert "Updated declaration for the journal." in text, "declaration edit did not rebuild the manuscript"

        # Removing a source must invalidate expansion, even without a newer input.
        for directory, name in (("declarations", "manuscript"), ("main", "manuscript"),
                                ("supplementary", "supplementary")):
            removed = root / f"manuscript/{directory}/99_removal.md"
            sentinel = f"Unique removed {directory} paragraph."
            removed.write_text(f"# Removal check\n\n{sentinel}\n")
            build("build", "--doc", name)
            output = root / f"output/{name}.docx"
            with zipfile.ZipFile(output) as archive:
                assert sentinel in "".join(ET.fromstring(archive.read("word/document.xml")).itertext())
            removed.unlink()
            build("build", "--doc", name)
            with zipfile.ZipFile(output) as archive:
                assert sentinel not in "".join(ET.fromstring(archive.read("word/document.xml")).itertext()), \
                    f"deleted {directory} source survived in Word"
            before = output.stat().st_mtime_ns
            build("build", "--doc", name)
            assert output.stat().st_mtime_ns == before, "unchanged source list rebuilt Word"
            assert not ran(f"expand: {name}") and ran("up to date") == 1, logged["text"]

        # A data update reaches each document in the same trazo build.
        manuscript = root / ".build/manuscript.md"
        previous = manuscript.stat().st_mtime_ns
        data.write_text(scores)
        build("build")
        producers(1, 1)
        assert manuscript.stat().st_mtime_ns > previous
        assert supplementary.stat().st_mtime_ns > previous_supplementary
        assert supplementary_docx.stat().st_mtime_ns > previous_supplementary_docx
        supplementary_source.unlink()

        # Diagram sources and every export format participate in the DOCX build.
        diagram = root / "figures/diagrams/fig2_workflow.json"
        original_diagram = diagram.read_bytes()
        png = root / "figures/external/fig2_workflow.png"
        svg = png.with_suffix(".svg")
        docx = root / "output/manuscript.docx"
        previous_png, previous_docx = png.stat().st_mtime_ns, docx.stat().st_mtime_ns
        build("build", "--doc", "manuscript")
        assert (png.stat().st_mtime_ns, docx.stat().st_mtime_ns) == (previous_png, previous_docx)
        content = json.loads(original_diagram)
        content["canvas"]["height_mm"] += 1
        diagram.write_text(json.dumps(content))
        build("build", "--doc", "manuscript")
        assert png.stat().st_mtime_ns > previous_png, "diagram JSON did not reach its PNG"
        assert docx.stat().st_mtime_ns > previous_docx, "updated diagram did not reach the DOCX"
        svg.unlink()
        build("build", "--doc", "manuscript")
        assert svg.is_file(), "missing SVG was not repaired when PNG existed"
        before_draft = png.read_bytes()
        content["meta"] = {**content.get("meta", {}), "draft": True}
        diagram.write_text(json.dumps(content))
        build("build", "--doc", "manuscript")
        assert png.read_bytes() == before_draft, "a draft replaced the manuscript figure"
        diagram.write_bytes(original_diagram)
        build("build", "--doc", "manuscript")

        # Without Chrome a stale diagram stops the Word build, not the preview
        # or the counts (they run --prepare=inputs: no export).
        content = json.loads(original_diagram)
        content["canvas"]["height_mm"] += 2
        diagram.write_text(json.dumps(content))
        no_chrome = {"TRAZO_CHROME": "/nonexistent"}
        for argv in (["build", "--prepare=inputs"], ["counts"], ["preview", "build"]):
            build(*argv, env=no_chrome)
        assert list((root / ".build/preview").glob("*.html")), "preview build wrote no page"
        failed = subprocess.run(["scripts/trazo", "build"], cwd=root,
                                env={**os.environ, **no_chrome}, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        assert failed.returncode and "Chrome not found (/nonexistent)" in failed.stdout, \
            failed.stdout
        diagram.write_bytes(original_diagram)
        build("build", "--doc", "manuscript")

        # Full workspace builds must generate missing/stale inputs before export.
        content = json.loads(original_diagram)
        content["nodes"] = [content["nodes"][0], {
            "id": "image", "kind": "image", "x": 48, "y": 4, "w": 38, "h": 14,
            "src": "figures/output/png/fig1_trend.png"}]
        content["nodes"][0]["text"] = "Cohort {{v:example.n}}"
        content["edges"] = []
        diagram.write_text(json.dumps(content))
        figure_script = root / "figures/scripts/fig1_trend.py"
        figure = root / "figures/output/png/fig1_trend.png"
        original_data, original_script = data.read_bytes(), figure_script.read_bytes()

        def exported_inputs(count: int) -> None:
            assert f"Cohort {count}" in svg.read_text(), "diagram has outdated values"
            image = base64.b64encode(figure.read_bytes()).decode()
            assert image in svg.read_text(), "diagram has an outdated generated image"
            with zipfile.ZipFile(docx) as archive:
                assert any(archive.read(name) == png.read_bytes() for name in archive.namelist()
                           if name.startswith("word/media/")), "DOCX has an outdated diagram"

        (root / "data/values.json").unlink()
        figure.unlink()
        run(["scripts/trazo", "build"])
        exported_inputs(16)
        data.write_bytes(original_data + b"P17,external,B,baseline,0.72\n")
        figure_script.write_text(original_script.decode().replace("0.79", "0.09"))
        run(["scripts/trazo", "build"])
        exported_inputs(17)

        # The build must also order image generation before diagram export:
        # a slow producer exposes a missing edge.
        figure_script.write_text(original_script.decode().replace(
            "canvas.save(", "import time\ntime.sleep(2)\ncanvas.save("))
        build("build", "--doc", "manuscript")
        exported_inputs(17)
        # The changed figure script runs once, and the next build finds it
        # fresh (.build/steps.json).
        assert ran("step fig1_trend") == 1, logged["text"]
        assert "step fig1_trend" not in run(["scripts/trazo", "build"])
        previous_png, previous_docx = png.stat().st_mtime_ns, docx.stat().st_mtime_ns
        build("build", "--doc", "manuscript")
        assert (png.stat().st_mtime_ns, docx.stat().st_mtime_ns) == (previous_png, previous_docx)
        data.write_bytes(original_data)
        figure_script.write_bytes(original_script)
        diagram.write_bytes(original_diagram)
        build("build", "--doc", "manuscript")

        settings = root / "trazo.toml"
        original_settings = settings.read_text()

        # Cross-references are trazo's own: a pandoc-crossref that fails is never run.
        shim = Path(folder) / "no-crossref"
        shim.mkdir()
        (shim / "pandoc-crossref").write_text("#!/bin/sh\necho 'pandoc-crossref was run' >&2\nexit 97\n")
        (shim / "pandoc-crossref").chmod(0o755)
        for name in ("manuscript", "supplementary"):
            (root / f"output/{name}.docx").unlink()
        build("build", env={"PATH": f"{shim}{os.pathsep}{os.environ['PATH']}"})
        assert (ran("docx: manuscript"), ran("docx: supplementary")) == (1, 1), logged["text"]
        settings.write_text(original_settings + '\n[submission]\ntitle_page = "separate"\ndeclarations = "separate"\n')
        build("build", "--submission")

        def word_text(name):
            with zipfile.ZipFile(root / f"output/{name}.docx") as archive:
                return "".join(ET.fromstring(archive.read("word/document.xml")).itertext())

        title_page = word_text("title_page")
        assert all(term in title_page for term in ("Revised Author", "Example University", "ORCID:",
                                                   "0000-0002-1825-0097")), title_page
        declarations = word_text("declarations")
        assert "Declarations" in declarations and "Consent for publication" in declarations, declarations
        paper_title = json.loads(subprocess.run(
            ["pandoc", "/dev/null", f"--metadata-file={metadata}", "-t", "json"],
            check=True, capture_output=True, text=True).stdout)["meta"]["title"]
        assert "Dear Editor" in word_text("cover_letter")
        assert "Revised Author" not in word_text("manuscript")
        assert "Declarations" not in word_text("manuscript")
        assert any(paragraph in word_text("cover_letter") for paragraph in
                       [" ".join(item.get("c", "") for item in paper_title["c"] if item["t"] == "Str")])
        settings.write_text(original_settings)

        # An after-docx step (DOCX post-processing) runs in a one-document build too.
        (root / "scripts/mark_docx.py").write_text(
            "import sys\nfrom pathlib import Path\n"
            "with open('marks.log', 'a') as log:\n    log.write(Path(sys.argv[1]).name + '\\n')\n")
        settings.write_text(original_settings + '\n[[build.steps]]\nname = "mark"\n'
                            'run = ["scripts/mark_docx.py", "{docx}"]\nafter = "docx"\ndocuments = ["manuscript"]\n')
        build("build", "--doc", "manuscript")
        assert ran("step mark: manuscript") == 1, logged["text"]
        assert (root / "marks.log").read_text() == "manuscript.docx\n"
        settings.write_text(original_settings)

        shutil.rmtree(root / "output")

        # Customized papers need no sample words, citations, figures or table merges.
        for path in (root / "manuscript/main").glob("*.md"):
            path.unlink()
        (root / "manuscript/main/report.md").write_text("# Findings\n\nA customized report.\n")
        settings.write_text(original_settings + '\n[submission]\noutput = "deliverables"\n'
                            '\n[documents.manuscript]\npath = "manuscript/main"\ndocx = "deliverables/manuscript.docx"\n'
                            '\n[documents.supplementary]\npath = "manuscript/supplementary"\nnumber_prefix = "S"\n'
                            'docx = "deliverables/supplementary.docx"\n')
        build("verify")
        assert (root / "deliverables/manuscript.docx").is_file()
        assert (root / "deliverables/supplementary.docx").is_file()
        assert not (root / "output").exists()
        print("Template build checks passed: producers run once, missing outputs, no-op rebuilds, "
              "presentation edits, metadata author rebuild, downstream rebuild, diagram freshness and drafts, "
              "no Chrome for the preview and counts, workspace input generation, embedded figure ordering, "
              "one run of a changed figure script, no pandoc-crossref run, after-docx steps, "
              "customized verify and output paths")


if __name__ == "__main__":
    if sys.argv[1] == "--layout":
        check_layout(Path(sys.argv[2]).resolve())
    elif sys.argv[1] == "--title":
        check_title(Path(sys.argv[2]).resolve(), sys.argv[3])
    else:
        check(Path(sys.argv[1]).resolve())
