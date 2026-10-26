import sys
from pathlib import Path

from _suite import _git, _make_junction, _write, cfg, check, skip, tmp
import backup as bp


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

# ---- the summary stays ASCII and never trails off ----------------------
summary = bp._notify_summary([{"leg": "repos", "totals": {
    "files": 6047, "bytes": 192 * 1024 * 1024, "bulk_excluded_dirs": 13}}], bp.EXIT_OK)
check(summary.isascii(), f"notify: the summary is pure ASCII (fleet-config#507): {summary}")
check("6047 files" in summary and "13 bulk dirs" in summary,
      "notify: the summary carries the counts")
check("no leg completed" in bp._notify_summary([], bp.EXIT_DEST_UNUSABLE),
      "notify: a run with no manifest says so instead of trailing off")

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
    skip("reparse: junction creation unavailable, so junction-detection was not verified")
check(not bp.is_reparse_point(real_dir), "reparse: an ordinary directory is not one")
check(bp.is_reparse_point(tmp / "does-not-exist"),
      "reparse: an unstattable path fails closed")

# ---- walk_files never crosses a junction ------------------------------
walk_root = tmp / "walk"
_write(walk_root / "keep.md")
_write(walk_root / "nested" / "deep.md")
_write(walk_root / "trash.log")
_write(walk_root / ".venv" / "lib.py")
junction_created = _make_junction(walk_root / "linked", real_dir)
errors: list = []
walked = {rel for _, rel, _ in bp.walk_files(walk_root, "", cfg, errors)}
check(walked == {"keep.md", "nested/deep.md"},
      f"walk: deny-list excludes .venv/** and *.log, got {sorted(walked)}")
check(not errors, "walk: a clean tree produces no errors")
if junction_created:
    check(not any(rel == "linked" or rel.startswith("linked/") for rel in walked),
          f"walk: a real junction is never crossed, got {sorted(walked)}")
else:
    skip("walk: junction creation unavailable, so junction-skipping was not verified")

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

# A native JSONL rollout can be appended between the source hash and its
# copy. The manifest must describe the bytes that actually landed, or the
# archive correctly rejects an otherwise usable snapshot.
live_src = _write(tmp / "live-copy" / "session.jsonl", "first\n")
live_day = tmp / "live-copy-dest"
original_sha256_file = bp.snapshot.sha256_file
mutated = [False]

def _mutating_hash(path: Path, chunk: int = 1024 * 1024) -> str:
    digest = original_sha256_file(path, chunk)
    if path == live_src and not mutated[0]:
        with live_src.open("a", encoding="utf-8") as handle:
            handle.write("second\n")
        mutated[0] = True
    return digest

bp.snapshot.sha256_file = _mutating_hash
try:
    live_entries, _, _ = bp.write_group(
        [(live_src, "session.jsonl", live_src.stat().st_size)],
        "g", live_day, None, {}, [],
    )
finally:
    bp.snapshot.sha256_file = original_sha256_file
check(live_entries[0]["sha256"] == original_sha256_file(
    live_day / "g" / "session.jsonl"
), "live copy: the manifest hashes the stable snapshot bytes, not an earlier source state")
check(live_entries[0]["size"] == (live_day / "g" / "session.jsonl").stat().st_size,
      "live copy: the manifest size describes the stable snapshot bytes")

# ---- iter_repos skips linked worktrees --------------------------------
_write(fleet / "not-a-repo" / "file.txt")
worktree = fleet / "demo-repo-wt-1"
worktree.mkdir()
_write(worktree / ".git", "gitdir: ../demo-repo/.git/worktrees/wt1")
found = {p.name for p in bp.iter_repos(fleet)}
check(found == {"demo-repo"},
      f"iter_repos: only real repos, worktrees and non-repos skipped, got {found}")
