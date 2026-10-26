"""Tests for the near-limit reading of the weekly /audit-fleet digest (skills/_lib/structure_census/near_limit.py).

Three layers:
  1. `measure.near_band` / `in_band` and `near_limit.count_near`, pure -- the band edges on both sides of
     a limit, a repo's own limits, and the vendored, `exclude`d and generated files left out.
  2. `near_limit.read_repo` and `fleet_audit_scan.structure_report` on throwaway git repos -- the numbers come
     from `origin/main` while the working tree differs; a repo that does not enable the structure ratchet is
     `not adopted`, and an unparseable `.fleet.toml`, an unparseable Python file or a missing ref is `unknown`,
     never a zero.
  3. `section.build_near`, pure -- deltas against the stored prior state, `(new)` without a prior reading, no
     deltas when the prior state could not be read, and the state lines round-tripping through `parse_near_state`.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/structure_census/test_near_limit.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
sys.path.insert(0, str(REPO / "tests" / "_lib"))
import fleet_audit_scan as fas  # noqa: E402
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import make_upstream_and_clone, run_git  # noqa: E402
from structure_census import measure, near_limit, repo_read  # noqa: E402
from structure_debt import section  # noqa: E402
from throwaway_dir import throwaway_dir  # noqa: E402

import structure_ratchet  # noqa: E402  (scripts/ is on the path once structure_census is imported)

_h = CheckHarness()
check = _h.check


# ---- 1. the band and the count ----

check(measure.near_band(1000) == (900, 1000) and measure.near_band(100) == (90, 100) and measure.near_band(50) == (45, 50),
      "near_band: the last tenth below the limit, the limit included")
check(repo_read.NEAR_FILE == (900, 1000) and repo_read.NEAR_FN == (90, 100),
      "census: its near-limit bands are the same definition")
for _size, _expected in ((899, False), (900, True), (1000, True), (1001, False)):
    check(measure.in_band(_size, (900, 1000)) is _expected, f"in_band: {_size} lines in 900-1000 is {_expected}")


def _py_lines(count: int) -> str:
    return "".join(f"v{i} = {i}\n" for i in range(count))


def _func(length: int, name: str = "f") -> str:
    """A function spanning exactly `length` lines (def line + body)."""
    return f"def {name}():\n" + "    pass\n" * (length - 1)


def _blobs(files: dict[str, str]) -> dict[str, bytes]:
    return {path: text.encode() for path, text in files.items()}


_CFG = structure_ratchet.parse_config("[structure_ratchet]\nenabled = true\nexclude = ['docs/**']\n")
assert _CFG is not None
_edges = _blobs({
    "src/f899.py": _py_lines(899), "src/f900.py": _py_lines(900), "src/f1000.py": _py_lines(1000), "src/f1001.py": _py_lines(1001),
    "src/fn89.py": _func(89, "a"), "src/fn90.py": _func(90, "b"), "src/fn100.py": _func(100, "c"), "src/fn101.py": _func(101, "d"),
})
check(near_limit.count_near(_edges, _CFG, ()) == {"files": 2, "fns": 2},
      f"count_near: 900 and 1000 lines, 90 and 100 line functions are in; 899/1001 and 89/101 are out, got {near_limit.count_near(_edges, _CFG, ())!r}")

_left_out = _blobs({
    "src/own.py": _py_lines(950),
    "vendor_tool/copy.py": _py_lines(950) + _func(95),
    "docs/excluded.py": _py_lines(950) + _func(95),
    "src/generated.py": "# DO NOT EDIT\n" + _py_lines(950) + _func(95),
    "src/_vendored/nav.py": _py_lines(950),
    "README.md": "x\n" * 950,
})
check(near_limit.count_near(_left_out, _CFG, ("vendor_tool",)) == {"files": 1, "fns": 0},
      "count_near: a vendored dest, an `exclude` glob, a generated file, a _vendored folder and non-code are not counted")

_tight = structure_ratchet.parse_config("[structure_ratchet]\nenabled = true\nmax_file_lines = 500\nmax_function_lines = 40\n")
assert _tight is not None
_own = _blobs({"a.py": _py_lines(460), "b.py": _py_lines(899) + _func(37, "g")})
check(near_limit.count_near(_own, _tight, ()) == {"files": 1, "fns": 1},
      "count_near: the band follows the repo's own limits (450-500 lines, 36-40 line functions)")
check(near_limit.count_near(_blobs({"ok.py": "x = 1\n", "bad.py": "def (:\n"}), _CFG, ()) == {"unparsed": ["bad.py"]},
      "count_near: a Python file that does not parse withholds the reading")


# ---- 2. one repo at origin's default branch ----

_TOML_ON = "[structure_ratchet]\nenabled = true\nexclude = ['docs/**']\n\n[vendored]\nx = { src = 'a', sha = 'b', dest = 'vendor_tool' }\n"


def _commit(repo: Path, files: dict[str, str], message: str) -> None:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    run_git(repo, "add", "-A", check=check)
    run_git(repo, "commit", "-q", "-m", message, check=check)


def _make_repo(root: Path, name: str, origin_files: dict[str, str]) -> Path:
    """`root/name` cloned from `root/_up/name`; the files exist only on origin's main, the checkout holds others."""
    up_root = root / "_up" / name
    up_root.mkdir(parents=True)
    up, work = make_upstream_and_clone(up_root, check)
    _commit(up, origin_files, "adopt")
    run_git(work, "fetch", "-q", "origin", check=check)
    run_git(work, "checkout", "-q", "-b", "feature", check=check)
    _commit(work, {"src/local_only.py": _py_lines(950), ".fleet.toml": ""}, "local")
    final = root / name
    work.rename(final)
    run_git(final, "remote", "set-url", "origin", f"https://github.com/ferraroroberto/{name}.git", check=check)
    return final


_tmp = throwaway_dir("near_limit_")
_make_repo(_tmp, "alpha", {
    ".fleet.toml": _TOML_ON, "src/a.py": _py_lines(855) + _func(95), "vendor_tool/v.py": _py_lines(950), "docs/g.py": _py_lines(950),
})
_make_repo(_tmp, "beta", {"src/a.py": _py_lines(950)})
_make_repo(_tmp, "gamma", {".fleet.toml": "[structure_ratchet\n", "src/a.py": "x = 1\n"})
_make_repo(_tmp, "delta", {".fleet.toml": _TOML_ON, "src/broken.py": "def (:\n"})
_make_repo(_tmp, "epsilon", {".fleet.toml": "[structure_ratchet]\nenabled = false\n", "src/a.py": _py_lines(950)})

_alpha = near_limit.read_repo(str(_tmp / "alpha"))
check(_alpha == {"state": "adopted", "files": 1, "fns": 1},
      f"read_repo: counted at origin/main (the working tree's 950-line file, vendored and excluded paths left out), got {_alpha!r}")
check(near_limit.read_repo(str(_tmp / "beta")) == {"state": "not_adopted"}, "read_repo: no .fleet.toml on origin -> not adopted")
check(near_limit.read_repo(str(_tmp / "epsilon")) == {"state": "not_adopted"}, "read_repo: the ratchet switched off -> not adopted")
_gamma = near_limit.read_repo(str(_tmp / "gamma"))
check(_gamma["state"] == "unknown" and ".fleet.toml cannot be read" in _gamma["reason"], f"read_repo: an unparseable .fleet.toml is unknown, got {_gamma!r}")
_delta = near_limit.read_repo(str(_tmp / "delta"))
check(_delta["state"] == "unknown" and "did not parse" in _delta["reason"] and "files" not in _delta,
      f"read_repo: an unparseable Python file is unknown, never a partial count, got {_delta!r}")
_noref = near_limit.read_repo(str(_tmp / "missing"))
check(_noref == {"state": "unknown", "reason": "no origin default branch ref"}, f"read_repo: no default ref is unknown, got {_noref!r}")

_prior = "\n".join(["structure-near:alpha: files=0 fns=3", "structure-near:beta: not adopted"])
with mock.patch.object(fas.fleet_repo_scan, "ignored_repos", return_value=frozenset()):
    _report = fas.structure_report(str(_tmp), None, load_state=lambda: _prior)
check(_report["near_lines"][0] == "- alpha  files 1 (+1)  fns 1 (-2)", f"structure_report: near-limit rows with the change, got {_report['near_lines'][:1]!r}")
check("structure-near:alpha: files=1 fns=1" in _report["state_lines"] and "structure-near:gamma: unknown" in _report["state_lines"],
      "structure_report: the near-limit state lines ride the stored state, unknown as its own state")
check(_report["headline"] is None, "structure_report: near-limit counts never reach the headline")


# ---- 3. the section ----

def _near(repo: str, files: int, fns: int) -> dict:
    return {"repo": repo, "state": "adopted", "files": files, "fns": fns}


_built = section.build_near([
    _near("alpha", 4, 7), _near("fresh", 2, 0),
    {"repo": "beta", "state": "not_adopted"}, {"repo": "gamma", "state": "unknown", "reason": "no origin default branch ref"},
], "structure-near:alpha: files=5 fns=7\nstructure-near:gamma: files=1 fns=1")
check(_built["lines"][:4] == [
    "- alpha  files 4 (-1)  fns 7 (±0)", "- beta  not adopted", "- fresh  files 2 (new)  fns 0 (new)",
    "- gamma  unknown (no origin default branch ref)",
], f"build_near: deltas, not adopted, (new) without a prior reading, unknown naming its reason, got {_built['lines'][:4]!r}")
check("6 files and 7 functions" in _built["lines"][4] and "across 2 adopted repos" in _built["lines"][4]
      and "1 not adopted, 1 unknown" in _built["lines"][4], f"build_near: fleet total keeps unknown out of the counts, got {_built['lines'][4]!r}")
check(len(_built["lines"]) == 5, "build_near: no week-over-week note when the prior state was read")

_noprior = section.build_near([_near("alpha", 4, 7)], None)
check("(+" not in _noprior["lines"][0] and "(new)" not in _noprior["lines"][0] and "unknown (previous digest state" in _noprior["lines"][-1],
      f"build_near: an unreadable prior withholds deltas and says so, got {_noprior['lines']!r}")
check(section.parse_near_state("\n".join(_built["state_lines"])) == {"alpha": {"files": 4, "fns": 7}, "fresh": {"files": 2, "fns": 0}},
      "build_near: the stored state lines parse back to this run's readings")
check(section.parse_state("\n".join(_built["state_lines"])) == {},
      "build_near: its state lines are not mistaken for the structure-baseline readings")

_h.report_and_exit("test_near_limit")
