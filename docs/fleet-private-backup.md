# Daily fleet-private backup

`hooks/backup_private.py` backs up everything git ignores — the files that
exist on this machine and nowhere else. Design record plus the restore
procedure; the engine itself is `hooks/backup_private.py` and its scheduled
launcher `hooks/run-backup-daily.bat`.

`hooks/backup_private.py` is **not a hook** — it is a plain scheduled program (`hooks/run-backup-daily.bat` → an app-launcher Job → Task Scheduler, daily at 03:00) that lives here because `hooks/` is this repo's junctioned Python-tool tier: `projects.toml`, `_lib.NO_WINDOW`, and the Slack transport are all already in it. It exists because `ferraroroberto/life-os#72` permanently deleted life-os's gitignored personal content — `identity/`, per-skill `context/`/`memory/`/`conversations/`, `.env` — which by design exists in exactly one place. **Anything git tracks is already safe on GitHub; anything git ignores lives on this machine and nowhere else**, and that ignored set is what this backs up (fleet-config#590).

**Selection is git-derived, in three layers.** `git ls-files --others --ignored --exclude-standard` per repo is the exact ignored set — nothing is hand-listed, so a personal file created tomorrow is covered without editing anything. A deny-list plus a per-file size cap (`max_file_mb`) drops the obvious rubbish. Then a **bulk-directory guard** drops what those two cannot: the deny-list and cap alone still select ~11.8 GB across 108k files, roughly 200× the genuinely irreplaceable set, because gitignore is also where every repo parks its generated output. So any **top-level** directory whose surviving subtree exceeds `bulk_dir_mb` is excluded whole, named with its size in the run report and the manifest, and re-admitted permanently with a per-repo `backup_include`. Top-level only and a single size threshold, deliberately: an operator has to be able to predict what this keeps without simulating an algorithm. A file-*count* threshold was tried alongside it and removed — "small but numerous" is the shape of precious data here (`whatsapp-radar/auth/` is 1,353 tiny files holding the WhatsApp session keys), not of bulk. The live selection is 5,689 files / 183 MB across 32 repos.

**Two legs, opposite directions**, so each volume holds the other's crown jewels: repo residue `E:` → `C:/Users/rober/backup/fleet-private`, and Claude Code's session transcripts (`~/.claude/projects/`, prunable by Claude Code itself) `C:` → `E:/backup/claude-transcripts`. A destination sharing a volume with its source is refused outright. That crossing is what makes either drive failing lose nothing — each volume holds the other's only copy — so the two legs are deliberately *not* consolidated into one location. Both use the singular `backup`, matching the `E:/backup` that already held this machine's disk images: a sibling `E:/backups` one letter away is what gets restored from by mistake under pressure (fleet-config#605).

**Storage is plain files with hardlink dedup.** Dated snapshots `<dest>/<YYYY-MM-DD>/<group>/<relpath>` plus a `latest/` mirror; a file whose sha256 is unchanged since the previous snapshot is hardlinked to it rather than copied (the `rsync --link-dest` shape), so every dated directory reads as a complete tree in Explorer while 14 dailies cost about one copy plus deltas. `latest/` is itself a hardlink mirror rather than a junction, so it stays valid after retention prunes the snapshot it was built from. Retention keeps 14 dailies plus one snapshot per ISO week for 8 weeks. Plain files are the point: **the restore has to need zero tooling**, because the incident restore was done by hand from plain sources.

**Restore procedure.** Copy the files back — there is nothing to install and nothing to decrypt:

```powershell
# Most recent copy of one repo's private files
Copy-Item -Recurse "C:\Users\rober\backup\fleet-private\latest\life-os\*" "E:\automation\life-os\"

# A specific day instead
Copy-Item -Recurse "C:\Users\rober\backup\fleet-private\2026-08-11\life-os\identity" "E:\automation\life-os\"

# Session transcripts (the other direction)
Copy-Item -Recurse "E:\backup\claude-transcripts\latest\projects\*" "$env:USERPROFILE\.claude\projects\"
```

`manifest.json` in each snapshot lists every file with its sha256, size and mtime, plus what was excluded and why — so "was this file backed up, and which day should I restore from?" is answerable without hunting the tree. Each destination root also carries a plain-text `HOW-TO-RESTORE.txt`, rewritten every run: a folder of repo names explains neither what wrote it nor how to use it, and the README that does explain it lives on the volume the backup exists to survive the loss of, so the instructions ship next to the data.

**It checks its own work rather than assuming.** Each run re-hashes a random sample (5%, floor 20, cap 500) of what it just wrote against its own manifest; a repo that had files in the previous manifest and backed up none this run is a hard failure, not a quiet success; failures aggregate across repos (every remaining repo is still attempted) and the process exits non-zero with a distinct code per condition — `1` repo IO failure, `2` verification mismatch, `3` unusable destination, `4` zero-file regression — which is what the Job's `alert_on_failure` keys off. `--check-freshness` reports `BACKUP_FRESHNESS=ok|stale|unknown` as three states and never folds "couldn't tell" into "fine" — a run interrupted mid-write (killed, machine shut down) leaves `.run-in-progress` in the dated snapshot, so a torn newest snapshot reports `unknown` ("last run did not finish") instead of the previous run's stale `ok`, and is never hardlinked against as a source (fleet-config#607).

```powershell
E:/automation/fleet-config/.venv/Scripts/python.exe hooks/backup_private.py --dry-run   # report selection, write nothing
E:/automation/fleet-config/.venv/Scripts/python.exe hooks/backup_private.py --check-freshness
```

Configuration is the `[backup]` table in `hooks/projects.toml` (its own table, not `[global]` keys: both `_lib.load_registry` and `skills/_lib/fleet_repo_scan.fleet_repos` enumerate that file by "table carrying a `cwd_prefix`", so a `[backup]` table cannot perturb the fleet-membership list). **`[backup]` is the last table in that file deliberately** — a TOML table header claims every bare key that follows it, and placing it above `architecture_ignore` silently re-parented that list out of `[global]` and dropped four repos off the architecture map. New `[global]` keys go above it. Per-repo nuance follows the `capture = true` precedent and lives in that repo's own table — `backup = false` to opt out, `backup_exclude` to drop a subtree before the guard measures it, `backup_include` to keep a top-level directory the guard would drop, `backup_include_globs` to exempt specific basenames from the global `deny_dirs`/`deny_globs` check (e.g. a `*.log` file that is actually security-relevant automation state, not routine debug noise), and `backup_always_include` to exempt specific relative paths from the global `max_file_mb` cap. Both are additive and repo-scoped only — the value of the global deny-list and size cap is that they stay predictable defaults for every other repo (fleet-config#722).

**Scheduling.** The daily 03:00 Job is registered in app-launcher's Jobs registry (machine-local `config/jobs.json`, so it is not committed here). It can only be created once `hooks/run-backup-daily.bat` exists at the primary checkout — the launcher's save-time preflight refuses a `script_path` that is not on disk, which is why this is a post-merge step rather than part of the branch:

```powershell
$body = @{ id = 'fleet-private-backup-daily'; name = 'Fleet Private Backup'
           script_path = 'E:\automation\fleet-config\hooks\run-backup-daily.bat'; args = ''
           schedule = @{ type = 'daily'; at = '03:00' }
           alert_on_failure = $true; visible = $true } | ConvertTo-Json -Depth 5
Invoke-RestMethod -Uri 'https://127.0.0.1:8445/api/jobs' -Method Post -Body $body `
  -ContentType 'application/json' -SkipCertificateCheck
```
