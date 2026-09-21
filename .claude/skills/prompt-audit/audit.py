"""Deterministic half of `/prompt-audit` (fleet-config#831, #832).

The skill audits every fleet instruction file against the vendors' current
prompting guidance. "The helper measures, the orchestrator judges": this module
does every exact, reproducible step — hashing fetched guides against their
baselines, enumerating the scan surface, counting lint hits, collapsing findings
shared with the scaffolding master, the skip-unchanged ledger, cadence state, and
rendering the digest and its chat line — so none of those numbers is ever
invented by a model. It never fetches anything (the orchestrator's own tool does)
and never edits an instruction file.

Vendor and audience vocabulary is data (`sources.toml` `[audiences.*]`); the
rule-set is `rules.md`, whose tags decide whether a hit is a `violation` or only
`consider` for a given file audience.

Subcommands (every line-oriented output uses the fleet `KEY=value|...` protocol):

  sources                         SOURCE=<id>|url=...|baseline=<sha>|marker=...
  diff-source --id I --file F [--final-url U]
                                  VERDICT=unchanged|changed|new-guide|not-checked|...
  inventory [--only REPO]         FILE=<path>|audience=...|kind=...|sha=...|lines=N
                                  SECTION=<path>#L<a>-L<b>|audience=...|heading=...
  lint (--file PATH | --all) [--only REPO] [--changed-only] [--rescan-all] [--detail]
                                  HITS=<path>|...|hits=R-01:2,R-13:4|... (+ HIT= lines)
  dedup --findings JSON           annotated findings JSON + DEDUP= summary
  state show | state mark (--source ID --verdict V | --scan) [--date D]
  ledger plan [--only REPO] [--rescan-all]
  ledger write --run JSON [--date D] [--dry-run]
  ledger comment --body-file FILE
  digest --run JSON               the run digest markdown
  ping --run JSON --comment-url U one ASCII chat line for a delivered run (exit 2 on a dry run)
  drift --run JSON [--dry-run] [--date D]
                                  upsert one `audit: prompt-drift findings` issue per repo
                                  DRIFT=<repo>|issue=...|tier=easy|hard|none|open=N|new=...

State lives in `~/.claude/prompt-audit/state.json` (override the directory with
`PROMPT_AUDIT_STATE_DIR`); a corrupt file degrades to everything due. The ledger
is the audit-managed `kind=prompt-audit` issue on fleet-config.

The code lives in the `prompt_audit/` package beside this file, one module per
concern (layout in its `__init__.py`, fleet-config#931); this file is only the
entry point the skill invokes.

stdlib + `skills/_lib` only. Run with the repo venv from the fleet-config root:
    E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/prompt-audit/audit.py inventory
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prompt_audit import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
