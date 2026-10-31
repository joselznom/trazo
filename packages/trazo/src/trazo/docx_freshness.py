"""Whether a document's Word file is out of date, as `trazo build
--submission` decides, for the submission check's "Files to upload".

trazo's own Word build compares the content of its inputs with the
hashes it recorded (build_graph), so a file saved again unchanged leaves the
Word file current. A document that a command builds (its `build`, or the
`submission` command) keeps its own freshness, so its Word file is compared
by time with the sources, images and settings it is built from.
"""

from pathlib import Path

from .build_graph import Builder, BuildError
from .project import CONFIG_NAME, Project
from .submission_check import IMAGE, includes, prose, read, relative


class WordFiles(Builder):
    """The submission build, asked about each Word file without building it:
    one per report, so `pandoc --version` runs once."""

    def __init__(self, project: Project) -> None:
        super().__init__(project, submission=True, log=lambda text: None)

    def did(self, label: str) -> None:
        raise BuildError(label)  # the build would start work here

    def stale(self, name: str, output: Path) -> str:
        """Why `output`, the Word file of `name`, is out of date, or "" when it is current."""
        project = self.project
        if project.document_build(name) or (project.config["kind"] == "article" and project.command("submission")
                                            and name in project.submission_documents):
            built = output.stat().st_mtime_ns
            newer = max(word_inputs(project, name), key=lambda path: path.stat().st_mtime_ns, default=None)
            return f"Older than {relative(newer)}" if newer and newer.stat().st_mtime_ns > built else ""
        try:
            self.pandoc_docx(name)
        except BuildError:
            return "Changed since the last build"
        return ""


def word_inputs(project: Project, name: str) -> list[Path]:
    """What a document's Word file is built from: its sources, includes and
    images, project configuration, metadata, values, bibliography, style and reference document."""
    found = {project.path(key) for key in ("metadata", "values", "bibliography", "csl", "reference_doc")}
    found.add(project.root / CONFIG_NAME)
    from .labels import cited  # numbers of the documents it cites
    pending, seen = [path for doc in (name, *cited(project, name)) for path in project.document_files(doc)], set()
    while pending:
        path = Path(pending.pop()).resolve()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        pending.extend(includes(path))
        found.update(project.root / match[1] for match in IMAGE.finditer(prose(read(path))))
    return [path for path in found | seen if path.is_file()]
