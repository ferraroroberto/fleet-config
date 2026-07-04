# Claude Code status line — Windows PowerShell
# Reads the status JSON from stdin and prints one line.
# Format: 4%c - 5%s - 10%w | sonnet | app-launcher (main)   (ctx/session/weekly used; ctx+session+weekly color-coded)

$input_text = [Console]::In.ReadToEnd()
if (-not $input_text) { exit 0 }
try { $data = $input_text | ConvertFrom-Json } catch { exit 0 }

# --- directory basename ---
$dir = $data.workspace.current_dir
if (-not $dir) { $dir = $data.cwd }
$basename = ''
if ($dir) {
    $basename = Split-Path -Leaf $dir.TrimEnd('\').TrimEnd('/')
}

# --- git branch (run in the actual cwd from the JSON, skip optional locks) ---
$branch = ''
if ($dir -and (Test-Path $dir -ErrorAction SilentlyContinue)) {
    $branch = git -C $dir --no-optional-locks branch --show-current 2>$null
    if ($LASTEXITCODE -ne 0) { $branch = '' }
    if ($branch) { $branch = $branch.Trim() }
}

# --- first segment: "basename (branch)" or just "basename" ---
$dir_seg = ''
if ($basename -and $branch) {
    $dir_seg = "$basename ($branch)"
} elseif ($basename) {
    $dir_seg = $basename
}

# --- model display name (family only: sonnet / opus / haiku) ---
$model = ''
if ($data.model -and $data.model.display_name) {
    $raw = $data.model.display_name
    if     ($raw -match 'opus')   { $model = 'opus' }
    elseif ($raw -match 'sonnet') { $model = 'sonnet' }
    elseif ($raw -match 'haiku')  { $model = 'haiku' }
    else                          { $model = $raw }
}

# --- context window % ---
# Color-coded against the 400k auto-compact line (CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=40):
#   green <30, yellow 30-34, red >=35 — red means "wrap up before auto-compact fires at 40%".
# used_percentage is null early in a session and right after a /compact; omit then.
$ctx_str = ''
$used = $data.context_window.used_percentage
if ($used -ne $null) {
    $pct = [int][math]::Round($used)
    $esc = [char]27
    if     ($pct -ge 35) { $col = "$esc[31m" }   # red
    elseif ($pct -ge 30) { $col = "$esc[33m" }   # yellow
    else                 { $col = "$esc[32m" }   # green
    $ctx_str = "$col${pct}%c$esc[0m"
}

# --- rate limits: session (5h rolling) and weekly (7d) ---
$five_h  = $data.rate_limits.five_hour.used_percentage
$seven_d = $data.rate_limits.seven_day.used_percentage

$esc = [char]27

# --- build usage segment: ctx - session - weekly (omit absent parts) ---
$usage_parts = @()
if ($ctx_str) { $usage_parts += $ctx_str }
if ($five_h -ne $null) {
    $p5 = [int][math]::Round($five_h)
    if     ($p5 -ge 80) { $c5 = "$esc[31m" }
    elseif ($p5 -ge 60) { $c5 = "$esc[33m" }
    else                { $c5 = '' }
    $usage_parts += "${c5}${p5}%s$(if ($c5) { "$esc[0m" })"
}
if ($seven_d -ne $null) {
    $p7 = [int][math]::Round($seven_d)
    if     ($p7 -ge 80) { $c7 = "$esc[31m" }
    elseif ($p7 -ge 60) { $c7 = "$esc[33m" }
    else                { $c7 = '' }
    $usage_parts += "${c7}${p7}%w$(if ($c7) { "$esc[0m" })"
}
$usage_seg = $usage_parts -join ' '

# --- assemble final line: usage first so it survives PTY cutoff ---
$segments = @()
if ($usage_seg) { $segments += $usage_seg }
if ($model)     { $segments += $model }
if ($dir_seg)   { $segments += $dir_seg }

Write-Host ($segments -join ' | ')

# --- rate-limits cache (app-launcher#326 / fleet-config#259) ---
# Pure additive side effect: cache the same 5h/7d numbers this script just
# printed (plus each window's reset epoch, which the printed line omits) to
# a shared JSON file so a non-statusline process (the app-launcher Board /
# Coding tabs) can read current usage without being the statusline itself.
# rate_limits is absent entirely before the first API response in a new
# session (per Claude Code's own statusline docs) — in that case $data.rate_limits
# is $null and both window reads below just come back $null, which is exactly
# the "unavailable window" shape the cache is meant to tolerate. Any failure
# here is swallowed — this must never break the statusline itself, which has
# already printed by this point regardless of what follows.
try {
    $five_h_resets  = $data.rate_limits.five_hour.resets_at
    $seven_d_resets = $data.rate_limits.seven_day.resets_at

    $five_h_window = $null
    if ($five_h -ne $null -or $five_h_resets -ne $null) {
        $five_h_window = [ordered]@{ used_percentage = $five_h; resets_at = $five_h_resets }
    }
    $seven_d_window = $null
    if ($seven_d -ne $null -or $seven_d_resets -ne $null) {
        $seven_d_window = [ordered]@{ used_percentage = $seven_d; resets_at = $seven_d_resets }
    }

    $cache = [ordered]@{
        five_hour    = $five_h_window
        seven_day    = $seven_d_window
        captured_at  = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    }

    # CLAUDE_HOOKS_STATE_DIR overrides the directory, same env var
    # hooks/session_state.py already honors — so tests never touch the real
    # shared file (and can't race a live session's own concurrent write).
    $stateDir = $env:CLAUDE_HOOKS_STATE_DIR
    if (-not $stateDir) { $stateDir = Join-Path $HOME '.claude\hooks\state' }
    if (-not (Test-Path $stateDir)) { New-Item -ItemType Directory -Force -Path $stateDir | Out-Null }
    $target = Join-Path $stateDir 'rate-limits.json'
    $json = $cache | ConvertTo-Json -Depth 4

    # tmp+move-force, retried — this file is written by every concurrently
    # running Claude Code session's statusline re-render, so a naive direct
    # write risks a reader seeing a torn write (mirrors session_state.py's
    # _write_rows tmp+os.replace-with-retry pattern). [System.IO.File]::Replace
    # was tried first but throws "The path is empty" on this PowerShell
    # 5.1 / .NET Framework combo regardless of backup-path value (verified
    # empirically) — Move-Item -Force reliably overwrites an existing target.
    for ($attempt = 0; $attempt -lt 3; $attempt++) {
        $tmp = Join-Path $stateDir ("rate-limits.json.tmp.$([guid]::NewGuid().ToString('N'))")
        try {
            # [System.Text.Encoding]::UTF8 (the static instance) writes a UTF-8
            # BOM by default — that BOM breaks Python's json.loads on the
            # reader side (json.JSONDecodeError on the leading ﻿), which
            # would make every read_rate_limits() call see this as a "corrupt
            # file" forever (verified empirically). A `New-Object
            # System.Text.UTF8Encoding($false)` instance writes plain UTF-8
            # with no preamble.
            $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
            [System.IO.File]::WriteAllText($tmp, $json, $utf8NoBom)
            Move-Item -LiteralPath $tmp -Destination $target -Force
            break
        } catch {
            Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
            Start-Sleep -Milliseconds (50 * ($attempt + 1))
        }
    }
} catch {
    # Advisory-only — never let a cache-write failure surface to the user.
}
