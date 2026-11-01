@echo off
rem Run trazo on Windows from PowerShell or cmd, as scripts/trazo does on
rem macOS and in Git Bash: the per-user install the plugin's setup made,
rem outside this project's pyproject.toml and uv.lock. When it is not the
rem release trazo.toml pins (version = "vX.Y.Z"), it runs anyway and says so.
rem   TRAZO_DEV=..\trazo\packages\trazo  run a local checkout (with uv)
rem   TRAZO_DATA_DIR=...  the per-user folder (default %LOCALAPPDATA%\trazo)
setlocal
for %%I in ("%~dp0..") do set "root=%%~fI"
if defined TRAZO_DATA_DIR (set "data=%TRAZO_DATA_DIR%") else set "data=%LOCALAPPDATA%\trazo"
rem trazo reads and writes UTF-8 everywhere, as on macOS (Python's UTF-8 mode).
set "PYTHONUTF8=1"
if not defined TRAZO_DEV goto installed
rem uv: on PATH, else the one setup unpacked, with its Python and cache kept
rem in the data folder (trazo.machine.uv_environment).
set "uv=uv"
where uv >nul 2>nul && goto dev
set "uv=%data%\uv\uv.exe"
if not exist "%uv%" (
    echo scripts/trazo: uv not found: set up trazo ^(the plugin's setup skill^) or install uv 1>&2
    exit /b 127
)
set "TRAZO_DATA_DIR=%data%"
set "UV_CACHE_DIR=%data%\cache"
set "UV_PYTHON_INSTALL_DIR=%data%\python"
set "UV_PYTHON_BIN_DIR=%data%\bin"
set "UV_TOOL_DIR=%data%\tools"
set "UV_TOOL_BIN_DIR=%data%\bin"
set "UV_MANAGED_PYTHON=1"
:dev
"%uv%" run --quiet --project "%TRAZO_DEV%" trazo %*
exit /b %errorlevel%

:installed
rem The first version = "..." line of trazo.toml, without quotes or a comment.
set "version="
for /f "usebackq tokens=1,* delims==" %%A in (`findstr /b /r /c:"version *=" "%root%\trazo.toml"`) do (
    if not defined version set "version=%%B"
)
if defined version for /f "tokens=1 delims=# " %%V in ("%version%") do set "version=%%~V"
if not defined version (
    echo scripts/trazo: trazo.toml has no version 1>&2
    exit /b 2
)
if not exist "%data%\venv\Scripts\python.exe" (
    echo scripts/trazo: trazo is not set up on this computer: set it up ^(the plugin's setup skill^) 1>&2
    exit /b 127
)
rem Its version is the name of its one trazo-VERSION.dist-info folder, as
rem scripts/trazo reads it; more than one is a broken install.
set "have="
set "count=0"
for /d %%D in ("%data%\venv\Lib\site-packages\trazo-*.dist-info") do (
    set /a count+=1
    set "have=%%~nxD"
)
if not "%count%"=="1" set "have="
if defined have set "have=%have:~6,-10%"
set "release=%version%"
if "%release:~0,1%"=="v" set "release=%release:~1%"
if "%have%"=="%release%" goto run
set "shown=another trazo"
if defined have set "shown=trazo %have%"
echo scripts/trazo: this paper pins trazo %version%, but %shown% is set up here: update the older one (the paper with the plugin's paper_update, or the plugin and its setup) 1>&2
:run
rem python.exe -m trazo, not trazo.exe: setup cannot replace a running trazo.exe.
"%data%\venv\Scripts\python.exe" -P -m trazo %*
exit /b %errorlevel%
