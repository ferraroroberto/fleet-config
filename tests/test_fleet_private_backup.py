"""Unit tests for the daily fleet-private backup engine (fleet-config#590).

Everything here runs against real temp trees, a real `git init`ed repo, a real
Windows junction, and real hardlinks — the three things this engine gets wrong
if it only ever meets mocks are exactly junction traversal, `st_nlink`, and what
`git ls-files --others --ignored` actually emits.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hooks"))
import backup_private as bp  # noqa: E402

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

    # ---- overlapping git entries are deduped ------------------------------
    # `git ls-files --directory` emits a partially-ignored directory's files AND a
    # collapsed entry for a wholly-ignored subdirectory under it, so the same file
    # is reached twice. Day one hid it (copy2 overwrites); day two failed 516 files
    # across 11 repos on os.link (fleet-config#590).
    original_entries = bp.git_ignored_entries
    bp.git_ignored_entries = lambda repo_dir: [".env", "identity/", "identity/me.md",
                                               "identity/", ".env"]
    try:
        overlapped = bp.select_repo(repo, cfg, bp.RepoOverrides())
    finally:
        bp.git_ignored_entries = original_entries
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
    (fleet / "skipme").mkdir(exist_ok=True)
    check(not bp.load_repo_overrides(fleet / "skipme", toml_path).enabled,
          "overrides: backup = false opts a repo out")
    check(bp.load_repo_overrides(fleet / "unknown-repo", toml_path).enabled,
          "overrides: a repo with no table defaults to being backed up")

    # ---- end-to-end run ----------------------------------------------------
    e2e_cfg = _cfg(tmp, dest=tmp / "e2e-dest", transcripts_src=tmp / "e2e-transcripts",
                   transcripts_dest=tmp / "e2e-tdest")
    _write(e2e_cfg.transcripts_src / "proj" / "session.jsonl", '{"a":1}')

    original_preflight = bp._preflight
    bp._preflight = lambda source, dest: None  # temp dirs share a volume by construction
    try:
        code, manifests = bp.run(e2e_cfg, only=None, toml_path=toml_path, notify=False,
                                 today="2026-08-11")
        check(code == bp.EXIT_OK, f"e2e: a clean run exits 0, got {code}")
        check({m["leg"] for m in manifests} == {"repos", "transcripts"},
              "e2e: both legs ran")
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
    finally:
        bp._preflight = original_preflight

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

    # ---- exit-code severity ordering --------------------------------------
    check(bp._worst([bp.EXIT_REPO_FAILURE, bp.EXIT_VERIFY_FAILED]) == bp.EXIT_VERIFY_FAILED,
          "exit: a verification failure outranks a repo failure")
    check(bp._worst([bp.EXIT_VERIFY_FAILED, bp.EXIT_DEST_UNUSABLE]) == bp.EXIT_DEST_UNUSABLE,
          "exit: an unusable destination outranks everything")
    check(bp._worst([]) == bp.EXIT_OK, "exit: no problems means exit 0")

finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_fleet_private_backup")
