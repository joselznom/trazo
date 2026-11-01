@echo off
rem Start trazo's MCP server (trazo mcp) on Windows, as plugin/mcp.sh does
rem on macOS: the per-user install made by setup, else, in a checkout of
rem this repository, the development venv (made by `make test-package`).
rem When neither can serve, start mcp-standin.ps1, a stand-in server whose
rem one tool, setup_status, offers setup. The apps run this file for the
rem command plugin/mcp, found through PATHEXT. It prints nothing itself:
rem stdout carries the protocol. Needs no PATH and writes nothing.
setlocal
for %%I in ("%~dp0..") do set "root=%%~fI"
rem Both apps share this per-user folder (trazo.machine.data_dir).
if defined TRAZO_DATA_DIR (set "data=%TRAZO_DATA_DIR%") else set "data=%LOCALAPPDATA%\trazo"
rem setup_status compares the installed trazo with this plugin's version.
set "TRAZO_PLUGIN_ROOT=%root%"
rem Both manifests pass --no-views, so the preview opens in the app's browser
rem only (see plugin/mcp.sh for why).
if "%~1"=="--no-views" set "TRAZO_MCP_VIEWS=0"
rem trazo reads and writes UTF-8 everywhere, as on macOS (Python's UTF-8 mode).
set "PYTHONUTF8=1"
rem An install without trazo\mcp.py (0.5.0 and older) has no `mcp` command.
rem It runs as python.exe -m trazo, not trazo.exe: Windows cannot replace a
rem running program, and setup reinstalls trazo.exe while this server runs.
if exist "%data%\venv\Scripts\trazo.exe" if exist "%data%\venv\Lib\site-packages\trazo\mcp.py" (
    "%data%\venv\Scripts\python.exe" -P -m trazo mcp
    exit /b
)
if exist "%root%\packages\trazo\.venv\Scripts\trazo.exe" (
    "%root%\packages\trazo\.venv\Scripts\python.exe" -P -m trazo mcp
    exit /b
)
rem Windows PowerShell 5.1 comes with every Windows 10 and 11.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%root%\plugin\mcp-standin.ps1" "%data%"
