<#
.SYNOPSIS
  Undo the fleet-root move: remove the junction and put the original tree back.

.DESCRIPTION
  Dry-run by default. With -Execute: refuses while any process runs from the
  tree, removes the junction <Source> (the link only; the copy under <Target>
  is left untouched), and renames <Source>.pre-move back to <Source>.

  The original tree still holds the projects.toml from before the move, so the
  backup points at its old source and destination again with no edit. Anything
  written under <Target> since the cut-over (commits, app state) stays there
  and is not carried back; compare and copy it by hand before deleting <Target>.

  Runbook: docs/fleet-root-move.md.
#>
[CmdletBinding()]
param(
    [string]$Source = 'E:\automation',
    [string]$Target = 'C:\automation',
    [switch]$Execute
)

$ErrorActionPreference = 'Stop'
function Stop-Run([string]$text) { Write-Host "FAILED: $text"; exit 1 }

$preMove = "$Source.pre-move"
$item = Get-Item -LiteralPath $Source -Force -ErrorAction SilentlyContinue
if (-not $item) { Stop-Run "$Source does not exist" }
if ($item.LinkType -ne 'Junction') { Stop-Run "$Source is not a junction: nothing to roll back" }
Write-Host "junction: $Source -> $($item.Target)"
if (-not (Test-Path -LiteralPath $preMove)) { Stop-Run "$preMove not found: the original tree is gone, rollback impossible" }
if (-not (Get-ChildItem -LiteralPath $preMove -Force | Select-Object -First 1)) { Stop-Run "$preMove is empty" }

$needle = [IO.Path]::GetFullPath($Target).TrimEnd('\')
$procs = @(Get-CimInstance Win32_Process | Where-Object {
    $_.ProcessId -ne $PID -and (
        ($_.ExecutablePath -and ($_.ExecutablePath.StartsWith($needle + '\', [StringComparison]::OrdinalIgnoreCase) -or
                                 $_.ExecutablePath.StartsWith($Source + '\', [StringComparison]::OrdinalIgnoreCase))) -or
        ($_.CommandLine -and ($_.CommandLine.Replace('/', '\').IndexOf($Source, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
                              $_.CommandLine.Replace('/', '\').IndexOf($needle, [StringComparison]::OrdinalIgnoreCase) -ge 0)))
})
Write-Host "processes running from the tree: $($procs.Count)"
$procs | Group-Object Name | ForEach-Object { Write-Host ("  {0} x{1}" -f $_.Name, $_.Count) }

Write-Host "plan: remove junction $Source; rename $preMove -> $Source; leave $Target in place"
if (-not $Execute) { Write-Host 'DRY-RUN: nothing was changed.'; exit 0 }
if ($procs.Count) { Stop-Run 'stop every process running from the tree first' }

# DirectoryInfo.Delete() without recursion removes the junction itself, never its target's contents.
(Get-Item -LiteralPath $Source -Force).Delete()
if (Test-Path -LiteralPath $Source) { Stop-Run "junction $Source still present" }
Rename-Item -LiteralPath $preMove -NewName (Split-Path $Source -Leaf)
$back = Get-Item -LiteralPath $Source -Force
if ($back.LinkType) { Stop-Run "$Source is still a link after rename" }
Write-Host "ROLLED BACK: $Source is the original tree again. $Target is untouched; delete it once nothing from it is needed."
exit 0
