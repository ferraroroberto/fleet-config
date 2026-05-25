<#
.SYNOPSIS
    Install claude-config into ~/.claude/ via Windows junctions and hardlinks.

.DESCRIPTION
    Creates a junction from C:/Users/<you>/.claude/hooks -> <repo>/hooks so edits
    in either path appear in the other instantly (no copy step).

    Junctions on Windows do NOT need admin or Developer Mode -- they work for
    directories on the same NTFS volume.

    The install is idempotent:
      - Existing junction pointing at the repo path  -> no-op (reports OK)
      - Existing junction pointing elsewhere         -> refuses with the existing target
      - Existing real directory                      -> refuses with "rename then re-run"
      - Nothing there                                -> creates the junction

    Records every link it creates in ~/.claude/.claude-config-installed.json so
    uninstall.ps1 can remove exactly what it added.

.NOTES
    Run from any directory; the script resolves its own location.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$RepoRoot       = Split-Path -Parent $MyInvocation.MyCommand.Path
$ClaudeHome     = Join-Path $env:USERPROFILE '.claude'
$ManifestPath   = Join-Path $ClaudeHome '.claude-config-installed.json'

# What to install. Each entry: { kind = 'junction'|'hardlink'; source = <relative to repo>; target = <relative to ~/.claude> }
$Items = @(
    @{ kind = 'junction'; source = 'hooks';    target = 'hooks' },
    @{ kind = 'junction'; source = 'commands'; target = 'commands' }
)

if (-not (Test-Path $ClaudeHome)) {
    New-Item -ItemType Directory -Path $ClaudeHome | Out-Null
}

# Load (or initialize) manifest
$manifest = @{}
if (Test-Path $ManifestPath) {
    try {
        $raw = Get-Content $ManifestPath -Raw -ErrorAction Stop
        if ($raw.Trim()) {
            $loaded = $raw | ConvertFrom-Json -ErrorAction Stop
            $loaded.PSObject.Properties | ForEach-Object { $manifest[$_.Name] = $_.Value }
        }
    } catch {
        Write-Warning "Existing manifest at $ManifestPath is unreadable; starting fresh."
        $manifest = @{}
    }
}

$created  = 0
$skipped  = 0
$blocked  = 0

foreach ($item in $Items) {
    $sourceAbs = Join-Path $RepoRoot   $item.source
    $targetAbs = Join-Path $ClaudeHome $item.target

    if (-not (Test-Path $sourceAbs)) {
        Write-Warning "Source missing, skipping: $sourceAbs"
        continue
    }

    if (Test-Path $targetAbs) {
        $existing = Get-Item $targetAbs -Force
        if ($existing.LinkType -in @('Junction', 'SymbolicLink', 'HardLink')) {
            # Compare normalized link target with desired source
            $linkTarget = $null
            try { $linkTarget = (Get-Item $targetAbs -Force).Target }
            catch { $linkTarget = $existing.Target }

            $linkTargetStr = if ($linkTarget -is [array]) { $linkTarget[0] } else { $linkTarget }
            $sourceFull    = (Resolve-Path $sourceAbs).Path

            if ($linkTargetStr -and ((Resolve-Path $linkTargetStr -ErrorAction SilentlyContinue).Path -eq $sourceFull)) {
                Write-Host "OK      $targetAbs (already linked to repo)" -ForegroundColor Green
                $manifest[$item.target] = @{ kind = $item.kind; source = $sourceAbs; installed_at = (Get-Date -Format 'o') }
                $skipped++
                continue
            } else {
                Write-Host "BLOCKED $targetAbs (linked to a different target: $linkTargetStr)" -ForegroundColor Yellow
                $blocked++
                continue
            }
        } else {
            Write-Host "BLOCKED $targetAbs (real directory/file exists)" -ForegroundColor Yellow
            Write-Host "        Rename or move it, then re-run install.ps1." -ForegroundColor Yellow
            $blocked++
            continue
        }
    }

    switch ($item.kind) {
        'junction' {
            New-Item -ItemType Junction -Path $targetAbs -Target $sourceAbs | Out-Null
            Write-Host "LINKED  $targetAbs  ->  $sourceAbs  (junction)" -ForegroundColor Cyan
        }
        'hardlink' {
            New-Item -ItemType HardLink -Path $targetAbs -Target $sourceAbs | Out-Null
            Write-Host "LINKED  $targetAbs  ->  $sourceAbs  (hardlink)" -ForegroundColor Cyan
        }
        default {
            throw "Unknown link kind: $($item.kind)"
        }
    }

    $manifest[$item.target] = @{ kind = $item.kind; source = $sourceAbs; installed_at = (Get-Date -Format 'o') }
    $created++
}

# Persist manifest
$manifest | ConvertTo-Json -Depth 5 | Set-Content -Path $ManifestPath -Encoding UTF8

Write-Host ""
Write-Host "Done. created=$created skipped=$skipped blocked=$blocked" -ForegroundColor Cyan
Write-Host "Manifest: $ManifestPath"
Write-Host ""
Write-Host "Next step: merge the 'hooks' block from settings.template.json into ~/.claude/settings.json,"
Write-Host "then restart Claude Code so the new hooks load."
