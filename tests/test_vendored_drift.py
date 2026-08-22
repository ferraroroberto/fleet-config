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


# ---- parse_scaffold_catalog / scaffold_catalog (project-scaffolding#230) ----

check(vd.parse_scaffold_catalog("") == {}, "no [components] table at all -> {}")
check(vd.parse_scaffold_catalog('layer = "governance"\n[vendored]\nnav = { src = "a", sha = "s", dest = "a" }\n') == {},
      "an adopter's [vendored] table is NOT a scaffold catalog -> {}")
check(vd.parse_scaffold_catalog('[components]\nnav = { src = "app/webapp/static/_vendored/nav" }\nno_window = { src = "src/no_window.py" }\n')
      == {"nav": "app/webapp/static/_vendored/nav", "no_window": "src/no_window.py"},
      "[components] parses to {key: src}")
check(vd.parse_scaffold_catalog('[components]\nok = { src = "a" }\nbad = "a"\nempty = { src = "" }\n') == {"ok": "a"},
      "malformed catalog entries are dropped, the readable ones survive")

_cat_tmp = Path(tempfile.mkdtemp(prefix="vendored_catalog_"))
try:
    _cat, _err = vd.scaffold_catalog(_cat_tmp)
    check(_cat == {} and _err is not None and ".fleet.toml" in _err,
          "scaffold_catalog: no .fleet.toml -> ({}, reason) -- unknown, never a silent empty")

    (_cat_tmp / ".fleet.toml").write_text('layer = "governance"\n', encoding="utf-8", newline="")
    _cat, _err = vd.scaffold_catalog(_cat_tmp)
    check(_cat == {} and _err is not None and "[components]" in _err,
          "scaffold_catalog: .fleet.toml without [components] -> ({}, reason naming the table)")

    (_cat_tmp / ".fleet.toml").write_text('[components]\nnav = { src = "nav" }\n', encoding="utf-8", newline="")
    _cat, _err = vd.scaffold_catalog(_cat_tmp)
    check(_cat == {"nav": "nav"} and _err is None,
          "scaffold_catalog: a real catalog -> (catalog, None)")
finally:
    shutil.rmtree(_cat_tmp, ignore_errors=True)


# ---- undeclared carriers + coverage, end-to-end over real git repos ----------
#
# The defect this proves (project-scaffolding#230): `/propagate-vendored` builds
# its adopter list from `[vendored]` entries, so a repo carrying a component it
# never declared is invisible -- the wave re-vendors the declarers, reports
# success, and leaves the rest stale with nobody told. Every assertion below
# fails against the pre-#230 module, which had no notion of a carrier at all.

_IDENT_EMAIL = "35553560+ferraroroberto@users.noreply.github.com"


def _init_repo(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    _git(d, "init", "-q")
    _git(d, "checkout", "-q", "-b", "main")
    _git(d, "config", "user.email", _IDENT_EMAIL)
    _git(d, "config", "user.name", "Test")


root = Path(tempfile.mkdtemp(prefix="vendored_carrier_"))
try:
    scaffold = root / "project-scaffolding"
    _init_repo(scaffold)

    nav = scaffold / "app" / "webapp" / "static" / "_vendored" / "nav"
    nav.mkdir(parents=True)
    (nav / "nav.css").write_text("nav v1\n", encoding="utf-8", newline="")
    (scaffold / "src").mkdir()
    (scaffold / "src" / "no_window.py").write_text("NO_WINDOW = 0\n", encoding="utf-8", newline="")
    (scaffold / ".fleet.toml").write_text(
        'layer = "governance"\nicon = "x"\ndescription = "d"\n\n'
        '[components]\n'
        'nav = { src = "app/webapp/static/_vendored/nav" }\n'
        'no_window = { src = "src/no_window.py" }\n',
        encoding="utf-8", newline="",
    )
    _git(scaffold, "add", "-A")
    _git(scaffold, "commit", "-q", "-m", "scaffold v1")
    scaffold_sha = _git(scaffold, "rev-parse", "HEAD")

    _BARE = 'layer = "working-web"\nicon = "x"\ndescription = "d"\n'

    def _make_repo(name: str, fleet_toml: str) -> Path:
        d = root / name
        _init_repo(d)
        (d / ".fleet.toml").write_text(fleet_toml, encoding="utf-8", newline="")
        return d

    # carrier_exact: has nav at the scaffold's own path, declares nothing.
    carrier_exact = _make_repo("carrier-exact", _BARE)
    dest = carrier_exact / "app" / "webapp" / "static" / "_vendored" / "nav"
    dest.mkdir(parents=True)
    (dest / "nav.css").write_text("nav v1\n", encoding="utf-8", newline="")
    _git(carrier_exact, "add", "-A")
    _git(carrier_exact, "commit", "-q", "-m", "carry nav, declare nothing")

    # carrier_forked: has nav, undeclared, and hand-changed. Reported, never
    # rewritten -- "never declared it" and "deliberately forked it" look
    # identical from the bytes, and only a human knows which.
    carrier_forked = _make_repo("carrier-forked", _BARE)
    dest = carrier_forked / "app" / "webapp" / "static" / "_vendored" / "nav"
    dest.mkdir(parents=True)
    (dest / "nav.css").write_text("nav v1 -- locally changed\n", encoding="utf-8", newline="")
    _git(carrier_forked, "add", "-A")
    _git(carrier_forked, "commit", "-q", "-m", "forked nav")

    # declared: carries nav AND declares it -> an adopter, not a carrier.
    declared = _make_repo(
        "declared-adopter",
        _BARE + '\n[vendored]\nnav = { src = "app/webapp/static/_vendored/nav", '
                f'sha = "{scaffold_sha}", dest = "app/webapp/static/_vendored/nav" }}\n'.replace("}}", "}"),
    )
    dest = declared / "app" / "webapp" / "static" / "_vendored" / "nav"
    dest.mkdir(parents=True)
    (dest / "nav.css").write_text("nav v1\n", encoding="utf-8", newline="")
    _git(declared, "add", "-A")
    _git(declared, "commit", "-q", "-m", "declared adopter")

    # innocent: carries no catalogued component at all -> silent, no finding.
    innocent = _make_repo("innocent", _BARE)
    (innocent / "README.md").write_text("nothing vendored here\n", encoding="utf-8", newline="")
    _git(innocent, "add", "-A")
    _git(innocent, "commit", "-q", "-m", "innocent")

    # broken: unparseable .fleet.toml -> its carrier status cannot be
    # established, so it must land in `carriers_unknown`, never in "clean".
    broken = _make_repo("broken-toml", 'layer = "working-web"\nthis is not = = toml\n')

    repos = {
        "project-scaffolding": scaffold,
        "carrier-exact": carrier_exact,
        "carrier-forked": carrier_forked,
        "declared-adopter": declared,
        "innocent": innocent,
        "broken-toml": broken,
    }
    report = vd.scan_fleet(scaffold, repos=repos)
    carriers = {(c["repo"], c["component"]): c for c in report["undeclared_carriers"]}

    check(("carrier-exact", "nav") in carriers,
          "carrier: a repo holding a catalogued component with no [vendored] entry is reported BY NAME")
    check(carriers.get(("carrier-exact", "nav"), {}).get("matches_head") is True,
          "carrier: a byte-identical undeclared copy reports matches_head=True (safe to adopt)")
    check(carriers.get(("carrier-forked", "nav"), {}).get("matches_head") is False
          and carriers.get(("carrier-forked", "nav"), {}).get("diff_files") == ["nav.css"],
          "carrier: a diverged undeclared copy is reported too, flagged matches_head=False + diff files")
    check(("declared-adopter", "nav") not in carriers,
          "carrier: a repo that DECLARES the component is an adopter, never double-reported as a carrier")
    check(not any(r == "innocent" for r, _ in carriers),
          "carrier: a repo carrying nothing catalogued produces no finding")
    check(not any(r == "broken-toml" for r, _ in carriers),
          "carrier: a repo whose manifest could not be read is not claimed to be a carrier")
    check(report["coverage"]["carriers_unknown"] == ["broken-toml"],
          "coverage: an unreadable manifest lands in carriers_unknown -- its own state, never folded into clean")
    check(report["coverage"]["declared_adopters"] == 1
          and report["coverage"]["undeclared_carriers"] == 2,
          "coverage: states BOTH counts, so a wave over 1 declared adopter cannot read as complete")
    check(report["coverage"]["catalog_known"] is True and report["catalog"]["count"] == 2,
          "coverage: catalog_known=True with the component count when the scaffold catalog parsed")

    only_nw = vd.scan_fleet(scaffold, component_filter="no_window", repos=repos)
    check(all(c["component"] == "no_window" for c in only_nw["undeclared_carriers"]),
          "carrier: --component filters the carrier sweep as well as the adopter sweep")

    # A scaffold with no [components] table: the answer is UNKNOWN, not "none".
    (scaffold / ".fleet.toml").write_text('layer = "governance"\nicon = "x"\ndescription = "d"\n',
                                          encoding="utf-8", newline="")
    blind = vd.scan_fleet(scaffold, repos=repos)
    check(blind["coverage"]["catalog_known"] is False and blind["undeclared_carriers"] == [],
          "no scaffold catalog -> catalog_known=False with zero carriers, i.e. 'could not look'")
    check(any("[components]" in str(e.get("error", "")) for e in blind["errors"]),
          "no scaffold catalog -> the reason is reported as an error, not silently swallowed")
finally:
    shutil.rmtree(root, ignore_errors=True)


# ---- committed-vs-working-tree byte population (the CRLF false-drift bug) ----
#
# These checkouts store LF and check out CRLF, so hashing an adopter's copy off
# the FILESYSTEM while hashing the scaffold's off its git blobs made every text
# component compare as drifted -- on the live fleet that was 32 of 33 declared
# entries reporting local_drift, and no undeclared carrier could ever report
# "identical". Both sides must read the same population: committed blobs.

root = Path(tempfile.mkdtemp(prefix="vendored_crlf_"))
try:
    scaffold = root / "project-scaffolding"
    _init_repo(scaffold)
    (scaffold / "src").mkdir()
    (scaffold / "src" / "no_window.py").write_text("a\nb\n", encoding="utf-8", newline="")
    (scaffold / ".fleet.toml").write_text(
        'layer = "governance"\nicon = "x"\ndescription = "d"\n\n'
        '[components]\nno_window = { src = "src/no_window.py" }\n',
        encoding="utf-8", newline="",
    )
    _git(scaffold, "add", "-A")
    _git(scaffold, "commit", "-q", "-m", "v1")
    sha = _git(scaffold, "rev-parse", "HEAD")

    adopter = root / "crlf-adopter"
    _init_repo(adopter)
    _git(adopter, "config", "core.autocrlf", "false")
    (adopter / "src").mkdir()
    # Committed with LF -- byte-identical to the scaffold's blob.
    (adopter / "src" / "no_window.py").write_text("a\nb\n", encoding="utf-8", newline="")
    (adopter / ".fleet.toml").write_text(
        'layer = "working-web"\nicon = "x"\ndescription = "d"\n\n'
        '[vendored]\nno_window = { src = "src/no_window.py", '
        f'sha = "{sha}", dest = "src/no_window.py" }}\n'.replace("}}", "}"),
        encoding="utf-8", newline="",
    )
    _git(adopter, "add", "-A")
    _git(adopter, "commit", "-q", "-m", "vendored")
    # ...then the working tree is CRLF, exactly as a Windows checkout leaves it.
    (adopter / "src" / "no_window.py").write_bytes(b"a\r\nb\r\n")

    check(vd.hash_component_local(adopter, "src/no_window.py")
          == vd.hash_dir_at_ref(scaffold, "main", "src/no_window.py"),
          "CRLF: an adopter's committed copy hashes equal to the scaffold's blob "
          "even though its working-tree bytes differ in every line")

    report = vd.scan_fleet(scaffold, repos={"project-scaffolding": scaffold, "crlf-adopter": adopter})
    entry = next(a for a in report["adopters"] if a["repo"] == "crlf-adopter")
    check(entry["local_drift"] is False,
          "CRLF: a byte-perfect vendored copy does NOT report local_drift from a CRLF working tree")

    # The fallback still sees an uncommitted-only component, so the change can
    # only over-report, never hide a carrier.
    loose = root / "loose"
    (loose / "src").mkdir(parents=True)
    (loose / "src" / "no_window.py").write_text("a\nb\n", encoding="utf-8", newline="")
    check(vd.hash_component_local(loose, "src/no_window.py") != {},
          "fallback: a non-git directory still hashes off the filesystem (over-report, never hide)")
finally:
    shutil.rmtree(root, ignore_errors=True)


_h.report_and_exit("test_vendored_drift")
