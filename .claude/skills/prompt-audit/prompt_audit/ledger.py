"""The skip-unchanged ledger (audit-managed `kind=prompt-audit` issue).

Part of the `prompt_audit` package (fleet-config#931); see `__init__.py`.
"""

from __future__ import annotations

import json
import re
from typing import Dict, Tuple

from .common import AUDIT_ISSUE, BLOCK_MARKER, KIND, LEDGER_CAP, LEDGER_REPO, clean, run_audit_issue


# ---- ledger ---------------------------------------------------------------------

_ENTRY_RE = re.compile(r"^([^:#\s<][^:]*?):[ \t]*([0-9a-f]{12})[ \t]*$", re.MULTILINE)


def parse_ledger(body: str) -> dict:
    idx = (body or "").find(BLOCK_MARKER)
    if idx == -1:
        return {"rubric": None, "run_at": None, "files": {}}
    block = body[idx:]
    end = block.find("-->", len(BLOCK_MARKER))
    block = block[:end] if end != -1 else block
    rub = re.search(r"^rubric-sha:[ \t]*([0-9a-f]+)[ \t]*$", block, re.MULTILINE)
    at = re.search(r"^last-run-at:[ \t]*(\S+)[ \t]*$", block, re.MULTILINE)
    return {"rubric": rub.group(1) if rub else None, "run_at": at.group(1) if at else None,
            "files": {m.group(1).strip(): m.group(2) for m in _ENTRY_RE.finditer(block)}}


def plan_scan(current: Dict[str, str], ledger: dict, rubric: str,
              rescan_all: bool = False) -> Dict[str, Tuple[str, str]]:
    plan = {}
    for key, sha in sorted(current.items()):
        if sha == "unmeasured":
            plan[key] = ("unmeasured", "unreadable")
        elif rescan_all:
            plan[key] = ("scan", "rescan-all")
        elif not ledger.get("rubric"):
            plan[key] = ("scan", "no-ledger")
        elif ledger["rubric"] != rubric:
            plan[key] = ("scan", "rubric-changed")
        elif key not in ledger["files"]:
            plan[key] = ("scan", "new")
        elif ledger["files"][key] != sha:
            plan[key] = ("scan", "changed")
        else:
            plan[key] = ("skip", "unchanged")
    return plan


def merge_ledger(ledger: dict, recorded: Dict[str, str], rubric: str) -> Tuple[Dict[str, str], int]:
    """New entries; prior entries survive only under the same rubric. Returns (files, dropped)."""
    base = dict(ledger["files"]) if ledger.get("rubric") == rubric else {}
    base.update(recorded)
    keys = sorted(base)
    return {k: base[k] for k in keys[:LEDGER_CAP]}, max(0, len(keys) - LEDGER_CAP)


def render_ledger_body(files: Dict[str, str], rubric: str, run_at: str,
                       sources: Dict[str, dict], dropped: int = 0) -> str:
    rows = [f"| `{sid}` | {c.get('vendor', '')} | {c.get('role', '')} | `{c.get('baseline_sha', '')}` "
            f"| {clean(str(c.get('baseline_marker') or '—'))} | {c.get('baseline_date', '')} |"
            for sid, c in sources.items()]
    lines = [
        "Standing ledger for `/prompt-audit` (fleet-config#831) — vendor-guide baselines plus the skip-unchanged file table. "
        "Managed by `.claude/skills/prompt-audit/audit.py`; do not hand-edit the block. The last comment is the current state of the audit.",
        "",
        "## Guide baselines (`sources.toml`)",
        "",
        "| source | vendor | role | baseline sha | marker | date |",
        "|---|---|---|---|---|---|",
        *rows,
        "",
        f"## Assessed files ({len(files)}{f', {dropped} over the cap — rescanned next run' if dropped else ''})",
        "",
        BLOCK_MARKER,
        "<!--",
        f"last-run-at: {run_at}",
        f"rubric-sha: {rubric}",
        *[f"{k}: {v}" for k, v in sorted(files.items())],
        "-->",
        "",
        f"rubric-sha `{rubric[:12]}` · last run {run_at} · {len(files)} file hashes recorded (hidden block above).",
    ]
    return "\n".join(lines) + "\n"


def _audit_issue(*args: str) -> str:
    return run_audit_issue(AUDIT_ISSUE, *args)


def read_ledger_issue() -> dict:
    data = json.loads(_audit_issue("get", "--repo", LEDGER_REPO, "--kind", KIND))
    return {"number": data.get("number"), **parse_ledger(data.get("body") or "")}
