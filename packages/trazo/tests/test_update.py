"""trazo update from an older template store to the current one."""

import hashlib
import json
import os
import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)

from trazo import agents, cli, manifest, new, update, variants
from trazo.agents import MARKER_NAME as MARKER
from trazo.project import DEFAULTS, Project, merge

TITLE = 'A "quoted" title: C:\\papers 😀'


def older_store(folder: Path) -> variants.Store:
    """The current store as an older release: v0.4.0, a different AGENTS.md,
    .gitignore and trazo.toml, a file the new release drops, and without
    tables/scripts/_data.py and figures/README.md (new in the current one)."""
    current = variants.Store()
    shutil.copytree(current.root, folder)
    index = json.loads((folder / "index.json").read_text(encoding="utf-8"))
    index["template"] = "v0.4.0"

    def blob(data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        (folder / "blobs" / digest).write_bytes(data)
        return digest

    for variant in index["variants"].values():
        files = variant["files"]
        for path in ("AGENTS.md", ".gitignore"):
            files[path] = blob((current.root / "blobs" / files[path]).read_bytes() + b"\n# old release\n")
        toml = (current.root / "blobs" / files["trazo.toml"]).read_bytes()
        files["trazo.toml"] = blob(toml.replace(b'version = "' + current.template.encode(),
                                                    b'version = "v0.4.0', 1) + b"# old comment\n")
        files["scripts/old-helper.sh"] = blob(b"echo old\n")
        del files["tables/scripts/_data.py"], files["figures/README.md"]
        variant["filled"].pop("figures/README.md", None)
    (folder / "index.json").write_text(json.dumps(index), encoding="utf-8")
    return variants.Store(folder)


class UpdateTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.store = variants.Store()
        self.old = older_store(self.tmp / "old-store")
        self.project = self.tmp / "paper"
        new.create(self.project, kind="article", example=True, title=TITLE, store=self.old)

    def snapshot(self, folder: Path) -> dict:
        return {p.relative_to(folder).as_posix(): p.read_bytes()
                for p in sorted(folder.rglob("*")) if p.is_file()}

    def test_update_replaces_merges_adds_and_leaves_the_manuscript(self):
        p = self.project
        (p / "AGENTS.md").write_text((p / "AGENTS.md").read_text() + "\nOur own rule.\n")
        (p / "manuscript/main/01_introduction.md").write_text("Our introduction.\n")
        (p / "trazo.toml").write_text((p / "trazo.toml").read_text() + '\n[submission]\njournal = "J"\n')
        (p / "README.md").write_text("Our readme.\n")  # changed; same in both templates
        manuscript = {k: v for k, v in self.snapshot(p).items() if k.startswith(("manuscript/", "data/"))}
        agents = (p / "AGENTS.md").read_bytes()

        report = update.update(p, self.store)

        self.assertEqual((report.template_from, report.template_to), ("v0.4.0", self.store.template))
        self.assertIn(".gitignore", report.replaced)
        self.assertEqual(sorted(report.needs_merge), ["AGENTS.md", "trazo.toml"])
        self.assertEqual(sorted(report.added), ["figures/README.md", "tables/scripts/_data.py"])
        self.assertEqual(report.removed, ["scripts/old-helper.sh"])
        self.assertEqual(report.skipped, [{"path": "README.md",
                                           "reason": "changed in the project; the template did not change it"}])
        fresh = self.tmp / "fresh"
        new.create(fresh, kind="article", example=True, title=TITLE, project_name="paper")
        expected = self.snapshot(fresh)
        self.assertEqual((p / "AGENTS.md").read_bytes(), agents)
        self.assertEqual((p / "AGENTS.md.new").read_bytes(), expected["AGENTS.md"])
        self.assertEqual((p / "trazo.toml.new").read_bytes(), expected["trazo.toml"])
        toml = (p / "trazo.toml").read_text()
        self.assertIn(f'version = "{self.store.template}"', toml)
        self.assertIn('journal = "J"', toml)
        self.assertIn("# old comment", toml)
        after = self.snapshot(p)
        for path, data in manuscript.items():
            self.assertEqual(after[path], data, path)
        # Every template-owned file the project did not change is now the new one.
        for path in manifest.read(fresh)["files"]:
            if path not in ("AGENTS.md", "trazo.toml", "README.md"):
                self.assertEqual(after[path], expected[path], path)
        self.assertTrue(os.access(p / "scripts/trazo", os.X_OK))
        self.assertEqual(manifest.read(p), {**manifest.read(fresh),
                                            "answers": {**manifest.read(fresh)["answers"], "project_name": "paper"}})

        # The agent merges; the next update has nothing left to merge.
        (p / "AGENTS.md.new").unlink()
        shutil.copy(fresh / "trazo.toml", p / "trazo.toml")
        (p / "trazo.toml.new").unlink()
        before = self.snapshot(p)
        again = update.update(p, self.store)
        self.assertTrue(again.up_to_date)
        self.assertRegex(again.notes[0], f"already at template {self.store.template}; .*newer trazo")
        self.assertEqual(self.snapshot(p), before)

    def test_a_downgrade_is_refused(self):
        update.update(self.project, self.store)
        before = self.snapshot(self.project)
        with self.assertRaisesRegex(ValueError, f"at template {self.store.template}, newer than .* v0.4.0"):
            update.update(self.project, self.old)
        self.assertEqual(self.snapshot(self.project), before)
        with patch("sys.stdout") as out:
            self.assertEqual(cli.main(["update", str(self.project)]), 0)
        self.assertIn("already at template", "".join(c.args[0] for c in out.write.call_args_list))

    def test_an_unmerged_new_file_is_reported(self):
        p = self.project
        (p / "AGENTS.md").write_text((p / "AGENTS.md").read_text() + "\nOur own rule.\n")
        (p / ".gitignore").write_text((p / ".gitignore").read_text() + "\n# ours\n")
        fresh = self.tmp / "fresh"
        new.create(fresh, kind="article", example=True, title=TITLE, project_name="paper")
        (p / "AGENTS.md.new").write_bytes((fresh / "AGENTS.md").read_bytes())
        (p / ".gitignore.new").write_text("an older merge\n")
        report = update.update(p, self.store)
        self.assertEqual(sorted(report.needs_merge), [".gitignore", "AGENTS.md"])
        self.assertIn("AGENTS.md.new from an earlier update is still unmerged", report.notes)
        self.assertIn(".gitignore.new from an earlier update was unmerged; replaced it", report.notes)
        self.assertEqual((p / ".gitignore.new").read_bytes(), (fresh / ".gitignore").read_bytes())
        self.assertEqual(sorted(p.glob("*.tmp")), [])

    def test_crlf_line_ends_are_not_a_change(self):
        p = self.project
        for path in (".gitignore", "README.md"):
            (p / path).write_bytes((p / path).read_bytes().replace(b"\n", b"\r\n"))
        report = update.update(p, self.store)
        self.assertIn(".gitignore", report.replaced)
        self.assertNotIn("README.md", [entry["path"] for entry in report.skipped])
        self.assertEqual(report.needs_merge, [])

    def test_skill_copies_are_regenerated_as_trazo_agents_does(self):
        def dropped_targets(folder, targets):
            kept = {target.resolve() for target in targets}
            return [name for name in DEFAULTS["paths"]["skill_targets"] if (folder / name).resolve() not in kept]

        def paper(name, paths):
            folder = self.tmp.resolve() / name  # as update names it
            new.create(folder, kind="article", example=True, title=TITLE, store=self.old)
            if paths:
                with (folder / "trazo.toml").open("a") as toml:
                    toml.write("\n[paths]\n" + "".join(f"{key} = {json.dumps(value)}\n" for key, value in paths.items()))
            project = Project(folder, merge(DEFAULTS, tomllib.loads((folder / "trazo.toml").read_text())), True)
            targets = project.paths("skill_targets")
            for target in targets:
                for skill, file, text in (("retired-skill", "SKILL.md", "A skill trazo no longer ships.\n"),
                                          ("retired-skill", MARKER, "Generated.\n"),
                                          ("add-reference", "SKILL.md", "Generated before the paper had its own.\n"),
                                          ("add-reference", "extra.md", "A file the template dropped.\n"),
                                          ("critique", "SKILL.md", "Edited by hand.\n"),
                                          ("my-notes", "SKILL.md", "The author's, unmarked.\n")):
                    path = target / skill / file
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text)
            for dropped in dropped_targets(folder, targets):
                # A dropped target keeps edited copies and does not restore removed ones.
                for file, text in (("SKILL.md", "A skill trazo no longer ships.\n"), (MARKER, "Generated.\n")):
                    path = folder / dropped / "retired-skill" / file
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text)
                (folder / dropped / "critique/SKILL.md").write_text("Edited by hand.\n")
                shutil.rmtree(folder / dropped / "add-reference")
            own = project.path("skills") / "add-reference"
            own.mkdir(parents=True, exist_ok=True)
            (own / "SKILL.md").write_text("The paper's own skill.\n")
            return folder, project

        for name, paths in (("default", None), ("dropped", {"skill_targets": [".agents/skills"]}),
                            ("moved", {"skills": "custom-skills", "skill_targets": ["custom-agent/skills"]}),
                            ("outside", {"skill_targets": [str(self.tmp.resolve() / "elsewhere/skills")]})):
            folder, project = paper(name, paths)
            targets = project.paths("skill_targets")
            report = update.update(folder, self.store)
            self.assertEqual(agents.check(agents.canonical_skills(project), project), [], name)
            for target in targets:
                self.assertIn(Path(os.path.relpath(target / "retired-skill", folder)).as_posix(), report.removed, name)
                self.assertFalse((target / "retired-skill").exists(), name)
                self.assertEqual((target / "add-reference/SKILL.md").read_text(), "The paper's own skill.\n")
                self.assertTrue((target / "my-notes/SKILL.md").is_file(), name)
            for dropped in dropped_targets(folder, targets):
                self.assertIn(f"{dropped}/retired-skill", report.removed, name)
                self.assertFalse((folder / dropped / "retired-skill").exists(), name)
                self.assertEqual((folder / dropped / "critique/SKILL.md").read_text(), "Edited by hand.\n")
                self.assertFalse((folder / dropped / "add-reference").exists(), name)
                self.assertTrue([note for note in report.notes if note.startswith(f"{dropped} is not among")], name)
            self.assertFalse([path for path in manifest.read(folder)["files"] if path.startswith(manifest.SKILL_COPIES)],
                             name)
            self.assertFalse([path for path in report.needs_merge if "/skills/" in path], name)

    def test_skill_copies_are_left_as_they_are_when_the_paper_config_or_skills_do_not_load(self):
        own = (".agents/skills/aaa-local/SKILL.md", "The paper's own skill.\n")  # the skills folder of "overlap"
        for name, paths, files in (
                ("config", "[unknown_table]\n", []),
                ("skills", "", [("agent-workflows/skills/draft/notes.md", "No SKILL.md beside it.\n")]),
                ("overlap", '[paths]\nskills = ".agents/skills"\n', [own]),
                # The paper's skills inside a retired copy in a template target it dropped.
                ("dropped", '[paths]\nskills = ".claude/skills/retired-skill/own"\nskill_targets = [".agents/skills"]\n',
                 [(".claude/skills/retired-skill/" + MARKER, "Generated.\n"),
                  (".claude/skills/retired-skill/own/mine/SKILL.md", "The paper's own skill.\n")]),
                # Folders that contain one another: a copy or the pruning in one would overwrite another
                # (a skill named "skills" copied into .agents replaces .agents/skills).
                ("ancestor", '[paths]\nskill_targets = [".agents"]\n',
                 [("agent-workflows/skills/skills/SKILL.md", "The paper's own skill.\n"),
                  (".agents/skills/critique/SKILL.md", "Edited by hand.\n")]),
                ("nested", '[paths]\nskill_targets = [".agents", ".agents/skills", ".claude/skills"]\n',
                 [("agent-workflows/skills/skills/SKILL.md", "The paper's own skill.\n"),
                  (".agents/skills/my-notes/SKILL.md", "The author's, unmarked.\n")]),
                ("inside", '[paths]\nskill_targets = [".agents/skills/retired-skill", ".claude/skills"]\n',
                 [(".agents/skills/retired-skill/" + MARKER, "Generated.\n")])):
            folder = self.tmp / name
            new.create(folder, kind="article", example=True, title=TITLE, store=self.old)
            files = [(".claude/skills/critique/SKILL.md", "Edited by hand.\n"), *files]
            for path, text in files:
                (folder / path).parent.mkdir(parents=True, exist_ok=True)
                (folder / path).write_text(text)
            with (folder / "trazo.toml").open("a") as toml:
                toml.write("\n" + paths)
            report = update.update(folder, self.store)
            for path, text in files:
                self.assertEqual((folder / path).read_text(), text, f"{name}: {path}")
            self.assertTrue([note for note in report.notes if "`scripts/trazo agents`" in note], name)

    def test_deleted_files_stay_deleted_and_project_files_are_added_when_new(self):
        (self.project / "AGENTS.md").unlink()
        (self.project / "data/example.csv").unlink()
        saved = manifest.read(self.project)
        saved["project"].remove("notes/ideas.md")  # as if new in this release
        manifest.write(self.project, saved)
        (self.project / "notes/ideas.md").unlink()
        report = update.update(self.project, self.store)
        self.assertFalse((self.project / "AGENTS.md").exists())
        self.assertFalse((self.project / "data/example.csv").exists())
        self.assertIn({"path": "AGENTS.md", "reason": "deleted in the project; not restored"}, report.skipped)
        self.assertIn("notes/ideas.md", report.added)

    def test_same_release_changes_nothing(self):
        fresh = self.tmp / "same"
        new.paper_create(fresh, kind="report", example=False, title="T")
        before = self.snapshot(fresh)
        report = update.paper_update(fresh)
        self.assertEqual([report[k] for k in ("replaced", "added", "removed", "needs_merge", "skipped")],
                         [[], [], [], [], []])
        self.assertEqual(self.snapshot(fresh), before)

    def test_a_project_without_manifest_is_refused(self):
        with self.assertRaisesRegex(ValueError, "not made by `trazo new`"):
            update.paper_update(self.tmp)
        with patch("sys.stderr"):
            self.assertEqual(cli.main(["update", str(self.tmp)]), 2)


if __name__ == "__main__":
    unittest.main()
