"""Writing and verifying snapshot files: hardlink-vs-copy, sample re-hash,
zero-file-regression detection, and SQLite's online-backup snapshotting.

Split out of `hooks/backup_private.py` (fleet-config#731). Consumes plain
tuples/dicts (never a `Selection` from `select.py`, never anything from
`retention.py`) so it has no sibling dependency but `config.py` -- `select.py`
imports `snapshot_sqlite`/`is_sqlite_sidecar` from here for the runtime-data
leg, not the other way round.
"""

from __future__ import annotations

import contextlib
import logging
import hashlib
import os
import random
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from .config import DB_LOCK_TIMEOUT_SECONDS, DB_SIDECAR_MARKERS, DB_SUFFIXES

logger = logging.getLogger("backup_private")


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def is_sqlite_sidecar(rel: str) -> bool:
    """True for the `-wal` / `-shm` / `-journal` companion of a database file.

    Matched against a *database* stem only — `notes-wal` is a file called
    `notes-wal`, while `tasks.sqlite3-wal` is SQLite's write-ahead log. A blanket
    `*-wal` rule would quietly drop the former, and this leg's whole premise is
    that nothing under the root goes missing without saying so.
    """
    name = rel.rstrip("/").rsplit("/", 1)[-1].lower()
    for marker in DB_SIDECAR_MARKERS:
        if not name.endswith(marker):
            continue
        if any(name[: -len(marker)].endswith(suffix) for suffix in DB_SUFFIXES):
            return True
    return False


def snapshot_sqlite(src: Path, dst: Path,
                    timeout: float = DB_LOCK_TIMEOUT_SECONDS) -> str:
    """Write a *consistent* copy of the live database `src` to `dst`.

    SQLite's online backup API, never a byte copy. These databases are written
    continuously by running services in WAL mode: copying the `.sqlite3` alone
    yields at best an image stale to the last checkpoint, and copying it
    alongside its `-wal`/`-shm` non-atomically can yield one that will not open
    at all. `Connection.backup` reads through the same locking layer the writers
    use, so what lands is a complete, openable database with no sidecar needed.

    Two connect attempts, deliberately. `mode=ro` is correct and is tried first,
    but SQLite needs to *write* the `-shm` to read a WAL database whose shared
    memory is not already mapped by a live process — so a service that is merely
    stopped, with a `-wal` left on disk, refuses a read-only open. Falling back
    to a normal read-write open lets SQLite recover that `-shm`; the backup
    itself still only reads the source. Returns which mode succeeded, so the
    fallback is reported rather than silent.

    Raises `OSError` when neither attempt works — locked, corrupt, or not a
    database — leaving no half-written file at `dst`. The caller records that as
    its own failure state; a database that could not be snapshotted must never
    look like one that simply was not there.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    base = src.resolve().as_uri()
    last: Optional[BaseException] = None
    for uri, mode in ((f"{base}?mode=ro", "read-only"), (base, "read-write")):
        with contextlib.suppress(OSError):
            dst.unlink()
        try:
            source = sqlite3.connect(uri, uri=True, timeout=timeout)
        except sqlite3.Error as exc:
            last = exc
            continue
        try:
            target = sqlite3.connect(str(dst), timeout=timeout)
            try:
                source.backup(target)
            finally:
                target.close()
            return mode
        except sqlite3.Error as exc:
            last = exc
        finally:
            source.close()
    with contextlib.suppress(OSError):
        dst.unlink()
    raise OSError(f"sqlite backup failed: {last}")


@contextlib.contextmanager
def runtime_data_staging() -> Iterator[Path]:
    """A temp tree holding this run's database snapshots, removed on every path.

    The snapshots have to exist as ordinary files before the generic engine can
    hash, hardlink and verify them, so they are staged rather than streamed —
    but they are a working artifact, not output, and a failed run must not leave
    a second copy of every fleet database on the SSD.
    """
    staging = Path(tempfile.mkdtemp(prefix="fleet-backup-sqlite-"))
    try:
        yield staging
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def write_group(items: Sequence[Tuple[Path, str, int]], group: str, snapshot_dir: Path,
                prev_dir: Optional[Path], prev_index: Dict[str, Dict[str, Any]],
                errors: List[str],
                vanished: Optional[List[str]] = None) -> Tuple[List[Dict[str, Any]], int, int]:
    """Write one group's files into `snapshot_dir`, hardlinking what has not changed.

    Returns `(entries, linked, copied)`. Every source file is hashed on the way
    through — the cheap `(size, mtime)` shortcut rsync uses is deliberately not
    taken, because the manifest's sha256 is what the sample verification and every
    future restore are checked against, and a hash inherited from yesterday's
    manifest would only ever prove yesterday's read.

    A source file that **vanishes between enumeration and read** is recorded in
    `vanished`, not in `errors`. The fleet's apps write and rotate their own
    gitignored artifacts while this runs (whatsapp-radar dropped a `runs/` entry
    mid-snapshot on the very first two-day test), so treating that as a failure
    would turn the nightly alert red most nights — and an alert that fires most
    nights is one nobody reads, which costs more than the file did. A real IO
    error (permission, disk) still fails the run.
    """
    vanished = vanished if vanished is not None else []
    entries: List[Dict[str, Any]] = []
    linked = copied = 0
    for abs_path, rel, size in items:
        target = snapshot_dir / group / rel
        try:
            digest = sha256_file(abs_path)
            mtime = abs_path.stat().st_mtime
            target.parent.mkdir(parents=True, exist_ok=True)
            # Idempotent per target: a re-run on the same date, or a retry after a
            # partial run, must overwrite rather than fail. `os.link` refuses an
            # existing target where `copy2` would silently overwrite it, so the
            # two paths below are only equivalent once this is cleared.
            if target.exists():
                target.unlink()
            previous = prev_index.get(f"{group}/{rel}")
            prev_file = (prev_dir / group / rel) if (prev_dir and previous) else None
            if previous and previous.get("sha256") == digest and prev_file and prev_file.is_file():
                os.link(prev_file, target)
                linked += 1
            else:
                shutil.copy2(abs_path, target)
                copied += 1
        except FileNotFoundError:
            vanished.append(rel)
            continue
        except OSError as exc:
            errors.append(f"copy {abs_path}: {exc}")
            continue
        entries.append({"path": rel, "sha256": digest, "size": size, "mtime": mtime})
    return entries, linked, copied


def verify_sample(snapshot_dir: Path, manifest: Dict[str, Any],
                  rng: Optional[random.Random] = None) -> Dict[str, Any]:
    """Re-hash a random sample of what was just written, against the manifest.

    A backup nobody has ever read back is a hope, not a backup. 5% of the run,
    floor 20 files, capped at 500 so a huge run stays inside its window.
    """
    rng = rng or random.Random()
    population = [
        (group, entry)
        for group, payload in (manifest.get("groups") or {}).items()
        for entry in payload.get("files", [])
    ]
    if not population:
        return {"status": "empty", "sampled": 0, "mismatches": []}

    size = min(len(population), max(20, min(500, len(population) // 20)))
    mismatches: List[Dict[str, Any]] = []
    for group, entry in rng.sample(population, size):
        path = snapshot_dir / group / entry["path"]
        try:
            actual = sha256_file(path)
        except OSError as exc:
            mismatches.append({"path": f"{group}/{entry['path']}", "error": str(exc)})
            continue
        if actual != entry["sha256"]:
            mismatches.append({
                "path": f"{group}/{entry['path']}",
                "expected": entry["sha256"], "actual": actual,
            })
    return {
        "status": "pass" if not mismatches else "fail",
        "sampled": size,
        "mismatches": mismatches,
    }


def check_zero_file_regressions(manifest: Dict[str, Any],
                                prev_manifest: Dict[str, Any]) -> List[str]:
    """Groups that had files in the previous manifest and have none now.

    The failure mode this exists for is silent: a `.gitignore` edit, a moved
    directory, or a mistyped `backup_exclude` can stop backing a repo up while
    every other repo keeps succeeding and the run stays green. A repo that was
    deliberately opted out is absent from `groups` entirely, not present-and-empty,
    so it does not trip this.
    """
    previous = {
        group: len(payload.get("files", []))
        for group, payload in (prev_manifest.get("groups") or {}).items()
    }
    regressed = []
    for group, payload in (manifest.get("groups") or {}).items():
        if previous.get(group, 0) > 0 and not payload.get("files"):
            regressed.append(group)
    return sorted(regressed)

