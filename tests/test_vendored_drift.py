"""Unit tests for the pure logic in skills/_lib/vendored_drift.py (fleet-config#338).

Exercises manifest parsing, the hash-diff/classify core (no git), local hashing
over synthetic temp dirs (both directory-shaped and single-file-shaped
components), and an end-to-end `scan_fleet` against a real throwaway scaffold
git repo (two commits of a component, so `local_drift` and `behind_head` can
each be proven true and false) with a fake manifest — no real fleet repo is
touched.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_vendored_drift.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import vendored_drift as vd  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import run_git  # noqa: E402

_h = CheckHarness()
check = _h.check


# ---- parse_vendored_manifest ----

check(vd.parse_vendored_manifest("") == {}, "no [vendored] table at all -> {}")
check(vd.parse_vendored_manifest('layer = "working-web"\nicon = "x"\ndescription = "d"\n') == {},
      "ordinary .fleet.toml with no [vendored] table -> {}")
check(vd.parse_vendored_manifest("[vendored]\n") == {}, "empty [vendored] table -> {}")

one = vd.parse_vendored_manifest(
    '[vendored]\nnav = { src = "app/webapp/static/_vendored/nav", sha = "abc123", dest = "app/webapp/static/_vendored/nav" }\n'
)
check(one == {"nav": {"src": "app/webapp/static/_vendored/nav", "sha": "abc123",
                       "dest": "app/webapp/static/_vendored/nav"}},
      "single vendored entry parses to {component: {src,sha,dest}}")

multi = vd.parse_vendored_manifest(
    '[vendored]\n'
    'nav = { src = "a", sha = "s1", dest = "a" }\n'
    'tray_lifecycle = { src = "app/tray/tray_lifecycle.ps1", sha = "s2", dest = "app/tray/tray_lifecycle.ps1" }\n'
)
check(set(multi) == {"nav", "tray_lifecycle"}, "multiple entries (dir-shaped + single-file-shaped) both parse")

malformed = vd.parse_vendored_manifest('[vendored]\nnav = "not-a-table"\n')
check(malformed == {}, "a non-table entry (malformed hand-edit) is dropped, not raised")


# ---- diff_hashes / classify_adopter ----

check(vd.diff_hashes({}, {}) == [], "diff_hashes: both empty -> no diff")
check(vd.diff_hashes({"a": "h1"}, {"a": "h1"}) == [], "diff_hashes: identical single entry -> no diff")
check(vd.diff_hashes({"a": "h1"}, {"a": "h2"}) == ["a"], "diff_hashes: differing hash -> flagged")
check(vd.diff_hashes({"a": "h1"}, {}) == ["a"], "diff_hashes: present only in a -> flagged")
check(vd.diff_hashes({}, {"a": "h1"}) == ["a"], "diff_hashes: present only in b -> flagged")
check(vd.diff_hashes({"a": "h1", "b": "h2"}, {"a": "h1", "b": "h3"}) == ["b"],
      "diff_hashes: only the differing key surfaces, matching key doesn't")

clean = vd.classify_adopter({"a": "h1"}, {"a": "h1"}, {"a": "h1"})
check(clean["local_drift"] is False and clean["behind_head"] is False,
      "classify: local==pinned==head -> clean on both axes")

hand_edit = vd.classify_adopter({"a": "h2"}, {"a": "h1"}, {"a": "h1"})
check(hand_edit["local_drift"] is True and hand_edit["behind_head"] is False,
      "classify: local diverges from pinned, pinned==head -> local_drift only")

stale_pin = vd.classify_adopter({"a": "h1"}, {"a": "h1"}, {"a": "h2"})
check(stale_pin["local_drift"] is False and stale_pin["behind_head"] is True,
      "classify: local matches its pin, but pin is behind HEAD -> behind_head only")

both = vd.classify_adopter({"a": "h3"}, {"a": "h1"}, {"a": "h2"})
check(both["local_drift"] is True and both["behind_head"] is True,
      "classify: both signals independently true at once")
check(both["local_diff_files"] == ["a"] and both["behind_diff_files"] == ["a"],
      "classify: diff file lists point at the offending path")


# ---- hash_dir_local: directory-shaped and single-file-shaped, + missing path ----

tmp = Path(tempfile.mkdtemp(prefix="vendored_drift_"))
try:
    comp_dir = tmp / "nav"
    comp_dir.mkdir()
    (comp_dir / "nav.css").write_text("body{}\n", encoding="utf-8", newline="")
    (comp_dir / "nav.js").write_text("console.log(1)\n", encoding="utf-8", newline="")
    hashes = vd.hash_dir_local(comp_dir)
    check(set(hashes) == {"nav.css", "nav.js"}, "hash_dir_local: directory -> one entry per file, relpath-keyed")
    check(hashes["nav.css"] == vd.sha256_bytes(b"body{}\n"), "hash_dir_local: hash matches sha256 of the bytes")

    single_file = tmp / "tray_lifecycle.ps1"
    single_file.write_text("# lifecycle\n", encoding="utf-8", newline="")
    fh = vd.hash_dir_local(single_file)
    check(fh == {"tray_lifecycle.ps1": vd.sha256_bytes(b"# lifecycle\n")},
          "hash_dir_local: single file -> {basename: hash}")

    check(vd.hash_dir_local(tmp / "does-not-exist") == {},
          "hash_dir_local: missing path -> {} (never vendored / deleted since)")
finally:
    shutil.rmtree(tmp, ignore_errors=True)


# ---- scan_fleet: end-to-end against a real throwaway scaffold + adopter repos ----

def _git(cwd: Path, *args: str) -> str:
    return run_git(cwd, *args, check=check)


root = Path(tempfile.mkdtemp(prefix="vendored_drift_fleet_"))
try:
    scaffold = root / "project-scaffolding"
    scaffold.mkdir()
    _git(scaffold, "init", "-q")
    _git(scaffold, "checkout", "-q", "-b", "main")
    _git(scaffold, "config", "user.email", "35553560+ferraroroberto@users.noreply.github.com")
    _git(scaffold, "config", "user.name", "Test")

    comp = scaffold / "app" / "webapp" / "static" / "_vendored" / "nav"
    comp.mkdir(parents=True)
    (comp / "nav.css").write_text("v1\n", encoding="utf-8", newline="")
    _git(scaffold, "add", "-A")
    _git(scaffold, "commit", "-q", "-m", "v1")
    v1_sha = _git(scaffold, "rev-parse", "HEAD")

    # A second scaffold commit changes the component -> v1 becomes "behind HEAD".
    (comp / "nav.css").write_text("v2\n", encoding="utf-8", newline="")
    _git(scaffold, "add", "-A")
    _git(scaffold, "commit", "-q", "-m", "v2")
    v2_sha = _git(scaffold, "rev-parse", "HEAD")
    check(v1_sha != v2_sha, "scaffold has two distinct commits")

    # adopter_clean: vendored at v1, local copy still byte-identical to v1 ->
    # local_drift=False, behind_head=True (v1 != v2).
    adopter_clean = root / "adopter-clean"
    dest_clean = adopter_clean / "app" / "webapp" / "static" / "_vendored" / "nav"
    dest_clean.mkdir(parents=True)
    (dest_clean / "nav.css").write_text("v1\n", encoding="utf-8", newline="")
    (adopter_clean / ".fleet.toml").write_text(
        f'layer = "working-web"\nicon = "x"\ndescription = "d"\n\n'
        f'[vendored]\nnav = {{ src = "app/webapp/static/_vendored/nav", sha = "{v1_sha}", '
        f'dest = "app/webapp/static/_vendored/nav" }}\n',
        encoding="utf-8",
    )

    # adopter_edited: vendored at v2 (matches current HEAD) but hand-edited locally
    # -> local_drift=True, behind_head=False.
    adopter_edited = root / "adopter-edited"
    dest_edited = adopter_edited / "app" / "webapp" / "static" / "_vendored" / "nav"
    dest_edited.mkdir(parents=True)
    (dest_edited / "nav.css").write_text("v2-hand-edited\n", encoding="utf-8", newline="")
    (adopter_edited / ".fleet.toml").write_text(
        f'layer = "working-web"\nicon = "x"\ndescription = "d"\n\n'
        f'[vendored]\nnav = {{ src = "app/webapp/static/_vendored/nav", sha = "{v2_sha}", '
        f'dest = "app/webapp/static/_vendored/nav" }}\n',
        encoding="utf-8",
    )

    # adopter_bare: a .fleet.toml with no [vendored] table -> "no manifest yet"
    # (the expected state of every real fleet repo before this skill's first run).
    adopter_bare = root / "adopter-bare"
    adopter_bare.mkdir()
    (adopter_bare / ".fleet.toml").write_text('layer = "working-web"\nicon = "x"\ndescription = "d"\n', encoding="utf-8", newline="")

    repos = {
        "project-scaffolding": scaffold,
        "adopter-clean": adopter_clean,
        "adopter-edited": adopter_edited,
        "adopter-bare": adopter_bare,
    }
    report = vd.scan_fleet(scaffold, repos=repos)

    check(report["repos_scanned"] == 4, "scan_fleet: repos_scanned counts every injected repo")
    check("adopter-bare" in report["no_manifest"], "scan_fleet: adopter with no [vendored] table -> no_manifest")
    check("project-scaffolding" not in report["no_manifest"] and
          not any(a["repo"] == "project-scaffolding" for a in report["adopters"]),
          "scan_fleet: the scaffold itself is never its own adopter")

    by_repo = {a["repo"]: a for a in report["adopters"]}
    check(by_repo["adopter-clean"]["local_drift"] is False and by_repo["adopter-clean"]["behind_head"] is True,
          "scan_fleet: clean local copy pinned at v1, scaffold now at v2 -> behind_head only")
    check(by_repo["adopter-edited"]["local_drift"] is True and by_repo["adopter-edited"]["behind_head"] is False,
          "scan_fleet: hand-edited local copy pinned at current v2 -> local_drift only")
    check(by_repo["adopter-clean"]["head_sha"] == v2_sha, "scan_fleet: head_sha resolves to the scaffold's real tip")

    # --component filter
    filtered = vd.scan_fleet(scaffold, component_filter="does-not-exist", repos=repos)
    check(filtered["adopters"] == [], "scan_fleet: --component filter with no match -> zero adopters reported")
finally:
    shutil.rmtree(root, ignore_errors=True)


_h.report_and_exit("test_vendored_drift")
