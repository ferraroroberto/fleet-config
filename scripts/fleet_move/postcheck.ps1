<#
.SYNOPSIS
  Prove the fleet root works from behind its junction after the move.

.DESCRIPTION
  Checks, in order: <Root> is a junction to <Target>; git reads every repo
  under <Root> through the junction; the private backup's dry-run passes (its
  repo leg now crosses from <Target>'s volume to the backup destination's).
  Without -SkipApps it also waits for the launcher's health endpoint, so run it
  again after the trays are back up. Prints one PASS/FAIL line per check and
  exits 1 if any failed. Changes nothing.

  Runbook: docs/fleet-root-move.md.
#>
[CmdletBinding()]
param(
    [string]$Root = 'E:\automation',
    [string]$Target = 'C:\automation',
    [string]$LauncherHealth = 'https://127.0.0.1:8445/healthz',
    [int]$HealthWaitSeconds = 120,
    # Default: the fleet's own backup, through the junction. Overridable for a scratch proof.
    [string]$BackupPython = '',
    [string]$BackupScript = '',
    [string]$BackupConfig = '',
    [switch]$SkipApps
)

$ErrorActionPreference = 'Stop'
$failed = 0
function Write-Result([bool]$ok, [string]$text) {
    if ($ok) { Write-Host "PASS $text" } else { Write-Host "FAIL $text"; $script:failed++ }
}

$item = Get-Item -LiteralPath $Root -Force -ErrorAction SilentlyContinue
$linkOk = $item -and $item.LinkType -eq 'Junction' -and ([IO.Path]::GetFullPath("$($item.Target)").TrimEnd('\') -ieq [IO.Path]::GetFullPath($Target).TrimEnd('\'))
Write-Result $linkOk "junction $Root -> $Target"
if (-not $linkOk) { exit 1 }

# A read-only sweep: no optional index refresh, so no index.lock is left behind.
$env:GIT_OPTIONAL_LOCKS = '0'
$repos = @(Get-ChildItem -LiteralPath $Root -Directory | Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName '.git') })
$gitBad = @()
foreach ($repo in $repos) {
    & git -C $repo.FullName status --porcelain=v1 --untracked-files=no 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { $gitBad += $repo.Name }
}
Write-Result ($gitBad.Count -eq 0) ("git status through the junction: {0} repos and worktrees, {1} failed {2}" -f $repos.Count, $gitBad.Count, ($gitBad -join ', '))

$py = $BackupPython
if (-not $py) { $py = Join-Path $Root 'fleet-config\.venv\Scripts\python.exe' }
$backup = $BackupScript
if (-not $backup) { $backup = Join-Path $Root 'fleet-config\hooks\backup_private.py' }
$backupArgs = @($backup, '--dry-run', '--no-notify', '--json')
if ($BackupConfig) { $backupArgs += @('--config', $BackupConfig) }
& $py @backupArgs | Out-Null
$code = $LASTEXITCODE
# Exit 3 is the backup's "destination unusable or on the source volume".
Write-Result ($code -eq 0) "backup dry-run exit $code (3 = destination shares the source volume)"

if (-not $SkipApps) {
    Add-Type -TypeDefinition 'public static class FleetMoveTrustLocal { public static bool Accept(object s, System.Security.Cryptography.X509Certificates.X509Certificate c, System.Security.Cryptography.X509Certificates.X509Chain ch, System.Net.Security.SslPolicyErrors e) { return true; } }' -ErrorAction SilentlyContinue
    # The launcher serves a self-signed certificate on loopback only.
    [Net.ServicePointManager]::ServerCertificateValidationCallback = [Net.Security.RemoteCertificateValidationCallback] [FleetMoveTrustLocal]::Accept
    $deadline = (Get-Date).AddSeconds($HealthWaitSeconds)
    $status = 'no answer'
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -UseBasicParsing -Uri $LauncherHealth -TimeoutSec 5
            $status = "HTTP $($r.StatusCode)"
            if ($r.StatusCode -eq 200) { break }
        } catch { $status = $_.Exception.Message }
        Start-Sleep -Seconds 5
    }
    Write-Result ($status -eq 'HTTP 200') "launcher health $LauncherHealth : $status"
}

if ($failed) { Write-Host "POSTCHECK=fail ($failed)"; exit 1 }
Write-Host 'POSTCHECK=pass'
exit 0
