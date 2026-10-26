import json
import sqlite3
import sys
from pathlib import Path

from _suite import _cfg, _write, check, tmp
from runtime_data import _snapshot_bounded, churn_db, churn_stop, churn_thread, churner, corrupt_dir, live_db, rd_src, saved_pages, snapshot_mod
import backup as bp


try:
    churn_out = _snapshot_bounded(churn_db, tmp / "snap-out" / "churn.sqlite3", 2.0, 20.0)
finally:
    snapshot_mod._BACKUP_PAGES_PER_STEP = saved_pages
    churn_stop.set()
    churn_thread.join(10)
    churner.close()
check(churn_out["finished"] and "no progress" in churn_out.get("error", ""),
      f"runtime: a copy restarted by a live writer is abandoned too, got {churn_out!r}")

# A quiet database is untouched by any of this: it still snapshots whole.
quiet_out = _snapshot_bounded(live_db, tmp / "snap-out" / "quiet.sqlite3", 2.0, 20.0)
check(quiet_out["finished"] and quiet_out.get("mode") in {"read-only", "read-write"},
      f"runtime: an unlocked database still snapshots after the change, got {quiet_out!r}")

# ---- selection: no size cap, no sidecars, plain files pass through ------
_write(rd_src / "home-automation" / "notes.txt", "small")
_write(rd_src / "home-automation" / "big.blob", "b" * 5000)   # ~5x the 1 KB cap
_write(rd_src / "home-automation" / "notes-wal", "not a sidecar")
rd_cfg = _cfg(tmp, runtime_data_src=rd_src, runtime_data_dest=tmp / "rd-dest")
check(rd_cfg.max_file_bytes == 1024,
      "runtime: the cap under test is genuinely smaller than the database")
check(live_db.stat().st_size > rd_cfg.max_file_bytes,
      "runtime: the live database really is over the cap (the check below means something)")

with bp.runtime_data_staging() as staging:
    rd_sel = bp.select_runtime_data(rd_cfg, staging)
    rd_rels = {rel for _, rel, _ in rd_sel.files}
    # Looked up defensively rather than with `next(...)`: if the size cap
    # ever comes back to this leg the database disappears from the
    # selection, and that must surface as the named check below, not as a
    # StopIteration traceback three lines earlier.
    staged_db = next((p for p, rel, _ in rd_sel.files
                      if rel == "home-automation/telemetry.sqlite3"), None)
    check(staged_db is not None and staging in staged_db.parents,
          "runtime: the database is selected from the staging tree, not copied live")
    staged_count = None
    if staged_db is not None:
        staged_rows = sqlite3.connect(str(staged_db))
        try:
            staged_count = staged_rows.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        finally:
            staged_rows.close()
    check(staged_count == 50, f"runtime: the staged database is queryable, got {staged_count}")
    staging_seen = staging
check(not staging_seen.exists(), "runtime: the staging tree is removed on the success path")

check("home-automation/telemetry.sqlite3" in rd_rels,
      f"runtime: the over-cap database is SELECTED — no max_file_mb on this leg, got {sorted(rd_rels)}")
check("home-automation/big.blob" in rd_rels,
      "runtime: an over-cap plain file under the root is selected too")
check(not rd_sel.oversize,
      f"runtime: nothing is reported as oversize, because nothing was dropped for size, got {rd_sel.oversize}")
check("home-automation/notes.txt" in rd_rels, "runtime: an ordinary file is copied normally")
check("home-automation/notes-wal" in rd_rels,
      "runtime: a plain file merely ending in -wal survives")
check(not any(r.endswith(("-wal", "-shm")) and ".sqlite" in r for r in rd_rels),
      f"runtime: the real -wal/-shm sidecars are excluded, got {sorted(rd_rels)}")
check(not rd_sel.errors, f"runtime: a healthy root produces no errors, got {rd_sel.errors}")

# Session transcripts are the payload; a size cap would silently drop the
# longest and most valuable conversations.
_write(rd_cfg.transcripts_src / "proj" / "huge.jsonl", "j" * 5000)
_write(rd_cfg.transcripts_src / "proj" / "small.jsonl", "{}")
t_sel = bp.select_transcripts(rd_cfg)
t_rels = {rel for _, rel, _ in t_sel.files}
check({"proj/small.jsonl", "proj/huge.jsonl"} <= t_rels,
      f"sessions: transcript files bypass max_file_mb, got {sorted(t_rels)}")
check(not t_sel.oversize, "sessions: transcript selection drops nothing for size")

# ---- Codex resume-critical allowlist + live SQLite snapshot -----------
codex_root = tmp / "codex-fixture"
codex_cfg = _cfg(tmp, codex_sessions_src=codex_root,
                 codex_sessions_dest=tmp / "codex-fixture-dest")
active_id = "11111111-1111-4111-8111-111111111111"
archived_id = "22222222-2222-4222-8222-222222222222"
active_rollout = _write(
    codex_root / "sessions" / f"rollout-2026-08-11-{active_id}.jsonl", "{}",
)
archived_rollout = _write(
    codex_root / "archived_sessions" / f"rollout-2026-08-10-{archived_id}.jsonl", "{}",
)
_write(codex_root / "history.jsonl", "{}")
_write(codex_root / "session_index.jsonl", "{}")
_write(codex_root / "auth.json", '{"secret":"must-not-leave-source"}')
state_db = codex_root / "state_5.sqlite"
state_conn = sqlite3.connect(state_db)
state_conn.execute("CREATE TABLE threads (id TEXT, rollout_path TEXT, archived INTEGER)")
state_conn.executemany(
    "INSERT INTO threads VALUES (?, ?, ?)",
    [(active_id, str(active_rollout), 0), (archived_id, str(archived_rollout), 1)],
)
state_conn.commit()
state_conn.close()
history_db = codex_root / "thread_history_1.sqlite"
history_conn = sqlite3.connect(history_db)
history_conn.execute("CREATE TABLE thread_items (thread_id TEXT, item_json TEXT)")
history_conn.commit()
history_conn.close()
with bp.runtime_data_staging() as staging:
    codex_sel = bp.select_codex_sessions(codex_cfg, staging)
    codex_rels = {rel for _, rel, _ in codex_sel.files}
    check({"history.jsonl", "session_index.jsonl", "state_5.sqlite",
           "thread_history_1.sqlite"} <= codex_rels,
          f"codex: indexes and safely-snapshotted DBs are selected, got {sorted(codex_rels)}")
    check(any(rel.startswith("sessions/") for rel in codex_rels)
          and any(rel.startswith("archived_sessions/") for rel in codex_rels),
          "codex: active and archived rollouts are both selected")
    check("auth.json" not in codex_rels, "codex: credentials are outside the allowlist")
    check({s["id"] for s in codex_sel.sessions} == {active_id, archived_id},
          f"codex: native inventory covers both session ids, got {codex_sel.sessions}")
    check(not codex_sel.errors, f"codex: healthy native state has no errors: {codex_sel.errors}")

# ---- explicit-deletion-only session archive --------------------------
archive_src = tmp / "archive-source"
archive_id = "33333333-3333-4333-8333-333333333333"
archive_file = _write(archive_src / f"{archive_id}.jsonl", "first")
archive_sel = bp.Selection(
    files=[(archive_file, archive_file.name, archive_file.stat().st_size)],
    sessions=[{"id": archive_id, "path": archive_file.name, "archived": False}],
)
archive_dest = tmp / "archive-dest"
first_archive = bp.run_leg(
    bp.CLAUDE_SESSIONS_LEG, {"projects": archive_sel}, archive_src,
    archive_dest, rd_cfg, "2026-08-11", False,
)
durable_file = archive_dest / bp.SESSION_ARCHIVE_DIR / "projects" / archive_file.name
check(first_archive["archive"]["status"] == "pass" and durable_file.is_file(),
      "archive: the first session copy is complete and verified")
archive_file.unlink()
second_archive = bp.run_leg(
    bp.CLAUDE_SESSIONS_LEG, {"projects": bp.Selection()}, archive_src,
    archive_dest, rd_cfg, "2026-08-12", False,
)
archive_index = json.loads(
    (archive_dest / bp.ARCHIVE_MANIFEST_NAME).read_text(encoding="utf-8")
)
check(durable_file.is_file() and durable_file.read_text(encoding="utf-8") == "first",
      "archive: source deletion never removes the last durable copy")
check(archive_id in archive_index["sessions"],
      "archive: a disappeared session remains indexed by native id")
check(second_archive["archive"]["retained_missing"] == 1,
      "archive: the manifest reports retention after source removal")
audit_after_removal = bp.audit_session_archive(
    archive_dest, {"projects": bp.Selection()},
)
check(audit_after_removal["status"] == "ok",
      "archive audit: source removal is safe when the durable archive still has the session")
lagging_file = _write(archive_src / archive_file.name, "newer live content")
lagging_audit = bp.audit_session_archive(
    archive_dest,
    {"projects": bp.Selection(files=[(
        lagging_file, lagging_file.name, lagging_file.stat().st_size,
    )], sessions=[{"id": archive_id, "path": lagging_file.name}])},
)
check(lagging_audit["status"] == "ok"
      and lagging_audit["lagging_files"] == [f"projects/{archive_file.name}"],
      f"archive audit: a newer live append is reported as lagging but remains restorable, "
      f"got {lagging_audit}")
new_archive_file = _write(archive_src / "new-session.jsonl", "not archived yet")
incomplete_audit = bp.audit_session_archive(
    archive_dest,
    {"projects": bp.Selection(files=[(
        new_archive_file, new_archive_file.name, new_archive_file.stat().st_size,
    )])},
)
check(incomplete_audit["status"] == "incomplete"
      and incomplete_audit["missing_files"] == ["projects/new-session.jsonl"],
      f"archive audit: a current unarchived file is incomplete, got {incomplete_audit}")
corrupt_archive_dest = tmp / "corrupt-archive"
corrupt_archive_dest.mkdir()
_write(corrupt_archive_dest / bp.ARCHIVE_MANIFEST_NAME, "not json")
unknown_audit = bp.audit_session_archive(corrupt_archive_dest, {"projects": bp.Selection()})
check(unknown_audit["status"] == "unknown",
      f"archive audit: an unreadable index is unknown, never ok, got {unknown_audit}")

# ---- a corrupt database is its own failure state, never a silent omission
_write(corrupt_dir / "task-os" / "notes.md", "still fine")
corrupt_cfg = _cfg(tmp, runtime_data_src=corrupt_dir, runtime_data_dest=tmp / "corrupt-dest")
with bp.runtime_data_staging() as staging:
    bad_sel = bp.select_runtime_data(corrupt_cfg, staging)
bad_rels = {rel for _, rel, _ in bad_sel.files}
check("task-os/tasks.sqlite3" not in bad_rels,
      "runtime: an unsnapshottable database is not passed off as backed up")
check(any("task-os/tasks.sqlite3" in e for e in bad_sel.errors),
      f"runtime: it is REPORTED as a failure instead, got {bad_sel.errors}")
check("task-os/notes.md" in bad_rels,
      "runtime: one bad database does not abort the rest of the root")

# ---- a missing root selects nothing, without erroring ------------------
absent_cfg = _cfg(tmp, runtime_data_src=tmp / "never-created")
with bp.runtime_data_staging() as staging:
    absent_sel = bp.select_runtime_data(absent_cfg, staging)
check(not absent_sel.files and not absent_sel.errors,
      f"runtime: an absent root is empty and quiet, got files={absent_sel.files} "
      f"errors={absent_sel.errors}")

# ---- the staging tree is removed on the failure path too ---------------
class _StagingCrash(Exception):
    pass

crash_staging = None
try:
    with bp.runtime_data_staging() as staging:
        crash_staging = staging
        raise _StagingCrash("died mid-leg")
except _StagingCrash:
    pass
check(crash_staging is not None and not crash_staging.exists(),
      "runtime: the staging tree is removed when the leg raises, not just when it succeeds")

# ---- the manifest does not claim a cap it did not apply ----------------
rd_policy = rd_cfg.policy_summary(bp.RUNTIME_DATA_LEG)
check(rd_policy["max_file_mb"] is None and "size_cap_exempt" in rd_policy,
      f"runtime: the leg's policy records the exemption rather than a cap it ignored, "
      f"got {rd_policy}")
check(rd_cfg.policy_summary("repos")["max_file_mb"] > 0,
      "runtime: the repos leg's policy still reports its real cap")

# ---- the destination explains this leg in its own terms ----------------
rd_note_root = tmp / "rd-notetest"
rd_note_root.mkdir()
bp.write_restore_note(rd_note_root, bp.RUNTIME_DATA_LEG, Path("C:/sqlite"))
rd_note = (rd_note_root / bp.RESTORE_NOTE_NAME).read_text(encoding="utf-8")
check("online backup API" in rd_note and "-wal" in rd_note,
      "runtime: the restore note explains that these are complete databases, not byte copies")
check("files git deliberately ignores" not in rd_note,
      "runtime: it does not repeat the other legs' 'files git ignores' framing")
session_note = (tmp / "notetest" / bp.RESTORE_NOTE_NAME).read_text(encoding="utf-8")
check("explicit-deletion-only" in session_note and "plaintext" in session_note,
      "runtime: while the session leg explains its durable private archive")

# ---- config ------------------------------------------------------------
rd_toml = tmp / "rd-projects.toml"
rd_toml.write_text(
    "[backup]\n"
    'runtime_data_src  = "D:/elsewhere/sqlite"\n'
    'runtime_data_dest = "D:/elsewhere/backup"\n',
    encoding="utf-8",
)
rd_loaded = bp.load_backup_config(rd_toml)
check(rd_loaded.runtime_data_src == Path("D:/elsewhere/sqlite"),
      "config: runtime_data_src is read from the [backup] table")
check(rd_loaded.runtime_data_dest == Path("D:/elsewhere/backup"),
      "config: runtime_data_dest is read from the [backup] table")
defaults = bp.load_backup_config(tmp / "absent.toml")
check(defaults.runtime_data_src == Path("C:/sqlite"),
      f"config: the default root is C:/sqlite, got {defaults.runtime_data_src}")
check(str(defaults.runtime_data_dest).upper().startswith("E:"),
      f"config: the default destination is on E:, so the leg crosses volumes, "
      f"got {defaults.runtime_data_dest}")
real_backup_table = bp._read_toml(bp.config._projects_toml_path()).get("backup", {})
check(real_backup_table.get("runtime_data_src") and real_backup_table.get("runtime_data_dest"),
      "projects.toml: the real [backup] table declares both runtime-data keys")
check(real_backup_table.get("codex_sessions_src")
      and real_backup_table.get("codex_sessions_dest"),
      "projects.toml: the real [backup] table declares both Codex-session keys")
real_cfg = bp.load_backup_config(bp.config._projects_toml_path())
check(not bp._same_volume(real_cfg.runtime_data_src.parent, real_cfg.runtime_data_dest.parent)
      or sys.platform != "win32",
      "projects.toml: the real runtime-data leg crosses volumes (preflight would refuse it)")
