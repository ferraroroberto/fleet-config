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
