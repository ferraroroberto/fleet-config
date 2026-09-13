<#
.SYNOPSIS
    Generic shim that wires Claude Code hook stdin -> a Python hook module.

.DESCRIPTION
    Claude Code invokes hooks as shell commands, passing the hook payload on
    stdin. On this Windows machine Claude Code routes through Git Bash, which
    strips backslashes in `settings.json` command strings -- so all hook
    commands point at this PowerShell script (forward-slash path) and pass
    the hook name as a parameter.

    The shim reads the whole of stdin as UTF-8 (per the global gotcha:
    `$input` is unreliable, and `[Console]::In` decodes with the OEM code
    page), then pipes it UTF-8-encoded to the Python hook module.

    Exit code propagates: 0 = allow, 2 = block, anything else = treated as 0
    by Claude Code.

.PARAMETER Hook
    The hook module name (without `.py` extension), located alongside this
    script under `hooks/`.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$Hook
)

$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$hookPath  = Join-Path $scriptDir "$Hook.py"

if (-not (Test-Path $hookPath)) {
    Write-Error "Hook module not found: $hookPath"
    exit 0   # missing hook is a config bug, not a tool-call problem -- don't block
}

# UTF-8 on both legs (fleet-config#912). `[Console]::In` decodes with the OEM
# code page (ibm850) and `$payload | python` encodes with `$OutputEncoding`
# (us-ascii in Windows PowerShell 5.1), so every non-ASCII codepoint reached the
# hook as `?`. Wrap the raw stdin stream instead of setting
# `[Console]::InputEncoding`, which calls the console API a windowless hook
# process may not have. BOM-less, so the payload's first byte stays `{`.
$utf8 = New-Object System.Text.UTF8Encoding($false)
$stdin = New-Object System.IO.StreamReader([Console]::OpenStandardInput(), $utf8)
$payload = $stdin.ReadToEnd()
$OutputEncoding = $utf8

# Prefer a real Python executable. WindowsApps aliases for `py` / `python` can
# hang in non-interactive hook processes, so skip those stubs if they appear
# first on PATH.
$pythonCmd = $null
$candidates = @(
    # fleet-config owns these hooks and ships its own project .venv
    # (fleet-config#350); prefer it, falling back to a system Python if the
    # venv is ever absent (the Test-Path guard below keeps this safe).
    "E:\automation\fleet-config\.venv\Scripts\python.exe",
    "$env:LOCALAPPDATA\Python\bin\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
)

foreach ($path in $candidates) {
    if ($path -and (Test-Path -LiteralPath $path)) {
        $pythonCmd = $path
        break
    }
}

if (-not $pythonCmd) {
    foreach ($name in @('py', 'python')) {
        $cmds = Get-Command $name -All -ErrorAction SilentlyContinue
        if ($cmds) {
            foreach ($c in $cmds) {
                if ($c.Source -notlike "*\WindowsApps\*") {
                    $pythonCmd = $c.Source
                    break
                }
            }
        }
        if ($pythonCmd) { break }
    }
}

if (-not $pythonCmd) {
    Write-Error "Neither 'py' nor 'python' is on PATH -- fleet-config hooks cannot run."
    exit 0
}

# Pipe stdin to the Python module
$payload | & $pythonCmd $hookPath
exit $LASTEXITCODE
