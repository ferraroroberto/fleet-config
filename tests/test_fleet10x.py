"""Unit tests for .claude/skills/fleet-10x/fleet10x.py (fleet-config#1253).

Exercises the deterministic halves of /fleet-10x on fixtures: repo selection
(threshold, `.fleet.toml` requirement, ordering, explicit override), the
per-repo recommendation schema, evidence linking, and the impact-vs-cost
ranking. The fact-gathering CLI runs against a throwaway git repo and a temp
state dir via CLAUDE_HOOKS_STATE_DIR (no real ~/.claude/hooks/state is
touched).

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_fleet10x.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / ".claude" / "skills" / "fleet-10x"
sys.path.insert(0, str(SKILL))
import fleet10x as f10  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import make_upstream_and_clone, run_git  # noqa: E402

_h = CheckHarness()
check = _h.check


def facts(name: str, commits: int, toml: bool = True, desc: str = "") -> f10.RepoFacts:
    return f10.RepoFacts(name=name, path=Path("E:/x") / name, commits=commits,
                         has_fleet_toml=toml, description=desc, default_branch="main")


# ---- select: threshold, .fleet.toml, order, quiet set ----

FLEET = [
    facts("beta", 20),
    facts("alpha", 300),
    facts("gamma", 19),
    facts("delta", 500, toml=False),
    facts("epsilon", 20),
    facts("zeta", 0),
]

sel = f10.select(FLEET, min_commits=20)
names = [r.name for r in sel.active]
check(names == ["alpha", "beta", "epsilon"],
      f"active = .fleet.toml repos at/above threshold, commits desc then name: {names}")
check("delta" not in names, "a repo without .fleet.toml is never selected, however busy")
check([r.name for r in sel.quiet] == ["gamma", "zeta"],
      f"quiet = .fleet.toml repos below threshold, name order: {[r.name for r in sel.quiet]}")
check(sel.override is False, "threshold selection reports override=False")

check([r.name for r in f10.select(list(reversed(FLEET)), min_commits=20).active] == names,
      "selection is independent of input order")
check([r.name for r in f10.select(FLEET, min_commits=21).active] == ["alpha"],
      "threshold is inclusive at exactly min_commits")

# ---- select: explicit override ----

ov = f10.select(FLEET, min_commits=20, only=["gamma", "alpha"])
check([r.name for r in ov.active] == ["alpha", "gamma"],
      "override selects exactly the named repos (below threshold allowed), same order rule")
check(ov.override is True, "override reports override=True")
check("gamma" not in [r.name for r in ov.quiet], "an overridden repo is not also quiet")
try:
    f10.select(FLEET, min_commits=20, only=["alpha", "nope"])
    check(False, "an unknown override name must raise")
except ValueError as e:
    check("nope" in str(e), "an unknown override name raises, naming it")

# ---- one_line: quiet-repo descriptions stay one short line ----

long_desc = "First sentence here. " + "x" * 400
line = f10.one_line(long_desc)
check(line == "First sentence here.", f"one_line keeps the first sentence: {line!r}")
check(len(f10.one_line("y" * 400)) <= f10.ONE_LINE_MAX, "one_line caps a sentence-less blob")
check(f10.one_line("") == "(no description)", "empty description has an explicit placeholder")

# ---- validate: the recommendation schema ----


def rec(**over) -> dict:
    base = {"title": "T", "impact": "H", "impact_reason": "r", "cost": "M",
            "what_changes": "w", "why_10x": "y", "first_step": "s",
            "evidence": ["app/main.py:12"]}
    base.update(over)
    return base


good = {"repo": "alpha", "summary": "s", "recommendations": [rec(), rec(), rec()]}
check(f10.validate(good) == [], f"a 3-rec doc is valid: {f10.validate(good)}")
check(any("3-7" in e for e in f10.validate({**good, "recommendations": [rec(), rec()]})),
      "fewer than 3 recommendations is invalid")
check(any("3-7" in e for e in f10.validate({**good, "recommendations": [rec()] * 8})),
      "more than 7 recommendations is invalid")
check(any("impact" in e for e in f10.validate({**good, "recommendations": [rec(impact="X"), rec(), rec()]})),
      "impact outside H/M/L is invalid")
check(any("cost" in e for e in f10.validate({**good, "recommendations": [rec(cost="XXL"), rec(), rec()]})),
      "cost outside S/M/L/XL is invalid")
check(any("evidence" in e for e in f10.validate({**good, "recommendations": [rec(evidence=[]), rec(), rec()]})),
      "a recommendation without evidence is invalid")
check(any("why_10x" in e for e in f10.validate({**good, "recommendations": [rec(why_10x=" "), rec(), rec()]})),
      "a blank required field is invalid")
check(any("repo" in e for e in f10.validate({**good, "repo": ""})), "a missing repo name is invalid")

# ---- link_evidence: file:line, #N, URLs ----

base = "https://github.com/ferraroroberto/alpha"
check(f10.link_evidence("app/main.py:12", "alpha", "main")
      == f"[app/main.py:12]({base}/blob/main/app/main.py#L12)", "file:line -> blob link with line anchor")
check(f10.link_evidence("README.md", "alpha", "main") == f"[README.md]({base}/blob/main/README.md)",
      "bare path -> blob link")
check(f10.link_evidence("#45", "alpha", "main") == f"[alpha#45]({base}/issues/45)", "#N -> issue link")
check(f10.link_evidence("task-os#7", "alpha", "main")
      == "[task-os#7](https://github.com/ferraroroberto/task-os/issues/7)", "repo#N -> that repo's issue")
url = "https://example.com/doc"
check(f10.link_evidence(url, "alpha", "main") == f"[{url}]({url})", "a URL links to itself")

# ---- rank: impact over cost, deterministic ----

docs = [
    {"repo": "beta", "summary": "", "recommendations": [
        rec(title="b-HL", impact="H", cost="L"), rec(title="b-LS", impact="L", cost="S"),
        rec(title="b-MM", impact="M", cost="M")]},
    {"repo": "alpha", "summary": "", "recommendations": [
        rec(title="a-HS", impact="H", cost="S"), rec(title="a-HXL", impact="H", cost="XL"),
        rec(title="a-MM", impact="M", cost="M")]},
]
ranked = f10.rank(docs)
order = [r["title"] for r in ranked]
check(order[0] == "a-HS", f"H/S ranks first: {order}")
check(order.index("a-MM") < order.index("b-MM"), "equal score ties break by impact, then repo name")
check(order.index("b-LS") < order.index("a-HXL"), f"L/S (1.0) outranks H/XL (0.75): {order}")
check(order == [r["title"] for r in f10.rank(list(reversed(docs)))], "ranking is input-order independent")
table = f10.ranked_table(ranked, {"alpha": "main", "beta": "main"})
check(table.splitlines()[0].startswith("| # | Repo |"), "ranked table has a header row")
check("[alpha](https://github.com/ferraroroberto/alpha)" in table, "ranked table links the repo")
check(len(table.splitlines()) == 2 + len(ranked), "one table row per recommendation")

# ---- quota line: observed or unknown, never estimated ----

check(f10._quota_line({"five_hour": 6.0, "seven_day": 37.0}, {"five_hour": 21.0, "seven_day": 40.5})
      == "five_hour:6%->21%,seven_day:37%->40.5%", "quota line shows start -> end per window")
check(f10._quota_line(None, {"five_hour": 21.0}) == "unknown", "no start reading -> unknown")
check(f10._quota_line({"five_hour": 6.0}, None) == "unknown", "no end reading -> unknown")

foot = f10.footer_text("rule-x", "1h05m", "unknown", 21, None)
check("Wall time: 1h05m" in foot and "rule-x" in foot, "footer states selection rule and wall time")
check("reported tokens: unknown" in foot, "footer says unknown tokens rather than inventing a number")
check("1,234,567" in f10.footer_text("r", "w", "q", 1, 1234567), "footer formats a known token count")

# ---- render_brief: every placeholder filled, read-only restated ----


brief = f10.render_brief(f10.BRIEF_TEMPLATE.read_text(encoding="utf-8"),
                         {"name": "alpha", "path": "E:/x/alpha", "branch": "main", "commits": 42},
                         Path("C:/run"), Path("E:/x"))
check(not re.search(r"\{[a-z_]+\}", brief), "rendered brief leaves no {placeholder} unfilled")
check("You are read-only" in brief and "Never edit, create or delete any file" in brief,
      "rendered brief restates read-only in full")
check("C:/run/repos/alpha.json" in brief, "rendered brief names the researcher's one output file")
check('"repo": "alpha"' in brief, "rendered brief's schema example carries the repo name")

# ---- CLI: facts from a real git repo, run dir under the state dir ----

tmp = Path(tempfile.mkdtemp(prefix="fleet10x-test-"))
try:
    root = tmp / "root"
    root.mkdir()
    _, work = make_upstream_and_clone(tmp, check)
    run_git(work, "commit", "--allow-empty", "-m", "two", check=check)
    check(f10.count_commits(work, "HEAD", dt.date.today() - dt.timedelta(days=60)) == 2,
          "count_commits counts the window's commits on the given ref")

    env = {**os.environ, "CLAUDE_HOOKS_STATE_DIR": str(tmp / "state"), "PYTHONUTF8": "1"}
    script = str(SKILL / "fleet10x.py")
    proc = subprocess.run([sys.executable, script, "rundir", "--date", "2026-10-05"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    check(proc.returncode == 0, f"rundir exits 0: {proc.stderr}")
    want = tmp / "state" / "fleet-10x" / "2026-10-05"
    check(f"RUN_DIR={want}" in proc.stdout, f"rundir prints the dated state path: {proc.stdout!r}")
    check((want / "repos").is_dir() and (want / "run.json").is_file(),
          "rundir creates repos/ and records run.json")

    (want / "repos" / "alpha.json").write_text(json.dumps(good), encoding="utf-8")
    proc = subprocess.run([sys.executable, script, "validate", str(want / "repos" / "alpha.json")],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    check(proc.returncode == 0 and "VALID=alpha" in proc.stdout, f"validate CLI passes a good doc: {proc.stdout}")
    (want / "repos" / "bad.json").write_text(json.dumps({"repo": "bad"}), encoding="utf-8")
    proc = subprocess.run([sys.executable, script, "validate", str(want / "repos" / "bad.json")],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    check(proc.returncode != 0 and "INVALID=bad" in proc.stdout, "validate CLI fails a bad doc loudly")

    proc = subprocess.run([sys.executable, script, "rank", str(want)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    check(proc.returncode != 0 and "INVALID=bad" in proc.stdout,
          f"rank exits non-zero and names an invalid researcher doc: {proc.stdout}")
    check("RANKED=3|REPOS=1" in proc.stdout, "rank still ranks the valid docs")
    check("QUOTA=unknown" in proc.stdout, "rank reports quota unknown when no statusline reading exists")
    check((want / "ranked.md").read_text(encoding="utf-8").count("[alpha](") == 3,
          "rank writes ranked.md with one row per recommendation")

    footer_cmd = [sys.executable, script, "footer", str(want), "--agents", "2"]
    proc = subprocess.run(footer_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    check(proc.returncode != 0, "footer refuses when report.md does not exist yet")
    (want / "report.md").write_text("# Report\n", encoding="utf-8")
    proc = subprocess.run(footer_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    text = (want / "report.md").read_text(encoding="utf-8")
    check(proc.returncode == 0 and text.startswith("# Report") and "## Run" in text,
          f"footer appends the run section to report.md: {proc.stdout}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_fleet10x")
