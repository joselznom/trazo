"""trazo.toml: defaults, overrides and errors."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo is imported)

from trazo import project


def load(text=None):
    folder = Path(tempfile.mkdtemp())
    if text is not None:
        (folder / project.CONFIG_NAME).write_text(text)
    with patch.dict(os.environ, {"TRAZO_ROOT": ""}):
        return project.Project.load(folder)


class ProjectTests(unittest.TestCase):
    def test_document_kind_defaults_and_validation(self):
        self.assertEqual(load().config["kind"], "article")
        for kind in ("article", "report"):
            self.assertEqual(load(f'kind = "{kind}"').config["kind"], kind)
        for value in ('"book"', '"Article"', "true", "[]", "{}"):
            with self.subTest(value=value), self.assertRaisesRegex(project.ConfigError, "kind"):
                load(f"kind = {value}")

    def test_fixture_is_the_current_project(self):
        self.assertEqual(project.PROJECT.root, _support.ROOT)
        self.assertTrue(project.PROJECT.found)
        self.assertIsNone(project.LOAD_ERROR)

    def test_defaults_without_a_config_file(self):
        found = load()
        self.assertFalse(found.found)
        self.assertEqual(found.documents["manuscript"], "manuscript/main")
        self.assertEqual(found.number_prefixes, {"manuscript": "", "supplementary": "S"})
        self.assertEqual(found.command("docx"), [])  # built in
        self.assertEqual(found.command_hint("values"), "scripts/trazo build --prepare=inputs")
        self.assertEqual(found.config["build"], {"env": {}, "steps": []})

    def test_root_is_found_from_a_subfolder(self):
        folder = Path(tempfile.mkdtemp())
        (folder / project.CONFIG_NAME).write_text('version = "v1.2.3"\n')
        (folder / "a" / "b").mkdir(parents=True)
        with patch.dict(os.environ, {"TRAZO_ROOT": ""}):
            found = project.Project.load(folder / "a" / "b")
        self.assertEqual(found.root, folder.resolve())
        self.assertEqual(found.config["version"], "v1.2.3")

    def test_overrides_merge_with_defaults(self):
        found = load('[paths]\nvalues = "results/values.json"\n'
                     '[commands]\ntables = \'make "tables fast"\'\n')
        self.assertEqual(found.relative("values"), "results/values.json")
        self.assertEqual(found.relative("review"), "notes/review")
        self.assertEqual(found.command("tables"), ["make", "tables fast"])

    def test_documents_replace_the_defaults(self):
        found = load('[documents.report]\npath = "report"\n')
        self.assertEqual(found.documents, {"report": "report"})
        self.assertEqual(found.titles, {"report": "Report"})
        self.assertEqual(found.number_prefixes, {"report": ""})

    def test_document_number_prefix_can_be_customized_or_disabled(self):
        cases = (("supplementary", None, "S"), ("supplementary", "", ""),
                 ("appendix", "A", "A"))
        for name, setting, expected in cases:
            with self.subTest(name=name, setting=setting):
                text = f'[documents.{name}]\npath = "report"\n'
                if setting is not None:
                    text += f'number_prefix = "{setting}"\n'
                self.assertEqual(load(text).number_prefixes, {name: expected})

    def test_a_title_prefix_may_hold_words(self):
        found = load('[documents.supplementary]\npath = "s"\ntitle_prefix = "Online Supplementary"\n')
        self.assertEqual(found.config["documents"]["supplementary"]["title_prefix"], "Online Supplementary")

    def test_each_document_has_its_own_full_build(self):
        found = load()
        self.assertEqual(found.document_build("manuscript"), [])  # commands.docx: built in
        self.assertEqual(found.document_build("supplementary"), [])
        self.assertEqual(found.document_docx("supplementary"), found.root / "output/supplementary.docx")
        found = load('[commands]\ndocx = ["just", "paper"]\n'
                     '[documents.supplementary]\npath = "supp"\nbuild = "make extra"\n'
                     '[documents.notes]\npath = "notes"\n'
                     '[documents.appendix]\npath = "appendix"\ndocx = "out/appendix.docx"\n')
        # Known names keep their default output; a renamed first document does not.
        self.assertEqual(found.document_build("supplementary"), ["make", "extra"])
        self.assertEqual(found.document_docx("supplementary"), found.root / "output/supplementary.docx")
        self.assertIsNone(found.document_build("notes"))
        self.assertIsNone(found.document_docx("notes"))
        # Without a build, only the first document falls back to commands.docx;
        # the others are built in.
        self.assertEqual(found.document_build("appendix"), [])
        self.assertEqual(load('[commands]\ndocx = []\n').document_build("manuscript"), [])
        self.assertEqual(load('[commands]\ndocx = ""\n').document_build("manuscript"), [])
        first = load('[commands]\ndocx = ["just", "paper"]\n[documents.paper]\npath = "p"\ndocx = "p.docx"\n')
        self.assertEqual(first.document_build("paper"), ["just", "paper"])

    def test_unknown_keys_are_errors(self):
        for text in ('colour = "red"\n', '[paths]\nvalue = "x"\n',
                     '[documents.report]\nfolder = "report"\n', 'paths = "x"\n'):
            with self.subTest(text=text), self.assertRaises(project.ConfigError):
                load(text)

    def test_invalid_values_fail_at_load_with_the_setting_name(self):
        cases = {
            'version = 1': "version",
            'source = []': "source",
            '[paths]\nvalues = 1': "paths.values",
            '[paths]\nreview = ""': "paths.review",
            '[paths]\nskill_targets = "skills"': "paths.skill_targets",
            '[paths]\nskill_targets = ["a", 1]': "paths.skill_targets",
            '[documents.report]\npath = []': "documents.report.path",
            '[documents.report]\npath = ""': "documents.report.path",
            '[documents.report]\npath = "report"\ntitle = 2': "documents.report.title",
            '[documents.report]\npath = "report"\nnumber_prefix = 2': "documents.report.number_prefix",
            '[documents.report]\npath = "report"\nnumber_prefix = "S "': "documents.report.number_prefix",
            '[documents.report]\npath = "report"\nnumber_prefix = "$$i$$"': "documents.report.number_prefix",
            '[documents.report]\npath = "report"\ntitle_prefix = "**Suppl**"': "documents.report.title_prefix",
            '[documents.report]\npath = "report"\ntitle_prefix = 1': "documents.report.title_prefix",
            '[documents.report]\npath = "report"\ndocx = 1': "documents.report.docx",
            '[documents.report]\npath = "report"\nbuild = ["make", 1]': "documents.report.build",
            '[commands]\npandoc = []': "commands.pandoc",
            '[commands]\ndocx = ["make", 1]': "commands.docx",
            '[build]\nenv = {MODE = 1}': "build.env",
            '[build]\nsteps = {}': "build.steps",
            '[[build.steps]]\nrun = ["a.py"]': "name",
            '[[build.steps]]\nname = "a"': "needs run or check",
            '[[build.steps]]\nname = "a"\nrun = ["a.py"]\nbefore = "docx"': "before",
            '[[build.steps]]\nname = "a"\nrun = ["a.py"]\nafter = "expand"': "after",
            '[[build.steps]]\nname = "a"\nrun = ["a.py"]\nwhen = "always"': "when",
            '[[build.steps]]\nname = "a"\nrun = ["a.py"]\ndocuments = ["manuscript"]': "documents",
            '[[build.steps]]\nname = "a"\nrun = ["a.py"]\noutputs = ["../x"]': "outputs",
            '[[build.steps]]\nname = "a"\nrun = ["a.py"]\ncolour = 1': "unknown key",
            '[[build.steps]]\nname = "a"\nrun = ["a.py"]\n[[build.steps]]\nname = "a"\nrun = ["b.py"]': "twice",
            "[commands]\ndocx = '\"unterminated'": "commands.docx",
            '[preview]\nwatch = "*.md"': "preview.watch",
            '[preview]\nwatch = [true]': "preview.watch",
            '[diagrams]\nchrome = false': "diagrams.chrome",
            '[counts]\nexclude = "abstract"': "counts.exclude",
            '[limits]\nwords = "3000"': "limits.words",
            '[limits]\nwords = -1': "limits.words",
            '[limits]\nfigures = true': "limits.figures",
            '[limits]\nkeywords = -1': "limits.keywords",
            '[limits]\nkeywords = 2.5': "limits.keywords",
            '[limits]\nkeywords = "6"': "limits.keywords",
            '[limits]\npages = 10': "limits.pages",
        }
        for text, name in cases.items():
            with self.subTest(text=text), self.assertRaisesRegex(project.ConfigError, name):
                load(text)

    def test_step_inputs_may_leave_the_project_but_outputs_may_not(self):
        steps = project.build_steps([{"name": "import", "run": ["i.py"], "when": "manual",
                                      "inputs": ["../results/*.csv", "/data/raw.csv"], "outputs": ["data/x.csv"]}])
        self.assertEqual(steps[0]["inputs"], ["../results/*.csv", "/data/raw.csv"])
        for output in ("../x.csv", "/tmp/x.csv"):
            with self.subTest(output=output), self.assertRaisesRegex(project.ConfigError, "inside the project"):
                project.build_steps([{"name": "a", "run": ["a.py"], "outputs": [output]}])

    def test_counts_and_limits(self):
        found = load('[counts]\nexclude = ["summary"]\n[limits]\nwords = 3000\nkeywords = 6\n')
        self.assertEqual(found.config["counts"]["exclude"], ["summary"])
        self.assertEqual(found.config["limits"],
                         {"words": 3000, "abstract": 0, "figures": 0, "tables": 0, "references": 0, "keywords": 6})
        self.assertIn("declarations", load().config["counts"]["exclude"])

    def test_chrome_override(self):
        with patch.dict(os.environ, {"TRAZO_CHROME": "/opt/chrome"}):
            self.assertEqual(load().chrome(), "/opt/chrome")


if __name__ == "__main__":
    unittest.main()
