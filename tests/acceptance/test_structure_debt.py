"""Tests for the structure-debt section of the weekly /audit-fleet digest (fleet-config#1552).

Three layers:
  1. `baseline.count_entries`, pure -- per-kind counts, the excess over the limits, and every
     malformed shape raising `Unknown` instead of counting as zero.
  2. `section.build`, pure -- the delta against a stored prior state, a grown baseline flagged in the
     headline and a shrunk one not, `(new)` for a repo with no prior reading, no deltas at all when the
     prior state could not be read, and the state lines round-tripping through `parse_state`.
  3. `fleet_audit_scan.structure_report` end to end on throwaway git repos -- the numbers come from
     `origin/<default>` while the working tree, and the checked-out branch, hold something else; a repo
     with no baseline is `not adopted`; one with an unparseable baseline is `unknown`.

Lives under tests/acceptance/ because the flat tests/ directory is baselined in
`.structure-ratchet-baseline.json` and takes no new module.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/acceptance/test_structure_debt.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
sys.path.insert(0, str(REPO / "tests" / "_lib"))
import fleet_audit_scan as fas  # noqa: E402
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import run_git  # noqa: E402
from structure_debt import baseline, section  # noqa: E402

_h = CheckHarness()
check = _h.check


def _baseline_text(entries: list[dict]) -> str:
    return json.dumps({"version": 1, "entries": entries})


_SAMPLE = _baseline_text([
    {"key": "size:src/a.py", "lines": 1500},
    {"key": "size:src/b.py", "lines": 1200},
    {"key": "fn:src/a.py:run", "lines": 130},
    {"key": "dir:src", "modules": 45},
    {"key": "layer:src/x.py->app", "imports": 1},
])


# ---- 1. counting ----

_counts = baseline.count_entries(_SAMPLE, 1000, 100)
check(_counts == {"files": 2, "fns": 1, "dirs": 1, "excess": 730},
      f"count_entries: per-kind counts and excess (500+200+30), layer entries uncounted, got {_counts!r}")
check(baseline.count_entries(_baseline_text([]), 1000, 100) == {"files": 0, "fns": 0, "dirs": 0, "excess": 0},
      "count_entries: an empty baseline is a real zero reading")
check(baseline.count_entries(_SAMPLE, 1400, 100)["excess"] == 100 + 30,
      "count_entries: the repo's own limit, not the default, sets the excess")

for _label, _bad in (
    ("not json", "{nope"),
    ("wrong version", json.dumps({"version": 2, "entries": []})),
    ("not an object", "[]"),
    ("entries not a list", json.dumps({"version": 1, "entries": {}})),
    ("entry without a key", json.dumps({"version": 1, "entries": [{"lines": 5}]})),
    ("size entry without lines", json.dumps({"version": 1, "entries": [{"key": "size:a.py"}]})),
):
    try:
        baseline.count_entries(_bad, 1000, 100)
        check(False, f"count_entries: {_label} must raise Unknown, not count")
    except baseline.Unknown:
        pass


# ---- 2. the section ----

def _reading(repo: str, files: int, fns: int, dirs: int, excess: int) -> dict:
    return {"repo": repo, "state": "adopted", "files": files, "fns": fns, "dirs": dirs, "excess": excess}


_PRIOR = "\n".join([
    "<!-- audit-fleet-digest -->", "last-run-at: 2026-10-15", "alpha: 3",
    "structure-last-run-at: 2026-10-15",
    "structure:alpha: files=1 fns=1 dirs=1 excess=100",
    "structure:beta: files=4 fns=0 dirs=0 excess=900",
    "structure:gamma: not adopted",
])
check(section.parse_state(_PRIOR) == {
    "alpha": {"files": 1, "fns": 1, "dirs": 1, "excess": 100},
    "beta": {"files": 4, "fns": 0, "dirs": 0, "excess": 900},
}, "parse_state: readings only; not adopted, other keys and the date line are no reading")

_grown = section.build([_reading("alpha", 2, 1, 1, 730), _reading("beta", 3, 0, 0, 700)], _PRIOR, "2026-10-22")
check(_grown["lines"][0] == "- alpha  files 2 (+1)  fns 1 (±0)  dirs 1 (±0)  excess 730 (+630)",
      f"build: per-kind counts with deltas, got {_grown['lines'][0]!r}")
check(_grown["lines"][1] == "- beta  files 3 (-1)  fns 0 (±0)  dirs 0 (±0)  excess 700 (-200)",
      f"build: a shrunk repo shows negative deltas, got {_grown['lines'][1]!r}")
check(_grown["grew"] == ["alpha"] and _grown["headline"] == "structure debt grew: alpha",
      f"build: the grown repo, and only it, is in the headline, got {_grown['headline']!r}")
check("7 baselined entries" in _grown["lines"][2] and "largest alpha (4), beta (3)" in _grown["lines"][2],
      f"build: fleet total with the largest repos, got {_grown['lines'][2]!r}")

_shrunk = section.build([_reading("alpha", 1, 1, 1, 90), _reading("beta", 3, 0, 0, 700)], _PRIOR, "2026-10-22")
check(_shrunk["headline"] is None and _shrunk["grew"] == [], "build: a shrunk or unchanged fleet is not flagged")

_states = section.build([
    {"repo": "gamma", "state": "not_adopted"},
    {"repo": "delta", "state": "unknown", "reason": "baseline is not valid JSON"},
    _reading("epsilon", 9, 9, 9, 9),
], _PRIOR, "2026-10-22")
check(_states["lines"][:2] == ["- delta  unknown (baseline is not valid JSON)", "- epsilon  files 9 (new)  fns 9 (new)  dirs 9 (new)  excess 9 (new)"],
      f"build: unknown names its reason, a repo with no prior is (new), got {_states['lines'][:2]!r}")
check(_states["lines"][2] == "- gamma  not adopted", "build: not adopted is its own state")
check("0 baselined entries" not in _states["lines"][3] and "1 not adopted, 1 unknown" in _states["lines"][3],
      f"build: not adopted and unknown are counted apart, never as zero entries, got {_states['lines'][3]!r}")
check(_states["headline"] is None, "build: a repo with no prior reading is never flagged as grown")

_noprior = section.build([_reading("alpha", 9, 9, 9, 9)], None, "2026-10-22")
check(_noprior["headline"] is None and "(+" not in _noprior["lines"][0] and "unknown (previous digest state" in _noprior["lines"][-1],
      f"build: an unreadable prior withholds deltas and says so, got {_noprior['lines']!r}")

_round = section.parse_state("\n".join(_grown["state_lines"]))
check(_round == {"alpha": {"files": 2, "fns": 1, "dirs": 1, "excess": 730},
                 "beta": {"files": 3, "fns": 0, "dirs": 0, "excess": 700}}
      and _grown["state_lines"][0] == "structure-last-run-at: 2026-10-22",
      "build: the stored state lines parse back to this run's readings")


# ---- 3. structure_report on real git repos: the default branch is what is read ----

def _commit(repo: Path, name: str, text: str, message: str) -> None:
    (repo / name).write_text(text, encoding="utf-8")
    run_git(repo, "add", name, check=check)
    run_git(repo, "commit", "-q", "-m", message, check=check)


def _make_repo(root: Path, name: str, origin_baseline: str | None) -> Path:
    """`root/name` cloned from `root/_up/name`; the baseline exists only on origin's main."""
    up = root / "_up" / name
    up.mkdir(parents=True)
    run_git(up, "init", "-q", check=check)
    run_git(up, "checkout", "-q", "-b", "main", check=check)
    run_git(up, "config", "user.email", "35553560+ferraroroberto@users.noreply.github.com", check=check)
    run_git(up, "config", "user.name", "Test", check=check)
    _commit(up, "README.md", "hello\n", "initial")
    work = root / name
    run_git(root, "clone", "-q", str(up), str(work), check=check)
    if origin_baseline is not None:
        _commit(up, baseline.DEFAULT_BASELINE, origin_baseline, "adopt")
        run_git(work, "fetch", "-q", "origin", check=check)
    # The checkout disagrees with origin: another branch, and a local file that must not be read.
    run_git(work, "checkout", "-q", "-b", "feature", check=check)
    (work / baseline.DEFAULT_BASELINE).write_text(_baseline_text([]), encoding="utf-8")
    run_git(work, "remote", "set-url", "origin", f"https://github.com/ferraroroberto/{name}.git", check=check)
    return work


_tmp = Path(tempfile.mkdtemp(prefix="structure_debt_"))
try:
    _make_repo(_tmp, "alpha", _SAMPLE)
    _make_repo(_tmp, "beta", None)
    _make_repo(_tmp, "gamma", "{nope")
    with mock.patch.object(fas.fleet_repo_scan, "ignored_repos", return_value=frozenset()):
        _report = fas.structure_report(str(_tmp), None, load_state=lambda: _PRIOR)
        _by = {ln.split()[1]: ln for ln in _report["lines"][:3]}
        check(_by["alpha"] == "- alpha  files 2 (+1)  fns 1 (±0)  dirs 1 (±0)  excess 730 (+630)",
              f"structure_report: alpha counted from origin/main while the working tree holds an empty baseline, got {_by['alpha']!r}")
        check(_by["beta"] == "- beta  not adopted", f"structure_report: no baseline -> not adopted, got {_by['beta']!r}")
        check(_by["gamma"].startswith("- gamma  unknown (baseline is not valid JSON"),
              f"structure_report: an unparseable baseline -> unknown, got {_by['gamma']!r}")
        check(_report["headline"] == "structure debt grew: alpha",
              f"structure_report: the grown baseline is flagged, got {_report['headline']!r}")

        _unreadable = fas.structure_report(str(_tmp), None, load_state=lambda: None)
        check(_unreadable["headline"] is None and "unknown (previous digest state" in _unreadable["lines"][-1],
              "structure_report: an unreadable prior state is reported, not read as no change")

        # scan() carries the section inside the one sweep, as its own key.
        with mock.patch.object(fas, "_scan_gates", return_value={"enumerated": 0}), \
                mock.patch.object(fas, "_load_digest_state", return_value=""), \
                mock.patch.object(fas, "gate_report", return_value={"lines": ["g"]}), \
                mock.patch.object(fas, "structure_report", return_value={"lines": ["x"]}) as _sr:
            _scanned = fas.scan(str(_tmp), None, True)
        check(_scanned.get("structure") == {"lines": ["x"]} and _sr.called,
              f"scan: the structure section is a key of the single sweep result, got {_scanned!r}")
finally:
    shutil.rmtree(_tmp, ignore_errors=True)

_h.report_and_exit("test_structure_debt")
