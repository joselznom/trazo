"""Full build index options through the CLI, including cached project output."""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)

from trazo.project import PACKAGE_DIR

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
# Two headings, a figure and a table: every index has something to list.
PAPER = (b"# Full paper\n\nOriginal manuscript text.\n\n## Detail\n\nMore text.\n\n"
         b"![A figure.](missing.png)\n\n| a |\n|---|\n| 1 |\n\n: A table.\n")


def docx(markdown):
    return subprocess.run(["pandoc", "--to=docx"], input=markdown, capture_output=True, check=True).stdout


def index_titles(path):
    with zipfile.ZipFile(path) as archive:
        document = ET.fromstring(archive.read("word/document.xml"))
    return ["".join(p.itertext()) for p in document.findall('.//w:sdtContent/w:p', NS)
            if p.find('w:pPr/w:pStyle', NS) is not None]


@unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
class BuildIndexes(unittest.TestCase):
    def project(self, root):
        """Two documents whose builds keep a cached DOCX, and one without a build."""
        (root / "exports").mkdir()
        for name in ("paper", "extra"):
            (root / f"exports/{name}.docx").write_bytes(docx(PAPER))
        cached = json.dumps([sys.executable, "-c", "print('cached project command')"])
        config = ['[commands]', *(f"{command} = {cached}" for command in ("values", "figures", "docx"))]
        for name, docx_path in (("paper", "exports/paper.docx"), ("extra", "exports/extra.docx"), ("notes", "")):
            config += [f"[documents.{name}]", f'path = "manuscript/{name}"', f'docx = "{docx_path}"']
            if name == "extra":
                config.append(f"build = {cached}")
        (root / "trazo.toml").write_text("\n".join(config))
        return {**os.environ, "TRAZO_ROOT": str(root), "PYTHONPATH": str(PACKAGE_DIR.parent)}

    def build(self, root, env, *flags):
        return subprocess.run([sys.executable, "-m", "trazo", "build", *flags], cwd=root,
                              env=env, capture_output=True, text=True, timeout=30)

    def test_indexes_can_be_changed_and_removed_from_cached_configured_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            env = self.project(root)
            output = root / "exports/paper.docx"
            with zipfile.ZipFile(output) as archive:
                original = {name: archive.read(name) for name in archive.namelist()}
            for flags, titles in ((["--toc", "--lof"], ["Table of Contents", "List of Figures"]),
                                  (["--lot"], ["List of Tables"]), ([], [])):
                with self.subTest(flags=flags):
                    # The first document uses commands.docx; it is the default.
                    result = self.build(root, env, *flags)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(index_titles(output), titles)
                    with zipfile.ZipFile(output) as archive:
                        document = ET.fromstring(archive.read("word/document.xml"))
                        self.assertIn("Original manuscript text.", "".join(document.itertext()))
                        for name, data in original.items():
                            if name not in ("word/document.xml", "word/settings.xml"):
                                self.assertEqual(archive.read(name), data, name)
                        if flags:
                            settings = ET.fromstring(archive.read("word/settings.xml"))
                            self.assertEqual(settings.find('w:updateFields', NS).get(f"{{{NS['w']}}}val"), "true")

    def test_each_document_builds_its_own_docx(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            env = self.project(root)
            result = self.build(root, env, "--doc", "extra", "--lot")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(index_titles(root / "exports/extra.docx"), ["List of Tables"])
            self.assertEqual(index_titles(root / "exports/paper.docx"), [])
            result = self.build(root, env, "--doc", "notes", "--toc")
            self.assertEqual(result.returncode, 1)
            self.assertIn("documents.notes has no full build", result.stderr)

    def test_indexes_follow_the_title_block(self):
        from trazo import docx_indexes
        title_block = (b"---\ntitle: Paper title\nsubtitle: A subtitle\nauthor: [First Author]\n"
                       b"date: 2026-10-19\nabstract: Short abstract.\n---\n\n")
        for markdown, before in ((title_block + PAPER, ["Title", "Subtitle", "Author", "Date", "AbstractTitle", "Abstract"]),
                                 (b'---\ntitle: Paper\n---\n\n::: {custom-style="Author"}\n\nAna\n\n:::\n\n'
                                  b'::: {custom-style="Affiliation"}\n\nHospital\n\n:::\n\n'
                                  b'::: {custom-style="Correspondence"}\n\nContact\n\n:::\n\n' + PAPER,
                                  ["Title", "Author", "Affiliation", "Correspondence"]),
                                 (PAPER, [])):
            with self.subTest(title_block=bool(before)):
                # Applying again replaces the indexes in the same place.
                data = docx_indexes.apply(docx_indexes.apply(docx(markdown), ["lot"]), ["toc"])
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    body = ET.fromstring(archive.read("word/document.xml")).find("w:body", NS)
                order = []
                for node in body:
                    if node.find('w:sdtPr/w:tag[@w:val="trazo-indexes"]', NS) is not None:
                        order.append("indexes")
                    elif (style := node.find("w:pPr/w:pStyle", NS)) is not None:
                        order.append(style.get(f"{{{NS['w']}}}val"))
                self.assertEqual(order[:len(before) + 2], [*before, "indexes", "Heading1"])
                self.assertEqual(order.count("indexes"), 1)

    def test_indexes_with_nothing_to_list_are_left_out(self):
        from trazo import docx_indexes
        data = docx(b"# Only heading\n\nText.\n")
        self.assertEqual(docx_indexes.apply(data, ["toc", "lof", "lot"]), data)
        # A previous index is still removed when nothing is added.
        with_lists = docx_indexes.apply(docx(PAPER), ["lof"])
        self.assertIn(b"trazo-indexes", zipfile.ZipFile(io.BytesIO(with_lists)).read("word/document.xml"))
        cleared = docx_indexes.apply(with_lists, [])
        self.assertNotIn(b"trazo-indexes", zipfile.ZipFile(io.BytesIO(cleared)).read("word/document.xml"))
