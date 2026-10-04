"""Skill-eval fold-in: the weekly eval job's aggregates, read into the digest and R-44 (fleet-config#1131).

Part of the `prompt_audit` package (fleet-config#931); see `__init__.py`.

`eval_rotation.py` runs on its own schedule and writes timestamped aggregates
(plus `latest.json`) under `~/.claude/prompt-audit/evals/`. This module reads the
newest two and reports, per skill and model, the pass count and its delta. It
turns a case that passed last time and fails now into an R-44 finding:
`violation` on a row the job marked `scored`, `consider` on an `advisory` one;
an `extra` model is reported but never a finding. Which tier is which is the
job's decision, recorded per row, so no model name lives here. A missing,
unreadable or stale aggregate is `not-checked`, never a pass. A red eval job
changes nothing about this run's own delivery: the eval section is reported,
never gated on.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .common import HOME_REPO
from .state import state_path

RUN_NAME = re.compile(r"^\d{8}T\d{4,6}\.json$")
STALE_DAYS = 8          # the job runs weekly; one missed week reads as not-checked
EVAL_RULE = "R-44"


def evals_dir() -> Path:
    return state_path().parent / "evals"


def _load(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("rows"), list) else None


def latest_two(root: Optional[Path] = None) -> Tuple[Optional[dict], Optional[dict]]:
    """(newest, the one before it) of the timestamped aggregates; `latest.json` is a copy, not a run."""
    root = root or evals_dir()
    # Only the job's timestamped aggregates (`20261004T210000.json`); `latest.json` and `rotation.json` are not runs.
    runs = sorted(p for p in root.glob("*.json") if RUN_NAME.match(p.name)) if root.is_dir() else []
    newest = _load(runs[-1]) if runs else None
    previous = _load(runs[-2]) if len(runs) > 1 else None
    return newest, previous


def _status(rows: List[dict]) -> Dict[Tuple[str, str, str], str]:
    return {(r.get("skill"), r.get("tier"), r.get("case")): r.get("status") for r in rows}


def _weights(rows: List[dict]) -> Dict[Tuple[str, str, str], str]:
    return {(r.get("skill"), r.get("tier"), r.get("case")): r.get("weight", "extra") for r in rows}


def fold(newest: Optional[dict], previous: Optional[dict], today: Optional[dt.date] = None) -> dict:
    """The digest's eval section and R-44's findings, from the newest aggregate and the one before it."""
    today = today or dt.date.today()
    if newest is None:
        return {"status": "not-checked", "reason": "no eval aggregate", "scores": [], "findings": []}
    try:
        started = dt.datetime.fromisoformat(newest["started"]).date()
    except (KeyError, TypeError, ValueError):
        return {"status": "not-checked", "reason": "aggregate has no start date", "scores": [], "findings": []}
    if (today - started).days > STALE_DAYS:
        return {"status": "not-checked", "reason": f"newest aggregate is from {started.isoformat()}",
                "scores": [], "findings": []}
    now, before = _status(newest["rows"]), _status((previous or {}).get("rows", []))
    weight = _weights(newest["rows"])
    paths = newest.get("paths", {})
    scores = []
    for skill, tier in sorted({(k[0], k[1]) for k in now}):
        mine = [s for k, s in now.items() if k[:2] == (skill, tier)]
        prev = [s for k, s in before.items() if k[:2] == (skill, tier)]
        passed = sum(1 for s in mine if s == "pass")
        delta = passed - sum(1 for s in prev if s == "pass") if prev else None
        unknown = sum(1 for s in mine if s in ("error", "not-run"))
        scores.append({"skill": skill, "tier": tier, "passed": passed, "total": len(mine), "unknown": unknown,
                       "delta": delta})
    findings = []
    for (skill, tier, case), status in sorted(now.items()):
        if status != "fail" or before.get((skill, tier, case)) != "pass":
            continue
        w = weight.get((skill, tier, case))
        if w not in ("scored", "advisory"):
            continue  # an extra model is reported, never a finding
        findings.append({"path": paths.get(skill, f"{HOME_REPO}/skills/{skill}/SKILL.md"), "rule": EVAL_RULE,
                         "verdict": "violation" if w == "scored" else "consider", "line": None, "text": "",
                         "note": f"eval case {case} passed last run and fails now on {tier}"})
    return {"status": "checked", "started": newest["started"], "scores": scores, "findings": findings}


def digest_lines(evals: Optional[dict]) -> List[str]:
    if not isinstance(evals, dict) or evals.get("status") != "checked":
        reason = (evals or {}).get("reason", "the fold-in did not run") if isinstance(evals, dict) else "the fold-in did not run"
        return [f"**Evals:** evals: not-checked ({reason})"]
    out = [f"**Evals:** aggregate of {evals.get('started', '?')}, {len(evals['findings'])} R-44 finding(s)"]
    for s in evals["scores"]:
        delta = "new" if s["delta"] is None else f"{s['delta']:+d}"
        unknown = f", {s['unknown']} not established" if s["unknown"] else ""
        out.append(f"- `{s['skill']}` {s['tier']}: {s['passed']}/{s['total']} pass ({delta}){unknown}")
    return out
