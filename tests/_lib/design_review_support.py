"""Fixtures shared by the design-review suites (fleet-config#1481).

The suites in `tests/design_review/` are the pure-logic leg; `tests/test_design_review_browser.py` is the
browser leg, which the gate routes to only when the design-review surface changed. Both read the
same rubric, fixture specs and fixture metrics, so they load them from here once. The
`test_design_judgment.py` and `test_design_report.py` CLI legs share `cli_runner` and `SPEC_ARGS`.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Callable, Tuple
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))

import design_review as dr  # noqa: E402
from design_review import evaluate as ev, rubric as rb  # noqa: E402

FIX = REPO / "tests" / "fixtures" / "design_review"
RUBRIC = REPO / "design.rubric.toml"
PKG = REPO / "skills" / "_lib" / "design_review"
# The flags every CLI leg passes to read the violating fixture spec for both themes.
SPEC_ARGS = ["--rubric", str(RUBRIC), "--spec", str(FIX / "spec_violating.md"), "--spec-dark", str(FIX / "spec_violating.md")]

rubric = dr.load_rubric(RUBRIC)
# This checkout's own design*.md, not ~/.claude (which is the primary's).
src_specs = rb.load_specs(REPO / "design.md", REPO / "design.dark.md")

# What a rule lookup falls back to when the rubric no longer carries the rule.
MISSING = {"status": "missing", "severity": None, "category": None, "owner": None, "evidence": [{"items": None}], "measured": None, "reason": "rule COMP-18 not in the rubric"}
L10_MISSING = dict(MISSING, reason="rule LAYOUT-10 not in the rubric")


def specs(name: str) -> dict:
    return rb.load_specs(FIX / f"spec_{name}.md", FIX / f"spec_{name}.md")


def doc(name: str) -> dict:
    return json.loads((FIX / f"metrics_{name}.json").read_text(encoding="utf-8"))


def c18_of(metrics: dict) -> dict:
    return next((r for r in ev.evaluate(metrics, rubric, specs("compliant"))["rules"] if r["id"] == "COMP-18"), MISSING)


light_c = specs("compliant")["light"]
out_c = ev.evaluate(doc("compliant"), rubric, specs("compliant"))
statuses_c = {r["id"]: r["status"] for r in out_c["rules"]}


def cli_runner(state: Path) -> Tuple[Callable[..., subprocess.CompletedProcess], Callable[[subprocess.CompletedProcess], dict]]:
    """`(run, kv)` for the design_review CLI, its hooks state kept in `state`.

    `run(*args)` executes the package as a subprocess; `kv(proc)` parses the `KEY=value` lines it prints.
    """
    env = {**os.environ, "CLAUDE_HOOKS_STATE_DIR": str(state)}

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(PKG), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, env=env)

    def kv(proc: subprocess.CompletedProcess) -> dict:
        return dict(l.split("=", 1) for l in proc.stdout.splitlines() if "=" in l)

    return run, kv


def comp(rule_id: str, controls_by_screen) -> dict:
    """The compliant fixture's `rule_id` result with each screen's `controls` patched by `controls_by_screen`."""
    d = doc("compliant")
    for s, patch in zip(d["screens"], controls_by_screen):
        s["metrics"]["controls"].update(patch)
    return next(r for r in ev.evaluate(d, rubric, specs("compliant"))["rules"] if r["id"] == rule_id)


def port_open(url: str) -> bool:
    p = urlsplit(url)
    try:
        with socket.create_connection((p.hostname, p.port), timeout=1):
            return True
    except OSError:
        return False
