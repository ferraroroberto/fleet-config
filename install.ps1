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
    [switch]$VerifyCodexSandbox,
    [switch]$VerifyGrokCompat
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
# agy (Antigravity CLI) loads plugins from ~/.gemini/config/plugins/<name>/ --
# verified live against agy 1.1.8 (fleet-config#546). Unlike every other agent,
# the wiring is agy's own `plugin install` (registry entry + file copy), not a
# link: its Go plugin scanner does not descend directory junctions (observed
# live -- a junctioned plugin dir loads 0 hooks), and file symlinks would drag
# elevation into a step agy handles fine itself. Install-AgyContextFilterPlugin
# below re-copies whenever the installed files drift from the repo source, and
# tests/run_acceptance.py fails loud on that drift between installs.
$GeminiConfHome = Join-Path (Join-Path $env:USERPROFILE '.gemini') 'config'
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

function Install-OtelProjectProfileHook {
    # fleet-config#310: wires shell/claude-otel-project.ps1 into $PROFILE for
    # both PowerShell 7 (pwsh) and Windows PowerShell 5.1, so `claude`
    # auto-tags OTEL_RESOURCE_ATTRIBUTES with the current repo name regardless
    # of which host launches it. Explicit paths (not the ambient $PROFILE)
    # because this function can run inside the elevated Windows PowerShell
    # 5.1 relaunch below, whose $PROFILE would otherwise point at the wrong
    # host's profile file, silently wiring the hook into the wrong shell.
    $sourceScript = Join-Path $RepoRoot 'shell\claude-otel-project.ps1'
    if (-not (Test-Path $sourceScript)) {
        Write-Warning "Source missing, skipping OTel project-attribution hook: $sourceScript"
        return
    }

    $markerBegin   = '# fleet-config:claude-otel-project BEGIN (docs/otel-project-attribution.md, fleet-config#310)'
    $markerEnd     = '# fleet-config:claude-otel-project END'
    $dotSourceLine = ". `"$sourceScript`""

    $profiles = @(
        (Join-Path $env:USERPROFILE 'Documents\PowerShell\Microsoft.PowerShell_profile.ps1'),
        (Join-Path $env:USERPROFILE 'Documents\WindowsPowerShell\Microsoft.PowerShell_profile.ps1')
    )

    foreach ($profilePath in $profiles) {
        $profileDir = Split-Path -Parent $profilePath
        if (-not (Test-Path $profileDir)) {
            New-Item -ItemType Directory -Path $profileDir -Force | Out-Null
        }
        if (-not (Test-Path $profilePath)) {
            New-Item -ItemType File -Path $profilePath | Out-Null
        }

        $content = Get-Content -Path $profilePath -Raw -ErrorAction SilentlyContinue
        if ($null -eq $content) { $content = '' }

        if ($content.Contains($markerBegin)) {
            if ($content.Contains($dotSourceLine)) {
                Write-Host "OK      $profilePath (OTel project hook already wired)" -ForegroundColor Green
            } else {
                Write-Host "BLOCKED $profilePath (OTel project hook marker present but points elsewhere)" -ForegroundColor Yellow
            }
            continue
        }

        $block = "`n$markerBegin`n$dotSourceLine`n$markerEnd`n"
        Add-Content -Path $profilePath -Value $block -Encoding UTF8
        Write-Host "LINKED  $profilePath  ->  $sourceScript  (profile dot-source)" -ForegroundColor Cyan
    }
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

function Invoke-GrokCompatVerification {
    # fleet-config#491: Grok Build gets NO links of its own. It reaches the
    # global instructions, the fleet skills, and every hook in this repo through
    # its own Claude-compatibility scanning, which is on by default. That makes
    # those defaults load-bearing config we do not own -- if xAI flips a cell,
    # or a managed/requirements layer turns one off, Grok silently loses the
    # fleet's guards with nothing in this repo changing. So verify the reach
    # itself rather than a link that deliberately does not exist.
    $grok = Get-Command grok -ErrorAction SilentlyContinue
    if (-not $grok) {
        $fallback = Join-Path $env:USERPROFILE '.grok\bin\grok.exe'
        if (Test-Path $fallback) { $grokPath = $fallback }
        else { throw "Grok CLI not found on PATH or at $fallback; cannot verify compat reach." }
    } else {
        $grokPath = $grok.Source
    }

    Write-Host ""
    Write-Host "Verifying Grok Claude-compatibility reach..." -ForegroundColor Cyan

    $raw = & $grokPath inspect --json 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "grok inspect --json failed with exit code $LASTEXITCODE."
    }

    try { $report = ($raw | Out-String) | ConvertFrom-Json }
    catch { throw "grok inspect --json returned unparseable output." }

    $problems = @()

    # 1. The compat cells this repo's reach depends on must be enabled.
    foreach ($surface in @('rules', 'agents', 'skills', 'hooks')) {
        $cell = $report.externalCompat.cells |
            Where-Object { $_.vendor -eq 'claude' -and $_.surface -eq $surface }
        if (-not $cell) {
            $problems += "compat.claude.$surface : not reported by grok inspect"
        } elseif (-not $cell.enabled) {
            $problems += "compat.claude.$surface : DISABLED (source: $($cell.source))"
        } else {
            Write-Host "OK      compat.claude.$surface enabled (source: $($cell.source))" -ForegroundColor Green
        }
    }

    # 2. The global instructions must actually resolve, not merely be scannable.
    $claudeMd = $report.projectInstructions |
        Where-Object { $_.scope -eq 'global' -and $_.path -like '*\.claude\*' }
    if ($claudeMd) {
        Write-Host "OK      global instructions reach Grok ($($claudeMd[0].path), ~$($claudeMd[0].approxTokens) tokens)" -ForegroundColor Green
    } else {
        $problems += "global instructions: no ~/.claude context file resolved"
    }

    # 3. The fleet skills must resolve through one of the scanned roots.
    $fleetSkills = $report.skills | Where-Object {
        $_.source.path -like '*\.claude\skills\*' -or $_.source.path -like '*\.agents\skills\*'
    }
    if ($fleetSkills.Count -gt 0) {
        Write-Host "OK      $($fleetSkills.Count) fleet skill(s) resolve inside Grok" -ForegroundColor Green
    } else {
        $problems += "skills: no fleet skill resolved from ~/.claude/skills or ~/.agents/skills"
    }

    if ($problems.Count -gt 0) {
        foreach ($p in $problems) { Write-Host "FAILED  $p" -ForegroundColor Red }
        throw "Grok compat reach is broken -- the fleet's hooks/skills/instructions may not be active in Grok sessions. See docs/adding-a-coding-harness.md."
    }

    Write-Host "OK      Grok reaches the fleet config through Claude compatibility." -ForegroundColor Green
}

# What to install. Each entry: { kind = 'junction'|'symlink'; source = <relative to repo>; target = <relative to base home>; base = 'claude'|'agents'|'codex'|'pi'|'copilot' (default 'claude') }
# NOTE: Grok Build (~/.grok) deliberately has NO entry here -- it reads ~/.claude
# directly via [compat.claude]. Verify that reach with -VerifyGrokCompat rather
# than adding a duplicate link (fleet-config#491).
$Items = @(
    @{ kind = 'junction'; source = 'hooks';                  target = 'hooks' },
    @{ kind = 'junction'; source = 'commands';               target = 'commands' },
    @{ kind = 'junction'; source = 'skills';                 target = 'skills' },
    # fleet-config#153: the ONE machine-local copy of tray_lifecycle.ps1 --
    # every sister app's tray.bat calls it at this stable path instead of
    # vendoring a byte-copy into each repo. Junction, not symlink: it is a
    # directory entry, same mechanism as hooks/ and skills/ above.
    @{ kind = 'junction'; source = 'tray';                   target = 'tray' },
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
    # agent's user-scope path so a single edit reaches them too. Pi also auto-discovers global
    # extensions from ~/.pi/agent/extensions, so its Claude-style footer/statusline is junctioned
    # from this repo (#188). Pi/Copilot settings files are tool-managed -- documented non-goals (#189).
    # Skills, however, ARE wired: Pi reads ~/.agents/skills (junctioned above); Copilot scans its
    # own ~/.copilot/skills/<name>/SKILL.md (auto-discovered, no enable step), junctioned below (#160).
    @{ kind = 'symlink';  source = 'global-CLAUDE.md';       target = 'AGENTS.md';              base = 'pi' },
    @{ kind = 'junction'; source = 'pi/extensions';          target = 'extensions';             base = 'pi' },
    @{ kind = 'symlink';  source = 'global-CLAUDE.md';       target = 'copilot-instructions.md'; base = 'copilot' },
    @{ kind = 'junction'; source = 'skills';                 target = 'skills';                 base = 'copilot' }
)

foreach ($baseDir in @($ClaudeHome, $AgentsHome, $CodexHome, $PiAgentHome, $CopilotHome)) {
    if (-not (Test-Path $baseDir)) {
        New-Item -ItemType Directory -Path $baseDir | Out-Null
    }
}

# agy (Antigravity CLI) context-filter plugin: registry + copy via agy's own
# installer, re-run only when the installed files drift from the repo source
# (see the $GeminiConfHome note above; fleet-config#546). Silent no-op when the
# agy CLI is not on this machine.
function Install-AgyContextFilterPlugin {
    $agy = Get-Command agy -ErrorAction SilentlyContinue
    if (-not $agy) { return }
    $source = Join-Path $RepoRoot 'agy\plugins\fleet-context-filter'
    $installed = Join-Path $GeminiConfHome 'plugins\fleet-context-filter'
    $fresh = $true
    foreach ($name in @('plugin.json', 'hooks.json')) {
        $src = Join-Path $source $name
        $dst = Join-Path $installed $name
        # Newline-insensitive compare: git renormalizes the repo copy to CRLF
        # while the installed copy keeps its installed bytes — not drift.
        if (-not (Test-Path $dst) -or (((Get-Content $src -Raw) -replace "`r`n", "`n") -ne ((Get-Content $dst -Raw) -replace "`r`n", "`n"))) {
            $fresh = $false
        }
    }
    if ($fresh) {
        Write-Host "OK      agy plugin fleet-context-filter (installed copy matches repo)" -ForegroundColor Green
        return
    }
    & $agy.Source plugin install $source | Out-Null
    Write-Host "INSTALL agy plugin fleet-context-filter (agy plugin install: registry + copy)" -ForegroundColor Cyan
}

# Copilot CLI context-filter hook (fleet-config#547): a per-feature file under
# ~/.copilot/hooks/ (Copilot's native user-scope hook location, camelCase
# events). A drift-guarded copy, same pattern as the agy plugin above — the
# hooks dir holds tool-managed and sister-repo files, so a whole-dir junction
# is off the table, and a file symlink would drag elevation into a step a
# byte-compare copy handles fine. tests/run_acceptance.py fails loud on drift.
function Install-CopilotContextFilterHook {
    if (-not (Get-Command copilot -ErrorAction SilentlyContinue)) { return }
    $src = Join-Path $RepoRoot 'copilot-hooks\fleet-context-filter.json'
    $dstDir = Join-Path $CopilotHome 'hooks'
    $dst = Join-Path $dstDir 'fleet-context-filter.json'
    if ((Test-Path $dst) -and (((Get-Content $src -Raw) -replace "`r`n", "`n") -eq ((Get-Content $dst -Raw) -replace "`r`n", "`n"))) {
        Write-Host "OK      copilot hook fleet-context-filter (installed copy matches repo)" -ForegroundColor Green
        return
    }
    if (-not (Test-Path $dstDir)) { New-Item -ItemType Directory -Path $dstDir | Out-Null }
    Copy-Item $src $dst -Force
    Write-Host "INSTALL copilot hook fleet-context-filter (drift-guarded copy)" -ForegroundColor Cyan
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
    # The parent exits on the child's exit code below, so it never reaches its
    # own Invoke-*Verification calls at the end of the script. Any switch not
    # forwarded here silently no-ops on exactly the machines that need the UAC
    # prompt -- a fresh install -- reporting a successful install for a
    # verification that never ran (fleet-config#681).
    if ($VerifyCodexSandbox) { $psArgs += '-VerifyCodexSandbox' }
    if ($VerifyGrokCompat)   { $psArgs += '-VerifyGrokCompat' }
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
Install-OtelProjectProfileHook
Install-AgyContextFilterPlugin
Install-CopilotContextFilterHook

Write-Host ""
Write-Host "Done. created=$created skipped=$skipped blocked=$blocked" -ForegroundColor Cyan
Write-Host "Manifest: $ManifestPath"
if ($VerifyCodexSandbox) {
    Invoke-CodexSandboxVerification
}
if ($VerifyGrokCompat) {
    Invoke-GrokCompatVerification
}
Write-Host ""
Write-Host "Next step: merge the 'hooks' block from settings.template.json into ~/.claude/settings.json,"
Write-Host "then restart Claude Code so the new hooks load."
