"""Snapshot lifecycle: the previous-snapshot lookup, run markers, the
`latest/` mirror (full rebuild and incremental reconcile), the restore note,
and the retention/freshness state machine.

Split out of `hooks/backup_private.py` (fleet-config#731). Depends only on
`config.py` -- nothing here reaches into `select.py` or `snapshot.py`, so
`cli.py` (the orchestrator) is the only module that has to know both this
module's bookkeeping and `snapshot.py`'s file-writing exist.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .config import (
    BackupConfig,
    DATE_FMT,
    FRESHNESS_OK,
    FRESHNESS_STALE,
    FRESHNESS_UNKNOWN,
    LATEST_DIR,
    MANIFEST_NAME,
    RUNTIME_DATA_LEG,
    RUNTIME_DATA_GROUP,
    RUN_MARKER_NAME,
)

logger = logging.getLogger("backup_private")


def dated_snapshots(dest_root: Path) -> List[Path]:
    """Every `YYYY-MM-DD` directory under `dest_root`, oldest first."""
    if not dest_root.is_dir():
        return []
    out = []
    for entry in dest_root.iterdir():
        if not entry.is_dir() or entry.name == LATEST_DIR:
            continue
        try:
            datetime.strptime(entry.name, DATE_FMT)
        except ValueError:
            continue
        out.append(entry)
    return sorted(out, key=lambda p: p.name)


def read_manifest(snapshot_dir: Path) -> Dict[str, Any]:
    path = snapshot_dir / MANIFEST_NAME
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def has_run_marker(snapshot_dir: Path) -> bool:
    """True if `snapshot_dir` carries `.run-in-progress` — a torn, mid-write tree."""
    return (snapshot_dir / RUN_MARKER_NAME).is_file()


def read_run_marker(snapshot_dir: Path) -> Dict[str, Any]:
    path = snapshot_dir / RUN_MARKER_NAME
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_run_marker(snapshot_dir: Path, leg: str) -> None:
    """Write `.run-in-progress` before the first file, so a death mid-write leaves
    a torn tree unambiguously marked (fleet-config#607). Failure to write it must
    never fail the backup itself — it degrades to the pre-#607 behaviour.
    """
    marker = {
        "leg": leg,
        "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        (snapshot_dir / RUN_MARKER_NAME).write_text(
            json.dumps(marker, ensure_ascii=False), encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("⚠️ could not write %s to %s: %s", RUN_MARKER_NAME, snapshot_dir, exc)


def clear_run_marker(snapshot_dir: Path) -> None:
    try:
        (snapshot_dir / RUN_MARKER_NAME).unlink()
    except OSError:
        pass


RESTORE_NOTE_NAME = "HOW-TO-RESTORE.txt"

_RESTORE_NOTE = """\
Fleet private backup — {leg}
============================================================
Written by hooks/backup_private.py in E:/automation/fleet-config
(fleet-config#590), daily at 03:00 via an app-launcher Job.

WHAT THIS IS
{what}
HOW TO RESTORE
  Copy the files back. There is nothing to install, unpack or decrypt —
  every folder below is an ordinary tree you can browse in Explorer.

    latest\\        the newest copy (start here)
    YYYY-MM-DD\\    that day's copy, kept 14 days + one per week for 8 weeks

  Example, restoring one project's private files:
    Copy-Item -Recurse "{example_src}" "{example_dst}"

  Files identical to the previous day are hardlinked, not duplicated:
  each dated folder is still a COMPLETE tree, it just costs no extra disk.
  Deleting an old dated folder never damages another one.

WHAT WAS LEFT OUT, AND WHY
  manifest.json in each dated folder lists every file with its sha256 and
  size, plus every directory skipped for being generated output and every
  file skipped for exceeding the size cap. If something you expected is
  missing, that file says why.

  Written {stamp}.
"""


_WHAT_IGNORED = """\
  A copy of the files git deliberately ignores in {source} — the ones
  that exist on this machine and nowhere else. Anything git tracks is
  already safe on GitHub and is NOT here.
"""

_WHAT_RUNTIME_DATA = """\
  The live SQLite databases of the always-on fleet services, which sit
  in {source} rather than inside their repos (project-scaffolding#243)
  and are therefore invisible to the git-derived backup of everything
  else.

  Each .sqlite3 here was taken with SQLite's online backup API while the
  service was still writing to it, so every file below is a COMPLETE,
  OPENABLE database — not a torn byte copy. There are deliberately no
  -wal / -shm files: the snapshot already contains their contents, and
  you do not need them to open one of these.
"""


def write_restore_note(dest_root: Path, leg: str, source: Path) -> None:
    """Drop a plain-text restore note at the destination root.

    The whole premise is that a restore needs zero tooling — but a folder full
    of repo names explains neither what wrote it nor how to use it, and the
    README that does explain it lives on the volume this exists to survive the
    loss of. So the instructions ship next to the data.
    """
    what = _WHAT_IGNORED
    if leg == "transcripts":
        example_src = str(dest_root / LATEST_DIR / "projects" / "*")
        example_dst = str(source)
    elif leg == RUNTIME_DATA_LEG:
        what = _WHAT_RUNTIME_DATA
        example_src = str(dest_root / LATEST_DIR / RUNTIME_DATA_GROUP / "home-automation" / "*")
        example_dst = str(source / "home-automation")
    else:
        example_src = str(dest_root / LATEST_DIR / "life-os" / "*")
        example_dst = str(source / "life-os")
    try:
        (dest_root / RESTORE_NOTE_NAME).write_text(
            _RESTORE_NOTE.format(
                leg=leg, source=source, example_src=example_src, example_dst=example_dst,
                what=what.format(source=source),
                stamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
            ),
            encoding="utf-8",
        )
    except OSError:  # a note we cannot write must never fail the backup itself
        logger.warning("⚠️ could not write %s to %s", RESTORE_NOTE_NAME, dest_root)


def rebuild_latest(dest_root: Path, snapshot_dir: Path) -> int:
    """Rebuild `latest/` as a hardlink mirror of `snapshot_dir`, from scratch.

    The repair path, kept reachable for the cases `reconcile_latest` below
    cannot handle safely (no `latest/` yet, or no readable previous manifest to
    diff against) and for `--rebuild-latest-full` — never the nightly default
    once a previous manifest exists (fleet-config#721).

    A mirror rather than a junction to the newest dated directory: hardlinks keep
    `latest/` valid after retention prunes the snapshot it was built from, and cost
    no bytes. Built beside the live one, then swapped in with two instant renames
    — `latest` -> `latest.old`, `latest.new` -> `latest` — with the slow delete of
    the retired tree moved *after* the swap. An interrupt can only ever land
    before the swap (untouched `latest`) or after it (fully-rebuilt `latest`,
    `latest.old` just garbage the next run sweeps) — never mid-delete, so
    `latest/` is never left half-deleted (fleet-config#720).
    """
    latest = dest_root / LATEST_DIR
    staging = dest_root / (LATEST_DIR + ".new")
    retiring = dest_root / (LATEST_DIR + ".old")
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    if retiring.exists():
        shutil.rmtree(retiring, ignore_errors=True)

    count = 0
    for source in snapshot_dir.rglob("*"):
        if source.is_dir():
            continue
        target = staging / source.relative_to(snapshot_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source, target)
        except OSError:
            shutil.copy2(source, target)
        count += 1

    if latest.exists():
        latest.rename(retiring)
    staging.rename(latest)
    if retiring.exists():
        shutil.rmtree(retiring, ignore_errors=True)
    return count


def _prune_empty_dirs(root: Path) -> None:
    """Remove directories left empty by `reconcile_latest`'s removals.

    Bottom-up so a directory that only becomes empty once its last child is
    pruned is still caught in the same pass. Re-checks each directory's actual
    contents rather than `os.walk`'s cached listing, since siblings deeper in
    the same walk may have just emptied it.
    """
    if not root.is_dir():
        return
    for dirpath, _dirnames, _filenames in os.walk(root, topdown=False):
        path = Path(dirpath)
        if path == root:
            continue
        try:
            next(path.iterdir())
        except StopIteration:
            try:
                path.rmdir()
            except OSError:
                pass
        except OSError:
            pass


def _previous_snapshot(
    dest_root: Path, today: str,
) -> Tuple[Optional[Path], Dict[str, Any], List[str]]:
    """The newest dated snapshot before `today` that has a readable manifest.

    A snapshot still carrying `.run-in-progress` was left mid-write by a run that
    never reached `manifest.json` — its tree is torn, so it is skipped rather than
    hardlinked against or trusted for content. Skipped names are returned so the
    caller can report the interrupted predecessor (fleet-config#607).
    """
    skipped: List[str] = []
    for snapshot in sorted(dated_snapshots(dest_root), key=lambda p: p.name, reverse=True):
        if snapshot.name >= today:
            continue
        if has_run_marker(snapshot):
            skipped.append(snapshot.name)
            continue
        manifest = read_manifest(snapshot)
        if manifest:
            return snapshot, manifest, skipped
    return None, {}, skipped


def _index_manifest(manifest: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """`{"<group>/<relpath>": entry}` for O(1) previous-snapshot lookups."""
    index: Dict[str, Dict[str, Any]] = {}
    for group, payload in (manifest.get("groups") or {}).items():
        for entry in payload.get("files", []):
            index[f"{group}/{entry['path']}"] = entry
    return index


def reconcile_latest(dest_root: Path, snapshot_dir: Path, manifest: Dict[str, Any],
                     prev_manifest: Dict[str, Any]) -> Tuple[int, int, int]:
    """Update `latest/` to match `snapshot_dir`'s manifest, touching only the
    files that changed since `prev_manifest` (fleet-config#721).

    `rebuild_latest` re-links every file in the tree every night regardless of
    how much actually changed — ~14.7k `os.link()` calls for a delta of a few
    hundred files on the transcripts leg. This instead diffs the two manifests
    by sha256 and applies just the delta: relink what changed or was added,
    unlink what dropped out of the new manifest, leave everything else exactly
    as it already sits in `latest/`.

    An entry unchanged since `prev_manifest` is skipped only after confirming it
    is still actually there — a cheap `is_file()` stat, not a full re-link — so
    `latest/` self-heals from any prior drift (a killed reconcile, manual
    meddling) instead of silently trusting a manifest that no longer matches
    disk. The dated snapshot such a file was originally hardlinked from may
    since have been pruned by retention; that's fine, the hardlink already
    sitting in `latest/` keeps its data alive by inode refcount, independent of
    the path it was created from.

    The `"{group}/{path}"` keys `_index_manifest` produces are themselves valid
    relative paths under both `snapshot_dir` and `latest/` — `write_group`
    targets `snapshot_dir / group / rel`, so no group/rel split is needed here.

    Returns `(added, updated, removed)`, so the caller can report the delta —
    not the tree size — as the leg's ongoing cost.
    """
    latest = dest_root / LATEST_DIR
    new_index = _index_manifest(manifest)
    prev_index = _index_manifest(prev_manifest)

    added = updated = removed = 0
    for key, entry in new_index.items():
        prev_entry = prev_index.get(key)
        target = latest / key
        if (prev_entry is not None and prev_entry.get("sha256") == entry.get("sha256")
                and target.is_file()):
            continue
        source = snapshot_dir / key
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.unlink()
        try:
            os.link(source, target)
        except OSError:
            shutil.copy2(source, target)
        if prev_entry is None:
            added += 1
        else:
            updated += 1

    for key in prev_index.keys() - new_index.keys():
        try:
            (latest / key).unlink()
        except OSError:
            continue
        removed += 1

    # `manifest.json` sits at `snapshot_dir`'s top level, outside every group, so
    # the per-group diff above never touches it — but it changes every run
    # regardless of group content, and a full rebuild always re-links it (its
    # `rglob` walks the whole tree). Refreshed unconditionally so `latest/`
    # never carries a stale manifest describing an earlier day.
    source_manifest = snapshot_dir / MANIFEST_NAME
    if source_manifest.is_file():
        latest_manifest = latest / MANIFEST_NAME
        if latest_manifest.exists():
            latest_manifest.unlink()
        try:
            os.link(source_manifest, latest_manifest)
        except OSError:
            shutil.copy2(source_manifest, latest_manifest)

    _prune_empty_dirs(latest)
    return added, updated, removed


def plan_retention(snapshot_names: Sequence[str], keep_daily: int,
                   keep_weekly: int) -> List[str]:
    """Names to delete: keep the newest `keep_daily`, then one per ISO week.

    Weeklies are chosen from what the daily window does not already cover, newest
    snapshot per ISO week, for the most recent `keep_weekly` weeks.
    """
    ordered = sorted(snapshot_names, reverse=True)
    keep = set(ordered[:keep_daily])

    weekly: Dict[Tuple[int, int], str] = {}
    for name in ordered[keep_daily:]:
        try:
            parsed = datetime.strptime(name, DATE_FMT)
        except ValueError:
            continue
        iso = parsed.isocalendar()
        weekly.setdefault((iso[0], iso[1]), name)

    for _, name in sorted(weekly.items(), reverse=True)[:keep_weekly]:
        keep.add(name)
    return sorted(name for name in ordered if name not in keep)


def prune(dest_root: Path, cfg: BackupConfig) -> List[str]:
    names = [p.name for p in dated_snapshots(dest_root)]
    removed = []
    for name in plan_retention(names, cfg.keep_daily, cfg.keep_weekly):
        shutil.rmtree(dest_root / name, ignore_errors=True)
        removed.append(name)
    return removed


def freshness(dest_root: Path, cfg: BackupConfig,
              now: Optional[datetime] = None) -> Tuple[str, Dict[str, Any]]:
    """Three-state freshness of `dest_root`'s newest successful snapshot.

    `unknown` is a real answer, not a soft pass: no destination, no snapshot, an
    unreadable manifest, or a manifest with no finish time all mean this check
    could not establish the fact — which the caller must be able to tell apart
    from "checked, and it is fine".
    """
    now = now or datetime.now(timezone.utc)
    snapshots = dated_snapshots(dest_root)
    if not snapshots:
        return FRESHNESS_UNKNOWN, {"reason": f"no snapshots under {dest_root}"}

    newest = snapshots[-1]
    if has_run_marker(newest):
        # The newest snapshot is mid-write (or died mid-write): whatever the
        # previous snapshot's manifest says, it does NOT describe this directory.
        # Reporting "unknown" here — rather than silently falling through to an
        # older manifest — is the whole point of #607: a check that cannot
        # establish the fact must say so, never fold it into "ok".
        return FRESHNESS_UNKNOWN, {
            "snapshot": newest.name,
            "reason": "last run did not finish",
        }

    for snapshot in reversed(snapshots):
        manifest = read_manifest(snapshot)
        if not manifest:
            continue
        finished = manifest.get("finished_at")
        if not finished:
            continue
        try:
            stamp = datetime.fromisoformat(finished)
        except ValueError:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        age_hours = (now - stamp).total_seconds() / 3600
        detail = {
            "snapshot": snapshot.name,
            "age_hours": round(age_hours, 1),
            "status": manifest.get("status"),
            "files": (manifest.get("totals") or {}).get("files"),
        }
        if manifest.get("status") != "ok":
            return FRESHNESS_STALE, {**detail, "reason": "last run did not report ok"}
        if age_hours > cfg.freshness_max_hours:
            return FRESHNESS_STALE, {**detail, "reason": f"older than {cfg.freshness_max_hours}h"}
        return FRESHNESS_OK, detail

    return FRESHNESS_UNKNOWN, {"reason": "no snapshot carries a readable manifest"}

