"""The time budget and the growth baseline (step 3): the `/e2e` 6b trigger.

Part of the `e2e_value` package; see its `__init__` for the reports and their shapes.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import fleet_toml
import git_run

from .sources import e2e_table, GIT_TIMEOUT_S, load_state, repo_slug, _gather
from .measures import is_e2e

__all__ = [
    "GROWTH_THRESHOLD",
    "time_budget_limit",
    "browser_leg_s",
    "time_budget",
    "audit_record_path",
    "read_audit_record",
    "write_audit_record",
    "growth",
    "audit_trigger",
]


# ---- time budget and the growth baseline (step 3) ------------------------------------------------

GROWTH_THRESHOLD = 10


def time_budget_limit(fleet_toml_text: Optional[str]) -> Tuple[Optional[int], str]:
    """`(limit_s, note)` from `.fleet.toml` `[e2e] time_budget_s`: a positive int, else None.

    Same validation as the node budget: a bool, a string, 0 or a negative is
    ignored with a note, so a typo can never silently raise the bar.
    """
    raw = e2e_table(fleet_toml_text).get("time_budget_s")
    if raw is None:
        return None, "no [e2e] time_budget_s declared"
    limit = fleet_toml.positive_int(raw)
    if limit is None:
        return None, f"invalid [e2e] time_budget_s {raw!r} ignored"
    return limit, "[e2e] time_budget_s"


def browser_leg_s(run: Dict[str, object], test_dirs: Sequence[str]) -> Optional[float]:
    """Wall seconds of the phases that ran e2e nodes (a parallel pass and a serial pass both count)."""
    node_phase: Dict[str, int] = run.get("node_phase") or {}  # type: ignore[assignment]
    phases: List[Dict[str, object]] = run.get("phases") or []  # type: ignore[assignment]
    idx = {node_phase[n] for n in run.get("nodes") or {} if is_e2e(n, test_dirs) and n in node_phase}  # type: ignore[union-attr]
    walls = [phases[i].get("wall_s") for i in sorted(idx)]
    if not idx or any(w is None for w in walls):
        return None
    return round(sum(float(w) for w in walls), 1)  # type: ignore[arg-type]


def time_budget(repo_root: Path, test_dirs: Sequence[str], log: Optional[Path] = None) -> Dict[str, object]:
    """`within` / `over` / `unknown` / `undeclared` for the browser leg of the latest quiet full-tier run.

    A run the log records as routed below `full` is not the suite; a run that
    overlapped another checkout's run is loaded. Neither gives a verdict, and
    neither does a missing log: all three are `unknown`, never `within`.
    """
    limit, note = time_budget_limit(fleet_toml.read_text(repo_root))
    if limit is None:
        return {"verdict": "undeclared", "seconds": None, "limit": None, "reason": note}
    g = _gather(repo_root, log)
    if "error" in g:
        return {"verdict": "unknown", "seconds": None, "limit": limit, "reason": str(g["error"])}
    logs: List[Tuple[Path, List[Dict[str, object]]]] = g["logs"]  # type: ignore[assignment]
    full = [(p, r) for p, runs in logs for r in runs
            if r["complete"] and r.get("routed_tier") in (None, "full") and isinstance(r.get("finished"), _dt.datetime)]
    if not full:
        return {"verdict": "unknown", "seconds": None, "limit": limit, "reason": "no completed full-tier run with a run window"}
    path, run = max(full, key=lambda pr: pr[1]["finished"])  # type: ignore[arg-type,return-value]
    load = load_state(run, [(p, [r for r in runs if r is not run]) for p, runs in logs])
    seconds = browser_leg_s(run, test_dirs)
    when = run["started"].isoformat() if run.get("started") else "?"  # type: ignore[union-attr]
    if load["state"] != "quiet":
        return {"verdict": "unknown", "seconds": seconds, "limit": limit, "reason": f"the latest full run ({when}) was {load['state']}"}
    if seconds is None:
        return {"verdict": "unknown", "seconds": None, "limit": limit, "reason": f"no e2e phase wall time in the run of {when}"}
    return {"verdict": "over" if seconds > limit else "within", "seconds": seconds, "limit": limit,
            "reason": f"browser leg {seconds:.0f} s on the quiet full run of {when} vs {limit} s ({note}); {path}"}


def audit_record_path(repo_root: Path) -> Path:
    """Where the last audit's test count lives: machine-local hooks state, one file per repo."""
    from hooks_state import state_dir
    name = (repo_slug(repo_root) or repo_root.resolve().name).replace("/", "-")
    return state_dir() / "e2e-audit" / f"{name}.json"


def read_audit_record(repo_root: Path) -> Optional[Dict[str, object]]:
    p = audit_record_path(repo_root)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("raw_tests"), int) else None


def write_audit_record(repo_root: Path, raw_tests: int, node_count: Optional[int]) -> Path:
    p = audit_record_path(repo_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    res = git_run.run_git(["-C", str(repo_root), "rev-parse", "--short", "HEAD"], timeout=GIT_TIMEOUT_S)
    rec = {"raw_tests": raw_tests, "node_count": node_count,
           "date": _dt.datetime.now(_dt.timezone.utc).date().isoformat(),
           "sha": res.stdout.strip() if res.returncode == 0 else None}
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec), encoding="utf-8")
    tmp.replace(p)
    return p


def growth(raw_tests: int, record: Optional[Dict[str, object]], threshold: int = GROWTH_THRESHOLD) -> Dict[str, object]:
    """Test functions gained since the last audit; `none-recorded` before the first one."""
    if record is None:
        return {"state": "none-recorded", "delta": None, "since": None, "trigger": False}
    delta = raw_tests - int(record["raw_tests"])  # type: ignore[call-overload]
    return {"state": "measured", "delta": delta, "since": record.get("date"), "trigger": delta >= threshold}


def audit_trigger(node_verdict: str, time_verdict: str, grow: Dict[str, object]) -> Tuple[str, str]:
    """`(yes|no|unknown, reason)`: whether `/e2e` 6b should run `/e2e-audit budget` (fleet-config#1018).

    Any one of: over the node budget, over the time budget, or ~10 new test
    functions since the last audit. An unmeasured leg with nothing else firing
    is `unknown`, never `no`. The open-issue check stays with the caller.
    """
    fired = []
    if node_verdict == "over":
        fired.append("over the node budget")
    if time_verdict == "over":
        fired.append("over the time budget")
    if grow.get("trigger"):
        fired.append(f"{format(int(grow['delta']), '+d')} test functions since the audit of {grow['since']}")
    if fired:
        return "yes", "; ".join(fired)
    unknown = [n for n, v in (("nodes", node_verdict), ("time", time_verdict)) if v in ("unmeasured", "unknown")]
    if unknown:
        return "unknown", f"{' and '.join(unknown)} not measured; nothing else fired"
    if grow.get("state") == "none-recorded":
        return "no", "within every declared budget; no growth baseline yet (an audit's `record` sets it)"
    return "no", f"within every declared budget; {format(int(grow['delta']), '+d')} test functions since {grow['since']}"
