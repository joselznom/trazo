# Add a scenario

How to write an acceptance scenario; the levels and how to run them are in [README.md](README.md).

1. Add a row to `catalog.toml` (`id` is `<level>-<name>`; `source` is
   `docs/plugin.md check N` or `new`; `areas` are the repo globs whose change
   selects it, on top of the shared list, see "What selects and what caches" in README.md).
   A row without a function is reported "not automated".
2. Write the function in a module of `n1/` or `n2/` (modules are imported
   by themselves):

```python
from ..context import Context
from ..drive import McpClient, run_hook
from ..registry import Skip, scenario
from ..sandbox import busy_port

@scenario("n1-setup-status-ok", needs=("darwin", "seed"))
def setup_status_ok(ctx: Context) -> None:
    sb, head = ctx.sandbox, ctx.checkout()      # head: this checkout, stamped 99.0.1
    sb.install(head)                            # seed clone + `setup.sh --offline`
    mcp = McpClient(sb, sb.plugin(head), cwd=sb.work)
    mcp.initialize()
    assert mcp.call("setup_status").data["next"] == "ok"
    assert mcp.close()[0] == 0
```

A scenario passes when it returns, fails on any exception (an `assert` with a
message is the usual way) and is skipped on `raise Skip("reason")`.
`needs` is a subset of `darwin`, `seed` (the data seed exists) and `online`
(`--online` was given); the runner skips it with the reason otherwise.

## What a scenario gets

- `ctx.sandbox` (`sandbox.py`): `home`, `data` (the default data folder under
  `home`, also `TRAZO_DATA_DIR`), `claude_config`, `codex_home`, `tmp`, `work`
  (put papers here). `env(extra=None, data_dir=True)` is the environment of an
  app-launched plugin (`PATH=/usr/bin:/bin`, `HOME`, `TMPDIR`, the config
  folders, `TRAZO_DATA_DIR`) plus `extra` (a `None` value removes a variable).
  `run(argv, env=, cwd=, timeout=, input=, check=)` runs to completion and
  records the command; `spawn(...)` starts a process that cleanup stops. Every
  `run` and `spawn` child leads its own process group, and cleanup stops those
  groups and the descendants of what it finds (children are collected before
  parents are signalled). A process the harness did not start, such as a log
  viewer of the owner with its cwd in the sandbox, is never signalled: the
  evidence lists it as "left running". A server that detached itself (new
  session) after its parent died is not found either, so it is listed too.
  `busy_port(8765, ctx.evidence)`, `free_port()` and `port_free(p)` help
  with ports (`busy_port` holds the port when it is free; when another process
  already listens there, that counts as busy, the process is left alone and the
  evidence names it from `lsof`, read-only).
- `ctx.last_release()` and `ctx.checkout(n=1)` (`versions.py`): a
  `PluginFolder` (`.version`, `.label`) for the highest `vX.Y.Z` tag, and for
  this checkout with uncommitted changes stamped `99.0.<n>`. Use two values of
  `n` for two later releases. Folders are cached under
  the cache and shared: never run from them; `sb.plugin(folder)`
  returns this scenario's own copy, which is what the apps' cache would hold.
- The install: `sb.install(folder)` fills the data folder with the seed's
  downloads and installs that plugin's trazo with `setup.sh --offline`, so
  an old install is `sb.install(ctx.last_release())`, then
  `McpClient(sb, sb.plugin(ctx.checkout()))` is the newer plugin over it.
  `sb.seed_data()` does only the first half. Both skip the scenario when
  there is no seed.
- `McpClient(sb, plugin_root, cwd=, env=)` (`drive.py`): `initialize()`
  (`connect_seconds`), `tools()`, `call(name, **arguments)` returning
  `.is_error`, `.data` (structured content) and `.text`, `rpc(...)`, `close()`
  returning `(exit code, stderr)`. `run_hook(sb, plugin_root, cwd=, source=)`
  returns `.context`, the hook's note or `None`.
- `ctx.evidence`: `note(text)`, `path(label, path)`, `data(label, value)`.
  Commands run through the sandbox, MCP calls and hook runs are recorded by
  themselves, with outputs trimmed.
