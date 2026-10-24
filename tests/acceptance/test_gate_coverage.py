"""Tests for the gate-coverage section of the weekly /audit-fleet digest (fleet-config#1619).

Three layers:
  1. `reader.read_config` / `reader.count_hits`, pure -- enabled ratchet tables and their
     baselines, the conventions rules, the hit count per baseline shape, and every malformed
     shape raising `Unknown` instead of counting as zero.
  2. `section.build`, pure -- week-over-week change against a stored prior state, `(new)` for
     a repo with no prior reading, `not adopted` and `unknown` as their own rows, no deltas at
     all when the prior state could not be read, and the state lines round-tripping.
  3. `fleet_audit_scan.gate_report` end to end on throwaway git repos -- the numbers come from
     `origin/<default>` while the working tree holds something else, and `scan()` carries the
     section as its own key.

Lives under tests/acceptance/ because the flat tests/ directory is baselined in
`.structure-ratchet-baseline.json` and takes no new module.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/acceptance/test_gate_coverage.py`  (also invoked by tests/run_acceptance.py)
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
from gate_coverage import reader, section  # noqa: E402
from git_fixtures import run_git  # noqa: E402
from structure_debt.baseline import Unknown  # noqa: E402

_h = CheckHarness()
check = _h.check


def _baseline(entries: list[dict]) -> str:
    return json.dumps({"version": 1, "entries": entries})


def _raises(fn) -> bool:
    try:
        fn()
    except Unknown:
        return True
    return False


# ---- 1. reading ----

_TOML = (
    '[static_ratchet]\nenabled = true\n'
    '[docs_ratchet]\nenabled = true\nbaseline = "ci/docs.json"\n'
    '[comment_ratchet]\nenabled = false\n'
    '[conventions_ratchet]\nenabled = true\nrules = ["no-window", "git-wrapper"]\n'
    '[e2e]\nfoo = 1\n'
)
_cfg = reader.read_config(_TOML)
check(_cfg == {"ratchets": {"static_ratchet": ".static-ratchet-baseline.json", "docs_ratchet": "ci/docs.json",
                            "conventions_ratchet": ".conventions-ratchet-baseline.json"},
               "rules": ["git-wrapper", "no-window"]},
      f"read_config: enabled tables with default or declared baselines, a disabled one dropped, got {_cfg!r}")
check(reader.read_config(None) == {"ratchets": {}, "rules": []} and reader.read_config("") == {"ratchets": {}, "rules": []},
      "read_config: no .fleet.toml is no ratchets, not an error")
check(_raises(lambda: reader.read_config("[static_ratchet\n")), "read_config: unparseable TOML is Unknown")
check(_raises(lambda: reader.read_config('[conventions_ratchet]\nenabled = true\nrules = "no-window"\n')),
      "read_config: a rules value that is not a list is Unknown")
check(reader.read_config('[conventions_ratchet]\nenabled = false\nrules = ["no-window"]\n')["rules"] == [],
      "read_config: a disabled conventions ratchet enforces no rules")

check(reader.count_hits(_baseline([{"key": "dup:a", "files": ["x", "y"]}, {"key": "unused:F401:b"}]), None) == 2,
      "count_hits: one hit per entry where the ratchet has no count field")
check(reader.count_hits(_baseline([{"key": "a", "count": 3}, {"key": "b", "count": 0}]), "count") == 3, "count_hits: integer counts")
check(reader.count_hits(_baseline([{"key": "a", "hits": {"R-1": 2, "R-2": 1}}]), "hits") == 3, "count_hits: per-rule dicts are summed")
check(reader.count_hits(_baseline([{"key": "a", "names": ["f", "g"]}]), "names") == 2, "count_hits: lists count their items")
check(reader.count_hits(_baseline([]), "count") == 0, "count_hits: an empty baseline is a real zero reading")
for _bad, _why in (("{nope", "invalid JSON"), (json.dumps({"version": 2, "entries": []}), "wrong version"),
                   (_baseline([{"key": "a"}]), "missing count field"), (_baseline([{"key": "a", "count": True}]), "bool count"),
                   (_baseline([{"nokey": 1}]), "entry without a key")):
    check(_raises(lambda b=_bad: reader.count_hits(b, "count")), f"count_hits: {_why} is Unknown, never zero")
check(reader.default_baseline("dead_code_ratchet") == ".dead-code-ratchet-baseline.json", "default_baseline: the ratchet's own default name")


# ---- 2. the section ----

def _adopted(repo: str, hits: dict, rules: list[str] | None = None) -> dict:
    return {"repo": repo, "state": "adopted", "hits": hits, "rules": rules or []}


_ENTRIES = [
    _adopted("alpha", {"static_ratchet": 4, "conventions_ratchet": 1}, ["no-window"]),
    _adopted("bravo", {"docs_ratchet": 10}),
    {"repo": "charlie", "state": "not_adopted"},
    {"repo": "delta", "state": "unknown", "reason": "baseline is not valid JSON"},
]
_PRIOR = "<!-- audit-fleet-digest -->\ngate:alpha: ratchets=1 rules=0 hits=6\ngate:charlie: not adopted\nalpha: 3\n"
_sec = section.build(_ENTRIES, _PRIOR, "2026-10-24")
_by = {ln.split()[1]: ln for ln in _sec["lines"][:4]}
check(_by["alpha"] == "- alpha  ratchets 2 (+1)  rules 1 (+1)  hits 5 (-1)  [conventions 1, static 4; rules: no-window]",
      f"build: week-over-week change against the stored reading, got {_by['alpha']!r}")
check(_by["bravo"] == "- bravo  ratchets 1 (new)  rules 0 (new)  hits 10 (new)  [docs 10]",
      f"build: a repo with no prior reading is (new), got {_by['bravo']!r}")
check(_by["charlie"] == "- charlie  not adopted" and _by["delta"] == "- delta  unknown (baseline is not valid JSON)",
      "build: not adopted and unknown are their own rows, never a zero reading")
check(_sec["lines"][4] == "- fleet total: 3 ratchets and 1 conventions rules enabled across 2 adopted repos; "
                         "15 baselined hits; 1 not adopted, 1 unknown", f"build: the fleet total, got {_sec['lines'][4]!r}")
check(_sec["state_lines"] == ["gate-last-run-at: 2026-10-24", "gate:alpha: ratchets=2 rules=1 hits=5",
                              "gate:bravo: ratchets=1 rules=0 hits=10", "gate:charlie: not adopted", "gate:delta: unknown"],
      f"build: the state lines to store back, got {_sec['state_lines']!r}")
check(section.parse_state("\n".join(_sec["state_lines"])) == {"alpha": {"ratchets": 2, "rules": 1, "hits": 5},
                                                             "bravo": {"ratchets": 1, "rules": 0, "hits": 10}},
      "parse_state: the stored lines read back; not adopted and unknown carry no reading")
_same = section.build(_ENTRIES, "\n".join(_sec["state_lines"]), "2026-10-31")
check("ratchets 2 (±0)  rules 1 (±0)  hits 5 (±0)" in _same["lines"][0], "build: an unchanged repo shows ±0")
_blind = section.build(_ENTRIES, None, "2026-10-24")
check("(" not in _blind["lines"][0].split("[")[0] and "unknown (previous digest state" in _blind["lines"][-1],
      "build: an unreadable prior state withholds every delta and says so")


# ---- 3. gate_report on real git repos: the default branch is what is read ----

def _make_repo(root: Path, name: str, files: dict[str, str]) -> Path:
    """`root/name` cloned from `root/_up/name`; `files` exist only on origin's main."""
    up = root / "_up" / name
    up.mkdir(parents=True)
    run_git(up, "init", "-q", check=check)
    run_git(up, "checkout", "-q", "-b", "main", check=check)
    run_git(up, "config", "user.email", "35553560+ferraroroberto@users.noreply.github.com", check=check)
    run_git(up, "config", "user.name", "Test", check=check)
    (up / "README.md").write_text("hello\n", encoding="utf-8")
    run_git(up, "add", "README.md", check=check)
    run_git(up, "commit", "-q", "-m", "initial", check=check)
    work = root / name
    run_git(root, "clone", "-q", str(up), str(work), check=check)
    if files:
        for rel, text in files.items():
            (up / rel).write_text(text, encoding="utf-8")
            run_git(up, "add", rel, check=check)
        run_git(up, "commit", "-q", "-m", "adopt", check=check)
        run_git(work, "fetch", "-q", "origin", check=check)
    # The checkout disagrees with origin: another branch, and local files that must not be read.
    run_git(work, "checkout", "-q", "-b", "feature", check=check)
    (work / ".fleet.toml").write_text("[static_ratchet]\nenabled = false\n", encoding="utf-8")
    run_git(work, "remote", "set-url", "origin", f"https://github.com/ferraroroberto/{name}.git", check=check)
    return work


_tmp = Path(tempfile.mkdtemp(prefix="gate_coverage_"))
try:
    _make_repo(_tmp, "alpha", {".fleet.toml": "[static_ratchet]\nenabled = true\n[comment_ratchet]\nenabled = true\n",
                               ".static-ratchet-baseline.json": _baseline([{"key": "dup:a"}, {"key": "dup:b"}])})
    _make_repo(_tmp, "beta", {})
    _make_repo(_tmp, "gamma", {".fleet.toml": "[static_ratchet]\nenabled = true\n", ".static-ratchet-baseline.json": "{nope"})
    with mock.patch.object(fas.fleet_repo_scan, "ignored_repos", return_value=frozenset()):
        _report = fas.gate_report(str(_tmp), None, load_state=lambda: "gate:alpha: ratchets=2 rules=0 hits=1\n")
        _by = {ln.split()[1]: ln for ln in _report["lines"][:3]}
        check(_by["alpha"] == "- alpha  ratchets 2 (±0)  rules 0 (±0)  hits 2 (+1)  [comment 0, static 2]",
              f"gate_report: alpha read from origin/main while the working tree disables it; a missing baseline is 0, got {_by['alpha']!r}")
        check(_by["beta"] == "- beta  not adopted", f"gate_report: no .fleet.toml -> not adopted, got {_by['beta']!r}")
        check(_by["gamma"].startswith("- gamma  unknown (.static-ratchet-baseline.json: baseline is not valid JSON"),
              f"gate_report: an unparseable baseline -> unknown with its path, got {_by['gamma']!r}")

        # scan() carries the section inside the one sweep, reading the digest state once.
        with mock.patch.object(fas, "_scan_gates", return_value={"enumerated": 0}), \
                mock.patch.object(fas, "_load_digest_state", return_value="") as _st, \
                mock.patch.object(fas, "structure_report", return_value={"lines": ["s"]}), \
                mock.patch.object(fas, "gate_report", return_value={"lines": ["g"]}) as _gr:
            _scanned = fas.scan(str(_tmp), None, True)
        check(_scanned.get("gate_coverage") == {"lines": ["g"]} and _gr.called and _st.call_count == 1,
              f"scan: the gate-coverage section is a key of the single sweep result, got {_scanned!r}")
finally:
    shutil.rmtree(_tmp, ignore_errors=True)

_h.report_and_exit("test_gate_coverage")
