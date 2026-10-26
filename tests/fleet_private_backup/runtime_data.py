import shutil
import sqlite3
import sys
import threading
import time
from pathlib import Path

from _suite import _write, check, tmp
import backup as bp


# =======================================================================
# The runtime-data leg (fleet-config#724)
# =======================================================================
# project-scaffolding#243 moved every always-on service's SQLite database to
# C:/sqlite/<app>/, outside every git working tree — invisible to the
# git-derived selection above. Everything here runs against REAL sqlite
# files with a REAL writer connection held open, because the two things this
# leg must get right (a live WAL database is not safely byte-copyable, and
# the global size cap must not apply) are both invisible to a mock.

# ---- sidecar classification -------------------------------------------
check(bp.is_sqlite_sidecar("app/tasks.sqlite3-wal"), "sidecar: -wal is a sidecar")
check(bp.is_sqlite_sidecar("app/tasks.sqlite3-shm"), "sidecar: -shm is a sidecar")
check(bp.is_sqlite_sidecar("app/store.db-journal"), "sidecar: -journal is a sidecar")
check(bp.is_sqlite_sidecar("APP/Tasks.SQLITE-WAL"), "sidecar: matching is case-insensitive")
check(not bp.is_sqlite_sidecar("app/tasks.sqlite3"), "sidecar: the database itself is not one")
check(not bp.is_sqlite_sidecar("notes-wal"),
      "sidecar: a plain file merely ending in -wal is NOT a sidecar (it has no db stem)")
check(not bp.is_sqlite_sidecar("app/readme.md"), "sidecar: an ordinary file is not one")

# ---- a live WAL database round-trips through the online backup API -----
rd_src = tmp / "runtime-live"
live_db = rd_src / "home-automation" / "telemetry.sqlite3"
live_db.parent.mkdir(parents=True, exist_ok=True)
writer = sqlite3.connect(str(live_db))
writer.execute("PRAGMA journal_mode=WAL")
writer.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, note TEXT)")
writer.executemany("INSERT INTO events (note) VALUES (?)",
                   [(f"event-{n}",) for n in range(50)])
writer.commit()
# Deliberately NOT closed: a running service holding the database open is the
# only state this leg ever meets in production, and it is the state in which
# a plain file copy is unsafe.
check((live_db.parent / "telemetry.sqlite3-wal").exists(),
      "runtime: the live database really is in WAL mode (-wal on disk)")

snap_target = tmp / "snap-out" / "telemetry.sqlite3"
mode = bp.snapshot_sqlite(live_db, snap_target)
check(mode in {"read-only", "read-write"}, f"runtime: snapshot reports its mode, got {mode}")
restored = sqlite3.connect(str(snap_target))
try:
    rows = restored.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    integrity = restored.execute("PRAGMA integrity_check").fetchone()[0]
    first = restored.execute("SELECT note FROM events ORDER BY id LIMIT 1").fetchone()[0]
finally:
    restored.close()
check(rows == 50, f"runtime: the snapshot is queryable with the same rows, got {rows}")
# The point of the online backup API, made concrete: with the writer still
# holding the database, the committed rows live in the -wal, and the main
# file on disk is a near-empty shell. A byte copy of it would restore an
# EMPTY table while looking like a successful backup.
check(snap_target.stat().st_size > live_db.stat().st_size,
      f"runtime: the snapshot folded in the -wal contents — it is larger than the "
      f"live main file it came from ({snap_target.stat().st_size} vs "
      f"{live_db.stat().st_size} bytes)")
byte_copy = tmp / "snap-out" / "byte-copy.sqlite3"
shutil.copy2(live_db, byte_copy)
naive = sqlite3.connect(str(byte_copy))
try:
    naive_rows = naive.execute("SELECT COUNT(*) FROM events").fetchone()[0]
except sqlite3.Error as exc:
    naive_rows = f"unreadable: {exc}"
finally:
    naive.close()
check(naive_rows != 50,
      f"runtime: and a plain file copy of the same live database really does LOSE "
      f"data ({naive_rows} rows) — which is why this leg does not copy bytes")
check(integrity == "ok", f"runtime: the snapshot passes integrity_check, got {integrity}")
check(first == "event-0", "runtime: the snapshot carries the real row contents")
check(not (snap_target.parent / "telemetry.sqlite3-wal").exists(),
      "runtime: a snapshot needs no -wal beside it — it is already complete")

# ---- a file that is not a database is a reported failure, not a silence --
corrupt_dir = tmp / "runtime-corrupt"
corrupt_db = corrupt_dir / "task-os" / "tasks.sqlite3"
_write(corrupt_db, "this is definitely not a sqlite database")
raised = False
try:
    bp.snapshot_sqlite(corrupt_db, tmp / "snap-out" / "corrupt.sqlite3")
except OSError:
    raised = True
check(raised, "runtime: an unopenable database raises rather than producing a fake snapshot")
check(not (tmp / "snap-out" / "corrupt.sqlite3").exists(),
      "runtime: a failed snapshot leaves no half-written file behind")

# ---- a lock held past the deadline is a bounded failure, not a hang ----
# fleet-config#1422: Connection.backup() with the default pages=-1 retries
# BUSY forever and ignores the connect timeout, so one database held under
# an exclusive lock blocked selection until something killed the process.
# Driven on a daemon thread so the pre-fix hang fails this check instead of
# hanging the suite.
def _snapshot_bounded(src_db: Path, out: Path, timeout: float, limit: float) -> dict:
    outcome: dict = {}

    def _run() -> None:
        began = time.monotonic()
        try:
            outcome["mode"] = bp.snapshot_sqlite(src_db, out, timeout=timeout)
        except OSError as exc:
            outcome["error"] = str(exc)
        outcome["seconds"] = time.monotonic() - began

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(limit)
    outcome["finished"] = not worker.is_alive()
    return outcome

held_db = tmp / "runtime-held" / "held.sqlite3"
held_db.parent.mkdir(parents=True, exist_ok=True)
held = sqlite3.connect(str(held_db), isolation_level=None)
held.execute("CREATE TABLE t (x)")
held.execute("BEGIN EXCLUSIVE")
try:
    held_out = _snapshot_bounded(held_db, tmp / "snap-out" / "held.sqlite3", 2.0, 20.0)
finally:
    held.close()
check(held_out["finished"] and "sqlite backup failed" in held_out.get("error", ""),
      f"runtime: a database locked past the deadline raises OSError, got {held_out!r}")
check(held_out.get("seconds", 99) < 8.0,
      f"runtime: ...and within the deadline plus a small margin, got {held_out.get('seconds')}")
check(not (tmp / "snap-out" / "held.sqlite3").exists(),
      "runtime: a stalled snapshot leaves no half-written file behind")

# A writer that commits between every step restarts the copy each time; the
# stall clock has to cover restarts too, not only lock waits. One page per
# step makes the restarts deterministic on a database this small.
churn_db = tmp / "runtime-churn" / "churn.sqlite3"
churn_db.parent.mkdir(parents=True, exist_ok=True)
churner = sqlite3.connect(str(churn_db), isolation_level=None, check_same_thread=False)
churner.execute("CREATE TABLE t (x BLOB)")
churner.execute("BEGIN")
for _ in range(20000):
    churner.execute("INSERT INTO t VALUES (randomblob(1000))")
churner.execute("COMMIT")
churn_stop = threading.Event()

def _churn() -> None:
    while not churn_stop.is_set():
        churner.execute("INSERT INTO t VALUES (randomblob(100))")
        time.sleep(0.005)

churn_thread = threading.Thread(target=_churn, daemon=True)
snapshot_mod = sys.modules["backup.snapshot"]
saved_pages = getattr(snapshot_mod, "_BACKUP_PAGES_PER_STEP", 1024)
snapshot_mod._BACKUP_PAGES_PER_STEP = 1
churn_thread.start()
