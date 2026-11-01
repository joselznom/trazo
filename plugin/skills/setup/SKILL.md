---
name: setup
user-invocable: false
description: Use when trazo is not set up on this computer (trazo's `setup_status` tool or a hook says so, or Pandoc or uv is missing), when a diagram or a PNG/PDF export fails because Chrome is not found, or when the user asks to set up or repair trazo.
---

# Set Up trazo

The user is a researcher, not a developer: setup is a chore you do for
them, quietly. Their request for trazo or paper work, or to set up or
repair trazo, is the go-ahead: start without asking. In a chat about
something else, a hook's note alone does not start setup.

Write to the user in the language they write in, in plain words about
what they can do afterwards (write, preview, export to Word). Commands,
paths, versions, tool names and terminal output stay inside your tool
calls; your messages carry only the plain status.

1. Call trazo's `setup_status` (no `project` needed), unless this chat
   already did. When no trazo tool is available at all (a minimal Linux
   whose Perl lacks the modules of the stand-in server), go on with step
   2 with an empty `plugin.root`. Keep its `plugin.root`. Then follow its
   `next`:
   - "plugin": the installed trazo is newer than this plugin (the other
     app's plugin installed it). Tell the user to update the trazo plugin
     in this app's plugin settings, and stop; setup would install the
     older trazo.
   - "restart": setup is done; tell the user to start a new chat, and stop.
   - "ok": trazo is set up. When the user asked to repair it, or
     `chrome.found` is false and they asked to set trazo up or their task
     needs a diagram or a PNG or PDF export (setup downloads the Chrome
     it lacks), go on; otherwise tell them it is ready in one line, and
     stop.
   - "setup" or "update": go on.

   This skill never creates or changes a paper, even when the chat's
   folder is empty.

2. Tell the user in one sentence that you are getting trazo ready on
   this computer and that it takes about a minute.

3. Run, with `plugin.root` from step 1 (if it is empty, the folder three
   levels above this skill's folder), as it is: no pipe such as
   `| tail`, which hides its exit status:

   ```bash
   sh "<plugin.root>/plugin/scripts/setup.sh"
   ```

   When the chat's folder, or a folder the user named, holds
   `trazo.toml`, add `--paper "<its absolute path>"` after `setup.sh`:
   setup then says whether that paper was made with an older trazo.

   It downloads only what is missing and also handles updates. In the
   ChatGPT app it needs the app's approval to use the network and to
   write outside the workspace: request that approval with the command
   (the user only approves the app's prompt); in the sandbox its
   downloads fail.

4. If it exited with an error, run it once more: it resumes and
   downloads only what is missing. If it fails again, explain its last
   error line in plain words (no internet connection, a full disk, a
   blocked download) and stop.

5. Once it succeeds, read its last lines yourself, then tell the user
   that trazo is ready and that a new chat loads it. Add only what
   changes what they can do:
   - This chat had no trazo tool at all (step 1): Claude Code remembers
     a failed server for about 15 minutes, so a new chat may still lack
     the tools. Tell the user that, in Claude Code, `/mcp`, then trazo,
     then Reconnect loads them at once.
   - A Chrome line (still no Chrome, Edge or Chromium found): setup
     downloads one on a Mac or an x64 Linux computer that has none, so
     this means the download failed (its error is just above; explain it
     as in step 4) or the computer is an ARM Linux one, where the user
     installs Chromium with the system's package manager. Diagrams and the
     preview's PNG and PDF exports stay off; offer to try again, or give
     the Google Chrome download page from that line.
   - A "Chrome does not start" line: on Linux, the computer lacks the
     system libraries Chrome needs. Diagrams and the PNG and PDF exports
     stay off until the user installs Google Chrome or Chromium with the
     system's package manager, which brings them; say so in plain words.
   - A `paper: older template` line: the paper was made with an earlier
     version of trazo. Suggest updating it in the new chat, in plain
     words about what it brings (the latest improvements to its tools and
     instructions; their own writing stays as it is), and leave the tool,
     script and version names out. Update it only when they ask.
   - A `quarantine:` line other than `none` (macOS), or no Pandoc
     version: the system blocked a downloaded tool. Say so in plain words
     instead of "ready", and stop; the quarantine attribute stays as it is.

## If the user asks what setup installs

uv, Python 3.12 and Pandoc (only when the computer has none), downloaded
from their official releases and checked against fixed checksums, and
trazo itself from the plugin's files. When the computer has no Chrome,
Edge or Chromium, also a headless Chrome from Chrome for Testing (about
110 MB), used only for diagrams and PNG and PDF exports; an installed
browser is never replaced. Everything goes into one folder,
`~/Library/Application Support/trazo` on a Mac, `~/.local/share/trazo` on
Linux (about 280 MB, about 500 MB with Chrome); no admin password and no
other change to the computer. Deleting that folder undoes it.
