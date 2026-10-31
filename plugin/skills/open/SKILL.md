---
name: open
description: Open trazo's live preview
disable-model-invocation: true
---

# Open the trazo Preview

trazo's `preview_start` (the trazo MCP server's tool) runs the one
preview server the paper needs. The app's own browser tools, which in the
Claude app include another `preview_start` and a `preview_stop`, only open
pages: use them to show the URL, never to start or stop the preview.

1. Find the paper: the chat's folder, or the folder the user named, when
   it has `trazo.toml`. Otherwise ask which paper; if there is none,
   say so and stop.
2. When trazo's `preview_start` is missing, this chat runs the
   stand-in server. Call trazo's `setup_status` and follow its `next`:
   `restart` means setup already finished; `setup` or `update`, follow
   the `trazo:setup` skill. Either way, end by telling the user to
   start a new chat and ask for the preview there (in the Claude app,
   `/trazo:open`), and stop: only a new chat loads the trazo
   tools.
3. Call trazo's `preview_start` with the paper folder's absolute path
   as `project`. It starts the preview, or returns the running one
   (`reused: true`, also when another chat started it).
4. Open the returned `url` as it is: its port is another one when 8765
   is busy.
   - Claude app: the Browser pane's `preview_start` with that `url`, or
     its `navigate`. The pane can stay hidden although the tool says it
     opened: one click on the app's Browser button in the toolbar shows
     it.
   - ChatGPT app: the app's `open_in_codex` tool with
     `target: {"type": "browser", "url": <url>}`, which opens it in the
     built-in browser. Without that tool, give the URL as a link: a
     click opens it there. The app may also show the preview full
     screen from `preview_start` (an MCP App, only for some accounts):
     open the browser all the same, it is the main path.
5. Reply in the user's language with the URL; in the Claude app add
   that, if the paper is not visible, the Browser button shows it. End
   with how to close it: `/trazo:close` in the Claude app,
   `$trazo:close` in the ChatGPT app.
