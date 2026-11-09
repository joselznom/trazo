"""Inputs edited while a build runs: what it publishes comes from one state of
its inputs, its records describe that state, and the next build catches up."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import _support
from test_build_graph import png

from trazo import build_graph, diagram, project
from trazo.project import PACKAGE_DIR

# Reads its input, writes the output, then edits the input as a concurrent
# writer would (once: the next run leaves it alone).
LATE = """from pathlib import Path
text = Path("input.txt").read_text()
Path("out.txt").write_text(text)
if text == "1":
    Path("input.txt").write_text("2")
"""

# Pandoc, except that a writer replaces a file with edit.bin as the Word
# file's first stage starts: after trazo hashed it, before Pandoc reads it
# (other Pandoc runs, such as reading the metadata, pass through). "once"
# edits during the first Word file only, "always" on every attempt (each
# time with different bytes).
SHIM = """import os, sys
from pathlib import Path
real, mode, target, *args = sys.argv[1:]
word = "--to=json" in args and any(arg.startswith("--bibliography") for arg in args)
if word and (mode == "always" or not Path("edited").exists()):
    count = int(Path("edited").read_text()) + 1 if Path("edited").exists() else 1
    data = Path("edit.bin").read_bytes()
    Path(target + ".tmp").write_bytes(data if count == 1 else data + b"\\n%% edit %d\\n" % count)
    os.replace(target + ".tmp", target)
    Path("edited").write_text(str(count))
os.execv(real, [real, *args]) if os.name != "nt" else sys.exit(__import__("subprocess").call([real, *args]))
"""

FIGURE = "figures/plot.png"


def run(root, *args, env=None):
    env = {**os.environ, **(env or {}), "TRAZO_ROOT": str(root), "PYTHONPATH": str(PACKAGE_DIR.parent)}
    return subprocess.run([sys.executable, "-m", "trazo", *args], cwd=root, env=env,
                          capture_output=True, text=True, timeout=180)


class StepInputs(unittest.TestCase):
    def test_an_input_edited_while_its_step_runs_is_built_again(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        (root / "late.py").write_text(LATE)
        (root / "input.txt").write_text("1")
        steps = [{"name": "late", "run": ["late.py"], "inputs": ["input.txt"], "outputs": ["out.txt"]}]
        config = project.merge(project.DEFAULTS, {"build": {"steps": steps, "env": {}}})

        def build():
            build_graph.Builder(project.Project(root, config, True), log=lambda text: None).slot("expand")
        with self.assertRaisesRegex(build_graph.BuildError, "input.txt changed while it ran"):
            build()
        build()  # catches up with the edit
        self.assertEqual((root / "out.txt").read_text(), "2")


@unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
class WordInputs(unittest.TestCase):
    def build(self, root, expect=0):
        result = run(root, "build")
        self.assertEqual(result.returncode, expect, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def project(self, folder, mode, target, edited):
        root = Path(folder)
        shutil.copytree(_support.FIXTURE, root, dirs_exist_ok=True)
        shutil.rmtree(root / "figures/diagrams")  # exporting needs Chrome
        (root / FIGURE).write_bytes(png(10))
        with (root / "manuscript/main/01_introduction.md").open("a") as handle:
            handle.write(f"\n![A plot.]({FIGURE}){{#fig:plot}}\n")
        (root / "shim.py").write_text(SHIM)
        (root / "edit.bin").write_bytes(edited)
        pandoc = [sys.executable, str(root / "shim.py"), shutil.which("pandoc"), mode, target]
        (root / "trazo.toml").write_text(f'version = "v0.0.0"\n[commands]\npandoc = {json.dumps(pandoc)}\n')
        return root

    @staticmethod
    def word(root):
        """The manuscript's text and its embedded images."""
        with zipfile.ZipFile(root / "output/manuscript.docx") as archive:
            text = re.sub(r"<[^>]+>", "", archive.read("word/document.xml").decode())
            return text, [archive.read(n) for n in archive.namelist() if n.startswith("word/media/")]

    def edits(self):
        """Each kind of Word input, its edited bytes, and whether the Word file shows them."""
        bib = (_support.FIXTURE / "bibliography/references.bib").read_text()
        meta = (_support.FIXTURE / "manuscript/metadata.yaml").read_text()
        return [("bibliography/references.bib", bib.replace("{A study}", "{Revised study}").encode(),
                 lambda text, media: "Revised study" in text),
                ("manuscript/metadata.yaml", meta.replace("Fixture paper", "Revised paper").encode(),
                 lambda text, media: "Revised paper" in text),
                (FIGURE, png(200), lambda text, media: png(200) in media)]

    def test_a_word_file_is_published_only_for_inputs_that_held_still(self):
        for target, edited, shows in self.edits():
            with self.subTest(target), tempfile.TemporaryDirectory() as folder:
                root = self.project(folder, "once", target, edited)
                original = (root / target).read_bytes()
                first = self.build(root)
                self.assertTrue(shows(*self.word(root)), first)
                # The edit is undone: the stamp must not claim the edited state.
                (root / target).write_bytes(original)
                self.assertIn("[build] docx: manuscript", self.build(root))
                self.assertFalse(shows(*self.word(root)))
                self.assertIn("changed during the build; building again", first)

    def test_inputs_that_keep_changing_fail_and_keep_the_earlier_word_file(self):
        target, edited, _ = self.edits()[0]
        with tempfile.TemporaryDirectory() as folder:
            root = self.project(folder, "once", target, edited)
            self.build(root)
            earlier = (root / "output/manuscript.docx").read_bytes()
            (root / "edited").unlink()
            config = root / "trazo.toml"
            config.write_text(config.read_text().replace('"once"', '"always"'))
            self.assertIn("changed during the build 2 times", self.build(root, expect=1))
            self.assertEqual((root / "output/manuscript.docx").read_bytes(), earlier)
            config.write_text(config.read_text().replace('"always"', '"once"'))  # the edits stop
            self.assertIn("[build] docx: manuscript", self.build(root))


@unittest.skipUnless(diagram.CHROME and Path(diagram.CHROME).exists(), "needs Chrome")
class DiagramInputs(unittest.TestCase):
    def test_a_diagram_edited_while_the_export_runs_is_exported_again(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(_support.FIXTURE, root, dirs_exist_ok=True)
            diagrams = root / "figures/diagrams"
            for name in ("a1", "a2"):  # exported in this order
                shutil.copy(diagrams / "example.json", diagrams / f"{name}.json")
            (diagrams / "example.json").unlink()
            first = diagrams / "a1.json"
            env = {**os.environ, "TRAZO_ROOT": str(root), "PYTHONPATH": str(PACKAGE_DIR.parent),
                   "PYTHONUNBUFFERED": "1"}
            output = []
            with subprocess.Popen([sys.executable, "-m", "trazo", "run", "diagrams"], cwd=root, env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as proc:
                for line in proc.stdout:
                    output.append(line)
                    if line.startswith("exported figures/external/a1.svg"):
                        # A save from the editor once a1 is exported, while a2 renders.
                        first.write_text(first.read_text().replace('"Input"', '"Inputs"'))
                proc.wait(timeout=180)
            self.assertIn("exported figures/external/a2.svg", "".join(output))
            again = run(root, "run", "diagrams")
            self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
            check = run(root, "diagram", "check", "a1", "--exports")
            self.assertEqual(check.returncode, 0, "".join(output) + again.stdout + check.stdout + check.stderr)


if __name__ == "__main__":
    unittest.main()
