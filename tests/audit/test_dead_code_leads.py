"""Unit tests for skills/_lib/dead_code/dead_code_leads.py (fleet-config#1527).

`/codebase-audit` bucket 2 gets vulture leads at floor 60 filtered by kind, a
caller search, and an honest `not-confirmed` state when the tool cannot run.
Synthetic git repos only.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/audit/test_dead_code_leads.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
from dead_code import dead_code_leads as dcl  # noqa: E402
from git_run import run_git  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

sys.path.insert(0, str(REPO / "tests"))
from acceptance.shared import SKIP_EXIT  # noqa: E402

# The gate runs this file under shared.PYTHON, which need not have vulture; the
# helper's own interpreter is the venv python, so re-run under that one.
if importlib.util.find_spec("vulture") is None:
    venv_py = next((p for p in (REPO / ".venv" / "Scripts" / "python.exe", REPO / ".venv" / "bin" / "python")
                    if p.exists() and str(p) != sys.executable), None)
    if venv_py is None:
        print("vulture is not installed and no .venv python was found: dead_code_leads NOT verified")
        sys.exit(SKIP_EXIT)
    sys.exit(subprocess.run([str(venv_py), __file__]).returncode)

_h = CheckHarness()
check = _h.check

PROD = """\
import os
from fastapi import FastAPI

app = FastAPI()


@app.get("/x")
def route_handler():
    return 1


def dead_fn(unused_arg):
    leftover = 1
    return 2


class DeadCls:
    def dead_method(self):
        pass

    @property
    def dead_prop(self):
        return 1

    def __repr__(self):
        return "x"

    def do_POST(self):
        pass


def whitelisted_fn():
    pass


def cfg_ignored():
    pass


def only_tests_call():
    pass


def dispatched():
    pass


def live():
    return dispatched.__name__


live()
"""
TEST = """\
from prod import only_tests_call


def test_something():
    only_tests_call()


def dead_test_helper():
    pass
"""


def make_repo(root: Path, files: Dict[str, str]) -> Path:
    root.mkdir(parents=True)
    run_git(["-C", str(root), "init", "-q"], check=True)
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8", newline="\n")
    run_git(["-C", str(root), "add", "-A"], check=True)
    return root


def names(verdict: Dict) -> Dict[str, bool]:
    return {h["name"]: h["test_only"] for h in verdict["hits"]}


tmp = Path(tempfile.mkdtemp(prefix="dead_code_leads_"))
try:
    repo = make_repo(tmp / "r1", {
        "prod.py": PROD,
        "tests/test_prod.py": TEST,
        "vulture_whitelist.py": "whitelisted_fn\n",
        "pyproject.toml": '[tool.vulture]\nignore_names = ["cfg_ignored"]\n',
        "launch.bat": "python -c \"import prod; prod.dispatched()\"\n",
    })
    v = dcl.leads(repo)
    got = names(v)
    check(v["state"] == "ok" and v["floor"] == 60, f"a normal run is ok at floor 60 -- {v['state']} {v['reason']}")
    check({"dead_fn", "DeadCls", "dead_method", "dead_prop"} <= set(got),
          f"a dead function, class, method and property are all reported -- {sorted(got)}")
    check(not {"route_handler", "do_POST", "__repr__", "whitelisted_fn", "cfg_ignored", "live",
               "test_something", "dead_test_helper"} & set(got),
          f"a route handler, protocol override, dunder, whitelisted name, [tool.vulture] ignore, "
          f"live symbol and anything defined in a test file are not reported -- {sorted(got)}")
    check(not {"os", "unused_arg", "leftover"} & set(got) and all(h["kind"] in dcl.KEPT_KINDS for h in v["hits"]),
          "imports, arguments and variables are filtered out so the ratchet and ruff never double-report")
    check(got.get("only_tests_call") is True and got.get("dead_fn") is False,
          f"a symbol only a test calls is tagged test_only; a dead one is not -- {got}")
    check(v["hit_count"] == len(v["hits"]) and v["ruff"] is None, "hit_count matches hits; ruff is off by default")

    # The floor regression: vulture scores these kinds at 60, so the issue's original floor of 80 reports none.
    src = [str(repo / "prod.py")]
    r80 = subprocess.run([sys.executable, "-m", "vulture", *src, "--min-confidence", "80"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("dead_fn" not in r80.stdout and "DeadCls" not in r80.stdout and dcl.CONFIDENCE_FLOOR == 60,
          "pinned at 60: a floor-80 vulture run reports no dead function or class")

    # not-confirmed: every way the run can fail to establish a fact is its own state with a reason.
    probes = {
        "vulture missing": [sys.executable, "-m", "no_such_vulture_module"],
        "vulture crash": [sys.executable, "-c", "import sys; sys.exit(2)"],
        "garbage output": [sys.executable, "-c", "print('not a vulture line')"],
        "exit 3 with nothing parsed": [sys.executable, "-c", "import sys; sys.exit(3)"],
    }
    for label, cmd in probes.items():
        bad = dcl.leads(repo, vulture_cmd=cmd)
        check(bad["state"] == "not-confirmed" and bad["reason"] and bad["hits"] == [],
              f"{label} is not-confirmed with a reason, never ok with zero hits -- {bad['state']} {bad['reason']}")
    saved = dcl.VULTURE_TIMEOUT_S
    dcl.VULTURE_TIMEOUT_S = 1
    try:
        slow = dcl.leads(repo, vulture_cmd=[sys.executable, "-c", "import time; time.sleep(30)"])
    finally:
        dcl.VULTURE_TIMEOUT_S = saved
    check(slow["state"] == "not-confirmed" and "timed out" in slow["reason"], f"a hung vulture is not-confirmed -- {slow['reason']}")
    notgit = tmp / "plain"
    notgit.mkdir()
    ng = dcl.leads(notgit)
    check(ng["state"] == "not-confirmed" and "git ls-files" in ng["reason"],
          f"an unreadable tracked-file list is not-confirmed -- {ng['reason']}")
    check(dcl.main(["leads", str(notgit)]) == 3 and dcl.main(["leads", str(repo)]) == 0,
          "the CLI exits 3 for not-confirmed and 0 for ok")

    empty = make_repo(tmp / "r2", {"README.md": "no python here\n"})
    ne = dcl.leads(empty)
    check(ne["state"] == "not-applicable", f"no tracked Python is not-applicable, not ok -- {ne['state']}")

    # ruff leads: only for a repo outside the static ratchet.
    plain = make_repo(tmp / "r3", {"m.py": "import os\n\ndef f():\n    x = 1\n    return 2\n\nf()\n"})
    rl = dcl.leads(plain, with_ruff=True)["ruff"]
    check(rl and rl["state"] == "ok" and {h["code"] for h in rl["hits"]} == {"F401", "F841"},
          f"--ruff reports unused imports and variables outside the ratchet -- {rl}")
    # The gate is read from origin/<default> (tests/acceptance/test_gate_enforced.py covers the skip);
    # a ratchet enabled only in a working tree with no origin is an unknown gate, so ruff still runs.
    ratchet = make_repo(tmp / "r4", {"m.py": "import os\n", ".fleet.toml": "[static_ratchet]\nenabled = true\n"})
    rv = dcl.leads(ratchet, with_ruff=True)
    check(rv["gate"]["state"] == "unknown" and rv["ruff"]["state"] == "ok" and rv["ruff"]["hits"],
          f"--ruff runs when the gate cannot be read from origin -- {rv['gate']} {rv['ruff']}")
    check(dcl._first_line("") == "" and dcl.is_test_path("tests/x.py") and dcl.is_test_path("pkg/test_a.py")
          and dcl.is_test_path("conftest.py") and not dcl.is_test_path("pkg/contest.py"),
          "test-path detection matches tests/, test_*.py and conftest.py only")

    # refs: the caller search sees non-Python callers and string dispatch, and skips the definition line.
    rr = dcl.refs(repo, ["dispatched", "dead_fn", "only_tests_call"])
    by = rr["names"]
    check(rr["state"] == "ok", f"refs on a readable repo is ok -- {rr['reason']}")
    check(by["dispatched"]["refs"] >= 2 and "launch.bat:1" in by["dispatched"]["locations"],
          f"a name used only from a .bat and a string dispatch still counts as referenced -- {by['dispatched']}")
    check(by["dead_fn"]["refs"] == 0 and by["dead_fn"]["files"] == 0, f"a dead name has zero refs -- {by['dead_fn']}")
    check(by["only_tests_call"]["test_only"] is True and by["dispatched"]["test_only"] is False,
          "refs flags a name referenced only from test files")
    (repo / "launch.bat").unlink()
    ur = dcl.refs(repo, ["dead_fn"])
    check(ur["state"] == "not-confirmed" and "unreadable" in ur["reason"],
          f"a tracked file that cannot be read makes refs not-confirmed, not a zero -- {ur['reason']}")
    check(dcl.main(["refs", str(repo), "dead_fn"]) == 3, "the refs CLI exits 3 on not-confirmed")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_dead_code_leads")
