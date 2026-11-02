---
name: update
user-invocable: false
description: Use when trazo's `setup_status` says `next` is not "ok", or when the user asks to update trazo or a paper.
---

# Update trazo

The user is a researcher, not a developer: an update is a chore you do
for them. Their request for trazo or paper work, or to update, is the
go-ahead. Write to the user in the language they write in, in plain words
about what changes for them. Commands, paths, versions, tool names and
tool results stay inside your tool calls; your messages carry only the
plain status.

1. Call trazo's `setup_status` with `project`, the paper folder (the
   chat's folder or the one the user named, when it holds `trazo.toml`),
   unless this chat just did. Follow its `next`:
   - "plugin": step 2.
   - "paper": step 3.
   - "setup" or "update": follow the `trazo:setup` skill, which installs
     the new trazo; the paper follows in the next chat.
   - "ok": when the user asked to update, tell them in one line that
     trazo and the paper are up to date. Then go on with their request.

2. **The plugin.** Tell the user in one sentence that a newer trazo is
   out and you are installing it. Call trazo's `plugin_update`.
   - `ok`: tell them it is installed and that a new chat finishes the
     update (it sets up the new version and brings the paper up to
     date). Then go on with their request: this chat's tools still work.
   - Otherwise, give them the steps for their app (`app`; when it is
     null, the app this chat runs in), then the same new-chat line:
     - Claude app: in the plugin settings, update trazo's marketplace,
       then trazo. In Claude Code in a terminal: `/plugin`, then
       Marketplaces, trazo, Update marketplace.
     - ChatGPT app: quit the app and open it again; it updates its
       plugins when it starts.

3. **The paper.** Tell the user in one sentence that you are bringing the
   paper up to date with the latest trazo, and that their writing stays
   as it is. Call trazo's `paper_update` with `project`.
   - For each path in `needs_merge`, merge `<path>.new` into `<path>`
     yourself: keep the user's changes and add what the new version
     brings, then delete `<path>.new`.
   - Tell them it is done, and what it brings in plain words: the latest
     improvements to the paper's tools and agent instructions. Then go on
     with their request.
   - On an error, explain it in plain words and go on with their request.
