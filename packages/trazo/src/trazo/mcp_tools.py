"""The tools the trazo MCP server (trazo.mcp) lists, and the
instructions it sends with its initialize reply."""

from __future__ import annotations

# Codex appends them to every tool's description, so they stay short.
INSTRUCTIONS = ("trazo creates, builds, checks and previews Markdown papers. Pass `project`, "
                "the absolute path of the paper folder, to every tool (paper_create takes "
                "`folder`, the new one). These tools run outside the app's command sandbox: use them, "
                "not `scripts/trazo`, for what they do. In a sandbox (the ChatGPT app's), "
                "`scripts/trazo` commands that start Chrome fail: `verify` and `diagram check`, "
                "`render`, `export` or `describe` when the paper has diagrams; ask the user once to "
                "approve running them outside it. Use the comments skill for review comments; "
                "the open and close skills require explicit user invocation.")

PROJECT_ARG = {"project": {"type": "string", "description": "absolute path of the paper folder (holds trazo.toml)"}}


def schema(required=("project",), **extra):
    return {"type": "object", "properties": {**PROJECT_ARG, **extra}, "required": list(required),
            "additionalProperties": False}


TOOLS = [
    {"name": "setup_status",
     "description": "Whether trazo is set up; launches no tool's --version: the per-user install and its "
                    "version against the plugin's, where uv, Pandoc and Chrome were found, the machine file's date, "
                    "and `next`: \"setup\" (run the setup skill), \"update\" (run setup again), "
                    "\"plugin\" (the install is newer: the user updates the plugin in the app) or \"ok\". "
                    "`next` ignores a project's own commands.pandoc, so check `pandoc.found` too. "
                    "`project` (optional) applies its trazo.toml.",
     "inputSchema": schema(required=())},
    {"name": "doctor",
     "description": "trazo doctor --json: each tool's path, version, where it was found and what to "
                    "fix; `ok` is false when a required tool is missing. `project` (optional) applies its trazo.toml.",
     "inputSchema": schema(required=())},
    {"name": "build",
     "description": "trazo build: the log and outputs, or the error. With `submission`, also the "
                    "separate submission files; call `check` next.",
     "inputSchema": schema(submission={"type": "boolean", "description": "also build the separate submission files"},
                           force={"type": "boolean", "description": "run every stage, even when fresh"})},
    {"name": "check",
     "description": "trazo submission check --json; succeeds even when the report has errors, so read "
                    "`errors` and `warnings`. Not the citation check.",
     "inputSchema": schema()},
    {"name": "paper_create",
     "description": "Create a paper from the template in `folder` (empty or new), without Copier or git "
                    "(trazo new). Returns the folder, template version and number of files written.",
     "inputSchema": {"type": "object", "properties": {
         "folder": {"type": "string", "description": "absolute path of the new paper folder (empty or missing)"},
         "kind": {"type": "string", "enum": ["article", "report"], "description": "document kind"},
         "example": {"type": "boolean", "description": "start with the example paper (false: empty sections)"},
         "title": {"type": "string", "description": "the paper's title"},
         "project_name": {"type": "string", "description": "lowercase name for pyproject.toml "
                                                           "(default: from the folder name)"}},
         "required": ["folder", "kind", "example", "title"], "additionalProperties": False}},
    {"name": "paper_update",
     "description": "Update a paper made by trazo new to this trazo's template "
                    "(trazo update). Returns replaced, added, removed, needs_merge (merge each <file>.new "
                    "into <file>, then delete it), skipped, notes and up_to_date.",
     "inputSchema": schema()},
    {"name": "preview_start",
     "description": "Regenerate changed values, tables and figures, then start the live preview or reuse "
                    "this folder's (also another chat's) once it shows them. Returns its URL (port 8765, else a "
                    "free one), kept in .build/preview.json.",
     "inputSchema": schema()},
    {"name": "preview_stop",
     "description": "Stop the paper's preview, also one another session started (`started_here` false).",
     "inputSchema": schema()},
]


# For the apps' approval prompts; paper_update removes files.
for tool in TOOLS:
    tool["annotations"] = {"readOnlyHint": tool["name"] in ("setup_status", "doctor", "check"),
                           "destructiveHint": tool["name"] == "paper_update"}
