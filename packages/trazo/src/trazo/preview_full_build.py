"""Export > Full <document>: the document's full build in the background.

The preview's paths and state stay in trazo.preview, and this code reads them from
there when it runs (preview.NAME), so patching them on trazo.preview reaches it.
"""

import sys

from .local_server import Conflict
from .local_server import RequestError as CommentError


def docx_status():
    from . import preview
    return preview.DOCX_JOB.status()


def start_docx_build(request):
    """Run a document's full build in the background; one build at a time.
    The request names the document (default: the first) and its indexes."""
    from . import preview
    if not isinstance(request, dict):
        raise CommentError("request body must be a JSON object")
    name = request.get("doc", next(iter(preview.DOCS)))
    if name not in preview.DOCS:
        raise CommentError(f"unknown document: {name}")
    command = preview.PROJECT.document_build(name)
    if command is None:
        raise CommentError(f"{preview.PROJECT.titles[name]} has no full build (documents.{name}.docx and build)")
    indexes = preview.export_indexes(request)
    if not isinstance(request.get("authors", False), bool):
        raise CommentError("export options must be booleans")
    shown = " ".join(command) or f"trazo build --doc {name}"
    initial = (f"$ {shown}  (may regenerate figures and tables first)",)
    if not preview.DOCX_JOB.start([sys.executable, "-m", "trazo", "build", "--doc", name,
                           *("--" + key for key in indexes),
                                   *(["--authors"] if request.get("authors") else [])], preview.ROOT, preview.DOCX_TIMEOUT, initial,
                          doc=name, shown=shown):
        raise Conflict("a DOCX build is already running")
    return docx_status()


def cancel_docx_build(request):
    from . import preview
    if not isinstance(request, dict):
        raise CommentError("request body must be a JSON object")
    if not preview.DOCX_JOB.stop("cancelled"):
        raise Conflict("no DOCX build is running")
    return docx_status()
