import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)

from trazo import variants

REPO = Path(__file__).resolve().parents[3]

HOSTILE = 'A "quoted" title: C:\\papers 😀'


class StoreTest(unittest.TestCase):
    def test_every_variant_is_complete(self):
        store = variants.Store()
        self.assertEqual(sorted(store.index["variants"]),
                         ["article-blank", "article-example", "report-blank", "report-example"])
        for name, variant in store.index["variants"].items():
            for path, digest in variant["files"].items():
                data = (store.root / "blobs" / digest).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), digest, path)
            self.assertLessEqual(set(variant["executable"]), set(variant["files"]))
            for path, names in variant["filled"].items():
                self.assertLessEqual(set(names), set(variants.FILLS[path]), path)
            self.assertIn("scripts/trazo", variant["executable"])
        self.assertEqual(store.index["skip_if_exists"],
                         ["manuscript/metadata.yaml", "manuscript/main/00c_abbreviations.md"])

    def test_report_variants_have_no_cover_letter(self):
        store = variants.Store()
        files = store.variant("report", True)["files"]
        self.assertFalse(any(path.startswith("manuscript/cover-letter/") for path in files))
        self.assertIn("manuscript/cover-letter/cover_letter.md", store.variant("article", False)["files"])

    def test_fill_escapes_each_file_as_its_jinja_source(self):
        title, name = variants.PLACEHOLDERS["title"], variants.PLACEHOLDERS["project_name"]
        cases = {
            "README.md": (f"# {title}\n", '# A "quoted" title: C:\\papers 😀\n'),
            "pyproject.toml": (f'name = "{name}"\ndescription = "{title}"\n',
                               'name = "my-paper"\ndescription = "A \\"quoted\\" title: C:\\\\papers 😀"\n'),
            "manuscript/metadata.yaml": (f'title: "{title}"\n',
                                         'title: "A \\"quoted\\" title: C:\\\\\\\\papers 😀"\n'),
            "manuscript/cover-letter/cover_letter.md": (
                f'manuscript "{title}" for', 'manuscript "A \\"quoted\\" title: C:\\\\papers 😀" for'),
        }
        for path, (stored, expected) in cases.items():
            self.assertEqual(variants.fill(path, stored, {"title": HOSTILE, "project_name": "my-paper"}),
                             expected, path)

    def test_an_answer_containing_a_placeholder_is_kept(self):
        tricky = "About " + variants.PLACEHOLDERS["project_name"]
        stored = f'name = "{variants.PLACEHOLDERS["project_name"]}"\ndescription = "{variants.PLACEHOLDERS["title"]}"'
        self.assertEqual(variants.fill("pyproject.toml", stored, {"title": tricky, "project_name": "x"}),
                         f'name = "x"\ndescription = "{tricky}"')

    def test_project_name_follows_copier_yml(self):
        for folder, expected in (("My Paper (draft) 2", "my-paper-draft-2"), ("--Ünïcode--", "n-code"),
                                 ("😀", "my-paper"), ("paper", "paper")):
            self.assertEqual(variants.project_name_for(folder), expected, folder)


class AnswersTest(unittest.TestCase):
    def test_answers_are_one_line_of_printable_text(self):
        variants.check_answers(HOSTILE, "my-paper")
        for title in ("two\nlines", "two\u2028lines", "two\u2029lines", "a\ufeffb", "a\x00b"):
            with self.assertRaisesRegex(ValueError, "one line"):
                variants.check_answers(title, "my-paper")


class GeneratorTest(unittest.TestCase):
    def test_skip_if_exists_takes_plain_paths_only(self):
        sys.path.insert(0, str(REPO / "scripts"))
        self.addCleanup(sys.path.remove, str(REPO / "scripts"))
        spec = importlib.util.spec_from_file_location("template_variants", REPO / "scripts/template-variants.py")
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "copier.yml"
            config.write_text("_skip_if_exists:\n  - a/b.md\n# note\n  - c.yaml\n_exclude: []\n")
            self.assertEqual(generator.skip_if_exists(Path(folder)), ["a/b.md", "c.yaml"])
            for entry in ('"a/b.md"', "a/*.md", "a/{b,c}.md"):
                config.write_text(f"_skip_if_exists:\n  - {entry}\n")
                with self.assertRaisesRegex(SystemExit, "only plain unquoted paths"):
                    generator.skip_if_exists(Path(folder))


if __name__ == "__main__":
    unittest.main()
