# The stand-in MCP server plugin\mcp.cmd starts on Windows when trazo
# cannot serve; plugin/mcp-standin.pl is the same server for macOS, which
# explains why it exists. It speaks the same stdio JSON-RPC as trazo.mcp and
# lists one tool, setup_status, which tells the agent to run the setup skill.
# Windows PowerShell 5.1 only (every Windows 10 and 11 has it); needs no
# PATH, writes nothing.
# Usage: powershell -File mcp-standin.ps1 DATA_FOLDER, with $env:TRAZO_PLUGIN_ROOT set.
param([string]$Data = "")
$ErrorActionPreference = "Stop"

$Protocols = @("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
$Root = if ($env:TRAZO_PLUGIN_ROOT) { $env:TRAZO_PLUGIN_ROOT } else { "" }
$Utf8 = New-Object System.Text.UTF8Encoding $false
$In = New-Object System.IO.StreamReader ([Console]::OpenStandardInput()), $Utf8
$Out = New-Object System.IO.StreamWriter ([Console]::OpenStandardOutput()), $Utf8
$Out.NewLine = "`n"
$Out.AutoFlush = $true

$Tool = [ordered]@{
    name = "setup_status"
    description = ("Why trazo's tools are missing: trazo is not set up on this computer, or the installed " +
        "trazo is too old to serve them. Returns the install and ``next``: `"setup`" or `"update`" (run " +
        "the trazo:setup skill), or `"restart`" (setup is done: a new chat loads the tools), and " +
        "``message``, what to tell the user, and ``pandoc``, a Pandoc setup would use instead of downloading " +
        "one. ``project`` is ignored.")
    inputSchema = [ordered]@{
        type = "object"
        properties = [ordered]@{project = [ordered]@{type = "string"
            description = "absolute path of the paper folder (holds trazo.toml)"}}
        required = @()
        additionalProperties = $false
    }
    annotations = [ordered]@{readOnlyHint = $true; destructiveHint = $false}
}

function Join($folder, [string[]]$names) {
    foreach ($name in $names) { $folder = [System.IO.Path]::Combine($folder, $name) }
    return $folder
}

function Site-Packages { return Join $Data @("venv", "Lib", "site-packages") }

function Has-Trazo { return [System.IO.File]::Exists((Join $Data @("venv", "Scripts", "trazo.exe"))) }

# The same rule as trazo.machine.installed_version and plugin/session-start.sh:
# the name of the venv's one trazo-VERSION.dist-info folder; "" for none or more.
function Installed-Version {
    if (-not (Has-Trazo)) { return "" }
    $site = Site-Packages
    if (-not [System.IO.Directory]::Exists($site)) { return "" }
    $found = @([System.IO.Directory]::GetDirectories($site) | ForEach-Object { [System.IO.Path]::GetFileName($_) } |
        Where-Object { $_ -cmatch '^trazo-.+\.dist-info$' })
    if ($found.Count -ne 1) { return "" }
    return $found[0] -creplace '^trazo-', '' -creplace '\.dist-info$', ''
}

# The install has trazo's MCP server.
function Install-Serves {
    return (Has-Trazo) -and [System.IO.File]::Exists((Join (Site-Packages) @("trazo", "mcp.py")))
}

# Set up, as plugin/session-start.sh decides: an installed trazo and a
# non-empty machine.json, which `trazo setup` writes last.
function Set-Up($installed) {
    $machine = New-Object System.IO.FileInfo (Join $Data @("machine.json"))
    return $installed -ne "" -and $machine.Exists -and $machine.Length -gt 0
}

# A Pandoc `trazo setup` would use instead of downloading one, by its rule
# (trazo.tool_setup.existing_pandoc): on PATH; Windows has no known location.
function Existing-Pandoc {
    foreach ($folder in ("$env:PATH" -split ';')) {
        if ($folder -eq "") { continue }
        try { $path = Join $folder @("pandoc.exe") } catch { continue }
        if ([System.IO.File]::Exists($path)) { return @($path, "PATH") }
    }
    return @("", "")
}

function Plugin-Version {
    if ($Root -eq "") { return "" }
    try {
        $manifest = [System.IO.File]::ReadAllText((Join $Root @(".claude-plugin", "plugin.json")), $Utf8) | ConvertFrom-Json
    } catch { return "" }
    if ($manifest -is [System.Management.Automation.PSCustomObject] -and $manifest.version -is [string]) {
        return $manifest.version
    }
    return ""
}

function Or-Null($value) { if ($value -ne "") { return $value } return $null }

function Setup-Status {
    $installed = Installed-Version
    $plugin = Plugin-Version
    # mcp.cmd would start the real server now: setup ran during this chat.
    $checkout = $Root -ne "" -and [System.IO.File]::Exists((Join $Root @("packages", "trazo", ".venv", "Scripts", "trazo.exe")))
    if ((Set-Up $installed) -and ((Install-Serves) -or $checkout)) {
        $next = "restart"
        $message = ("trazo is now set up, but this chat started before setup and has only this stand-in " +
            "server. Tell the user to start a new chat to load all the trazo tools.")
    } elseif (Set-Up $installed) {
        $next = "update"
        $message = ("trazo $installed is installed but cannot run the plugin's MCP server, so only this " +
            "stand-in server runs. Run the trazo:setup skill: it reinstalls trazo from the plugin's " +
            "files and downloads only what is missing. After setup, a new chat loads " +
            "all the trazo tools.")
    } elseif ($installed -ne "" -and (Install-Serves)) {  # so machine.json is missing or empty
        $next = "setup"
        $message = ("trazo setup did not finish on this computer (trazo is installed but its machine file " +
            "is missing, for example after a failed download), so only this stand-in server runs. Run the " +
            "trazo:setup skill again: it downloads only what is missing. " +
            "Once it finishes, a new chat loads all the trazo tools.")
    } else {
        $next = "setup"
        $message = ("trazo is not set up on this computer, so only this stand-in server runs. Run the " +
            "trazo:setup skill: the user's request for trazo or paper work is the go-ahead. " +
            "After setup, a new chat loads all the trazo tools.")
    }
    $pandoc = Existing-Pandoc
    $same = $null
    if ($installed -ne "" -and $plugin -ne "") { $same = $installed -ceq $plugin }
    return [ordered]@{
        plugin = [ordered]@{root = (Or-Null $Root); version = (Or-Null $plugin)}
        install = [ordered]@{folder = $Data; installed = ($installed -ne ""); version = (Or-Null $installed)
            matches_plugin = $same}
        pandoc = [ordered]@{found = ($pandoc[0] -ne ""); path = (Or-Null $pandoc[0]); where = (Or-Null $pandoc[1])}
        next = $next
        message = $message
    }
}

function Encode($value) { return ConvertTo-Json -InputObject $value -Compress -Depth 20 }

function Send($message) {
    $reply = [ordered]@{jsonrpc = "2.0"}
    foreach ($key in $message.Keys) { $reply[$key] = $message[$key] }
    $Out.WriteLine((Encode $reply))
}

function Send-Error($id, $code, $text) {
    Send ([ordered]@{id = $id; error = [ordered]@{code = $code; message = $text}})
}

function Has($object, $name) {
    return $object -is [System.Management.Automation.PSCustomObject] -and
        @($object.PSObject.Properties.Name) -ccontains $name
}

function Is-Integer($value) {
    return $value -is [int] -or $value -is [long] -or ($value -is [decimal] -and [decimal]::Truncate($value) -eq $value)
}

# The message's id when it is a valid one (string or integer), else $null.
function Request-Id($message) {
    if (-not (Has $message "id")) { return $null }
    $id = $message.id
    if ($id -is [string] -or (Is-Integer $id)) { return $id }
    return $null
}

function Call-Tool($id) {
    try {
        $result = Setup-Status
        $text = ConvertTo-Json -InputObject $result -Depth 20
        $reply = [ordered]@{content = @([ordered]@{type = "text"; text = $text}); structuredContent = $result
            isError = $false}
    } catch {
        $reply = [ordered]@{content = @([ordered]@{type = "text"; text = "setup_status failed: $_"}); isError = $true}
    }
    Send ([ordered]@{id = $id; result = $reply})
}

function Handle($message) {
    $isObject = $message -is [System.Management.Automation.PSCustomObject]
    if ($isObject -and -not (Has $message "method") -and ((Has $message "result") -or (Has $message "error"))) { return }
    $id = Request-Id $message
    if (-not $isObject -or -not ($message.jsonrpc -is [string]) -or $message.jsonrpc -cne "2.0" -or
            -not ($message.method -is [string]) -or ((Has $message "id") -and $null -eq $id)) {
        Send-Error $id -32600 "Invalid Request"
        return
    }
    if (-not (Has $message "id")) { return }  # a notification: no reply
    $method = $message.method
    $params = if (Has $message "params") { $message.params } else { [pscustomobject]@{} }
    if (-not ($params -is [System.Management.Automation.PSCustomObject])) {
        Send-Error $id -32602 "params must be an object"
    } elseif ($method -ceq "initialize") {
        $asked = $params.protocolVersion
        $version = if ($asked -is [string] -and $Protocols -ccontains $asked) { $asked } else { $Protocols[0] }
        $plugin = Plugin-Version
        if ($plugin -eq "") { $plugin = "0" }
        Send ([ordered]@{id = $id; result = [ordered]@{
            protocolVersion = $version
            capabilities = [ordered]@{tools = @{}}
            serverInfo = [ordered]@{name = "trazo"; version = $plugin}
            instructions = ("trazo is not set up on this computer, or the installed trazo is too old: only " +
                "setup_status is available. Call it, then run the trazo:setup skill when the user asks " +
                "for trazo or paper work.")}})
    } elseif ($method -ceq "ping") {
        Send ([ordered]@{id = $id; result = @{}})
    } elseif ($method -ceq "tools/list") {
        Send ([ordered]@{id = $id; result = [ordered]@{tools = @($Tool)}})
    } elseif ($method -ceq "tools/call") {
        $name = $params.name
        $arguments = $params.arguments
        # trazo.mcp's `or {}`: absent, null, false, "", 0 or [] count as no arguments.
        if (-not $arguments -or ($arguments -is [array] -and $arguments.Count -eq 0)) { $arguments = [pscustomobject]@{} }
        if (-not ($name -is [string]) -or $name -cne "setup_status") {
            $shown = if ($name -is [string]) { $name } else { Encode $name }
            Send-Error $id -32602 "Unknown tool: $shown"
        } elseif (-not ($arguments -is [System.Management.Automation.PSCustomObject])) {
            Send-Error $id -32602 "arguments must be an object"
        } else {
            Call-Tool $id
        }
    } else {
        Send-Error $id -32601 "Method not found: $method"
    }
}

if ((Setup-Status).next -eq "update") {
    [Console]::Error.WriteLine("trazo is too old to run the MCP server; ask the agent to update it")
} else {
    [Console]::Error.WriteLine("trazo is not set up; ask the agent to set it up")
}
while ($null -ne ($line = $In.ReadLine())) {
    if ($line -notmatch '\S') { continue }
    try {
        # ConvertFrom-Json unrolls a top-level array: keep it one value.
        $message = ,($line | ConvertFrom-Json)
        $message = $message[0]
    } catch {
        Send-Error $null -32700 "Parse error"
        continue
    }
    try {
        $null = Handle $message
    } catch {
        $id = Request-Id $message
        if ($null -ne $id -and (Has $message "method")) {
            Send-Error $id -32603 "Internal error: $_"
        } else {
            [Console]::Error.WriteLine("trazo stand-in: $_")
        }
    }
}
exit 0
