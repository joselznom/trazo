---
name: close
description: Close trazo's live preview
disable-model-invocation: true
---

# Close the trazo Preview

trazo's `preview_stop` (the trazo MCP server's tool) stops the
paper's preview server, also one another chat started. The Claude app's
Browser pane has a `preview_stop` of its own: never use it for this.

1. Find the paper: the chat's folder, or the folder the user named, when
   it has `trazo.toml`. Otherwise ask which paper; if there is none,
   say so and stop.
2. When trazo's `preview_stop` is missing, this chat has no trazo
   tools, so it started no preview: tell the user that a chat with the
   trazo tools closes it (it also stops when the app quits), and
   stop.
3. Call trazo's `preview_stop` with the paper folder's absolute path
   as `project`.
4. Reply in the user's language, in one line: the preview is closed, or
   none was running. When `stopped` is true, add that the browser tab
   showing it can be closed too.
