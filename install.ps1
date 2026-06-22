<#
.SYNOPSIS
    Install fleet-config into ~/.claude/ via Windows junctions and symlinks.

.DESCRIPTION
    Creates a link from C:/Users/<you>/.claude/<name> -> <repo>/<name> so edits
    in either path appear in the other instantly (no copy step).

    Link kinds:
      - 'junction' for directories. Cross-volume OK. No admin needed.
      - 'symlink'  for files cross-volume. Requires admin (or Developer Mode).
                   The script self-elevates with a single UAC prompt only when
                   symlink work is actually pending.

    The install is idempotent:
      - Existing link pointing at the repo path  -> no-op (reports OK)
      - Existing link pointing elsewhere         -> refuses with the existing target
      - Existing real file/directory             -> refuses with "rename then re-run"
      - Nothing there                            -> creates the link

    Records every link it creates in ~/.claude/.fleet-config-installed.json so
    uninstall.ps1 can remove exactly what it added.

.NOTES
    Run from any directory; the script resolves its own location.
#>

[CmdletBinding()]
param(
    [switch]$VerifyCodexSandbox
)

$ErrorActionPreference = 'Stop'

$RepoRoot       = Split-Path -Parent $MyInvocation.MyCommand.Path
$ClaudeHome     = Join-Path $env:USERPROFILE '.claude'
$AgentsHome     = Join-Path $env:USERPROFILE '.agents'
$CodexHome      = Join-Path $env:USERPROFILE '.codex'
# Pi's config dir is ~/.pi/agent (PI_CODING_AGENT_DIR), where it reads a user-scope AGENTS.md;
# Copilot reads ~/.copilot/copilot-instructions.md. Both verified empirically (#189).
$PiAgentHome    = Join-Path (Join-Path $env:USERPROFILE '.pi') 'agent'
$CopilotHome    = Join-Path $env:USERPROFILE '.copilot'
$ManifestPath   = Join-Path $ClaudeHome '.fleet-config-installed.json'

# Link targets live under a base home. 'claude' (default) -> ~/.claude; 'agents' -> ~/.agents
# (the cross-agent skills location Codex reads); 'codex' -> ~/.codex (Codex's own home, for
# AGENTS.md, hooks/, prompts/, hooks.json); 'pi' -> ~/.pi/agent and 'copilot' -> ~/.copilot
# (each agent's user-scope context-file home, #189). Keep targets base-relative so one repo
# source can be linked into more than one home.
function Get-BaseHome([string]$base) {
    switch ($base) {
        'agents'  { $AgentsHome }
        'codex'   { $CodexHome }
        'pi'      { $PiAgentHome }
        'copilot' { $CopilotHome }
        default   { $ClaudeHome }
    }
}

# A manifest key must be unique across bases: the bare target 'skills' is used by both
# ~/.claude/skills and ~/.agents/skills, so non-default bases get a 'base/target' key.
function Get-ManifestKey($item) {
    if ($item.base -and $item.base -ne 'claude') { "$($item.base)/$($item.target)" } else { $item.target }
}

function Invoke-CodexSandboxVerification {
    $codex = Get-Command codex -ErrorAction SilentlyContinue
    if (-not $codex) {
        throw "Codex CLI not found on PATH; cannot verify sandbox startup."
    }
    $codexPath = $codex.Source

    Write-Host ""
    Write-Host "Verifying Codex workspace-write sandbox..." -ForegroundColor Cyan

    $probe = 'Print exactly: codex-sandbox-ok'
    $output = & $codexPath exec --sandbox workspace-write $probe 2>&1
    $exitCode = $LASTEXITCODE
    $text = $output | Out-String
    $trimmedText = $text.TrimEnd()

    if ($exitCode -ne 0) {
        Write-Host $trimmedText
        throw "Codex workspace-write sandbox verification failed with exit code $exitCode. Use --sandbox danger-full-access only as a temporary fallback."
    }

    if ($text -notmatch 'codex-sandbox-ok') {
        Write-Host $trimmedText
        throw "Codex workspace-write sandbox ran, but the sentinel output was missing."
    }

    Write-Host "OK      Codex workspace-write sandbox completed." -ForegroundColor Green
}

# What to install. Each entry: { kind = 'junction'|'symlink'; source = <relative to repo>; target = <relative to base home>; base = 'claude'|'agents' (default 'claude') }
$Items = @(
    @{ kind = 'junction'; source = 'hooks';                  target = 'hooks' },
    @{ kind = 'junction'; source = 'commands';               target = 'commands' },
    @{ kind = 'junction'; source = 'skills';                 target = 'skills' },
    @{ kind = 'junction'; source = 'skills';                 target = 'skills';                 base = 'agents' },
    @{ kind = 'symlink';  source = 'statusline-command.ps1'; target = 'statusline-command.ps1' },
    @{ kind = 'symlink';  source = 'global-CLAUDE.md';       target = 'CLAUDE.md' },
    @{ kind = 'symlink';  source = 'design.md';              target = 'design.md' },
    @{ kind = 'symlink';  source = 'design.dark.md';         target = 'design.dark.md' },
    # Codex (~/.codex): mirror the same source files into Codex's own home so editing once is
    # live in both agents. Skills reach Codex AND Pi via the ~/.agents/skills junction above --
    # that is the documented user-skills path both scan (Codex per developers.openai.com/codex/skills;
    # Pi per pi.dev/docs/skills, which also reads ~/.agents/skills). All four agents use the same
    # SKILL.md format, so a junction is the whole port -- no per-agent translation (#160).
    @{ kind = 'junction'; source = 'hooks';                  target = 'hooks';                  base = 'codex' },
    @{ kind = 'junction'; source = 'commands';               target = 'prompts';                base = 'codex' },
    @{ kind = 'symlink';  source = 'global-CLAUDE.md';       target = 'AGENTS.md';              base = 'codex' },
    @{ kind = 'symlink';  source = 'codex-hooks.json';       target = 'hooks.json';             base = 'codex' },
    # Pi (~/.pi/agent) and Copilot (~/.copilot): link the one global context file into each
    # agent's user-scope path so a single edit reaches them too. Pi/Copilot expose no hook or
    # statusline surface and their settings files are tool-managed -- documented non-goals (#189).
    # Skills, however, ARE wired: Pi reads ~/.agents/skills (junctioned above); Copilot scans its
    # own ~/.copilot/skills/<name>/SKILL.md (auto-discovered, no enable step), junctioned below (#160).
    @{ kind = 'symlink';  source = 'global-CLAUDE.md';       target = 'AGENTS.md';              base = 'pi' },
    @{ kind = 'symlink';  source = 'global-CLAUDE.md';       target = 'copilot-instructions.md'; base = 'copilot' },
    @{ kind = 'junction'; source = 'skills';                 target = 'skills';                 base = 'copilot' }
)

foreach ($baseDir in @($ClaudeHome, $AgentsHome, $CodexHome, $PiAgentHome, $CopilotHome)) {
    if (-not (Test-Path $baseDir)) {
        New-Item -ItemType Directory -Path $baseDir | Out-Null
    }
}

# Self-elevation pre-pass: file symlinks require admin (or Developer Mode) on Windows.
# Junctions and hardlinks do not. So we only relaunch under UAC if there is real
# symlink work pending. Reinstalls that find the symlinks already in place stay UAC-free.
function Test-IsElevated {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal $id).IsInRole(
        [Security.Principal.WindowsBuiltinRole]::Administrator
    )
}

$needsElevation = $false
foreach ($item in $Items) {
    if ($item.kind -ne 'symlink') { continue }
    $sourceAbs = Join-Path $RepoRoot          $item.source
    $targetAbs = Join-Path (Get-BaseHome $item.base) $item.target
    if (-not (Test-Path $sourceAbs)) { continue }
    if (-not (Test-Path $targetAbs)) { $needsElevation = $true; break }
    $existing = Get-Item $targetAbs -Force
    if ($existing.LinkType -ne 'SymbolicLink') { $needsElevation = $true; break }
    $linkTarget = $existing.Target
    $linkTargetStr = if ($linkTarget -is [array]) { $linkTarget[0] } else { $linkTarget }
    $resolved = (Resolve-Path $linkTargetStr -ErrorAction SilentlyContinue).Path
    if ($resolved -ne (Resolve-Path $sourceAbs).Path) { $needsElevation = $true; break }
}

if ($needsElevation -and -not (Test-IsElevated)) {
    Write-Host "Symlink creation requires admin (cross-volume file linking). Requesting UAC..." -ForegroundColor Yellow
    $psExe   = 'C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'
    $psArgs  = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath)
    $proc    = Start-Process -FilePath $psExe -ArgumentList $psArgs -Verb RunAs -Wait -PassThru
    exit $proc.ExitCode
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
    $sourceAbs   = Join-Path $RepoRoot               $item.source
    $targetAbs   = Join-Path (Get-BaseHome $item.base) $item.target
    $manifestKey = Get-ManifestKey $item

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
                $manifest[$manifestKey] = @{ kind = $item.kind; source = $sourceAbs; target = $targetAbs; installed_at = (Get-Date -Format 'o') }
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
        'symlink' {
            New-Item -ItemType SymbolicLink -Path $targetAbs -Target $sourceAbs | Out-Null
            Write-Host "LINKED  $targetAbs  ->  $sourceAbs  (symlink)" -ForegroundColor Cyan
        }
        default {
            throw "Unknown link kind: $($item.kind)"
        }
    }

    $manifest[$manifestKey] = @{ kind = $item.kind; source = $sourceAbs; target = $targetAbs; installed_at = (Get-Date -Format 'o') }
    $created++
}

# Persist manifest
$manifest | ConvertTo-Json -Depth 5 | Set-Content -Path $ManifestPath -Encoding UTF8

Write-Host ""
Write-Host "Done. created=$created skipped=$skipped blocked=$blocked" -ForegroundColor Cyan
Write-Host "Manifest: $ManifestPath"
if ($VerifyCodexSandbox) {
    Invoke-CodexSandboxVerification
}
Write-Host ""
Write-Host "Next step: merge the 'hooks' block from settings.template.json into ~/.claude/settings.json,"
Write-Host "then restart Claude Code so the new hooks load."
