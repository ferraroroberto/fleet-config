<#
.SYNOPSIS
    Uninstall fleet-config: remove only the links install.ps1 created.

.DESCRIPTION
    Reads ~/.claude/.fleet-config-installed.json and removes each recorded
    junction/hardlink, then deletes the manifest. Never touches anything not
    in the manifest -- real files and unrelated directories under ~/.claude/
    are left untouched.

    Also removes the three pieces of state install.ps1 writes OUTSIDE the
    junction/symlink manifest, none of which a manifest-only uninstall would
    ever reach (fleet-config#681):
      - the OTel project-attribution $PROFILE block (fleet-config#310), from
        both the pwsh and Windows PowerShell 5.1 profile files;
      - the agy context-filter plugin under
        ~/.gemini/config/plugins/fleet-context-filter (fleet-config#546);
      - the Copilot CLI context-filter hook at
        ~/.copilot/hooks/fleet-context-filter.json (fleet-config#547).
    Each mirrors its Install-* counterpart and removes only what that function
    writes -- a hook file this repo did not author is left alone.

    Does NOT modify ~/.claude/settings.json -- remove the hooks block yourself
    if you want it gone.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$ClaudeHome     = Join-Path $env:USERPROFILE '.claude'
$ManifestPath   = Join-Path $ClaudeHome '.fleet-config-installed.json'
# Mirrors install.ps1's own $GeminiConfHome / $CopilotHome.
$GeminiConfHome = Join-Path (Join-Path $env:USERPROFILE '.gemini') 'config'
$CopilotHome    = Join-Path $env:USERPROFILE '.copilot'

function Remove-OtelProjectProfileHook {
    # Mirrors Install-OtelProjectProfileHook's marker in install.ps1 --
    # strips exactly the BEGIN..END block, leaving the rest of the profile
    # (if the user has added anything else to it by then) untouched.
    $markerBegin = '# fleet-config:claude-otel-project BEGIN (docs/otel-project-attribution.md, fleet-config#310)'
    $markerEnd   = '# fleet-config:claude-otel-project END'

    $profiles = @(
        (Join-Path $env:USERPROFILE 'Documents\PowerShell\Microsoft.PowerShell_profile.ps1'),
        (Join-Path $env:USERPROFILE 'Documents\WindowsPowerShell\Microsoft.PowerShell_profile.ps1')
    )

    foreach ($profilePath in $profiles) {
        if (-not (Test-Path $profilePath)) {
            Write-Host "MISSING $profilePath (no profile file -- nothing to remove)" -ForegroundColor DarkGray
            continue
        }

        $lines = Get-Content -Path $profilePath
        $beginIdx = [array]::IndexOf($lines, $markerBegin)
        if ($beginIdx -lt 0) {
            Write-Host "SKIP    $profilePath (OTel project hook not present)" -ForegroundColor DarkGray
            continue
        }
        $endIdx = [array]::IndexOf($lines, $markerEnd)
        if ($endIdx -lt $beginIdx) {
            Write-Host "SKIP    $profilePath (marker found but END missing -- leaving alone, edit by hand)" -ForegroundColor Yellow
            continue
        }

        $kept = @()
        for ($i = 0; $i -lt $lines.Count; $i++) {
            if ($i -ge $beginIdx -and $i -le $endIdx) { continue }
            $kept += $lines[$i]
        }
        Set-Content -Path $profilePath -Value $kept -Encoding UTF8
        Write-Host "REMOVED OTel project hook from $profilePath" -ForegroundColor Cyan
    }
}

function Remove-AgyContextFilterPlugin {
    # Counterpart to install.ps1's Install-AgyContextFilterPlugin. agy owns the
    # plugin registry as well as the copied files, so the removal goes through
    # agy's own `plugin uninstall <name>` (verified against the live CLI's
    # `agy plugin` help) rather than deleting the directory behind its back and
    # leaving a dangling registry entry. Directory left over anyway (a failed
    # or partial uninstall) -> say so; never delete a tree agy still lists.
    $installed = Join-Path $GeminiConfHome 'plugins\fleet-context-filter'
    $agy = Get-Command agy -ErrorAction SilentlyContinue
    if (-not $agy) {
        if (Test-Path $installed) {
            Write-Host "SKIP    $installed (agy CLI not on this machine -- remove by hand)" -ForegroundColor Yellow
        } else {
            Write-Host "MISSING agy plugin fleet-context-filter (nothing to remove)" -ForegroundColor DarkGray
        }
        return
    }
    if (-not (Test-Path $installed)) {
        Write-Host "MISSING agy plugin fleet-context-filter (nothing to remove)" -ForegroundColor DarkGray
        return
    }
    & $agy.Source plugin uninstall 'fleet-context-filter' | Out-Null
    if (Test-Path $installed) {
        Write-Host "SKIP    $installed (agy plugin uninstall left the files -- remove by hand)" -ForegroundColor Yellow
    } else {
        Write-Host "REMOVED agy plugin fleet-context-filter" -ForegroundColor Cyan
    }
}

function Remove-CopilotContextFilterHook {
    # Counterpart to install.ps1's Install-CopilotContextFilterHook. ~/.copilot/hooks
    # is a shared dir holding tool-managed and sister-repo files, so this removes
    # exactly the one file that installer writes and nothing else.
    $dst = Join-Path (Join-Path $CopilotHome 'hooks') 'fleet-context-filter.json'
    if (-not (Test-Path $dst)) {
        Write-Host "MISSING $dst (nothing to remove)" -ForegroundColor DarkGray
        return
    }
    Remove-Item -LiteralPath $dst -Force
    Write-Host "REMOVED $dst (copilot context-filter hook)" -ForegroundColor Cyan
}

Remove-OtelProjectProfileHook
Remove-AgyContextFilterPlugin
Remove-CopilotContextFilterHook
Write-Host ""

if (-not (Test-Path $ManifestPath)) {
    Write-Host "No manifest at $ManifestPath -- nothing else to uninstall." -ForegroundColor Yellow
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
