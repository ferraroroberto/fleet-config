# Fleet root move

How the fleet root `E:/automation` moves to another volume (the NVMe `C:` drive) while every existing absolute path keeps working, and how the private backup's repo leg follows it. The data moves and the path stays: the tree is copied to `C:/automation`, the original is renamed to `E:/automation.pre-move`, and a directory junction `E:/automation` -> `C:/automation` takes its place. Rollback removes the junction and renames the original back.

The scripts are in `scripts/fleet_move/`:

| File | What it does |
|---|---|
| `cutover.ps1` | Preflight, copy, compare, rename, junction, backup carry-over, `[backup]` edit, post-check. Dry-run unless `-Execute`. |
| `postcheck.ps1` | Junction present, git reads every repo through it, backup dry-run passes, launcher health (unless `-SkipApps`). Changes nothing. |
| `rollback.ps1` | Removes the junction and renames `E:/automation.pre-move` back. Dry-run unless `-Execute`. |
| `measure_tree.py` | One walk of a tree that never enters a junction: bytes, files, heaviest folders, every link and its target, every `index.lock`. `compare` checks a copy against its source. |
| `copy_linked.py` | Copies the backup snapshot store keeping its hardlinks, so the store's size does not multiply by the number of snapshots. |
| `backup_paths.py` | Sets `[backup] source_root` and `dest` in `projects.toml`, keeping the original as `projects.toml.pre-move`. |

The Python helpers are stdlib-only and run with the base interpreter named in `fleet-config/.venv/pyvenv.cfg` (`-I`), never with a venv inside the tree, so nothing in the tree is held open when it is renamed.

## Why the backup destination changes in the same step

The repo leg's preflight refuses a destination on the source's volume, and it compares the resolved paths: once `E:/automation` is a junction to `C:`, the source resolves to `C:`, so the old destination under the user profile on `C:` is refused (exit 3). Changing it before the move would put `E:` source and `E:` destination together and fail the same way. So `cutover.ps1` changes both keys after the junction exists: `source_root = "C:/automation"`, `dest = "E:/backup/fleet-private"`. The existing snapshots are copied to the new destination first, with their hardlinks, so retention and next-day hardlinking continue from them. The old store stays where it is until the owner removes it.

`hooks/backup/cli.py`'s `--dry-run` applies the same-volume refusal too, so the post-check's dry-run proves the destination, not only the selection.

## Junction-sensitivity inventory

| Item | Behind the junction | Status |
|---|---|---|
| Agent-home links (`~/.claude`, `~/.codex`, `~/.agents`, `~/.pi/agent`, `~/.copilot`) made by `install.ps1` | Their targets are `E:/automation/fleet-config/...`; a link to a path under a junction resolves through it. | safe |
| Task Scheduler `\AppLauncher\` and `\AppLauncherLite\` jobs, launcher `jobs.json` `script_path` | Absolute `E:/automation/...` paths resolve through the junction. | safe |
| Startup-folder tray scripts | Absolute paths, as above. | safe |
| Worktree `.venv` junctions inside the tree | `robocopy /SJ /SL` copies a junction as a junction with its target unchanged (`E:\automation\<repo>\.venv`), which resolves through the root junction. `measure_tree.py compare` checks every link target. | safe (proven in a scratch layout) |
| git worktree `gitdir` files and `.git/worktrees/*/gitdir` | They hold absolute `E:` paths; git works through both the junction path and the real `C:` path. | safe (proven in a scratch layout) |
| venv `pyvenv.cfg` and console-script launchers | `home` points at the base Python outside the tree; launchers hold absolute `E:` paths that resolve through the junction. | safe |
| Backup same-volume preflight (`hooks/backup/cli.py` `_same_volume`) | Resolves both paths, so it sees the source on `C:`. | needs a change: the `[backup]` edit in `cutover.ps1` |
| Backup per-repo overrides (`hooks/backup/config.py` `load_repo_overrides`) | Compares the repo's resolved path with `cwd_prefix`; both sides are now resolved, so an `E:` prefix still matches a repo walked on `C:`. | safe after this change |
| Backup `--dry-run` | Applies the same-volume refusal. | safe after this change |
| Backup legs other than repos (transcripts, Codex sessions, runtime data) | Sources on `C:`, destinations on `E:`, unchanged by the move. | safe |
| Hook project detection (`hooks/hook_wire/registry.py` `detect_project`) and every `cwd_prefix` match | String match of the cwd the harness reports against `E:/automation/...`. Matches while sessions start at `E:` paths; a harness that reported a resolved `C:` cwd would match no project. | unknown: post-move probe in the runbook |
| Agent transcript folders (`~/.claude/projects/<cwd key>`) | Keyed by the session's cwd. An `E:` cwd keeps the existing key; a resolved `C:` cwd would start a new folder and lose the project's memory. | unknown: same probe |
| git ownership check (`safe.directory`) | robocopy does not copy owners; files are owned by whoever runs the copy. Run the cut-over as the normal (non-elevated) user. The post-check runs git in every repo. | unknown until the post-check |
| Code in other fleet repos that compares a resolved path with an `E:` literal | Only fleet-config's code was swept. | unknown: nightly jobs and the post-check surface it |

## Runbook

The owner runs this; an agent prepares and proves it but never performs the move. Steps 1–4 can run any time; step 5 onward needs the fleet stopped.

1. **Rehearse.** Copy the scripts out of the tree and run the dry-run. It measures the tree once (heavy on a busy disk; run it when the box is quiet) and lists every blocker:
   ```powershell
   Copy-Item -Recurse E:\automation\fleet-config\scripts\fleet_move $env:TEMP\fleet_move -Force
   C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -ExecutionPolicy Bypass -File $env:TEMP\fleet_move\cutover.ps1
   ```
   `READY=no` with only "process(es) running from the tree" is the expected result while the fleet runs.
2. **Record a backup baseline:** `E:\automation\fleet-config\.venv\Scripts\python.exe E:\automation\fleet-config\hooks\backup_private.py --dry-run --no-notify` and note its exit code, so a post-move failure can be told apart from one that was already there.
3. **Pick a quiet window.** The copy of a tree this size takes tens of minutes from the SMR disk; nightly jobs must not fire during it. Pause the `\AppLauncher\` scheduled jobs for the window, or run it after the nightly jobs finish.
4. **Commit or stash** any work in progress in every checkout; the tree is copied as-is either way, but a clean state makes the compare easy to read.
5. **Stop everything that runs from the tree** (owner only): every agent session, including the session-host on :8446, every tray app and the launcher on :8445, the local LLM hub on :8000 and its voice backends, and any terminal or Explorer window whose current folder is under `E:\automation`. Re-run step 1's dry-run until it prints `READY=yes`.
6. **Cut over:**
   ```powershell
   C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -ExecutionPolicy Bypass -File $env:TEMP\fleet_move\cutover.ps1 -Execute
   ```
   It stops at the first failed step. Before the rename the original is untouched (delete the partial `C:\automation` and retry); after it, `rollback.ps1 -Execute` undoes the move.
7. **Start the fleet again** (reboot, or each tray's restart recipe), then run the full post-check, which waits for the launcher's health endpoint:
   ```powershell
   C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -ExecutionPolicy Bypass -File $env:TEMP\fleet_move\postcheck.ps1
   ```
8. **Time the gate on the moved tree:** run fleet-config's gate once, the same command as before the move, and record its wall-clock time and the disk's idle percentage while it runs. Compare both with the pre-move baseline in the move's issue. One way to sample idle time is a background `Get-Counter '\PhysicalDisk(*)\% Idle Time' -SampleInterval 15` for the whole run.
   ```powershell
   Set-Location E:\automation\fleet-config
   Measure-Command { & E:/automation/fleet-config/.venv/Scripts/python.exe skills/_lib/gate_evidence.py run --label gate -- E:/automation/fleet-config/.venv/Scripts/python.exe tests/run_acceptance.py | Out-Host }
   ```
9. **Probe the two unknowns:** start one agent session in `E:\automation\fleet-config` and confirm it lands in the existing `~/.claude/projects/E--automation-fleet-config` folder (not a new `C--automation-...` one) and that a project-routed hook (a completion ping) still names the project.
10. **Watch the next nightly backup:** its repo leg should report `C:\automation` as the source and `E:\backup\fleet-private` as the destination, and hardlink most files against the carried-over snapshot.
11. **Keep `E:\automation.pre-move`** until the owner confirms deletion, and the old backup store under the user profile likewise.

### Rollback

With the fleet stopped (step 5):

```powershell
C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -ExecutionPolicy Bypass -File $env:TEMP\fleet_move\rollback.ps1 -Execute
```

It removes the junction and renames `E:\automation.pre-move` back. The original tree still holds its pre-move `projects.toml`, so the backup points at its old source and destination with no edit. Anything written under `C:\automation` since the cut-over stays there and is not carried back.
