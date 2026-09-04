"""Daily snapshot of every fleet repo's gitignored-but-precious residue.

The thin CLI entry point: preflight, per-leg orchestration, exit-code
aggregation, and argument parsing. `main`'s `--help` description is this
docstring's first line, same contract as before the split (fleet-config#590).

Split out of `hooks/backup_private.py` (fleet-config#731) -- everything below
is orchestration wiring `config.py`, `select.py`, `snapshot.py`,
`retention.py`, and `report.py` together; none of the actual selection,
snapshotting, or retention logic lives here. `hooks/backup_private.py` is now
a several-line shim that imports `main` from here, so the daily job's
invocation (`python hooks/backup_private.py [--dry-run|...]`) is unchanged.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .config import (
    BackupConfig,
    DATE_FMT,
    EXIT_DEST_UNUSABLE,
    EXIT_OK,
    EXIT_REPO_FAILURE,
    EXIT_VERIFY_FAILED,
    EXIT_ZERO_FILES_REGRESSION,
    FRESHNESS_OK,
    LATEST_DIR,
    MANIFEST_NAME,
    RUNTIME_DATA_GROUP,
    RUNTIME_DATA_LEG,
    _FRESHNESS_EXIT,
    _SEVERITY,
    load_backup_config,
    load_repo_overrides,
)
from .select import Selection, iter_repos, select_repo, select_runtime_data, select_transcripts
from .snapshot import check_zero_file_regressions, runtime_data_staging, verify_sample, write_group
from .retention import (
    _index_manifest,
    _previous_snapshot,
    clear_run_marker,
    freshness,
    prune,
    read_run_marker,
    reconcile_latest,
    rebuild_latest,
    write_restore_note,
    write_run_marker,
)
from .report import _notify, report

logger = logging.getLogger("backup_private")


def _same_volume(a: Path, b: Path) -> bool:
    """True when two paths sit on the same volume.

    Drive-letter comparison on Windows, device id elsewhere. Used to enforce
    #590's hard constraint — a snapshot on the source volume dies with it.
    """
    if sys.platform == "win32":
        return os.path.splitdrive(str(a.resolve()))[0].lower() == \
            os.path.splitdrive(str(b.resolve()))[0].lower()
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def _preflight(source: Path, dest: Path) -> Optional[str]:
    """Refuse to write a snapshot that shares a volume with its source."""
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"destination {dest} is not writable: {exc}"
    if _same_volume(source, dest):
        return (f"destination {dest} is on the same volume as {source} — "
                "a same-volume snapshot dies with the volume")
    return None


def run_leg(leg: str, groups: Dict[str, Selection], source: Path, dest_root: Path,
            cfg: BackupConfig, today: str, dry_run: bool,
            force_rebuild_latest: bool = False) -> Dict[str, Any]:
    """Snapshot one leg's already-selected groups and return its manifest."""
    started = datetime.now(timezone.utc)
    manifest: Dict[str, Any] = {
        "leg": leg,
        "date": today,
        "started_at": started.isoformat(),
        "finished_at": None,
        "source": str(source),
        "dest": str(dest_root),
        "policy": cfg.policy_summary(leg),
        "groups": {},
        "totals": {},
        "verification": {},
        "status": "unknown",
        "recovered_marker": None,
        "skipped_interrupted": [],
    }

    snapshot_dir = dest_root / today
    if dry_run:
        prev_dir, prev_manifest, skipped_interrupted = None, {}, []
    else:
        # The marker goes down before the first file write, so a death anywhere
        # between here and the manifest write below leaves `.run-in-progress`
        # behind as unambiguous proof the tree is torn (fleet-config#607). A
        # marker already sitting here belongs to an earlier run for this same
        # date that never finished — self-healing: this run overwrites the
        # directory and clears it on success, but the report names it first.
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        manifest["recovered_marker"] = read_run_marker(snapshot_dir) or None
        write_run_marker(snapshot_dir, leg)
        prev_dir, prev_manifest, skipped_interrupted = _previous_snapshot(dest_root, today)
    prev_index = _index_manifest(prev_manifest)
    manifest["skipped_interrupted"] = skipped_interrupted

    linked = copied = 0
    for name, selection in sorted(groups.items()):
        errors = list(selection.errors)
        vanished = list(selection.vanished)
        if dry_run:
            entries = [
                {"path": rel, "sha256": None, "size": size, "mtime": None}
                for _, rel, size in selection.files
            ]
        else:
            entries, group_linked, group_copied = write_group(
                selection.files, name, snapshot_dir, prev_dir, prev_index, errors, vanished,
            )
            linked += group_linked
            copied += group_copied
        manifest["groups"][name] = {
            "status": "failed" if errors else "ok",
            "files": entries,
            "bytes": sum(entry["size"] for entry in entries),
            "bulk_excluded": selection.bulk_excluded,
            "oversize": selection.oversize,
            "errors": errors,
            "vanished": vanished,
        }

    total_files = sum(len(payload["files"]) for payload in manifest["groups"].values())
    manifest["totals"] = {
        "files": total_files,
        "bytes": sum(payload["bytes"] for payload in manifest["groups"].values()),
        "linked": linked,
        "copied": copied,
        "groups": len(manifest["groups"]),
        "bulk_excluded_dirs": sum(
            len(payload["bulk_excluded"]) for payload in manifest["groups"].values()
        ),
        "vanished": sum(len(payload["vanished"]) for payload in manifest["groups"].values()),
    }

    if dry_run:
        manifest["status"] = "dry-run"
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        return manifest

    manifest["verification"] = verify_sample(snapshot_dir, manifest)
    manifest["regressions"] = check_zero_file_regressions(manifest, prev_manifest)
    failed_groups = [
        name for name, payload in manifest["groups"].items() if payload["status"] == "failed"
    ]
    manifest["status"] = "ok" if (
        manifest["verification"]["status"] in {"pass", "empty"}
        and not manifest["regressions"]
        and not failed_groups
    ) else "failed"
    manifest["finished_at"] = datetime.now(timezone.utc).isoformat()

    (snapshot_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    # The manifest now describes this directory (even a "failed" one) — the
    # marker's job, proving the tree is torn, is done (fleet-config#607).
    clear_run_marker(snapshot_dir)
    # A full rebuild is the only correct move with nothing to diff against — no
    # `latest/` yet, or no readable previous manifest — and is always available
    # as an explicit repair path via `force_rebuild_latest` (fleet-config#721).
    if force_rebuild_latest or not (dest_root / LATEST_DIR).is_dir() or not prev_manifest.get("groups"):
        manifest["latest_files"] = rebuild_latest(dest_root, snapshot_dir)
        manifest["latest_mode"] = "full"
    else:
        added, updated, removed = reconcile_latest(dest_root, snapshot_dir, manifest, prev_manifest)
        manifest["latest_files"] = total_files
        manifest["latest_mode"] = "incremental"
        manifest["latest_delta"] = {"added": added, "updated": updated, "removed": removed}
    manifest["pruned"] = prune(dest_root, cfg)
    write_restore_note(dest_root, leg, source)
    return manifest


def collect_repos(cfg: BackupConfig, only: Optional[str],
                  toml_path: Optional[Path]) -> Tuple[Dict[str, Selection], List[str]]:
    groups: Dict[str, Selection] = {}
    skipped: List[str] = []
    for repo_dir in iter_repos(cfg.source_root):
        if only and repo_dir.name != only:
            continue
        overrides = load_repo_overrides(repo_dir, toml_path)
        if not overrides.enabled:
            skipped.append(repo_dir.name)
            continue
        selection = select_repo(repo_dir, cfg, overrides)
        if selection.files or selection.errors or selection.bulk_excluded:
            groups[repo_dir.name] = selection
    return groups, skipped


def _worst(codes: Sequence[int]) -> int:
    return max(codes, key=lambda code: _SEVERITY.get(code, 0), default=EXIT_OK)


def run(cfg: BackupConfig, *, dry_run: bool = False, only: Optional[str] = None,
        toml_path: Optional[Path] = None, notify: bool = True,
        today: Optional[str] = None,
        rebuild_latest_full: bool = False) -> Tuple[int, List[Dict[str, Any]]]:
    """Run both legs. Returns `(exit_code, manifests)`."""
    today = today or datetime.now().strftime(DATE_FMT)
    codes: List[int] = []
    manifests: List[Dict[str, Any]] = []

    repo_groups, skipped = collect_repos(cfg, only, toml_path)
    if skipped:
        logger.info("ℹ️ opted out via projects.toml: %s", ", ".join(skipped))

    # The runtime-data leg's database snapshots live in a temp tree that has to
    # outlive selection and survive until `run_leg` has copied out of it — hence
    # an ExitStack around the whole loop rather than a `with` per leg. It is
    # removed on every path out of here, including an exception.
    with contextlib.ExitStack() as stack:
        legs = [("repos", repo_groups, cfg.source_root, cfg.dest)]
        if not only:
            legs.append(("transcripts", {"projects": select_transcripts(cfg)},
                         cfg.transcripts_src, cfg.transcripts_dest))
            if cfg.runtime_data_src.is_dir():
                # `--dry-run` still takes the snapshots (into the temp staging
                # tree it then throws away): "which databases can actually be
                # snapshotted right now" is the one question a dry run of this
                # leg exists to answer, and a report that assumed the answer
                # would be worth nothing.
                staging = stack.enter_context(runtime_data_staging())
                legs.append((RUNTIME_DATA_LEG,
                             {RUNTIME_DATA_GROUP: select_runtime_data(cfg, staging)},
                             cfg.runtime_data_src, cfg.runtime_data_dest))
            else:
                # A skip, not a failure: the fleet's move to this root
                # (project-scaffolding#243) is still in flight, so an absent
                # directory means "nothing has migrated yet", not "the backup
                # broke". Said out loud, because a leg that quietly does nothing
                # is indistinguishable from one that quietly stopped working.
                logger.info(
                    "ℹ️ %s leg skipped: %s does not exist yet — no runtime data "
                    "has been relocated here (project-scaffolding#243)",
                    RUNTIME_DATA_LEG, cfg.runtime_data_src,
                )

        for leg, groups, source, dest_root in legs:
            if not dry_run:
                problem = _preflight(source, dest_root)
                if problem:
                    logger.error("❌ %s leg: %s", leg, problem)
                    codes.append(EXIT_DEST_UNUSABLE)
                    continue
            manifest = run_leg(leg, groups, source, dest_root, cfg, today, dry_run,
                               force_rebuild_latest=rebuild_latest_full)
            manifests.append(manifest)
            report(manifest)
            if dry_run:
                continue
            if manifest["verification"]["status"] == "fail":
                codes.append(EXIT_VERIFY_FAILED)
            if manifest.get("regressions"):
                codes.append(EXIT_ZERO_FILES_REGRESSION)
            if any(payload["status"] == "failed" for payload in manifest["groups"].values()):
                codes.append(EXIT_REPO_FAILURE)

    exit_code = _worst(codes)
    if notify and not dry_run:
        _notify(manifests, exit_code)
    return exit_code, manifests


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be snapshotted; write nothing.")
    parser.add_argument("--check-freshness", action="store_true",
                        help="Report BACKUP_FRESHNESS=ok|stale|unknown and exit.")
    parser.add_argument("--only", help="Limit the repo leg to one repo (debugging).")
    parser.add_argument("--config", type=Path, help="Path to projects.toml.")
    parser.add_argument("--no-notify", action="store_true", help="Suppress the Telegram ping.")
    parser.add_argument("--json", action="store_true", help="Print the run summary as JSON.")
    parser.add_argument("--rebuild-latest-full", action="store_true",
                        help="Force a full rebuild of latest/ instead of the incremental "
                             "reconcile — the repair path if latest/ is ever suspect.")
    args = parser.parse_args(argv)

    # stdout, line-buffered, for the app-launcher Jobs pane (fleet-config#605).
    # Two separate defects, both invisible until a run went through the real
    # scheduler path: `logging.basicConfig` defaults to **stderr**, and the Jobs
    # pane captured 0 bytes for a 35-minute run (stdout 0 / stderr 1615, measured);
    # and stdout is block-buffered when it is a pipe rather than a console, so
    # even on the right stream the whole log would only surface at exit. An
    # unattended job whose failure leaves no reason behind cannot be diagnosed.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    cfg = load_backup_config(args.config)

    if args.check_freshness:
        worst = FRESHNESS_OK
        details = {}
        legs = [("repos", cfg.dest), ("transcripts", cfg.transcripts_dest)]
        if cfg.runtime_data_src.is_dir():
            legs.append((RUNTIME_DATA_LEG, cfg.runtime_data_dest))
        else:
            # The leg does not run while its source root is absent, so it has no
            # snapshot to be fresh or stale about. Reported as its own state
            # rather than as `unknown` — "this leg is not active yet" is a fact
            # we established, not one we failed to.
            details[RUNTIME_DATA_LEG] = {
                "state": "not-yet-active",
                "reason": f"{cfg.runtime_data_src} does not exist",
            }
        for leg, dest_root in legs:
            state, detail = freshness(dest_root, cfg)
            details[leg] = {"state": state, **detail}
            if _FRESHNESS_EXIT[state] > _FRESHNESS_EXIT[worst]:
                worst = state
        print(f"BACKUP_FRESHNESS={worst}")
        for leg, detail in details.items():
            print(f"  {leg}: {json.dumps(detail, ensure_ascii=False)}")
        return _FRESHNESS_EXIT[worst]

    started = time.monotonic()
    exit_code, manifests = run(
        cfg, dry_run=args.dry_run, only=args.only, toml_path=args.config,
        notify=not args.no_notify, rebuild_latest_full=args.rebuild_latest_full,
    )
    if args.json:
        print(json.dumps(
            [{k: v for k, v in m.items() if k != "groups"} for m in manifests],
            indent=2, ensure_ascii=False,
        ))
    logger.info("%s in %.1fs (exit %d)",
                "✅ backup complete" if exit_code == EXIT_OK else "❌ backup FAILED",
                time.monotonic() - started, exit_code)
    return exit_code

