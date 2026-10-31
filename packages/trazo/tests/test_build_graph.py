"""trazo build without Make: step order, freshness, slots and the full build."""

import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path
from unittest.mock import patch

import _support

from trazo import build_graph, project
from trazo.project import PACKAGE_DIR

# A step script: appends its name to runs.log, then writes each output with
# the value of $MODE (or "none").
STEP = """import os, sys
from pathlib import Path
name, *outputs = sys.argv[1:]
with open("runs.log", "a") as log:
    log.write(name + "\\n")
for output in outputs:
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(os.environ.get("MODE", "none") + Path("input.txt").read_text())
"""


def step(name, *outputs, **keys):
    return {"name": name, "run": ["step.py", name, *outputs], "outputs": list(outputs), **keys}


class Steps(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / "step.py").write_text(STEP)
        (self.root / "input.txt").write_text("1")

    def project(self, steps=(), env=None, commands=None):
        data = {"build": {"steps": list(steps), "env": env or {}}, "commands": commands or {}}
        return project.Project(self.root, project.merge(project.DEFAULTS, data), True)

    def builder(self, *steps, **options):
        env = options.pop("env", None)
        commands = options.pop("commands", None)
        return build_graph.Builder(self.project(steps, env, commands), log=lambda text: None, **options)

    def runs(self):
        log = self.root / "runs.log"
        names = log.read_text().split() if log.exists() else []
        log.unlink(missing_ok=True)
        return names

    def test_order_follows_inputs_then_declaration(self):
        steps = project.build_steps([
            {"name": "plot", "run": ["p.py"], "inputs": ["data/*.csv"], "outputs": ["fig.png"]},
            {"name": "data", "run": ["d.py"], "outputs": ["data/derived.csv"]},
            {"name": "other", "run": ["o.py"]},
        ])
        self.assertEqual([s["name"] for s in build_graph.order(steps)], ["data", "plot", "other"])
        cycle = project.build_steps([
            {"name": "a", "run": ["a.py"], "inputs": ["b.txt"], "outputs": ["a.txt"]},
            {"name": "b", "run": ["b.py"], "inputs": ["a.txt"], "outputs": ["b.txt"]},
        ])
        with self.assertRaisesRegex(build_graph.BuildError, "cycle between a, b"):
            build_graph.order(cycle)

    def test_fresh_steps_are_skipped_and_missing_outputs_rerun(self):
        steps = [step("one", "out/one.txt", inputs=["input.txt"]), step("two", "out/two.txt")]
        self.builder(*steps).slot("expand")
        self.assertEqual(self.runs(), ["one", "two"])
        builder = self.builder(*steps)
        builder.slot("expand")
        self.assertEqual((self.runs(), builder.ran), ([], []))
        (self.root / "out/two.txt").unlink()
        self.builder(*steps).slot("expand")
        self.assertEqual(self.runs(), ["two"])
        (self.root / "out/one.txt").write_text("")  # empty counts as missing
        self.builder(*steps).slot("expand")
        self.assertEqual(self.runs(), ["one"])
        (self.root / "input.txt").write_text("2")  # declared input
        self.builder(*steps).slot("expand")
        self.assertEqual(self.runs(), ["one"])
        (self.root / "step.py").write_text(STEP + "\n")  # the script always counts
        self.builder(*steps).slot("expand")
        self.assertEqual(self.runs(), ["one", "two"])
        self.builder(*steps, force=True).slot("expand")
        self.assertEqual(self.runs(), ["one", "two"])
        state = json.loads((self.root / ".build/steps.json").read_text())
        self.assertEqual(state["step:one"]["outputs"], ["out/one.txt"])

    def test_a_step_that_does_not_write_its_output_fails(self):
        broken = step("broken", "out/a.txt")
        broken["outputs"].append("out/never.txt")
        with self.assertRaisesRegex(build_graph.BuildError, "did not write out/never.txt"):
            self.builder(broken).slot("expand")
        failing = {"name": "fails", "run": ["-c", "raise SystemExit(3)"]}
        with self.assertRaisesRegex(build_graph.BuildError, "exit 3"):
            self.builder(failing).slot("expand")

    def test_env_is_passed_and_hashed(self):
        steps = [step("one", "out.txt")]
        self.builder(*steps, env={"MODE": "canonical"}).slot("expand")
        self.assertEqual((self.root / "out.txt").read_text(), "canonical1")
        self.builder(*steps, env={"MODE": "canonical"}).slot("expand")
        self.assertEqual(self.runs(), ["one"])
        builder = build_graph.Builder(self.project(steps, {"MODE": "canonical"}), env={"MODE": "draft"},
                                      log=lambda text: None)
        builder.slot("expand")
        self.assertEqual(self.runs(), ["one"])
        self.assertEqual((self.root / "out.txt").read_text(), "draft1")

    def test_slots_and_when(self):
        steps = [step("late", "late.txt"), step("early", "early.txt", before="values"),
                 step("figure", "figure.txt", before="diagrams"),
                 step("deliverable", "pptx.txt", when="submission"),
                 step("import", "import.txt", when="manual")]
        builder = self.builder(*steps)
        for slot in project.STEP_SLOTS:
            builder.slot(slot)
        self.assertEqual(self.runs(), ["early", "figure", "late"])
        builder = self.builder(*steps, submission=True)
        builder.slot("expand")
        self.assertEqual(self.runs(), ["deliverable"])
        manual = self.builder(*steps)
        manual.step(manual.named("import"))
        self.assertEqual(self.runs(), ["import"])

    def test_a_values_step_replaces_the_stage(self):
        (self.root / "scripts").mkdir()
        (self.root / "scripts/build_values.py").write_text("raise SystemExit('built-in values ran')")
        self.builder(step("values", "data/values.json")).values()
        self.assertEqual(self.runs(), ["values"])
        # Without the step, the built-in stage runs the template's script.
        with self.assertRaisesRegex(build_graph.BuildError, "values failed"):
            self.builder().values()

    def test_a_failed_step_runs_again_and_loses_what_it_wrote(self):
        (self.root / "partial.py").write_text(
            "from pathlib import Path\n"
            "text = Path('input.txt').read_text()\n"
            "Path('out.txt').write_text('partial' if text == 'fail' else 'full ' + text)\n"
            "with open('runs.log', 'a') as log: log.write('partial\\n')\n"
            "raise SystemExit(1 if text == 'fail' else 0)\n")
        partial = {"name": "partial", "run": ["partial.py"], "inputs": ["input.txt"], "outputs": ["out.txt"]}
        self.builder(partial).slot("expand")
        (self.root / "input.txt").write_text("fail")
        with self.assertRaisesRegex(build_graph.BuildError, "exit 1"):
            self.builder(partial).slot("expand")
        self.assertFalse((self.root / "out.txt").exists())  # Make's .DELETE_ON_ERROR
        (self.root / "input.txt").write_text("1")  # the cause reverted: same hash as before
        self.runs()
        self.builder(partial).slot("expand")
        self.assertEqual(self.runs(), ["partial"])
        self.assertEqual((self.root / "out.txt").read_text(), "full 1")
        # A failure that writes nothing keeps the previous output.
        (self.root / "partial.py").write_text("raise SystemExit(2)\n")
        with self.assertRaisesRegex(build_graph.BuildError, "exit 2"):
            self.builder(partial).slot("expand")
        self.assertEqual((self.root / "out.txt").read_text(), "full 1")
        self.assertNotIn("step:partial", json.loads((self.root / ".build/steps.json").read_text()))

    def test_missing_inputs_fail_and_empty_globs_warn(self):
        missing = step("one", "out.txt", inputs=["data/derivd.csv"])
        with self.assertRaisesRegex(build_graph.BuildError, "step one: input data/derivd.csv does not exist"):
            self.builder(missing).slot("expand")
        self.assertEqual(self.runs(), [])
        with patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.builder(step("one", "out.txt", inputs=["data/derivd/*.csv"])).slot("expand")
        self.assertIn("step one: input data/derivd/*.csv matches no file", stderr.getvalue())
        self.assertEqual(self.runs(), ["one"])

    def test_inputs_may_be_outside_the_project(self):
        outer = self.root
        self.root = outer / "project"
        self.root.mkdir()
        for name in ("step.py", "input.txt"):
            shutil.copy(outer / name, self.root / name)
        (outer / "sibling.csv").write_text("a")
        absolute = outer / "absolute.csv"
        absolute.write_text("b")
        steps = [step("import", "out.txt", inputs=["../sibling.csv", str(absolute)], when="manual")]
        builder = self.builder(*steps)
        builder.step(builder.named("import"))
        self.assertEqual(self.runs(), ["import"])
        builder = self.builder(*steps)
        builder.step(builder.named("import"))
        self.assertEqual(self.runs(), [])
        for changed in (outer / "sibling.csv", absolute):
            changed.write_text("changed " + changed.name)
            builder = self.builder(*steps)
            builder.step(builder.named("import"))
            self.assertEqual(self.runs(), ["import"])

    def test_env_does_not_rerun_built_in_stages(self):
        (self.root / "manuscript/main").mkdir(parents=True)
        (self.root / "manuscript/main/01.md").write_text("# Title\n")
        self.builder(env={"MODE": "a"}).expand("manuscript")
        builder = self.builder(env={"MODE": "b"})
        builder.expand("manuscript")
        self.assertEqual(builder.ran, [])
        self.assertEqual(builder.digest(["uv", "sync"], [], env=False),
                         self.builder(env={"MODE": "a"}).digest(["uv", "sync"], [], env=False))

    def test_steps_run_in_the_project_environment(self):
        builder = self.builder()
        venv = self.root / ".venv"
        builder._python = str(venv / "bin" / "python")
        with patch.dict(os.environ, {"VIRTUAL_ENV": "/elsewhere/.venv"}):
            env = builder.step_env()
        self.assertEqual(env["VIRTUAL_ENV"], str(venv))
        self.assertTrue(env["PATH"].startswith(str(venv / "bin") + os.pathsep))

    def test_records_of_other_processes_are_kept(self):
        first, second = self.builder(), self.builder()  # both read steps.json before either writes
        first.record("a", "1")
        second.record("b", "2")
        state = json.loads((self.root / ".build/steps.json").read_text())
        self.assertEqual(sorted(state), ["a", "b"])

    def test_a_missing_program_names_doctor(self):
        builder = self.builder()
        with self.assertRaisesRegex(build_graph.BuildError, "Pandoc: no-such-pandoc not found.*trazo doctor"):
            builder.execute(["no-such-pandoc", "x.md"], "Pandoc")

    @unittest.skipIf(os.name == "nt", "the fake uv is a shell script")
    def test_uv_from_the_data_folder_creates_the_project_python(self):
        # An app launched from the Dock has no uv on PATH; setup put it in the data folder.
        (self.root / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0"\n')
        data, empty = self.root / "data", self.root / "empty"
        (data / "uv").mkdir(parents=True)
        empty.mkdir()
        uv = data / "uv" / "uv"
        uv.write_text(f'#!/bin/sh\necho "$*" > uv.log\n/usr/bin/env | /usr/bin/grep "^UV_" > uv.env\n'
                      f'/bin/mkdir -p .venv/bin\n/bin/ln -sf "{sys.executable}" .venv/bin/python\n')
        uv.chmod(0o755)
        with patch.dict(os.environ, {"TRAZO_DATA_DIR": str(data), "PATH": str(empty)}):
            self.assertEqual(self.builder().python(), str(self.root / ".venv/bin/python"))
            self.assertEqual((self.root / "uv.log").read_text(), "sync --quiet\n")
            # Its Python, cache and tools stay in the data folder, as with setup.sh.
            seen = dict(line.split("=", 1) for line in (self.root / "uv.env").read_text().splitlines())
            self.assertEqual(seen.get("UV_PYTHON_INSTALL_DIR"), str(data / "python"))
            self.assertEqual(seen.get("UV_CACHE_DIR"), str(data / "cache"))
            self.assertEqual(seen.get("UV_MANAGED_PYTHON"), "1")
            uv.unlink()
            (self.root / ".venv/bin/python").unlink()
            with self.assertRaisesRegex(build_graph.BuildError, "uv not found.*run trazo doctor"):
                self.builder().python()

    def test_env_overrides_reach_a_trazo_build_that_a_command_runs(self):
        # `trazo build --env K=V` -> commands.submission -> the trazo build it runs
        command = [sys.executable, "-c", "import os; open('env.txt', 'w').write(os.environ['TRAZO_BUILD_ENV'])"]
        builder = build_graph.Builder(self.project(commands={"submission": command}), env={"MODE": "draft"},
                                      log=lambda text: None)
        builder.run_command("submission")
        passed = (self.root / "env.txt").read_text()
        self.assertEqual(json.loads(passed), {"MODE": "draft"})
        with patch.dict(os.environ, {"TRAZO_BUILD_ENV": passed}):
            child = build_graph.Builder(self.project(env={"MODE": "canonical", "OTHER": "1"}))
        self.assertEqual(child.env, {"MODE": "draft", "OTHER": "1"})
        self.assertEqual(child.overrides, {"MODE": "draft"})
        with patch.dict(os.environ, {"TRAZO_BUILD_ENV": passed}):  # explicit --env wins
            self.assertEqual(build_graph.Builder(self.project(), env={}).env, {})

    def prepare_main(self, mode, steps):
        """`trazo build --prepare=MODE` on this test's project."""
        from trazo import builds
        real, made = build_graph.Builder, self.project(steps)
        with patch("trazo.build_graph.Builder", lambda **keys: real(made, log=lambda text: None, **keys)):
            return builds.main([f"--prepare={mode}"])

    @patch("trazo.diagram.all_diagrams", return_value=[])  # the fixture's, which need Chrome
    def test_prepare_runs_the_steps_before_expand_when_stale(self, _):
        steps = [step("fig", "figures/fig.png", inputs=["input.txt"]),
                 step("manual", "manual.txt", when="manual"), step("pages", after="docx")]
        self.assertEqual(self.prepare_main("all", steps), 0)
        self.assertEqual(self.runs(), ["fig"])
        for mode in ("all", "inputs"):
            self.assertEqual(self.prepare_main(mode, steps), 0)
        self.assertEqual(self.runs(), [])
        (self.root / "input.txt").write_text("2")
        self.assertEqual(self.prepare_main("inputs", steps), 0)
        self.assertEqual(self.runs(), ["fig"])

    def test_prepare_inputs_runs_the_figure_steps_but_exports_no_diagram(self):
        from trazo import diagram
        steps = [step("fig", "figures/fig.png", inputs=["input.txt"], before="diagrams")]
        source = self.root / "figures/diagrams/flow.json"
        stale = [patch.object(diagram, "all_diagrams", return_value=[source]),
                 patch.object(diagram, "export_state", return_value="stale"),
                 patch.object(diagram, "cmd_export", return_value=1)]  # as without Chrome
        for item in stale:
            item.start()
            self.addCleanup(item.stop)
        with patch("sys.stderr", io.StringIO()) as err:
            self.assertEqual(self.prepare_main("all", steps), 1)
        self.assertIn("diagram export failed", err.getvalue())
        self.assertEqual(self.runs(), ["fig"])
        self.assertEqual(self.prepare_main("inputs", steps), 0)
        diagram.cmd_export.assert_called_once()  # only by --prepare=all

    def test_empty_commands_are_built_in_and_others_run_as_written(self):
        builder = self.builder()
        self.assertFalse(builder.run_command("values"))
        command = [sys.executable, "-c", "open('ran.txt', 'w').write('yes')"]
        builder = self.builder(commands={"values": command})
        self.assertTrue(builder.run_command("values"))
        self.assertEqual((self.root / "ran.txt").read_text(), "yes")
        builder.values()  # the command replaces the built-in stage
        self.assertEqual(builder.ran, [f"values: {' '.join(command)}"] * 2)


class ImageTargets(unittest.TestCase):
    def test_captions_with_brackets_and_titles(self):
        markdown = ("![[Short title.]{.caption-title} Long [@ref] and \\] text.](figures/output/png/x.png){#fig:x}\n"
                    '![plain](<figures/a b.png> "title") ![web](https://x/y.png) [link](z.png) ![x](y.png)\n')
        self.assertEqual(build_graph.image_targets(markdown), ["figures/output/png/x.png", "figures/a b.png", "y.png"])


def png(red):
    """A 1x1 PNG of one colour."""
    chunk = lambda kind, data: (struct.pack(">I", len(data)) + kind + data  # noqa: E731
                                + struct.pack(">I", zlib.crc32(kind + data)))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes([0, red, 0, 0]))) + chunk(b"IEND", b""))


def word_text(path):
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml").decode()
    return re.sub(r' w:rsid\w*="[^"]*"', "", xml)


@unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
class MissingImage(unittest.TestCase):
    def test_an_image_pandoc_cannot_find_fails_the_word_build(self):
        # Pandoc only warns; a missing image must fail the build.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(_support.FIXTURE, root, dirs_exist_ok=True)
            shutil.rmtree(root / "figures/diagrams")
            (root / "trazo.toml").write_text('version = "v0.0.0"\n[crossref]\nengine = "trazo"\n')
            section = sorted((root / "manuscript/main").glob("*.md"))[0]
            with section.open("a", encoding="utf-8") as handle:
                handle.write("\n![A figure no step makes](figures/output/png/not built.png)\n")
            env = {**os.environ, "TRAZO_ROOT": str(root), "PYTHONPATH": str(PACKAGE_DIR.parent)}
            result = subprocess.run([sys.executable, "-m", "trazo", "build"], cwd=root, env=env,
                                    capture_output=True, text=True, timeout=120)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("Pandoc could not find figures/output/png/not built.png", result.stderr)
            self.assertFalse((root / "output/manuscript.docx").exists())


@unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
class FullBuild(unittest.TestCase):
    def copy(self, folder):
        root = Path(folder)
        shutil.copytree(_support.FIXTURE, root, dirs_exist_ok=True)
        shutil.rmtree(root / "figures/diagrams")  # exporting needs Chrome
        return root

    def run_in(self, root, *command, env=None):
        env = {**os.environ, **(env or {}), "TRAZO_ROOT": str(root), "PYTHONPATH": str(PACKAGE_DIR.parent)}
        result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_a_changed_figure_rebuilds_the_word_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.copy(folder)
            (root / "figures/scripts").mkdir(parents=True, exist_ok=True)
            (root / "figures/scripts/plot.py").write_text(
                "from pathlib import Path\nimport struct, zlib, sys\n"
                f"sys.path.insert(0, {str(Path(__file__).parent)!r})\n"
                "from test_build_graph import png\n"
                "Path('figures/output/png').mkdir(parents=True, exist_ok=True)\n"
                "Path('figures/output/png/plot.png').write_bytes(png(int(Path('figures/red.txt').read_text())))\n")
            (root / "figures/red.txt").write_text("10")
            (root / "trazo.toml").write_text(
                'version = "v0.0.0"\n[[build.steps]]\nname = "plot"\nrun = ["figures/scripts/plot.py"]\n'
                'inputs = ["figures/red.txt"]\noutputs = ["figures/output/png/plot.png"]\nbefore = "diagrams"\n')
            with (root / "manuscript/main/01_introduction.md").open("a") as handle:
                handle.write("\n![[Short title.]{.caption-title} A long caption.](figures/output/png/plot.png){#fig:plot}\n")
            trazo = (sys.executable, "-m", "trazo", "build", "--doc", "manuscript")
            self.run_in(root, *trazo)
            self.assertIn("[build] up to date", self.run_in(root, *trazo))
            (root / "figures/red.txt").write_text("200")  # the step runs, then the Word file
            log = self.run_in(root, *trazo)
            self.assertEqual(re.findall(r"\[build\] (step \w+|docx: \w+)", log), ["step plot", "docx: manuscript"])
            # The image alone changes (not through its step): the Word file still follows.
            (root / "figures/output/png/plot.png").write_bytes(png(99))
            log = self.run_in(root, *trazo)
            self.assertEqual(re.findall(r"\[build\] (step \w+|docx: \w+)", log), ["docx: manuscript"])
            with zipfile.ZipFile(root / "output/manuscript.docx") as archive:
                media = [archive.read(n) for n in archive.namelist() if n.startswith("word/media/")]
            self.assertIn(png(99), media)

    def test_after_docx_steps_run_on_each_word_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.copy(folder)
            (root / "scripts").mkdir()
            (root / "scripts/mark.py").write_text(
                "import sys, zipfile\n"
                "with zipfile.ZipFile(sys.argv[1], 'a') as archive:\n"
                "    archive.writestr('customXml/mark.txt', sys.argv[2])\n")
            (root / "trazo.toml").write_text(
                'version = "v0.0.0"\n[build.env]\nMODE = "canonical"\n'
                '[[build.steps]]\nname = "mark"\nrun = ["scripts/mark.py", "{docx}", "marked"]\n'
                'after = "docx"\ndocuments = ["manuscript"]\n')
            self.run_in(root, sys.executable, "-m", "trazo", "build")
            with zipfile.ZipFile(root / "output/manuscript.docx") as archive:
                self.assertEqual(archive.read("customXml/mark.txt"), b"marked")
            with zipfile.ZipFile(root / "output/supplementary.docx") as archive:
                self.assertNotIn("customXml/mark.txt", archive.namelist())
            # Word files are finished after the step: tables keep their styles.
            ET.fromstring(zipfile.ZipFile(root / "output/manuscript.docx").read("word/document.xml"))
            self.assertIn("[build] up to date", self.run_in(root, sys.executable, "-m", "trazo", "build"))
            log = self.run_in(root, sys.executable, "-m", "trazo", "build", "--env", "MODE=draft")
            self.assertIn("[build] docx: manuscript", log)

    @unittest.skipIf(os.name == "nt", "PATH folders with a linked pandoc")
    def test_the_word_hash_does_not_depend_on_where_pandoc_is_found(self):
        # A terminal finds Pandoc on PATH; an app from the Dock (PATH=/usr/bin:/bin)
        # runs the same Pandoc through the machine file.
        pandoc = Path(shutil.which("pandoc")).resolve()
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as tools:
            root = self.copy(folder)
            (root / "trazo.toml").write_text('version = "v0.0.0"\n[crossref]\nengine = "trazo"\n')
            (Path(tools) / "bin").mkdir()
            (Path(tools) / "bin/pandoc").symlink_to(pandoc)
            data = Path(tools) / "data"
            data.mkdir()
            (data / "machine.json").write_text(json.dumps({"tools": {"pandoc": {"path": str(pandoc)}}}))
            terminal = {"PATH": f"{tools}/bin{os.pathsep}/usr/bin{os.pathsep}/bin", "TRAZO_DATA_DIR": str(data)}
            gui = {"PATH": f"/usr/bin{os.pathsep}/bin", "TRAZO_DATA_DIR": str(data)}
            trazo = (sys.executable, "-m", "trazo", "build")
            self.assertIn("[build] docx: manuscript", self.run_in(root, *trazo, env=gui))
            self.assertIn("[build] up to date", self.run_in(root, *trazo, env=terminal))
            self.assertIn("[build] up to date", self.run_in(root, *trazo, env=gui))


if __name__ == "__main__":
    unittest.main()
