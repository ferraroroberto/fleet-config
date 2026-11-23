"""Tests for `ui_proof merge` (skills/_lib/ui_proof/merge.py), the one scripted `gh pr merge`.

The hook never sees a merge a script spawns, so this helper carries the same
gate: a UI-touching PR with no proof page is refused and `gh pr merge` never
runs; an unknown gate refuses too; only `GATE=pass` merges, in the repo the gate
judged. `gh` is a recording fake, so no network is touched. One case composes
the real `merge_gate.run` over a temp UX repo (its two `gh` lookups faked).

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/web_review/test_ui_proof_merge.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
from git_run import run_git  # noqa: E402
from ui_proof import cli, merge, merge_gate  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import init_repo  # noqa: E402

_h = CheckHarness()
check = _h.check

SLUG = "acme/widget"
CLAUDE_MD = "# Project\n\n## UX surface\n- design spec applies: yes\n- paths:\n  - app/static/**/*.css\n"


class FakeGh:
    """Records every `gh` argv; answers with a fixed exit code."""

    def __init__(self, code: int = 0) -> None:
        self.code, self.calls = code, []

    def __call__(self, argv, timeout=None):
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(["gh", *argv], self.code, "merged", "")


def fixed_gate(code, lines):
    return lambda cwd, selector, slug: (code, list(lines))


def fields(lines):
    return dict(line.split("=", 1) for line in lines if "=" in line)


here = Path(".")
os.environ.pop(merge.BYPASS_ENV, None)

# Refused: the gate says the UI diff has no proof page; gh is never called.
gh = FakeGh()
code, lines = merge.merge(here, "7", None, ["--squash"],
                          gate=fixed_gate(1, ["GATE=refuse", f"PR={SLUG}#7", "NEED=.proof/7/index.html"]),
                          run_gh=gh)
out = fields(lines)
check(code == 1 and out["MERGE"] == "refused" and not gh.calls,
      f"a refused gate blocks the merge with no gh call — got {code} {out} calls={gh.calls}")

# Unknown (exit 3) and a malformed pass both refuse, never merge.
for gate_code, gate_lines in ((3, ["GATE=unknown", "REASON=gh down"]), (0, ["GATE=refuse"]), (5, [])):
    gh = FakeGh()
    code, lines = merge.merge(here, "7", None, [], gate=fixed_gate(gate_code, gate_lines), run_gh=gh)
    check(code == 3 and fields(lines)["MERGE"] == "refused" and not gh.calls,
          f"gate ({gate_code}, {gate_lines}) is unknown, no merge — got {code} {lines} calls={gh.calls}")

# Pass: merges once, in the repo the gate judged, with the caller's flags.
gh = FakeGh()
code, lines = merge.merge(here, "7", None, ["--auto", "--squash"],
                          gate=fixed_gate(0, ["GATE=pass", f"PR={SLUG}#7"]), run_gh=gh)
check(code == 0 and fields(lines)["MERGE"] == "ok"
      and gh.calls == [["pr", "merge", "7", "-R", SLUG, "--auto", "--squash"]],
      f"a pass merges once with -R the judged repo — got {code} {lines} calls={gh.calls}")
gh = FakeGh()
merge.merge(here, "7", None, [], gate=fixed_gate(0, ["GATE=pass", "VISUAL_PROOF=n/a", f"REPO={SLUG}"]), run_gh=gh)
check(gh.calls == [["pr", "merge", "7", "-R", SLUG]], f"an n/a pass merges in REPO= — got {gh.calls}")

# gh's own failure is reported with its exit code.
gh = FakeGh(code=1)
code, lines = merge.merge(here, "7", None, [], gate=fixed_gate(0, ["GATE=pass", f"PR={SLUG}#7"]), run_gh=gh)
check(code == 1 and fields(lines)["MERGE"] == "failed", f"gh failing is reported — got {code} {lines}")

# -R among the gh args is refused before the gate: it could point the merge elsewhere.
gh = FakeGh()
code, lines = merge.merge(here, "7", None, ["--repo=other/repo"], gate=fixed_gate(0, ["GATE=pass"]), run_gh=gh)
check(code == 2 and not gh.calls, f"-R among the gh args is refused — got {code} {lines}")

# The CLI passes the flags after `--` through untouched (here, the refused -R).
code = cli.main(["merge", ".", "--pr", "7", "--", "--squash", "-R", "x/y"])
check(code == 2, f"`ui_proof merge . --pr 7 -- --squash -R x/y` reaches the helper's -R refusal — got {code}")

# The owner's bypass skips the gate and is logged.
state = Path(tempfile.mkdtemp())
os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(state)
os.environ[merge.BYPASS_ENV] = "gate broken"
try:
    gh = FakeGh()
    code, lines = merge.merge(here, "7", SLUG, [], gate=fixed_gate(1, ["GATE=refuse"]), run_gh=gh)
    log = (state / merge.BYPASS_LOG).read_text(encoding="utf-8").splitlines()
    check(code == 0 and fields(lines)["GATE"] == "bypassed" and gh.calls == [["pr", "merge", "7", "-R", SLUG]]
          and json.loads(log[-1])["reason"] == "gate broken",
          f"the bypass merges and logs — got {code} {lines} log={log}")
finally:
    os.environ.pop(merge.BYPASS_ENV, None)
    shutil.rmtree(state, ignore_errors=True)

# The real gate over a temp UX repo: a UI file with no page is refused, no merge.
ux = init_repo(empty_commit=False)
try:
    (ux / "CLAUDE.md").write_text(CLAUDE_MD, encoding="utf-8")
    run_git(["-C", str(ux), "add", "-A"])
    run_git(["-C", str(ux), "commit", "-q", "-m", "base"])
    run_git(["-C", str(ux), "remote", "add", "origin", f"https://github.com/{SLUG}.git"])
    pr = lambda selector, slug: {"number": 7, "headRefName": "feat/gone", "baseRefName": "main"}  # noqa: E731
    files = lambda number, slug: ["app/static/view.css"]  # noqa: E731
    gh = FakeGh()
    code, lines = merge.merge(ux, "7", None, ["--squash"],
                              gate=lambda c, s, sl: merge_gate.run(c, s, sl, pr, files), run_gh=gh)
    out = fields(lines)
    check(code == 1 and out["GATE"] == "refuse" and out["MERGE"] == "refused" and not gh.calls,
          f"a scripted merge of a UI PR with no proof page is refused — got {code} {out} calls={gh.calls}")
finally:
    shutil.rmtree(ux, ignore_errors=True)


_h.report_and_exit("ui_proof_merge")
