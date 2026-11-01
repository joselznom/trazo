# Set trazo up for this Windows user: uv, Python 3.12, trazo and Pandoc.
# The Windows twin of setup.sh (x64 only), with the same steps and exit codes:
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File plugin\scripts\setup.ps1 [--offline] [PLUGIN_ROOT]
#
# PLUGIN_ROOT (the folder with packages\trazo) defaults to the one this
# script is in. --offline (the preview's update, trazo.preview_updates)
# downloads nothing: it exits 3 when uv, Python or Pandoc must be
# downloaded, which only setup in the chat does.
# Everything goes into the per-user data folder ($env:TRAZO_DATA_DIR, else
# %LOCALAPPDATA%\trazo); no PATH, profile, registry or admin change.
# This script only bootstraps uv (pinned below, sha256 from the release's
# assets) and trazo's environment; `trazo setup` downloads Pandoc.
# The setup skill runs it (plugin/skills/setup/SKILL.md). Windows
# PowerShell 5.1 reads a script without a BOM as ANSI: keep it ASCII.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # the progress bar slows downloads down

$UvVersion = "0.13.5"
$UvSha256 = "a38b8d1902b7cfbc76b423ba1bb456f623f42064cdc72628b6075b89afd1019a"
$Name = "setup.ps1"

function Fail([string]$message, [int]$code = 1) {
    [Console]::Error.WriteLine("${Name}: $message")
    exit $code
}

$offline = $false
$rest = @($args)
if ($rest.Count -gt 0 -and $rest[0] -eq "--offline") { $offline = $true; $rest = @($rest | Select-Object -Skip 1) }
$root = if ($rest.Count -gt 0) { $rest[0] } else { [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\..")) }
$data = if ($env:TRAZO_DATA_DIR) { $env:TRAZO_DATA_DIR } else { Join-Path $env:LOCALAPPDATA "trazo" }
# A 32-bit PowerShell on 64-bit Windows says x86 here and AMD64 in the other variable.
$arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
if ($arch -ne "AMD64") { Fail "Windows x64 only for now (this is $arch)" }
if (-not (Test-Path -LiteralPath (Join-Path $root "packages\trazo\pyproject.toml") -PathType Leaf)) {
    Fail "$root is not the plugin folder (no packages\trazo)"
}

New-Item -ItemType Directory -Force -Path $data | Out-Null
$uv = Join-Path $data "uv\uv.exe"
$haveUv = $false
if (Test-Path -LiteralPath $uv -PathType Leaf) {
    try {
        $version = & $uv --version 2>$null
        $haveUv = $LASTEXITCODE -eq 0 -and "$version" -like "uv $UvVersion *"
    } catch {}  # a broken uv.exe: download it again
}
if (-not $haveUv) {
    if ($offline) { Fail "offline: uv $UvVersion must be downloaded; nothing was changed" 3 }
    $url = "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip"
    # Left by a run that was killed.
    Get-ChildItem -LiteralPath $data -Filter ".setup.*" -Force | Remove-Item -Recurse -Force
    $temporary = Join-Path $data (".setup." + [System.IO.Path]::GetRandomFileName())
    New-Item -ItemType Directory -Path $temporary | Out-Null
    try {
        Write-Output "Downloading $url"
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $zip = Join-Path $temporary "uv.zip"
        try {
            Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
        } catch {
            Fail "could not download $url ($($_.Exception.Message))"
        }
        if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash -ne $UvSha256) {
            Fail "uv-x86_64-pc-windows-msvc.zip does not have the expected sha256; nothing was installed"
        }
        Expand-Archive -LiteralPath $zip -DestinationPath (Join-Path $temporary "uv")
        if (Test-Path -LiteralPath (Join-Path $data "uv")) { Remove-Item -LiteralPath (Join-Path $data "uv") -Recurse -Force }
        Move-Item -LiteralPath (Join-Path $temporary "uv") -Destination (Join-Path $data "uv")
    } finally {
        Remove-Item -LiteralPath $temporary -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# Keep uv's Python, cache and tools inside the data folder (the same
# settings as trazo.machine.uv_environment, which builds use), and run trazo
# in Python's UTF-8 mode, as plugin\mcp.cmd does.
$env:TRAZO_DATA_DIR = $data
$env:UV_CACHE_DIR = Join-Path $data "cache"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $data "python"
$env:UV_PYTHON_BIN_DIR = Join-Path $data "bin"
$env:UV_TOOL_DIR = Join-Path $data "tools"
$env:UV_TOOL_BIN_DIR = Join-Path $data "bin"
$env:UV_MANAGED_PYTHON = "1"
$env:PYTHONUTF8 = "1"
# Offline, uv fails rather than download Python or trazo's build backend:
# exit 3 then too, so setup in the chat downloads them.
if ($offline) { $env:UV_OFFLINE = "1" }
function Run-Uv {
    & $uv @args
    if ($LASTEXITCODE -ne 0) {
        if ($offline) { Fail "offline: uv $($args[0]) failed, probably for a download" 3 }
        exit $LASTEXITCODE
    }
}
# --no-registry: uv would otherwise list this Python in the Windows registry.
Run-Uv python install --no-bin --no-registry 3.12
# A running trazo (the MCP server, a preview) holds venv\Scripts\python.exe
# open, and Windows cannot replace a running program: keep a venv that has
# one. trazo runs as python.exe -m trazo, so reinstalling it below works.
$venv = Join-Path $data "venv"
if (-not (Test-Path -LiteralPath (Join-Path $venv "Scripts\python.exe") -PathType Leaf)) {
    Run-Uv venv --quiet --clear --python 3.12 $venv
}
Run-Uv pip install --quiet --python (Join-Path $venv "Scripts\python.exe") --reinstall-package trazo (Join-Path $root "packages\trazo")
$setup = @("setup")
if ($offline) { $setup += "--offline" }
& (Join-Path $venv "Scripts\trazo.exe") @setup
exit $LASTEXITCODE
