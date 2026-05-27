# Claude Code status line — Windows PowerShell
# Reads the status JSON from stdin and prints one line.
# Format: app-launcher (main) | Claude Opus 4.7 | 78% | cc 48k^ 3kv

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

# --- model display name ---
$model = ''
if ($data.model -and $data.model.display_name) {
    $model = $data.model.display_name
}

# --- remaining context % (pre-calculated field; omit when absent or no messages yet) ---
$ctx_seg = ''
$remaining = $data.context_window.remaining_percentage
if ($remaining -ne $null) {
    $ctx_seg = [string][math]::Round($remaining) + '%'
}

# --- today's Claude Code token totals (parsed from ~/.claude/projects JSONL files) ---
$tok_seg = ''
if ($dir) {
    try {
        # Encode the path the same way Claude Code does:
        # E:\automation\local-llm-hub  →  E--automation-local-llm-hub
        $encoded = $dir -replace ':\\', '--' -replace '\\', '-' -replace '/', '-'
        $proj_dir = Join-Path $env:USERPROFILE ".claude\projects\$encoded"
        if (Test-Path $proj_dir -ErrorAction SilentlyContinue) {
            $today_utc = (Get-Date).ToUniversalTime().Date
            $total_in  = 0
            $total_out = 0
            Get-ChildItem $proj_dir -Filter '*.jsonl' -ErrorAction SilentlyContinue |
                Where-Object { $_.LastWriteTimeUtc.Date -ge $today_utc } |
                ForEach-Object {
                    Get-Content $_.FullName -ErrorAction SilentlyContinue |
                        ForEach-Object {
                            try {
                                $obj = $_ | ConvertFrom-Json
                                if ($obj.type -eq 'assistant' -and $obj.message.usage) {
                                    $u = $obj.message.usage
                                    $total_in  += [int]($u.input_tokens)             +
                                                  [int]($u.cache_creation_input_tokens)
                                    $total_out += [int]($u.output_tokens)
                                }
                            } catch {}
                        }
                }
            if ($total_in -gt 0 -or $total_out -gt 0) {
                $inv = [System.Globalization.CultureInfo]::InvariantCulture
                function fmt_k([long]$n) {
                    if ($n -ge 1000000) { return [string]::Format($inv, '{0:0.#}M', $n / 1000000.0) }
                    if ($n -ge 1000)    { return [string]::Format($inv, '{0:0.#}k', $n / 1000.0) }
                    return $n.ToString()
                }
                $tok_seg = 'cc ' + (fmt_k $total_in) + '^ ' + (fmt_k $total_out) + 'v'
            }
        }
    } catch {}
}

# --- assemble segments, skipping empty ones ---
$segments = @()
if ($dir_seg) { $segments += $dir_seg }
if ($model)   { $segments += $model }
if ($ctx_seg) { $segments += $ctx_seg }
if ($tok_seg) { $segments += $tok_seg }

Write-Host ($segments -join ' | ')
