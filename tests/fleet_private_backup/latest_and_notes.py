import os
import shutil
import sys
from pathlib import Path

from _suite import _write, check, tmp
from selection_rules import fleet, repo
import backup as bp


# ---- cross-volume preflight -------------------------------------------
check(bp._same_volume(tmp, tmp / "child"), "volume: two temp paths share a volume")
problem = bp._preflight(tmp / "fleet", tmp / "dest-same-volume")
check(problem and "same volume" in problem,
      "preflight: refuses a destination on the source volume")
if sys.platform == "win32" and Path("E:/").exists() and str(tmp)[:1].upper() == "C":
    check(not bp._same_volume(Path("C:/"), Path("E:/")),
          "volume: C: and E: are correctly seen as different volumes")

# ---- latest/ mirror ----------------------------------------------------
mirror_root = tmp / "mirrortest"
mirror_snap = mirror_root / "2026-08-11"
_write(mirror_snap / "g" / "a.md", "alpha")
_write(mirror_snap / bp.MANIFEST_NAME, "{}")
count = bp.rebuild_latest(mirror_root, mirror_snap)
latest_file = mirror_root / bp.LATEST_DIR / "g" / "a.md"
check(count == 2 and latest_file.is_file(), "latest: the mirror is built")
check(latest_file.read_text(encoding="utf-8") == "alpha", "latest: content matches")
check(os.stat(latest_file).st_nlink >= 2, "latest: the mirror is hardlinked, not copied")
_write(mirror_snap / "g" / "b.md", "beta")
bp.rebuild_latest(mirror_root, mirror_snap)
check((mirror_root / bp.LATEST_DIR / "g" / "b.md").is_file(),
      "latest: a rebuild picks up new files")
check(not (mirror_root / (bp.LATEST_DIR + ".new")).exists(),
      "latest: the staging directory is swapped in, not left behind")

# ---- latest/ survives an interrupt during teardown ---------------------
# fleet-config#720: rebuild_latest() used to shutil.rmtree() the live
# `latest/` *before* renaming the new one in — a kill mid-delete left
# `latest/` half-deleted for as long as the delete took (minutes, on a
# large tree). The swap is now two instant renames with the slow delete
# moved after, so a raise during that final cleanup must land on an
# already-fully-swapped `latest/`, never a partial one.
retiring_dir = mirror_root / (bp.LATEST_DIR + ".old")
_write(mirror_snap / "g" / "c.md", "gamma")
real_rmtree = shutil.rmtree

def _rmtree_raise_on_retiring(path, *args, **kwargs):
    if Path(path) == retiring_dir:
        raise OSError("simulated kill during teardown")
    return real_rmtree(path, *args, **kwargs)

bp.shutil.rmtree = _rmtree_raise_on_retiring
try:
    raised = False
    try:
        bp.rebuild_latest(mirror_root, mirror_snap)
    except OSError:
        raised = True
finally:
    bp.shutil.rmtree = real_rmtree
check(raised, "latest: the simulated teardown kill actually raised")
check((mirror_root / bp.LATEST_DIR / "g" / "c.md").is_file(),
      "latest: an interrupt during teardown still leaves latest/ fully rebuilt")
check(retiring_dir.is_dir(),
      "latest: the retired tree is left behind as garbage, not corrupting latest/")

# a following run sweeps the stale latest.old and does not fail on it
_write(mirror_snap / "g" / "d.md", "delta")
count2 = bp.rebuild_latest(mirror_root, mirror_snap)
expected_count = sum(1 for p in mirror_snap.rglob("*") if not p.is_dir())
check((mirror_root / bp.LATEST_DIR / "g" / "d.md").is_file(),
      "latest: the next run recovers cleanly after a torn teardown")
check(not retiring_dir.exists(),
      "latest: the stale latest.old is swept, not left behind forever")
check(count2 == expected_count,
      f"latest: the recovered mirror covers every file, got count={count2} vs {expected_count}")

# ---- the destination explains itself ----------------------------------
note_root = tmp / "notetest"
note_root.mkdir()
bp.write_restore_note(note_root, "repos", Path("E:/automation"))
note = (note_root / bp.RESTORE_NOTE_NAME).read_text(encoding="utf-8")
check("HOW TO RESTORE" in note and "Copy-Item" in note,
      "note: the destination carries restore instructions, not just data")
check("manifest.json" in note and "hardlinked" in note,
      "note: it explains the manifest and why dated folders are cheap")
check("backup_private.py" in note, "note: it names what wrote it")
bp.write_restore_note(note_root, "transcripts", Path.home() / ".claude" / "projects")
check("projects" in (note_root / bp.RESTORE_NOTE_NAME).read_text(encoding="utf-8"),
      "note: the transcripts leg gets its own example paths")
missing_root = tmp / "nonexistent-dir" / "deeper"
bp.write_restore_note(missing_root, "repos", Path("E:/automation"))
check(not missing_root.exists(),
      "note: an unwritable destination is warned about, never raised")

# ---- config loading ----------------------------------------------------
toml_path = tmp / "projects.toml"
toml_path.write_text(
    "[backup]\n"
    f'source_root = "{(tmp / "fleet").as_posix()}"\n'
    f'dest = "{(tmp / "cfgdest").as_posix()}"\n'
    "keep_daily = 3\n"
    "max_file_mb = 0.001\n"
    "bulk_dir_mb = 0.002\n"
    "\n"
    "[demo-repo]\n"
    f'cwd_prefix = "{repo.as_posix()}"\n'
    'backup_exclude = ["_local/vm/**"]\n'
    'backup_include_globs = ["webapp.log", "watchdog.log"]\n'
    'backup_always_include = ["webapp/telemetry.sqlite3"]\n'
    "\n"
    "[skipme]\n"
    f'cwd_prefix = "{(fleet / "skipme").as_posix()}"\n'
    "backup = false\n",
    encoding="utf-8",
)
loaded = bp.load_backup_config(toml_path)
check(loaded.keep_daily == 3, "config: keep_daily is read from the [backup] table")
check(loaded.max_file_bytes == int(0.001 * 1024 * 1024),
      "config: max_file_mb converts to bytes")
check(loaded.source_root == tmp / "fleet", "config: source_root is read")
check(loaded.deny_dirs == bp.DEFAULT_DENY_DIRS,
      "config: an unset key falls back to its default")
check(bp.load_backup_config(tmp / "absent.toml").keep_daily == 14,
      "config: a missing projects.toml yields usable defaults")

overrides = bp.load_repo_overrides(repo, toml_path)
check(overrides.enabled and overrides.exclude == ("_local/vm/**",),
      "overrides: a repo's own table supplies backup_exclude")
check(overrides.include_globs == ("webapp.log", "watchdog.log"),
      "overrides: a repo's own table supplies backup_include_globs")
check(overrides.always_include == ("webapp/telemetry.sqlite3",),
      "overrides: a repo's own table supplies backup_always_include")
(fleet / "skipme").mkdir(exist_ok=True)
check(not bp.load_repo_overrides(fleet / "skipme", toml_path).enabled,
      "overrides: backup = false opts a repo out")
check(bp.load_repo_overrides(fleet / "unknown-repo", toml_path).enabled,
      "overrides: a repo with no table defaults to being backed up")
