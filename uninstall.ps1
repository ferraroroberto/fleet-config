<#
.SYNOPSIS
    Uninstall fleet-config: remove only the links install.ps1 created.

.DESCRIPTION
    Reads ~/.claude/.fleet-config-installed.json and removes each recorded
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
$ManifestPath = Join-Path $ClaudeHome '.fleet-config-installed.json'

if (-not (Test-Path $ManifestPath)) {
    Write-Host "No manifest at $ManifestPath -- nothing to uninstall." -ForegroundColor Yellow
    return
}

$manifest = Get-Content $ManifestPath -Raw | ConvertFrom-Json

$removed = 0
$missing = 0
$skipped = 0

foreach ($prop in $manifest.PSObject.Properties) {
    # Prefer the absolute target recorded by install.ps1 (handles non-~/.claude bases like
    # ~/.agents); fall back to the legacy ~/.claude-relative key for older manifests.
    $target    = if ($prop.Value.target) { $prop.Value.target } else { Join-Path $ClaudeHome $prop.Name }
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

    # Remove the link itself, not the target. The .NET delete call is type-specific:
    # Directory::Delete handles junctions and directory symlinks; File::Delete handles
    # file symlinks and hardlinks. Using Directory::Delete on a file reparse point throws
    # "The directory name is invalid." (see #136), so branch on the type we already fetched.
    if ($info.PSIsContainer) { [System.IO.Directory]::Delete($target, $false) }
    else                     { [System.IO.File]::Delete($target) }
    Write-Host "REMOVED $target ($entryKind)" -ForegroundColor Cyan
    $removed++
}

Remove-Item -LiteralPath $ManifestPath -Force
Write-Host ""
Write-Host "Done. removed=$removed missing=$missing skipped=$skipped" -ForegroundColor Cyan
Write-Host "Reminder: edit ~/.claude/settings.json by hand if you want the 'hooks' block gone too."
