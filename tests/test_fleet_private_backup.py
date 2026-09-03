"""Unit tests for the daily fleet-private backup engine (fleet-config#590).

Everything here runs against real temp trees, a real `git init`ed repo, a real
Windows junction, and real hardlinks — the three things this engine gets wrong
if it only ever meets mocks are exactly junction traversal, `st_nlink`, and what
`git ls-files --others --ignored` actually emits.
"""

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hooks"))
import backup as bp  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

NOW = datetime(2026, 8, 11, 3, 0, 0, tzinfo=timezone.utc)


def _write(path: Path, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", creationflags=bp._lib.NO_WINDOW,
    )


def _make_junction(link: Path, target: Path) -> bool:
    """Create a real directory junction; False when the platform cannot."""
    if sys.platform != "win32":
        try:
            os.symlink(target, link, target_is_directory=True)
            return True
        except OSError:
            return False
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True, text=True, creationflags=bp._lib.NO_WINDOW,
    )
    return result.returncode == 0 and link.exists()


def _cfg(tmp: Path, **overrides) -> bp.BackupConfig:
    base = dict(
        source_root=tmp / "fleet",
        dest=tmp / "dest",
        transcripts_src=tmp / "transcripts",
        transcripts_dest=tmp / "tdest",
        # Absent by default, so every pre-existing case here keeps its two-leg
        # shape and the runtime-data cases opt in explicitly.
        runtime_data_src=tmp / "runtime-data-src",
        runtime_data_dest=tmp / "runtime-data-dest",
        keep_daily=14,
        keep_weekly=8,
        max_file_bytes=1024,
        bulk_dir_bytes=2048,
        freshness_max_hours=48,
    )
    base.update(overrides)
    return bp.BackupConfig(**base)


tmp = Path(tempfile.mkdtemp(prefix="backup_private_"))
try:
    cfg = _cfg(tmp)

    # ---- deny-list ---------------------------------------------------------
    check(bp._denied(".venv/lib/x.py", cfg), "deny: a .venv segment anywhere is denied")
    check(bp._denied("app/node_modules/y.js", cfg), "deny: node_modules nested is denied")
    check(bp._denied("logs/run.log", cfg), "deny: *.log matches the glob list")
    check(bp._denied("build/out.PYC", cfg), "deny: glob match is case-insensitive")
    check(bp._denied("app/.VENV/lib.py", cfg), "deny: a deny-dir match is case-insensitive")
    check(not bp._denied("identity/me.md", cfg), "deny: an ordinary personal file survives")
    check(not bp._denied(".env", cfg), "deny: .env survives")
    check(not bp._denied("venv-notes.md", cfg),
          "deny: a name merely containing a denied word is not denied")

    # ---- per-repo include_globs exempts an otherwise-denied basename -------
    exempt = bp.RepoOverrides(include_globs=("webapp.log", "watchdog.log"))
    check(not bp._denied("webapp/webapp.log", cfg, exempt),
          "deny: include_globs exempts a *.log basename anywhere in the tree")
    check(not bp._denied("logs/watchdog.log", cfg, exempt),
          "deny: include_globs also bypasses a denied *directory* (logs/)")
    check(bp._denied("webapp/other.log", cfg, exempt),
          "deny: include_globs is a specific-basename allowlist, not a blanket *.log pass")
    check(bp._denied("webapp/webapp.log", cfg),
          "deny: with no overrides passed, the glob still denies as before")

    # ---- Slack summary stays ASCII and never trails off --------------------
    summary = bp._slack_summary([{"leg": "repos", "totals": {
        "files": 6047, "bytes": 192 * 1024 * 1024, "bulk_excluded_dirs": 13}}], bp.EXIT_OK)
    check(summary.isascii(), f"slack: the summary is pure ASCII (fleet-config#507): {summary}")
    check("6047 files" in summary and "13 bulk dirs" in summary,
          "slack: the summary carries the counts")
    check("no leg completed" in bp._slack_summary([], bp.EXIT_DEST_UNUSABLE),
          "slack: a run with no manifest says so instead of trailing off")

    # ---- exclude/include glob matching ------------------------------------
    check(bp._matches_any("_local/vm/disk.vhdx", ["_local/vm/**"]), "match: ** glob form")
    check(bp._matches_any("_local/vm/disk.vhdx", ["_local/vm"]), "match: bare-prefix form")
    check(bp._matches_any("_local/vm", ["_local/vm/"]), "match: trailing-slash form")
    check(not bp._matches_any("_local/notes.md", ["_local/vm/**"]),
          "match: a sibling of an excluded dir is not excluded")

    # ---- reparse-point detection (the junction footgun) -------------------
    real_dir = (tmp / "real").resolve()
    real_dir.mkdir()
    _write(real_dir / "inside.md", "precious")
    junction = tmp / "link"
    if _make_junction(junction, real_dir):
        check(bp.is_reparse_point(junction), "reparse: a real junction is detected")
        check(not junction.is_symlink() or sys.platform != "win32",
              "reparse: is_symlink() is False for a junction (the reason this helper exists)")
    else:
        check(True, "reparse: junction creation unavailable — skipped")
    check(not bp.is_reparse_point(real_dir), "reparse: an ordinary directory is not one")
    check(bp.is_reparse_point(tmp / "does-not-exist"),
          "reparse: an unstattable path fails closed")

    # ---- walk_files never crosses a junction ------------------------------
    walk_root = tmp / "walk"
    _write(walk_root / "keep.md")
    _write(walk_root / "nested" / "deep.md")
    _write(walk_root / "trash.log")
    _write(walk_root / ".venv" / "lib.py")
    if _make_junction(walk_root / "linked", real_dir):
        pass
    errors: list = []
    walked = {rel for _, rel, _ in bp.walk_files(walk_root, "", cfg, errors)}
    check(walked == {"keep.md", "nested/deep.md"},
          f"walk: deny-list + junction skipped, got {sorted(walked)}")
    check(not errors, "walk: a clean tree produces no errors")

    # ---- bulk-directory guard ---------------------------------------------
    items = [
        (tmp / "a", "root.env", 10),
        (tmp / "b", "identity/me.md", 100),
        (tmp / "c", "generated/big.json", 4096),
    ]
    kept, excluded = bp._apply_bulk_guard(items, cfg, bp.RepoOverrides())
    kept_rels = {rel for _, rel, _ in kept}
    check(kept_rels == {"root.env", "identity/me.md"},
          f"bulk: the oversized top-level dir is dropped, got {sorted(kept_rels)}")
    check(len(excluded) == 1 and excluded[0]["path"] == "generated/",
          "bulk: the exclusion is reported with its path")
    check(excluded[0]["files"] == 1 and excluded[0]["bytes"] == 4096,
          "bulk: the exclusion carries size and file count")

    kept2, excluded2 = bp._apply_bulk_guard(
        items, cfg, bp.RepoOverrides(include=("generated",)))
    check(len(kept2) == 3 and not excluded2, "bulk: backup_include re-admits a dropped dir")

    huge_root = [(tmp / "a", "one.bin", 999999)]
    kept3, excluded3 = bp._apply_bulk_guard(huge_root, cfg, bp.RepoOverrides())
    check(len(kept3) == 1 and not excluded3,
          "bulk: files at the repo root are never subject to the guard")

    # fleet-config#722: a forced-include file must not (a) get dropped along with
    # its bulk-excluded sibling directory, nor (b) count toward pushing that
    # directory's total over the threshold in the first place.
    forced_items = [
        (tmp / "d", "webapp/small.txt", 100),               # 100 B, well under 2 KB cap
        (tmp / "e", "webapp/telemetry.sqlite3", 4096),        # forced; over the 2 KB bulk cap alone
    ]
    kept4, excluded4 = bp._apply_bulk_guard(
        forced_items, cfg, bp.RepoOverrides(always_include=("webapp/telemetry.sqlite3",)))
    kept4_rels = {rel for _, rel, _ in kept4}
    check(kept4_rels == {"webapp/small.txt", "webapp/telemetry.sqlite3"},
          f"bulk: always_include survives even though its dir would otherwise be dropped, got {sorted(kept4_rels)}")
    check(not excluded4,
          "bulk: the forced file's bytes don't count toward its sibling dir's bulk total")

    kept4_plain, excluded4_plain = bp._apply_bulk_guard(forced_items, cfg, bp.RepoOverrides())
    check(not kept4_plain and excluded4_plain,
          "bulk: without the override, the same two files together do trip the guard "
          "(proves the exemption above is doing real work, not a no-op)")

    # ---- selection over a real git repo -----------------------------------
    fleet = tmp / "fleet"
    repo = fleet / "demo-repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _write(repo / ".gitignore", "\n".join([
        ".env", "identity/", ".venv/", "node_modules/", "*.log", "generated/",
        "big.dat", "_local/",
    ]))
    _write(repo / "README.md", "tracked")
    _write(repo / ".env", "SECRET=1")
    _write(repo / "identity" / "me.md", "who I am")
    _write(repo / "identity" / "deep" / "more.md", "nested personal file")
    _write(repo / ".venv" / "lib" / "site.py", "junk")
    _write(repo / "node_modules" / "pkg" / "index.js", "junk")
    _write(repo / "app.log", "noise")
    _write(repo / "big.dat", "z" * 5000)              # over max_file_bytes (1 KB)
    # Individually under the 1 KB file cap, together over the 2 KB dir threshold —
    # so this can only be caught by the bulk-dir guard, not by the size cap.
    for n in range(4):
        _write(repo / "generated" / f"out{n}.json", "y" * 900)
    _write(repo / "_local" / "notes.md", "keep me")
    _write(repo / "_local" / "vm" / "disk.img", "q" * 900)

    selection = bp.select_repo(repo, cfg, bp.RepoOverrides())
    rels = {rel for _, rel, _ in selection.files}
    check(".env" in rels, "select: .env is backed up")
    check("identity/me.md" in rels and "identity/deep/more.md" in rels,
          "select: a personal directory is backed up recursively")
    check(not any(r.startswith(".venv/") for r in rels), "select: .venv is absent")
    check(not any(r.startswith("node_modules/") for r in rels),
          "select: node_modules is absent")
    check("app.log" not in rels, "select: a *.log file is absent")
    check("README.md" not in rels, "select: a git-tracked file is not in the ignored set")
    check("big.dat" not in rels, "select: an over-cap file is absent")
    check(any(o["path"] == "big.dat" for o in selection.oversize),
          "select: the over-cap file is reported, not silently dropped")
    check(any(e["path"] == "generated/" for e in selection.bulk_excluded),
          "select: the bulk directory is reported")
    check(not selection.errors, f"select: no errors, got {selection.errors}")

    narrowed = bp.select_repo(repo, cfg, bp.RepoOverrides(exclude=("_local/vm/**",)))
    narrowed_rels = {rel for _, rel, _ in narrowed.files}
    check("_local/notes.md" in narrowed_rels,
          "select: backup_exclude on a subtree rescues its siblings from the guard")
    check(not any(r.startswith("_local/vm/") for r in narrowed_rels),
          "select: the excluded subtree itself is gone")

    opted_out = bp.select_repo(repo, cfg, bp.RepoOverrides(exclude=("**",)))
    check(not opted_out.files, "select: a catch-all exclude selects nothing")

    # ---- fleet-config#722: per-repo glob/size exemptions --------------------
    log_exempt = bp.select_repo(repo, cfg, bp.RepoOverrides(include_globs=("app.log",)))
    log_exempt_rels = {rel for _, rel, _ in log_exempt.files}
    check("app.log" in log_exempt_rels,
          "select: backup_include_globs rescues a specific *.log basename")

    size_exempt = bp.select_repo(repo, cfg, bp.RepoOverrides(always_include=("big.dat",)))
    size_exempt_rels = {rel for _, rel, _ in size_exempt.files}
    check("big.dat" in size_exempt_rels,
          "select: backup_always_include rescues a specific over-cap file")
    check(not any(o["path"] == "big.dat" for o in size_exempt.oversize),
          "select: an always_include file is not double-reported as oversize")

    # ---- overlapping git entries are deduped ------------------------------
    # `git ls-files --directory` emits a partially-ignored directory's files AND a
    # collapsed entry for a wholly-ignored subdirectory under it, so the same file
    # is reached twice. Day one hid it (copy2 overwrites); day two failed 516 files
    # across 11 repos on os.link (fleet-config#590).
    original_entries = bp.select.git_ignored_entries
    bp.select.git_ignored_entries = lambda repo_dir: [".env", "identity/", "identity/me.md",
                                               "identity/", ".env"]
    try:
        overlapped = bp.select_repo(repo, cfg, bp.RepoOverrides())
    finally:
        bp.select.git_ignored_entries = original_entries
    overlapped_rels = [rel for _, rel, _ in overlapped.files]
    check(len(overlapped_rels) == len(set(overlapped_rels)),
          f"dedupe: overlapping git entries yield no duplicate paths, got {overlapped_rels}")
    check(set(overlapped_rels) == {".env", "identity/me.md", "identity/deep/more.md"},
          f"dedupe: every distinct file still survives, got {sorted(set(overlapped_rels))}")
    check(bp._dedupe_by_rel([(tmp, "a", 1), (tmp, "a", 1), (tmp, "b", 2)]) ==
          [(tmp, "a", 1), (tmp, "b", 2)], "dedupe: the helper keeps first occurrence order")

    # ---- writing over an existing target is idempotent ---------------------
    rerun_src = tmp / "rerun"
    _write(rerun_src / "f.md", "content")
    rerun_day = tmp / "rerun-dest"
    rerun_items = [(rerun_src / "f.md", "f.md", 7)]
    rerun_errs: list = []
    bp.write_group(rerun_items, "g", rerun_day, None, {}, rerun_errs)
    entries_again, _, _ = bp.write_group(rerun_items, "g", rerun_day, None, {}, rerun_errs)
    check(not rerun_errs, f"rerun: writing over an existing target succeeds, got {rerun_errs}")
    check(len(entries_again) == 1 and (rerun_day / "g" / "f.md").is_file(),
          "rerun: the file is still there afterwards")

    # ---- iter_repos skips linked worktrees --------------------------------
    _write(fleet / "not-a-repo" / "file.txt")
    worktree = fleet / "demo-repo-wt-1"
    worktree.mkdir()
    _write(worktree / ".git", "gitdir: ../demo-repo/.git/worktrees/wt1")
    found = {p.name for p in bp.iter_repos(fleet)}
    check(found == {"demo-repo"},
          f"iter_repos: only real repos, worktrees and non-repos skipped, got {found}")

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

    # The transcripts leg still caps — this exemption is scoped to one leg, and
    # "unify the two selectors" must stay a change someone has to make on purpose.
    _write(rd_cfg.transcripts_src / "proj" / "huge.jsonl", "j" * 5000)
    _write(rd_cfg.transcripts_src / "proj" / "small.jsonl", "{}")
    t_sel = bp.select_transcripts(rd_cfg)
    t_rels = {rel for _, rel, _ in t_sel.files}
    check("proj/small.jsonl" in t_rels and "proj/huge.jsonl" not in t_rels,
          f"runtime: the transcripts leg still applies max_file_mb, got {sorted(t_rels)}")
    check(any(o["path"] == "proj/huge.jsonl" for o in t_sel.oversize),
          "runtime: and still reports what it dropped for size")

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
    check("files git deliberately ignores" in
          (tmp / "notetest" / bp.RESTORE_NOTE_NAME).read_text(encoding="utf-8"),
          "runtime: while the git-derived legs keep theirs unchanged")

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
    real_backup_table = bp._read_toml(ROOT / "hooks" / "projects.toml").get("backup", {})
    check(real_backup_table.get("runtime_data_src") and real_backup_table.get("runtime_data_dest"),
          "projects.toml: the real [backup] table declares both runtime-data keys")
    real_cfg = bp.load_backup_config(ROOT / "hooks" / "projects.toml")
    check(not bp._same_volume(real_cfg.runtime_data_src.parent, real_cfg.runtime_data_dest.parent)
          or sys.platform != "win32",
          "projects.toml: the real runtime-data leg crosses volumes (preflight would refuse it)")

    # ---- end-to-end run ----------------------------------------------------
    e2e_cfg = _cfg(tmp, dest=tmp / "e2e-dest", transcripts_src=tmp / "e2e-transcripts",
                   transcripts_dest=tmp / "e2e-tdest")
    _write(e2e_cfg.transcripts_src / "proj" / "session.jsonl", '{"a":1}')

    original_preflight = bp.cli._preflight
    bp.cli._preflight = lambda source, dest: None  # temp dirs share a volume by construction
    try:
        code, manifests = bp.run(e2e_cfg, only=None, toml_path=toml_path, notify=False,
                                 today="2026-08-11")
        check(code == bp.EXIT_OK, f"e2e: a clean run exits 0, got {code}")
        check({m["leg"] for m in manifests} == {"repos", "transcripts"},
              "e2e: the two git-derived legs ran, and the runtime-data leg is "
              "cleanly absent because its root does not exist (fleet-config#724)")
        repos_manifest = next(m for m in manifests if m["leg"] == "repos")
        check(repos_manifest["status"] == "ok", "e2e: the repos leg reports ok")
        check("demo-repo" in repos_manifest["groups"], "e2e: the demo repo was snapshotted")
        snapshot = e2e_cfg.dest / "2026-08-11"
        check((snapshot / "demo-repo" / ".env").is_file(),
              "e2e: .env landed in the dated snapshot")
        check((snapshot / "demo-repo" / "identity" / "me.md").is_file(),
              "e2e: the personal directory landed in the dated snapshot")
        check(not (snapshot / "demo-repo" / ".venv").exists(),
              "e2e: .venv is absent from the written snapshot")
        check((e2e_cfg.dest / bp.LATEST_DIR / "demo-repo" / ".env").is_file(),
              "e2e: latest/ mirrors the snapshot")
        check((snapshot / bp.MANIFEST_NAME).is_file(), "e2e: the manifest was written")
        written = json.loads((snapshot / bp.MANIFEST_NAME).read_text(encoding="utf-8"))
        check(written["verification"]["status"] == "pass",
              "e2e: the run verified its own output")
        check(written["policy"]["bulk_dir_mb"] > 0, "e2e: the manifest records the policy")
        transcripts_manifest = next(m for m in manifests if m["leg"] == "transcripts")
        check(transcripts_manifest["totals"]["files"] == 1,
              "e2e: the transcripts leg snapshotted its tree")
        check((e2e_cfg.transcripts_dest / "2026-08-11" / "projects" / "proj"
               / "session.jsonl").is_file(), "e2e: the transcripts leg wrote to its own dest")

        code2, manifests2 = bp.run(e2e_cfg, only=None, toml_path=toml_path, notify=False,
                                   today="2026-08-12")
        repos2 = next(m for m in manifests2 if m["leg"] == "repos")
        check(code2 == bp.EXIT_OK, f"e2e: the second run also exits 0, got {code2}")
        check(repos2["totals"]["linked"] > 0 and repos2["totals"]["copied"] == 0,
              f"e2e: an unchanged second run is all hardlinks, got {repos2['totals']}")

        dry_code, dry_manifests = bp.run(e2e_cfg, dry_run=True, toml_path=toml_path,
                                         notify=False, today="2026-08-13")
        check(dry_code == bp.EXIT_OK and all(m["status"] == "dry-run" for m in dry_manifests),
              "e2e: --dry-run reports without writing")
        check(not (e2e_cfg.dest / "2026-08-13").exists(),
              "e2e: --dry-run created no snapshot directory")
        check(not (tmp / "runtime-data-dest").exists(),
              "e2e: a skipped runtime-data leg writes nothing at all, not an empty snapshot")

        # ---- end-to-end, runtime-data leg ---------------------------------
        rd_e2e = _cfg(tmp, dest=tmp / "rd-e2e-dest",
                      transcripts_src=tmp / "rd-e2e-transcripts",
                      transcripts_dest=tmp / "rd-e2e-tdest",
                      runtime_data_src=rd_src, runtime_data_dest=tmp / "rd-e2e-rdest")
        _write(rd_e2e.transcripts_src / "proj" / "session.jsonl", '{"a":1}')
        rd_code, rd_manifests = bp.run(rd_e2e, toml_path=toml_path, notify=False,
                                       today="2026-08-20")
        check(rd_code == bp.EXIT_OK, f"rd-e2e: a clean three-leg run exits 0, got {rd_code}")
        check({m["leg"] for m in rd_manifests} ==
              {"repos", "transcripts", bp.RUNTIME_DATA_LEG},
              f"rd-e2e: all three legs ran, got {[m['leg'] for m in rd_manifests]}")
        rd_manifest = next(m for m in rd_manifests if m["leg"] == bp.RUNTIME_DATA_LEG)
        check(rd_manifest["status"] == "ok", f"rd-e2e: the leg reports ok, got {rd_manifest}")
        check(rd_manifest["policy"]["max_file_mb"] is None,
              "rd-e2e: the written manifest records that no size cap was applied")
        rd_snapshot = (rd_e2e.runtime_data_dest / "2026-08-20" /
                       bp.RUNTIME_DATA_GROUP / "home-automation")
        check((rd_snapshot / "telemetry.sqlite3").is_file(),
              "rd-e2e: the database landed in the dated snapshot")
        check(not (rd_snapshot / "telemetry.sqlite3-wal").exists()
              and not (rd_snapshot / "telemetry.sqlite3-shm").exists(),
              "rd-e2e: no -wal/-shm sidecar was written beside it")
        snap_rows = None
        if (rd_snapshot / "telemetry.sqlite3").is_file():
            snap_conn = sqlite3.connect(str(rd_snapshot / "telemetry.sqlite3"))
            try:
                snap_rows = snap_conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            except sqlite3.Error as exc:
                snap_rows = f"unreadable: {exc}"
            finally:
                snap_conn.close()
        check(snap_rows == 50,
              f"rd-e2e: the SNAPSHOTTED file on the backup volume opens and holds the "
              f"live rows, got {snap_rows}")
        check(rd_manifest["verification"]["status"] == "pass",
              "rd-e2e: the leg verified its own output like any other")
        check((rd_e2e.runtime_data_dest / bp.RESTORE_NOTE_NAME).is_file(),
              "rd-e2e: the destination carries its own restore note")

        # A database that goes bad is a failure AND, once it is the only one,
        # a zero-file regression — the protection that catches a leg quietly
        # backing up nothing where it used to back something up.
        writer.close()  # release the live handle before overwriting the file
        live_db.write_text("no longer a database at all", encoding="utf-8")
        for sidecar in ("telemetry.sqlite3-wal", "telemetry.sqlite3-shm"):
            (live_db.parent / sidecar).unlink(missing_ok=True)
        for stray in ("notes.txt", "big.blob", "notes-wal"):
            (live_db.parent / stray).unlink(missing_ok=True)
        bad_code, bad_manifests = bp.run(rd_e2e, toml_path=toml_path, notify=False,
                                         today="2026-08-21")
        bad_manifest = next(m for m in bad_manifests if m["leg"] == bp.RUNTIME_DATA_LEG)
        check(bad_manifest["groups"][bp.RUNTIME_DATA_GROUP]["status"] == "failed",
              "rd-e2e: an unsnapshottable database fails its group, never a quiet pass")
        check(any("telemetry.sqlite3" in e
                  for e in bad_manifest["groups"][bp.RUNTIME_DATA_GROUP]["errors"]),
              "rd-e2e: the manifest names the database it could not snapshot")
        check(bad_manifest["regressions"] == [bp.RUNTIME_DATA_GROUP],
              f"rd-e2e: backing up 0 files where there were files still trips the "
              f"zero-file regression check, got {bad_manifest['regressions']}")
        check(bad_code != bp.EXIT_OK, f"rd-e2e: the run exits non-zero, got {bad_code}")
    finally:
        bp.cli._preflight = original_preflight
        writer.close()

    # ---- latest/ incremental reconcile (fleet-config#721) ------------------
    # A full rebuild used to re-hardlink every file in the tree on every run,
    # regardless of how much actually changed. This proves a run over a small
    # delta touches only the delta: one file changed, one removed, one added.
    incr_fleet = tmp / "incr-fleet"
    incr_repo = incr_fleet / "incr-repo"
    incr_repo.mkdir(parents=True)
    _git(incr_repo, "init", "-q")
    _write(incr_repo / ".gitignore", "priv/\n")
    _write(incr_repo / "priv" / "keep.md", "unchanged")
    _write(incr_repo / "priv" / "change.md", "v1")
    _write(incr_repo / "priv" / "remove.md", "gone soon")

    incr_cfg = _cfg(tmp, source_root=incr_fleet, dest=tmp / "incr-dest",
                    transcripts_src=tmp / "incr-transcripts", transcripts_dest=tmp / "incr-tdest")
    original_preflight3 = bp.cli._preflight
    bp.cli._preflight = lambda source, dest: None  # temp dirs share a volume by construction
    try:
        code1, manifests1 = bp.run(incr_cfg, only="incr-repo", toml_path=toml_path,
                                   notify=False, today="2026-08-11")
        check(code1 == bp.EXIT_OK, f"incr: the baseline run is clean, got {code1}")
        repos1 = next(m for m in manifests1 if m["leg"] == "repos")
        check(repos1.get("latest_mode") == "full",
              f"incr: the first run has no previous manifest to diff against, so it "
              f"fully rebuilds latest/, got {repos1.get('latest_mode')}")
        latest_root = incr_cfg.dest / bp.LATEST_DIR / "incr-repo" / "priv"
        keep_ino_before = (latest_root / "keep.md").stat().st_ino

        # A small delta: one file changes content, one is removed, one is added.
        _write(incr_repo / "priv" / "change.md", "v2")
        (incr_repo / "priv" / "remove.md").unlink()
        _write(incr_repo / "priv" / "new.md", "brand new")

        code2, manifests2 = bp.run(incr_cfg, only="incr-repo", toml_path=toml_path,
                                   notify=False, today="2026-08-12")
        check(code2 == bp.EXIT_OK, f"incr: the delta run is clean, got {code2}")
        repos2 = next(m for m in manifests2 if m["leg"] == "repos")
        check(repos2.get("latest_mode") == "incremental",
              f"incr: a run with a valid previous manifest reconciles instead of "
              f"rebuilding, got {repos2.get('latest_mode')}")
        delta = repos2.get("latest_delta") or {}
        check(delta == {"added": 1, "updated": 1, "removed": 1},
              f"incr: the work done is proportional to the change (1 add, 1 update, "
              f"1 remove out of 4 files), not to the tree size, got {delta}")

        check((latest_root / "new.md").is_file()
              and (latest_root / "new.md").read_text(encoding="utf-8") == "brand new",
              "incr: latest/ picks up the added file")
        check((latest_root / "change.md").read_text(encoding="utf-8") == "v2",
              "incr: latest/ reflects the changed file's new content")
        check(not (latest_root / "remove.md").exists(),
              "incr: a file deleted from source is gone from latest/ too")
        keep_ino_after = (latest_root / "keep.md").stat().st_ino
        check(keep_ino_before == keep_ino_after,
              "incr: an unchanged file is never touched — same inode before and after")
        latest_manifest_written = json.loads(
            (incr_cfg.dest / bp.LATEST_DIR / bp.MANIFEST_NAME).read_text(encoding="utf-8"))
        check(latest_manifest_written.get("date") == "2026-08-12",
              "incr: latest/manifest.json — outside every group, so untouched by the "
              "per-group diff — is still refreshed to today's, not left stale")

        # Full rebuild remains reachable as a repair path, and lands on the same
        # membership the incremental reconcile just produced (fleet-config#721 AC).
        bp.rebuild_latest(incr_cfg.dest, incr_cfg.dest / "2026-08-12")
        latest_rels = {
            str(p.relative_to(incr_cfg.dest / bp.LATEST_DIR)).replace("\\", "/")
            for p in (incr_cfg.dest / bp.LATEST_DIR).rglob("*") if p.is_file()
        }
        snapshot_rels = {
            str(p.relative_to(incr_cfg.dest / "2026-08-12")).replace("\\", "/")
            for p in (incr_cfg.dest / "2026-08-12").rglob("*") if p.is_file()
        }
        check(latest_rels == snapshot_rels,
              "incr: a full rebuild is byte-identical in membership to what the "
              "incremental reconcile had already produced")

        # Self-heal: a file the manifest says is unchanged, but that has gone
        # missing from latest/ (a killed reconcile, manual meddling), must not
        # be silently trusted — the next reconcile notices and relinks it.
        (latest_root / "keep.md").unlink()
        code3, manifests3 = bp.run(incr_cfg, only="incr-repo", toml_path=toml_path,
                                   notify=False, today="2026-08-13")
        repos3 = next(m for m in manifests3 if m["leg"] == "repos")
        check(repos3.get("latest_mode") == "incremental",
              f"incr: an untouched-content day still reconciles incrementally, "
              f"got {repos3.get('latest_mode')}")
        check((latest_root / "keep.md").is_file()
              and (latest_root / "keep.md").read_text(encoding="utf-8") == "unchanged",
              "incr: a file missing from latest/ despite an unchanged manifest entry "
              "is relinked, not left absent")

        code4, manifests4 = bp.run(incr_cfg, only="incr-repo", toml_path=toml_path,
                                   notify=False, today="2026-08-14", rebuild_latest_full=True)
        repos4 = next(m for m in manifests4 if m["leg"] == "repos")
        check(repos4.get("latest_mode") == "full",
              f"incr: --rebuild-latest-full forces the repair path even with a valid "
              f"previous manifest, got {repos4.get('latest_mode')}")
    finally:
        bp.cli._preflight = original_preflight3

    # ---- an interrupted run: marker left behind, recovered, and reported ---
    # A real death mid-write, simulated by making write_group raise after the
    # marker is already down — the same failure shape as a killed process or a
    # power loss between the first write and manifest.json (fleet-config#607).
    torn_cfg = _cfg(tmp, dest=tmp / "torn-dest", transcripts_src=tmp / "torn-transcripts",
                    transcripts_dest=tmp / "torn-tdest")
    original_preflight2 = bp.cli._preflight
    original_write_group = bp.cli.write_group
    bp.cli._preflight = lambda source, dest: None  # temp dirs share a volume by construction

    class _SimulatedCrash(Exception):
        pass

    def _crashing_write_group(*args, **kwargs):
        raise _SimulatedCrash("process died mid-write")

    try:
        code1, _ = bp.run(torn_cfg, only="demo-repo", toml_path=toml_path,
                          notify=False, today="2026-08-10")
        check(code1 == bp.EXIT_OK, "torn: the baseline run is clean")
        check(not bp.has_run_marker(torn_cfg.dest / "2026-08-10"),
              "torn: an ordinary successful run removes its own marker")

        bp.cli.write_group = _crashing_write_group
        crashed = False
        try:
            bp.run(torn_cfg, only="demo-repo", toml_path=toml_path,
                  notify=False, today="2026-08-11")
        except _SimulatedCrash:
            crashed = True
        finally:
            bp.cli.write_group = original_write_group
        check(crashed, "torn: the simulated mid-write crash propagates (nothing swallows it)")

        torn_snapshot = torn_cfg.dest / "2026-08-11"
        check(bp.has_run_marker(torn_snapshot),
              "torn: a run interrupted between the first write and the manifest "
              "leaves .run-in-progress behind")
        check(not (torn_snapshot / bp.MANIFEST_NAME).exists(),
              "torn: an interrupted run never reaches manifest.json")

        state, detail = bp.freshness(torn_cfg.dest, torn_cfg, now=NOW)
        check(state == bp.FRESHNESS_UNKNOWN and detail.get("reason") == "last run did not finish",
              f"torn: --check-freshness reports unknown over the torn snapshot, got {state}")

        # The next day's run skips the torn snapshot as a hardlink source and
        # names it as the interrupted predecessor in its own manifest/report.
        code3, manifests3 = bp.run(torn_cfg, only="demo-repo", toml_path=toml_path,
                                   notify=False, today="2026-08-12")
        check(code3 == bp.EXIT_OK, "torn: the following day's run is clean")
        repos3 = next(m for m in manifests3 if m["leg"] == "repos")
        check(repos3["skipped_interrupted"] == ["2026-08-11"],
              f"torn: the next run's report names the interrupted predecessor, "
              f"got {repos3['skipped_interrupted']}")
        check(bp.has_run_marker(torn_snapshot),
              "torn: a stuck marker from a different date is left untouched, not silently cleared")

        report_buf = io.StringIO()
        report_handler = logging.StreamHandler(report_buf)
        prior_level = bp.logger.level
        bp.logger.addHandler(report_handler)
        bp.logger.setLevel(logging.INFO)
        try:
            bp.report(repos3)
        finally:
            bp.logger.removeHandler(report_handler)
            bp.logger.setLevel(prior_level)
        check("2026-08-11" in report_buf.getvalue() and "never finished" in report_buf.getvalue(),
              f"torn: report() prints the interrupted predecessor's name, got "
              f"{report_buf.getvalue()!r}")

        # Re-running the SAME date the torn run died on is self-healing: it
        # overwrites the directory, recovers (and reports) the stale marker, and
        # clears it on success.
        code4, manifests4 = bp.run(torn_cfg, only="demo-repo", toml_path=toml_path,
                                   notify=False, today="2026-08-11")
        check(code4 == bp.EXIT_OK, "torn: a clean re-run over the same date succeeds")
        repos4 = next(m for m in manifests4 if m["leg"] == "repos")
        check(repos4["recovered_marker"] is not None
              and repos4["recovered_marker"]["leg"] == "repos",
              f"torn: the re-run's manifest names the marker it recovered, "
              f"got {repos4['recovered_marker']}")
        check(not bp.has_run_marker(torn_snapshot),
              "torn: a successful re-run over a marked directory clears the marker")

        recovered_buf = io.StringIO()
        recovered_handler = logging.StreamHandler(recovered_buf)
        prior_level2 = bp.logger.level
        bp.logger.addHandler(recovered_handler)
        bp.logger.setLevel(logging.INFO)
        try:
            bp.report(repos4)
        finally:
            bp.logger.removeHandler(recovered_handler)
            bp.logger.setLevel(prior_level2)
        check("recovered" in recovered_buf.getvalue(),
              f"torn: report() names the marker it recovered on this run, got "
              f"{recovered_buf.getvalue()!r}")
    finally:
        bp.cli._preflight = original_preflight2
        bp.cli.write_group = original_write_group

    # ---- the real projects.toml: [backup] must not steal [global]'s keys ---
    # A TOML table header claims every bare key that follows it. Adding [backup]
    # above `architecture_ignore` re-parented that list out of [global] and
    # dropped four repos off the architecture map; this asserts the ordering
    # rather than trusting the comment that now says so (fleet-config#590).
    real_toml = ROOT / "hooks" / "projects.toml"
    real_text = real_toml.read_text(encoding="utf-8")
    real_data = json.loads(json.dumps(bp._read_toml(real_toml), default=str))
    check("architecture_ignore" in real_data.get("global", {}),
          "projects.toml: architecture_ignore still belongs to [global]")
    check("never_kill_ports" in real_data.get("global", {}),
          "projects.toml: [global] still owns never_kill_ports")
    check("architecture_ignore" not in real_data.get("backup", {}),
          "projects.toml: [backup] has not swallowed a [global] key")
    check(real_text.rindex("[backup]") > real_text.rindex("[global]"),
          "projects.toml: [backup] stays below [global]")
    check(real_data.get("backup", {}).get("source_root"),
          "projects.toml: the real [backup] table is populated")
    check(bp.load_backup_config(real_toml).bulk_dir_bytes > 0,
          "projects.toml: the real config loads")

    # ---- the unattended log reaches stdout, line-buffered ------------------
    # fleet-config#605: logging.basicConfig defaults to stderr, so the
    # app-launcher Jobs pane captured 0 bytes for a 35-minute run. Driven as a
    # real subprocess with the streams split, because that is the only way the
    # defect shows — in-process logging assertions cannot see it.
    proc = subprocess.run(
        [sys.executable, str(ROOT / "hooks" / "backup_private.py"), "--check-freshness"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, creationflags=bp._lib.NO_WINDOW,
    )
    check("BACKUP_FRESHNESS=" in proc.stdout,
          f"log: the report goes to stdout, got stdout={proc.stdout[:80]!r}")
    src = (ROOT / "hooks" / "backup" / "cli.py").read_text(encoding="utf-8")
    check("stream=sys.stdout" in src, "log: logging is pinned to stdout, not the stderr default")
    check("line_buffering=True" in src,
          "log: stdout is line-buffered so a piped run shows progress before it exits")

    # ---- exit-code severity ordering --------------------------------------
    check(bp._worst([bp.EXIT_REPO_FAILURE, bp.EXIT_VERIFY_FAILED]) == bp.EXIT_VERIFY_FAILED,
          "exit: a verification failure outranks a repo failure")
    check(bp._worst([bp.EXIT_VERIFY_FAILED, bp.EXIT_DEST_UNUSABLE]) == bp.EXIT_DEST_UNUSABLE,
          "exit: an unusable destination outranks everything")
    check(bp._worst([]) == bp.EXIT_OK, "exit: no problems means exit 0")

finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_fleet_private_backup")
