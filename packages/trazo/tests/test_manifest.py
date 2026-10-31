import json
import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)

from trazo import manifest, variants

ANSWERS = {"example": True, "kind": "article", "project_name": "my-paper", "title": "Paper title"}
TITLE = 'Ünïcode "quoted" title: C:\\papers'



class ManifestTest(unittest.TestCase):
    def test_build_splits_template_and_project_files(self):
        store = variants.Store()
        files = store.render("article", True, {"title": TITLE, "project_name": "my-paper"})
        built = manifest.build(store.template, ANSWERS, files, store.index["skip_if_exists"])
        self.assertEqual(built["answers"], ANSWERS)
        for path in ("AGENTS.md", "scripts/trazo", "trazo.toml", ".gitignore",
                     "figures/scripts/_style.py", "templates/reference_universal.docx"):
            self.assertIn(path, built["files"])
        for path in ("manuscript/metadata.yaml", "manuscript/main/01_introduction.md",
                     "data/example.csv", "figures/scripts/fig1_trend.py",
                     "tables/scripts/generate_tables.py", "bibliography/references.bib"):
            self.assertIn(path, built["project"])
        self.assertFalse(set(built["files"]) & set(built["project"]))

    def test_text_hashes_ignore_crlf(self):
        self.assertEqual(manifest.file_hash("Makefile", b"a\r\nb\n"), manifest.file_hash("Makefile", b"a\nb\n"))
        self.assertEqual(manifest.file_hash("trazo.toml", b'version = "v1"\r\nx = 1\r\n'),
                         manifest.file_hash("trazo.toml", b'version = "v2"\nx = 1\n'))
        for binary in (b"\0a\r\n", b"\xff\r\n"):
            self.assertNotEqual(manifest.file_hash("x.bin", binary),
                                manifest.file_hash("x.bin", binary.replace(b"\r\n", b"\n")))

    def test_version_key(self):
        self.assertLess(manifest.version_key("v0.9.9"), manifest.version_key("v0.10.0"))
        self.assertLess(manifest.version_key("v0.5.0"), manifest.version_key("v0.5.0-3-gabc1234"))
        with self.assertRaisesRegex(ValueError, "expected vX.Y.Z"):
            manifest.version_key("main")

    def test_trazo_toml_hash_ignores_the_version(self):
        one = manifest.file_hash("trazo.toml", b'# x\nversion = "v0.5.0"\nkind = "article"\n')
        two = manifest.file_hash("trazo.toml", b'# x\nversion = "v0.6.0"\nkind = "article"\n')
        self.assertEqual(one, two)
        self.assertNotEqual(one, manifest.file_hash("trazo.toml", b'# x\nversion = "v0.6.0"\nkind = "report"\n'))
        self.assertNotEqual(manifest.file_hash("README.md", b'version = "1"'),
                            manifest.file_hash("README.md", b'version = "2"'))

    def test_read_and_write(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "not made by `trazo new`"):
                manifest.read(Path(folder))
            built = manifest.build("v1", ANSWERS, {"Makefile": b"all:\n", "data/x.csv": b"a\n"}, [])
            manifest.write(Path(folder), built)
            self.assertEqual(manifest.read(Path(folder)), built)
            self.assertEqual(built["project"], ["data/x.csv"])
            manifest.write(Path(folder), {**built, "files": {"README.md": None}})
            with self.assertRaisesRegex(ValueError, "not a trazo manifest"):
                manifest.read(Path(folder))
            (Path(folder) / manifest.PATH).write_text(json.dumps({"format": 9}))
            with self.assertRaisesRegex(ValueError, "not a trazo manifest"):
                manifest.read(Path(folder))


if __name__ == "__main__":
    unittest.main()
