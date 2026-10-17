"""Fixtures shared by the two design-review suites (fleet-config#1481).

`tests/test_design_review.py` is the pure-logic leg; `tests/test_design_review_browser.py` is the
browser leg, which the gate routes to only when the design-review surface changed. Both read the
same rubric, fixture specs and fixture metrics, so they load them from here once.
"""
from __future__ import annotations

import json
import socket
import sys
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))

import design_review as dr  # noqa: E402
from design_review import evaluate as ev, rubric as rb  # noqa: E402

FIX = REPO / "tests" / "fixtures" / "design_review"
RUBRIC = REPO / "design.rubric.toml"

rubric = dr.load_rubric(RUBRIC)

# What a rule lookup falls back to when the rubric no longer carries the rule.
MISSING = {"status": "missing", "severity": None, "category": None, "owner": None, "evidence": [{"items": None}], "measured": None, "reason": "rule COMP-18 not in the rubric"}
L10_MISSING = dict(MISSING, reason="rule LAYOUT-10 not in the rubric")


def specs(name: str) -> dict:
    return rb.load_specs(FIX / f"spec_{name}.md", FIX / f"spec_{name}.md")


def doc(name: str) -> dict:
    return json.loads((FIX / f"metrics_{name}.json").read_text(encoding="utf-8"))


def c18_of(metrics: dict) -> dict:
    return next((r for r in ev.evaluate(metrics, rubric, specs("compliant"))["rules"] if r["id"] == "COMP-18"), MISSING)


def port_open(url: str) -> bool:
    p = urlsplit(url)
    try:
        with socket.create_connection((p.hostname, p.port), timeout=1):
            return True
    except OSError:
        return False
