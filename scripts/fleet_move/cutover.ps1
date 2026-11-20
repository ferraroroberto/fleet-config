<#
.SYNOPSIS
  Move the fleet root to another volume and leave a junction at the old path.

.DESCRIPTION
  Dry-run by default: runs every preflight check and prints the plan, moving
  nothing. With -Execute it copies the tree (junctions and symlinks copied as
  links, never followed), proves the copy matches, renames the original to
  <Source>.pre-move, creates the junction <Source> -> <Target>, carries the
  private backup's snapshot store to its new volume with its hardlinks intact,
  points projects.toml's [backup] table at the new source and destination, and
  runs postcheck.ps1 -SkipApps. Any failed step stops the run; nothing before
  the rename touches the original tree, and rollback.ps1 undoes the rest.

  Run it from a copy outside the tree (the runbook copies this folder to
  %TEMP%) with Windows PowerShell 5.1, after every fleet app and agent session
  is stopped: a process holding a file in the tree makes the rename fail.

  Runbook: docs/fleet-root-move.md.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File cutover.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File cutover.ps1 -Execute
#>
[CmdletBinding()]
param(
    [string]$Source = 'E:\automation',
    [string]$Target = 'C:\automation',
    [string]$OldBackupDest = (Join-Path $env:USERPROFILE 'backup\fleet-private'),
    [string]$NewBackupDest = 'E:\backup\fleet-private',
    # Default: the live fleet-config's machine-local projects.toml under <Source>.
    [string]$ProjectsToml = '',
    # Passed through to postcheck.ps1; empty means the fleet's own backup under <Source>.
    [string]$BackupPython = '',
    [string]$BackupScript = '',
    # The base interpreter the fleet venvs were made from: it lives outside the
    # tree, so running the helpers holds nothing open there.
    [string]$Python = '',
    # Free space the target volume must keep after the copy.
    [int]$MinHeadroomGB = 50,
    # Dry-run only: a saved `measure_tree.py measure` result, so a rehearsal on a
    # busy disk skips the second walk. -Execute always measures afresh.
    [string]$MeasureJson = '',
    [switch]$Execute
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$blockers = New-Object System.Collections.Generic.List[string]

function Write-Step([string]$text) { Write-Host "== $text" }
function Write-Info([string]$text) { Write-Host "   $text" }
function Add-Blocker([string]$text) { $blockers.Add($text); Write-Host "   BLOCKER: $text" }
function Stop-Run([string]$text) { Write-Host "FAILED: $text"; exit 1 }

function Test-Under([string]$path, [string]$root) {
    $p = [IO.Path]::GetFullPath($path).TrimEnd('\') + '\'
    $r = [IO.Path]::GetFullPath($root).TrimEnd('\') + '\'
    return $p.StartsWith($r, [StringComparison]::OrdinalIgnoreCase)
}

function Invoke-Helper([string]$script, [string[]]$arguments) {
    $out = & $Python -I (Join-Path $here $script) @arguments
    return @{ Code = $LASTEXITCODE; Json = ($out -join "`n") }
}

function Resolve-BasePython {
    if ($Python) { return $Python }
    $cfg = Join-Path $Source 'fleet-config\.venv\pyvenv.cfg'
    if (-not (Test-Path $cfg)) { return '' }
    $line = Get-Content $cfg | Where-Object { $_ -match '^\s*home\s*=' } | Select-Object -First 1
    if (-not $line) { return '' }
    return Join-Path ($line -replace '^\s*home\s*=\s*', '').Trim() 'python.exe'
}

$mode = 'DRY-RUN'
if ($Execute) { $mode = 'EXECUTE' }
Write-Host "Fleet root move ($mode): $Source -> $Target"

# ---- preflight -------------------------------------------------------------
Write-Step 'preflight'
$srcItem = Get-Item -LiteralPath $Source -Force -ErrorAction SilentlyContinue
if (-not $srcItem) { Stop-Run "source $Source does not exist" }
if ($srcItem.LinkType) { Stop-Run "source $Source is already a $($srcItem.LinkType) to $($srcItem.Target): the move has run" }
$preMove = "$Source.pre-move"
if (Test-Path -LiteralPath $preMove) { Add-Blocker "$preMove already exists" }
if (Test-Path -LiteralPath $Target) {
    if (Get-ChildItem -LiteralPath $Target -Force | Select-Object -First 1) { Add-Blocker "target $Target exists and is not empty" }
}
if ([IO.Path]::GetPathRoot($Source) -eq [IO.Path]::GetPathRoot($Target)) { Add-Blocker 'source and target are on the same volume' }
if (Test-Under $here $Source) {
    $msg = "this script runs from inside the tree ($here); copy the folder to `$env:TEMP first"
    if ($Execute) { Add-Blocker $msg } else { Write-Info "note: $msg" }
}

$Python = Resolve-BasePython
if (-not $Python -or -not (Test-Path $Python)) { Stop-Run "base Python not found (pass -Python)" }
if (Test-Under $Python $Source) { Add-Blocker "Python $Python is inside the tree" }
Write-Info "python: $Python"

$procs = @(Get-CimInstance Win32_Process | Where-Object {
    $_.ProcessId -ne $PID -and (
        ($_.ExecutablePath -and (Test-Under $_.ExecutablePath $Source)) -or
        ($_.CommandLine -and ($_.CommandLine.Replace('/', '\').IndexOf($Source, [StringComparison]::OrdinalIgnoreCase) -ge 0)))
})
if ($procs.Count) {
    Add-Blocker "$($procs.Count) process(es) running from the tree"
    $procs | Group-Object Name | Sort-Object Count -Descending | ForEach-Object { Write-Info ("  {0} x{1}" -f $_.Name, $_.Count) }
}

if ($MeasureJson -and -not $Execute) {
    Write-Info "reusing the measurement in $MeasureJson"
    $m = @{ Code = 0; Json = (Get-Content -Raw -LiteralPath $MeasureJson) }
} else {
    Write-Info 'measuring the tree (one walk)...'
    $m = Invoke-Helper 'measure_tree.py' @('measure', $Source, '--top', '15')
}
if ($m.Code -ne 0) { Stop-Run "measure failed (exit $($m.Code)): size not established" }
$measure = $m.Json | ConvertFrom-Json
$needGB = [math]::Ceiling($measure.bytes / 1GB)
$freeGB = [math]::Floor((Get-PSDrive ($Target.Substring(0, 1))).Free / 1GB)
Write-Info ("tree: {0} GiB, {1} files, {2} links; target free {3} GiB" -f $measure.gib, $measure.files, @($measure.reparse_points.PSObject.Properties).Count, $freeGB)
if ($freeGB - $needGB -lt $MinHeadroomGB) { Add-Blocker "target would keep $($freeGB - $needGB) GiB free, under $MinHeadroomGB" }
if (@($measure.index_locks).Count) { Add-Blocker ("index.lock present: " + (@($measure.index_locks) -join ', ')) }
if (@($measure.errors).Count) { Add-Blocker "$(@($measure.errors).Count) unreadable path(s) in the tree" }
$outside = @($measure.reparse_points.PSObject.Properties | Where-Object { -not (Test-Under ($_.Value -replace '^\\\\\?\\', '') $Source) })
Write-Info "links pointing outside the tree (copied as links, targets unchanged): $($outside.Count)"

$toml = $ProjectsToml
if (-not $toml) { $toml = Join-Path $Source 'fleet-config\hooks\projects.toml' }
if (-not (Test-Path $toml)) { Add-Blocker "projects.toml not found at $toml" }
if (-not (Test-Path $OldBackupDest)) { Add-Blocker "backup store $OldBackupDest not found" }
if ((Test-Path $NewBackupDest) -and (Get-ChildItem -LiteralPath $NewBackupDest -Force | Select-Object -First 1)) { Add-Blocker "new backup destination $NewBackupDest is not empty" }
if ([IO.Path]::GetPathRoot($NewBackupDest) -eq [IO.Path]::GetPathRoot($Target)) { Add-Blocker 'new backup destination shares the target volume' }
if (Test-Path $toml) {
    $b = Invoke-Helper 'backup_paths.py' @($toml, '--source-root', $Target, '--dest', $NewBackupDest, '--dry-run')
    if ($b.Code -ne 0) { Add-Blocker "projects.toml [backup] edit would fail (exit $($b.Code))" } else { Write-Info ("[backup] change: " + (($b.Json | ConvertFrom-Json).new | ConvertTo-Json -Compress)) }
}

$robocopyArgs = @($Source, $Target, '/E', '/COPY:DAT', '/DCOPY:DAT', '/SJ', '/SL', '/R:1', '/W:1', '/MT:16', '/NP', '/NFL', '/NDL')
Write-Step 'plan'
Write-Info ("1. robocopy " + ($robocopyArgs -join ' ') + ' /LOG:<temp log>')
Write-Info "2. compare file count, bytes and every link target: $Source vs $Target"
Write-Info "3. rename $Source -> $preMove"
Write-Info "4. junction $Source -> $Target"
Write-Info "5. copy backup store (hardlinks kept) $OldBackupDest -> $NewBackupDest"
Write-Info "6. projects.toml [backup] source_root = $Target, dest = $NewBackupDest (original kept as projects.toml.pre-move)"
Write-Info "7. postcheck.ps1 -SkipApps"

if ($blockers.Count) { Write-Host "READY=no ($($blockers.Count) blocker(s))" } else { Write-Host 'READY=yes' }
if (-not $Execute) { Write-Host 'DRY-RUN: nothing was changed.'; exit 0 }
if ($blockers.Count) { Stop-Run 'preflight blockers; nothing was changed' }

# ---- execute ---------------------------------------------------------------
$log = Join-Path $env:TEMP ("fleet-move-robocopy-{0:yyyyMMdd-HHmmss}.log" -f (Get-Date))
Write-Step "1. copy (log: $log)"
& robocopy.exe @robocopyArgs "/LOG:$log" | Out-Null
$rc = $LASTEXITCODE
# robocopy: 0-7 is success (bit flags for copied/extra/mismatched), 8+ means a copy failed.
if ($rc -ge 8) { Stop-Run "robocopy exit $rc; see $log. Original untouched; delete $Target before retrying" }
Write-Info "robocopy exit $rc"

Write-Step '2. compare'
$c = Invoke-Helper 'measure_tree.py' @('compare', $Source, $Target)
Write-Host $c.Json
if ($c.Code -ne 0) { Stop-Run "copy does not match the source. Original untouched; delete $Target before retrying" }

Write-Step '3. rename'
try { Rename-Item -LiteralPath $Source -NewName (Split-Path $preMove -Leaf) }
catch { Stop-Run "rename failed (a process still holds the tree?): $($_.Exception.Message). Original untouched" }

Write-Step '4. junction'
New-Item -ItemType Junction -Path $Source -Target $Target | Out-Null
$j = Get-Item -LiteralPath $Source -Force
if ($j.LinkType -ne 'Junction') { Stop-Run "junction not created at $Source; run rollback.ps1 -Execute" }
Write-Info "$Source -> $($j.Target)"

Write-Step '5. backup store'
$k = Invoke-Helper 'copy_linked.py' @($OldBackupDest, $NewBackupDest)
Write-Host $k.Json
if ($k.Code -ne 0) { Stop-Run "backup store copy failed; the fleet move stands, the backup still points at $OldBackupDest" }

Write-Step '6. projects.toml [backup]'
$b = Invoke-Helper 'backup_paths.py' @($toml, '--source-root', $Target, '--dest', $NewBackupDest)
Write-Host $b.Json
if ($b.Code -ne 0) { Stop-Run 'projects.toml edit failed; the backup still points at the old destination' }

Write-Step '7. postcheck'
& (Join-Path $here 'postcheck.ps1') -Root $Source -Target $Target -SkipApps -BackupPython $BackupPython -BackupScript $BackupScript -BackupConfig $toml
if ($LASTEXITCODE -ne 0) { Stop-Run 'postcheck failed; see above. rollback.ps1 undoes the move' }
Write-Host "DONE. Keep $preMove until the owner confirms deletion."
exit 0
