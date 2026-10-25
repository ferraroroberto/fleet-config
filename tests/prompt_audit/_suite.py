"""Shared preamble of the prompt-audit suites: import paths, the loaded rule set, the planted and clean fixtures, and the entry helpers.

Imported first by every `test_*.py` here (the script's own folder is on `sys.path`). Every part drives
the deterministic half of /prompt-audit against synthetic fixtures in a temp dir; no real repo,
ledger issue or ~/.claude/prompt-audit is touched.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SKILL = REPO / ".claude" / "skills" / "prompt-audit"
sys.path.insert(0, str(SKILL))
import prompt_audit as pa  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

CFG = pa.load_toml()
AUD = CFG["audiences"]
RULES = pa.parse_rules(pa.RULES_MD.read_text(encoding="utf-8"))

PLANTED = """# Planted

- CRITICAL: You MUST run the gate.
- If in doubt, use the search tool.
- Always double-check your answer.
- Summarize progress every 3 tool calls.
- Hold all findings for the final response.
- Do not use markdown in replies.
- Think step by step before answering.
- Never think about unrelated files.
- Only report high-severity issues.
- Use a prefill for JSON output.
- Before August 2025 use the old endpoint.
- Outline a plan before editing.
- Always commit to the branch.
- Never commit to the branch.
- Run `MUST_FLAG` here.

```bash
double-check CRITICAL every 3 tool calls
```

Route Claude requests through the hub.
"""

CLEAN = """# Clean

Run the verification gate before pushing, because CI only checks formatting.
Keep changes to what the issue asks for.
"""

GLOBAL = """# Global

Keep commits small.

### Spawning *(Claude Code only — skip on other agents)*

Never spawn more than three workers.

### Shared

Never push to main.
"""

SKILL_GOOD = """---
name: good
description: Audits the thing and reports drift. E.g. "/good", "check the thing".
---

# good

Body.
"""

SKILL_BAD = """---
name: bad
description: Use this when you want your thing checked. E.g. "can you check my thing".
---

# bad
"""


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def entry_for(text: str, kind: str = "claude-md", audience: str = pa.NEUTRAL, key: str = "x/CLAUDE.md") -> pa.Entry:
    return pa.Entry(key=key, path=Path(key), repo=key.split("/")[0], kind=kind, audience=audience,
                    data=text.encode("utf-8"))


def by_rule(res: pa.LintResult, rule: str) -> list:
    return [h for h in res.hits if h.rule == rule]


rub = "a" * 64


run = {
    "date": "2026-09-13", "rubric": rub, "scan_ran": True,
    "sources": ["VERDICT=unchanged|id=s1|sha=x|marker=none|reason=identical",
                "VERDICT=not-checked|id=s2|sha=unmeasured|marker=unmeasured|reason=fetch failed"],
    "plan": ["PLAN=r/a.md|action=scan|reason=new|sha=1", "PLAN=r/b.md|action=scan|reason=new|sha=2",
             "PLAN=r/c.md|action=skip|reason=unchanged|sha=3", "PLAN=r/d.md|action=scan|reason=new|sha=4"],
    "judgments": {"r/a.md": [{"rule": "R-01", "verdict": "violation", "line": 3, "text": "MUST", "note": "caps"},
                             {"rule": "R-18", "verdict": "compliant"}],
                  "r/b.md": [], "r/d.md": None},
}


__all__ = ['AUD', 'CFG', 'CLEAN', 'GLOBAL', 'PLANTED', 'REPO', 'RULES', 'SKILL', 'SKILL_BAD', 'SKILL_GOOD', '_h', 'by_rule', 'check', 'entry_for', 'pa', 'rub', 'run', 'write']
