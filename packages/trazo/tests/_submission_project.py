"""The article project the submission check's tests run on."""

import json
import shutil
import tempfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import _support

from trazo import counts, dependencies, expand, frontmatter, numbering, preview, submission_check, submission_fields
from trazo.project import DEFAULTS, Project, merge

COUNTS = {"words": 120, "abstract": 40, "figures": 2, "tables": 1, "references": 2,
          "left_out": [], "heads": [], "limits": {}}
METADATA = """title: "A *study* title"
short-title: "Study"
author:
  - name: "Ana Pérez"
    email: "ana@example.org"
    corresponding: true
    orcid: "0000-0002-1825-0097"
    affiliations: [{ref: uni}]
  - name: {given: Bo, family: Li}
# [TODO: Bo Li's affiliation]
affiliations:
  - id: uni
    department: Dept
    name: University
keywords: [alpha, beta, gamma]
reference-section-title: References
"""



class SubmissionProject:
    """A TestCase mixin: setUp writes the project, bind() points trazo at it."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.write("manuscript/main/00_abstract.md",
                   "# Abstract\n\n## Objectives {#objectives}\n\nWe tested {{v:n}} cases [@first].\n\n"
                   "## Conclusions\n\n- One finding.\n")
        self.write("manuscript/main/01_intro.md",
                   "# Introduction\n\nText with a [PLACEHOLDER: a long\ninstruction across\nlines] here.\n\n"
                   "Code `[TODO: inline code]` and\n\n```md\n[TBD: fenced]\n```\n\n[TODO]\n")
        self.write("manuscript/supplementary/S1.md", "# Supplementary\n\nNothing [TBD: table S1].\n")
        self.write("manuscript/declarations/declarations.md",
                   "# Declarations\n\n## Ethics {#decl-ethics}\n\nApproved [@second].\n\n"
                   "## Consent for publication {#decl-consent}\n\nNot applicable.\n\n"
                   "## Funding {#decl-funding .form-only}\n\nGrant {{v:n}}.\n\n"
                   "## Data {#decl-data}\n\n[PLACEHOLDER: repository]\n\n"
                   "## Code {#decl-code}\n\n<!-- write here -->\n")
        self.write("manuscript/cover-letter/letter.md", "Dear Editor,\n\n[PLACEHOLDER: the\nmain finding]\n")
        self.write("manuscript/metadata.yaml", METADATA)
        self.write("data/values.json", json.dumps({"values": {"n": {"value": 42, "text": "42"}}}))
        self.write("bibliography/references.bib",
                   "@article{first, author={Alpha, A}, title={First source}, journal={Journal}, year={2020}}\n"
                   "@article{second, author={Beta, B}, title={Second source}, journal={Journal}, year={2021}}\n")
        shutil.copy(_support.ROOT / "bibliography/csl/ieee.csl", self.root / "bibliography/ieee.csl")

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def project(self, extra=None):
        config = merge(DEFAULTS, {"paths": {"csl": "bibliography/ieee.csl"}})
        return Project(self.root, merge(config, extra or {}), True)

    def bind(self, project):
        stack = ExitStack()
        self.addCleanup(stack.close)
        for module in (expand, frontmatter, numbering, counts, preview, submission_check, submission_fields, dependencies):
            stack.enter_context(patch.object(module, "PROJECT", project))
        stack.enter_context(patch.object(expand, "ROOT", self.root))
        stack.enter_context(patch.object(counts, "current_config", return_value=project.config))
        stack.enter_context(patch.object(preview, "DOCS", project.documents))
        stack.enter_context(patch.object(preview, "ROOT", self.root))
        stack.enter_context(patch.object(preview, "PANDOC_FLAGS", [
            flag.replace("bibliography/csl/ieee.csl", "bibliography/ieee.csl")
            for flag in preview.PANDOC_FLAGS]))
        return project

    def groups(self, result):
        return {group["id"]: group["items"] for group in result["groups"]}
