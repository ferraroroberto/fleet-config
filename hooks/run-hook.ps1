<#
.SYNOPSIS
    Generic shim that wires Claude Code hook stdin -> a Python hook module.

.DESCRIPTION
    Claude Code invokes hooks as shell commands, passing the hook payload on
    stdin. On this Windows machine Claude Code routes through Git Bash, which
    strips backslashes in `settings.json` command strings -- so all hook
    commands point at this PowerShell script (forward-slash path) and pass
    the hook name as a parameter.

    The shim reads stdin (per the global gotcha: `[Console]::In.ReadToEnd()`
    is the only reliable way), then pipes it to the Python hook module via
    the `py` launcher.

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

$payload = [Console]::In.ReadToEnd()

# Prefer a real Python executable. WindowsApps aliases for `py` / `python` can
# hang in non-interactive hook processes, so skip those stubs if they appear
# first on PATH.
$pythonCmd = $null
$candidates = @(
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
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -notlike "*\WindowsApps\*") {
            $pythonCmd = $cmd.Source
            break
        }
    }
}

if (-not $pythonCmd) {
    Write-Error "Neither 'py' nor 'python' is on PATH -- fleet-config hooks cannot run."
    exit 0
}

# Pipe stdin to the Python module
$payload | & $pythonCmd $hookPath
exit $LASTEXITCODE
