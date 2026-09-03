"""Daily snapshot of every fleet repo's gitignored-but-precious residue.

fleet-config#590, filed after the 2026-08-10 incident (ferraroroberto/life-os#72)
permanently deleted life-os's gitignored personal content — `identity/`, per-skill
`context/`/`memory/`/`conversations/`, `.env` — which by design exists in exactly
one place. Recovery succeeded only by luck. Anything git *tracks* is already safe
on GitHub; anything git *ignores* lives on this machine and nowhere else. That
ignored set is what this backs up.

Not a hook — a plain scheduled program (`run-backup-daily.bat` → an app-launcher
Job → Task Scheduler). It lives in `hooks/` because that is this repo's junctioned
Python-tool tier: `projects.toml`, `_lib.NO_WINDOW`, and the Slack transport it
needs are all already here, and the acceptance matrix's spawn-flag scanner and the
README-Layout gate already cover this directory.

Selection, in three layers
--------------------------
1. **git decides.** `git ls-files --others --ignored --exclude-standard` per repo
   is the exact ignored set. Nothing is hand-listed, so a personal file created
   tomorrow is covered without touching this file.
2. **A deny-list + per-file size cap** drops the obvious rubbish (`.venv`,
   `node_modules`, caches, `*.log`, `*.pyc`, anything over `max_file_mb`).
3. **A bulk-directory guard** drops what layers 1-2 cannot: the measurement behind
   #590 found layers 1-2 alone still select **11.8 GB across 108k files**, ~200x
   the genuinely irreplaceable set, because gitignore is also where every repo
   parks its generated output (content-management `planning/` 2.8 GB,
   voice-transcriber `archive/` 2.5 GB, app-launcher `webapp/` 2.3 GB). So any
   **top-level** directory in a repo whose surviving subtree exceeds
   `bulk_dir_mb` is excluded whole, **named with its size in the run report and
   the manifest**, and re-admitted permanently with a per-repo `backup_include`.
   Top-level only, and a single size threshold, on purpose: an operator has to be
   able to predict what this keeps without simulating an algorithm.

   A file-*count* threshold was tried alongside the size one and removed: measured
   over the real fleet, every directory it caught that size did not was ≤1.2 MB,
   and one of them was `whatsapp-radar/auth/` — 1,353 tiny files holding the
   WhatsApp session keys. "Small but numerous" is the shape of precious data here,
   not of bulk.

The guard is the one place this deviates from "no allow-list to go stale" — a
`backup_include` list is a small allow-list. It fails toward backing up *less*,
which is why every exclusion is reported rather than silent, and why a repo that
had files yesterday and has none today is a hard failure (`check_zero_file_regressions`).

Storage
-------
Dated snapshots `<dest>/<YYYY-MM-DD>/`, plus a `latest/` mirror. A file whose
content is unchanged since the previous snapshot is **hardlinked** to it rather
than copied (the `rsync --link-dest` shape), so every dated directory reads as a
complete plain-file tree in Explorer while 14 dailies cost about one copy plus
deltas. Plain files are deliberate: the #590 restore has to need zero tooling,
because the incident restore was done by hand from plain sources.

Three legs run each night, and every one of them crosses volumes, so each drive
holds the other's crown jewels: repo residue E: -> C:; Claude Code's session
transcripts (`~/.claude/projects/`, the actual recovery goldmine, and prunable by
Claude Code itself) C: -> E:; and the relocated runtime-data root C: -> E:.

The third leg exists because `project-scaffolding#243` moved every always-on
service's SQLite database *out* of its repo's working tree, to `C:/sqlite/<app>/`
(the SSD, so seven services fsync-ing at a poll interval stop hammering the
spinning E: drive). Selection here is git-derived, and that root is outside every
git working tree — so the relocation makes those databases invisible to layer 1
entirely: not dropped-and-reported, just absent (fleet-config#724). Two things
about this leg are deliberate and must not be "tidied":

- **No `max_file_mb` cap.** The transcripts leg drops oversize files; here that
  would silently drop exactly the databases the leg exists for
  (home-automation's `telemetry.sqlite3` is already 18 MB against a 10 MB cap).
  That silent drop is the defect fleet-config#722 just fixed on the repos leg.
- **Databases are snapshotted, not copied.** These are live WAL-mode files under
  continuous write. Copying the `.sqlite3` alone yields a stale-to-last-checkpoint
  image at best; copying it together with `-wal`/`-shm` non-atomically can yield a
  corrupt one. `snapshot_sqlite` uses SQLite's online backup API into a temp
  staging tree, which the generic engine below then treats as ordinary files, and
  the `-wal`/`-shm` sidecars are skipped because that snapshot already contains
  their committed contents.

Honesty rules
-------------
Every run re-hashes a random sample of what it just wrote against its own
manifest; a repo that lost all its files since the last manifest fails the run;
`--check-freshness` reports `ok` / `stale` / `unknown` as three distinct states
and never folds "couldn't tell" into "fine". Failures are aggregated, not fatal
on first error — every remaining repo is still attempted — and the process exits
non-zero, which is what the app-launcher Job's `alert_on_failure` keys off.

A run interrupted mid-write (killed, machine shut down) leaves the dated
snapshot torn — half-rewritten but sitting next to a manifest that still
describes the *previous* run. `run_leg` writes `<snapshot>/.run-in-progress`
before the first file and clears it only after `manifest.json` lands, so
`freshness` can tell a torn newest snapshot apart from a genuinely fresh one
(`unknown`, never `ok`) and `_previous_snapshot` never hardlinks against one
(fleet-config#607). A stale marker is self-healing: the next run for that same
date overwrites the directory and clears it.

stdlib only. Never follows a junction or symlink (see `is_reparse_point` — on
Windows `Path.is_symlink()` returns **False** for a junction, so a symlink-based
guard walks straight into every worktree's `.venv`).

Run it as a **directory** -- `python <...>/hooks/backup_private.py` is a thin
shim that imports `main` from `.cli` and keeps that invocation unchanged; the
daily job (`hooks/run-backup-daily.bat`) and the docs need no edits.

Layout (fleet-config#731 -- this was one 1768-line module carrying six largely
independent concerns behind a single CLI, and the biggest file in the repo's
runtime trees; the engine, the reporting, and the CLI are testable
independently, so it only went one way):

  config.py     BackupConfig / RepoOverrides, projects.toml loaders, every
                exit/freshness/name constant. The bottom of the DAG -- every
                sibling module imports from here, this one imports nothing
                from them.
  snapshot.py   Hardlink-vs-copy file writing, sample re-hash verification,
                zero-file-regression detection, and SQLite's online-backup
                snapshotting (a live WAL database has to be *snapshotted*,
                not byte-copied).
  select.py     The three-layer selection engine (git-ignored set, deny-list
                + size cap, bulk-directory guard) for the repos/transcripts
                legs, plus the runtime-data leg's selection (which calls into
                `snapshot.py` for the SQLite side).
  retention.py  The previous-snapshot lookup, run markers, the `latest/`
                mirror (full rebuild + incremental reconcile), the restore
                note, and the retention/freshness state machine.
  report.py     The per-leg human-readable report and the Slack summary ping.
  cli.py        The thin orchestrator: preflight, `run_leg`, `collect_repos`,
                exit-code aggregation, `run`, and `main`'s argument parsing.
                Everything above is wired together here; none of the actual
                selection/snapshot/retention logic lives in this file.

Every name below is imported and re-exported so existing callers and tests
keep addressing this engine as `backup.<name>` / `backup_private.py`'s own
former flat namespace, unchanged (fleet-config#731 review).
"""

from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _lib  # noqa: E402

from .config import (  # noqa: E402
    DATE_FMT,
    DB_LOCK_TIMEOUT_SECONDS,
    DB_SIDECAR_MARKERS,
    DB_SUFFIXES,
    DEFAULT_DENY_DIRS,
    DEFAULT_DENY_GLOBS,
    EXIT_DEST_UNUSABLE,
    EXIT_OK,
    EXIT_REPO_FAILURE,
    EXIT_VERIFY_FAILED,
    EXIT_ZERO_FILES_REGRESSION,
    FRESHNESS_OK,
    FRESHNESS_STALE,
    FRESHNESS_UNKNOWN,
    LATEST_DIR,
    MANIFEST_NAME,
    RUNTIME_DATA_GROUP,
    RUNTIME_DATA_LEG,
    RUN_MARKER_NAME,
    BackupConfig,
    RepoOverrides,
    _FRESHNESS_EXIT,
    _SEVERITY,
    _projects_toml_path,
    _read_toml,
    load_backup_config,
    load_repo_overrides,
)
from .select import (  # noqa: E402
    Selection,
    _apply_bulk_guard,
    _dedupe_by_rel,
    _denied,
    _forced_include,
    _matches_any,
    git_ignored_entries,
    is_reparse_point,
    iter_repos,
    select_repo,
    select_runtime_data,
    select_transcripts,
    walk_files,
)
from .snapshot import (  # noqa: E402
    check_zero_file_regressions,
    is_sqlite_sidecar,
    runtime_data_staging,
    sha256_file,
    snapshot_sqlite,
    verify_sample,
    write_group,
)
from .retention import (  # noqa: E402
    RESTORE_NOTE_NAME,
    _RESTORE_NOTE,
    _WHAT_IGNORED,
    _WHAT_RUNTIME_DATA,
    _index_manifest,
    _previous_snapshot,
    _prune_empty_dirs,
    clear_run_marker,
    dated_snapshots,
    freshness,
    has_run_marker,
    plan_retention,
    prune,
    read_manifest,
    read_run_marker,
    rebuild_latest,
    reconcile_latest,
    write_restore_note,
    write_run_marker,
)
from .report import _leg_duration_seconds, _mb, _notify, _slack_summary, report  # noqa: E402
from .cli import (  # noqa: E402
    _preflight,
    _same_volume,
    _worst,
    collect_repos,
    main,
    run,
    run_leg,
)

logger = logging.getLogger("backup_private")

__all__ = [
    "BackupConfig", "RepoOverrides", "Selection",
    "EXIT_OK", "EXIT_REPO_FAILURE", "EXIT_VERIFY_FAILED", "EXIT_DEST_UNUSABLE",
    "EXIT_ZERO_FILES_REGRESSION", "FRESHNESS_OK", "FRESHNESS_STALE", "FRESHNESS_UNKNOWN",
    "MANIFEST_NAME", "RUN_MARKER_NAME", "LATEST_DIR", "DATE_FMT", "RUNTIME_DATA_LEG",
    "RUNTIME_DATA_GROUP", "DB_SUFFIXES", "DB_SIDECAR_MARKERS", "DB_LOCK_TIMEOUT_SECONDS",
    "DEFAULT_DENY_DIRS", "DEFAULT_DENY_GLOBS", "RESTORE_NOTE_NAME",
    "load_backup_config", "load_repo_overrides",
    "is_reparse_point", "sha256_file", "git_ignored_entries",
    "walk_files", "select_repo", "select_transcripts", "select_runtime_data", "iter_repos",
    "is_sqlite_sidecar", "snapshot_sqlite", "runtime_data_staging",
    "write_group", "verify_sample", "check_zero_file_regressions",
    "dated_snapshots", "read_manifest", "has_run_marker", "read_run_marker",
    "write_run_marker", "clear_run_marker", "write_restore_note", "rebuild_latest",
    "reconcile_latest", "plan_retention", "prune", "freshness",
    "collect_repos", "run_leg", "run", "main",
    "report", "logger",
]
