# Claude Code status line — Windows PowerShell
# Reads the status JSON from stdin and prints one line.
# Format: 4% | sonnet | app-launcher (main)   (context % USED first, color-coded)

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

# --- used context % (pre-calculated field; omit when absent or no messages yet) ---
# Color-coded against the 400k auto-compact line (CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=40):
#   green <30, yellow 30-34, red >=35 — red means "wrap up before auto-compact fires at 40%".
# used_percentage is null early in a session and right after a /compact; omit the segment then.
$ctx_seg = ''
$used = $data.context_window.used_percentage
if ($used -ne $null) {
    $pct = [int][math]::Round($used)
    $esc = [char]27
    if     ($pct -ge 35) { $col = "$esc[31m" }   # red
    elseif ($pct -ge 30) { $col = "$esc[33m" }   # yellow
    else                 { $col = "$esc[32m" }   # green
    $ctx_seg = "$col$pct%$esc[0m"
}

# --- assemble segments, skipping empty ones (context % first, then model, then dir/branch) ---
$segments = @()
if ($ctx_seg) { $segments += $ctx_seg }
if ($model)   { $segments += $model }
if ($dir_seg) { $segments += $dir_seg }

Write-Host ($segments -join ' | ')
