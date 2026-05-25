<#
.SYNOPSIS
    Uninstall claude-config: remove only the links install.ps1 created.

.DESCRIPTION
    Reads ~/.claude/.claude-config-installed.json and removes each recorded
    junction/hardlink, then deletes the manifest. Never touches anything not
    in the manifest -- real files and unrelated directories under ~/.claude/
    are left untouched.

    Does NOT modify ~/.claude/settings.json -- remove the hooks block yourself
    if you want it gone.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$ClaudeHome   = Join-Path $env:USERPROFILE '.claude'
$ManifestPath = Join-Path $ClaudeHome '.claude-config-installed.json'

if (-not (Test-Path $ManifestPath)) {
    Write-Host "No manifest at $ManifestPath -- nothing to uninstall." -ForegroundColor Yellow
    return
}

$manifest = Get-Content $ManifestPath -Raw | ConvertFrom-Json

$removed = 0
$missing = 0
$skipped = 0

foreach ($prop in $manifest.PSObject.Properties) {
    $target    = Join-Path $ClaudeHome $prop.Name
    $entryKind = $prop.Value.kind

    if (-not (Test-Path $target)) {
        Write-Host "MISSING $target (already gone)" -ForegroundColor DarkGray
        $missing++
        continue
    }

    $info = Get-Item $target -Force
    if ($info.LinkType -notin @('Junction', 'SymbolicLink', 'HardLink')) {
        Write-Host "SKIP    $target (not a link any more -- leaving alone)" -ForegroundColor Yellow
        $skipped++
        continue
    }

    # Remove the link itself, not the target
    [System.IO.Directory]::Delete($target, $false) 2>$null
    if (Test-Path $target) {
        # Fallback for hardlinks on files
        Remove-Item -LiteralPath $target -Force
    }
    Write-Host "REMOVED $target ($entryKind)" -ForegroundColor Cyan
    $removed++
}

Remove-Item -LiteralPath $ManifestPath -Force
Write-Host ""
Write-Host "Done. removed=$removed missing=$missing skipped=$skipped" -ForegroundColor Cyan
Write-Host "Reminder: edit ~/.claude/settings.json by hand if you want the 'hooks' block gone too."
