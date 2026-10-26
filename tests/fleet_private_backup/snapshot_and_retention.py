import json
import os
from datetime import datetime
from datetime import timedelta

from _suite import NOW, _write, cfg, check, tmp
import backup as bp


# ---- hardlink dedup ----------------------------------------------------
link_root = tmp / "linktest"
src = link_root / "src"
_write(src / "stable.md", "unchanged content")
_write(src / "moving.md", "v1")
day1 = link_root / "2026-08-10"
day2 = link_root / "2026-08-11"
items = [(src / "stable.md", "stable.md", 17), (src / "moving.md", "moving.md", 2)]
errs: list = []
entries1, linked1, copied1 = bp.write_group(items, "g", day1, None, {}, errs)
check(linked1 == 0 and copied1 == 2, "hardlink: the first snapshot copies everything")
check(not errs, f"hardlink: no errors on first write, got {errs}")

_write(src / "moving.md", "v2 changed")
manifest1 = {"groups": {"g": {"files": entries1}}}
entries2, linked2, copied2 = bp.write_group(
    items, "g", day2, day1, bp._index_manifest(manifest1), errs)
check(linked2 == 1 and copied2 == 1,
      f"hardlink: unchanged linked, changed copied (linked={linked2} copied={copied2})")
check(os.stat(day2 / "g" / "stable.md").st_nlink >= 2,
      "hardlink: the unchanged file really shares an inode with yesterday")
check(os.stat(day2 / "g" / "moving.md").st_nlink == 1,
      "hardlink: the changed file is an independent copy")
check((day2 / "g" / "moving.md").read_text(encoding="utf-8") == "v2 changed",
      "hardlink: the changed file carries the new content")
check((day1 / "g" / "moving.md").read_text(encoding="utf-8") == "v1",
      "hardlink: yesterday's snapshot still holds the old content")

# ---- a source file that vanishes mid-run is not a failure -------------
# Live fleet apps rotate their own gitignored artifacts while this runs
# (whatsapp-radar dropped a runs/ entry during the first two-day test). That
# must be its own state: an alert that fires most nights is one nobody reads.
vanish_src = tmp / "vanish"
_write(vanish_src / "here.md", "present")
vanish_items = [(vanish_src / "here.md", "here.md", 7),
                (vanish_src / "gone.md", "gone.md", 7)]  # never created
v_errors: list = []
v_vanished: list = []
v_entries, _, _ = bp.write_group(vanish_items, "g", tmp / "vanish-dest", None, {},
                                 v_errors, v_vanished)
check(v_vanished == ["gone.md"], f"vanish: the missing file is recorded, got {v_vanished}")
check(not v_errors, f"vanish: it is NOT recorded as an error, got {v_errors}")
check(len(v_entries) == 1, "vanish: the surviving file is still backed up")
perm_errors: list = []
bp.write_group([(vanish_src / "here.md", "sub/dir/x.md", 7)], "g",
               tmp / "vanish-dest", None, {}, perm_errors, [])
check(not perm_errors, "vanish: a nested target path is created, not an error")

# ---- sample verification ----------------------------------------------
manifest2 = {"groups": {"g": {"files": entries2}}}
verdict = bp.verify_sample(day2, manifest2)
check(verdict["status"] == "pass" and verdict["sampled"] == 2,
      f"verify: a clean snapshot passes, got {verdict}")
(day2 / "g" / "moving.md").write_text("corrupted after the fact", encoding="utf-8")
verdict_bad = bp.verify_sample(day2, manifest2)
check(verdict_bad["status"] == "fail" and verdict_bad["mismatches"],
      "verify: a corrupted snapshot file is caught")
(day2 / "g" / "gone.md").write_text("temp", encoding="utf-8")
missing_manifest = {"groups": {"g": {"files": [
    {"path": "absent.md", "sha256": "0" * 64, "size": 1, "mtime": 0}]}}}
check(bp.verify_sample(day2, missing_manifest)["status"] == "fail",
      "verify: a manifest entry with no file on disk is a failure")
check(bp.verify_sample(day2, {"groups": {}})["status"] == "empty",
      "verify: an empty run reports 'empty', not 'pass'")

# ---- zero-file regression ---------------------------------------------
prev = {"groups": {"life-os": {"files": [{"path": "identity/me.md"}]},
                   "quiet-repo": {"files": []}}}
now_manifest = {"groups": {"life-os": {"files": []}, "quiet-repo": {"files": []}}}
check(bp.check_zero_file_regressions(now_manifest, prev) == ["life-os"],
      "regression: a repo that lost every file is flagged")
check(bp.check_zero_file_regressions(
    {"groups": {"life-os": {"files": [{"path": "x"}]}}}, prev) == [],
    "regression: a repo that still has files is not flagged")
check(bp.check_zero_file_regressions({"groups": {}}, prev) == [],
      "regression: an opted-out repo (absent, not empty) is not flagged")

# ---- retention ---------------------------------------------------------
names = [(datetime(2026, 8, 11) - timedelta(days=n)).strftime(bp.DATE_FMT)
         for n in range(120)]
doomed = bp.plan_retention(names, keep_daily=14, keep_weekly=8)
survivors = sorted(set(names) - set(doomed), reverse=True)
check(all(n in survivors for n in names[:14]), "retention: the 14 newest dailies survive")
check(len(survivors) == 14 + 8,
      f"retention: 14 dailies + 8 weeklies survive, got {len(survivors)}")
weeks = {datetime.strptime(n, bp.DATE_FMT).isocalendar()[:2] for n in survivors[14:]}
check(len(weeks) == 8, "retention: the weeklies land in 8 distinct ISO weeks")
check(bp.plan_retention(names[:5], 14, 8) == [],
      "retention: fewer snapshots than the daily window deletes nothing")

# ---- freshness is three-state -----------------------------------------
fresh_root = tmp / "freshtest"
state, detail = bp.freshness(fresh_root, cfg, now=NOW)
check(state == bp.FRESHNESS_UNKNOWN,
      "freshness: no destination at all reports unknown, never ok")
snap = fresh_root / "2026-08-11"
snap.mkdir(parents=True)
state, _ = bp.freshness(fresh_root, cfg, now=NOW)
check(state == bp.FRESHNESS_UNKNOWN,
      "freshness: a snapshot with no readable manifest reports unknown")
(snap / bp.MANIFEST_NAME).write_text(json.dumps({
    "status": "ok", "finished_at": NOW.isoformat(), "totals": {"files": 3},
}), encoding="utf-8")
state, detail = bp.freshness(fresh_root, cfg, now=NOW)
check(state == bp.FRESHNESS_OK and detail["snapshot"] == "2026-08-11",
      f"freshness: a fresh successful run reports ok, got {state}")
state, _ = bp.freshness(fresh_root, cfg, now=NOW + timedelta(hours=72))
check(state == bp.FRESHNESS_STALE, "freshness: an old snapshot reports stale")
(snap / bp.MANIFEST_NAME).write_text(json.dumps({
    "status": "failed", "finished_at": NOW.isoformat(),
}), encoding="utf-8")
state, _ = bp.freshness(fresh_root, cfg, now=NOW)
check(state == bp.FRESHNESS_STALE,
      "freshness: a recent but failed run is not ok")

# ---- torn-snapshot marker (fleet-config#607) ---------------------------
marker_root = tmp / "markertest"
day_a = marker_root / "2026-08-10"
day_b = marker_root / "2026-08-11"
_write(day_a / "g" / "a.md", "content")
(day_a / bp.MANIFEST_NAME).write_text(json.dumps({
    "status": "ok", "finished_at": NOW.isoformat(),
    "groups": {"g": {"files": [{"path": "a.md", "sha256": "x", "size": 1}]}},
}), encoding="utf-8")

check(not bp.has_run_marker(day_a), "marker: a finished snapshot carries no marker")
day_b.mkdir(parents=True)
bp.write_run_marker(day_b, "repos")
check(bp.has_run_marker(day_b), "marker: write_run_marker leaves the file behind")
read_back = bp.read_run_marker(day_b)
check(read_back.get("leg") == "repos" and "pid" in read_back and "started_at" in read_back,
      f"marker: it carries leg, pid and start time, got {read_back}")
bp.clear_run_marker(day_b)
check(not bp.has_run_marker(day_b), "marker: clear_run_marker removes it")
bp.clear_run_marker(day_b)  # clearing an absent marker is not an error

# freshness: the newest snapshot carrying a marker reports unknown, never ok,
# even though an older manifest right beside it looks perfectly fine.
bp.write_run_marker(day_b, "repos")
state, detail = bp.freshness(marker_root, cfg, now=NOW)
check(state == bp.FRESHNESS_UNKNOWN and detail.get("reason") == "last run did not finish",
      f"freshness: a torn newest snapshot reports unknown, got {state} {detail}")
check(detail.get("snapshot") == "2026-08-11", "freshness: it names the torn snapshot")

# _previous_snapshot: a marked snapshot is skipped, falling back to the older
# clean one, and the skip is reported for the caller to name in its own report.
prev_dir, prev_manifest, skipped = bp._previous_snapshot(marker_root, "2026-08-12")
check(prev_dir == day_a and skipped == ["2026-08-11"],
      f"_previous_snapshot: skips the torn snapshot and falls back, got "
      f"prev_dir={prev_dir} skipped={skipped}")
check(prev_manifest.get("status") == "ok",
      "_previous_snapshot: the fallback manifest is the older clean one")
