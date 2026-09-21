"""Deterministic half of `/prompt-audit`, as a package (fleet-config#831, #832, #931).

`audit.py` beside this package is the CLI entry the skill invokes; its
docstring lists the subcommands. This was one 1462-line module carrying eleven
concerns behind a single dispatcher, the same shape `skills/_lib/design_lint`
recorded fixing (fleet-config#564), and `rules.md` grows it the same way. The
code moved verbatim; only imports changed.

Layout, in dependency order (no module imports a later one):

  common.py     paths, fleet constants, the `skills/_lib` imports, small pure helpers
  sources.py    vendor-guide freshness gate
  inventory.py  audiences + the scan-surface inventory
  rules.py      `rules.md` parsing and per-audience verdicts
  lint.py       the R-01..R-17 lint engine
  dedup.py      findings shared with the scaffolding master / lite global
  state.py      cadence state
  ledger.py     the skip-unchanged ledger issue
  digest.py     run digest + chat ping
  drift.py      the prompt-drift cleanup-issue merge/backlog engine
  cli.py        argparse + the `cmd_*` handlers

The names below are the public surface `tests/test_prompt_audit.py` drives.
"""

from __future__ import annotations

from .common import (
    NEUTRAL,
    RULES_MD,
    fleet_repos,
    load_toml,
    rules_rubric,
    sha12,
)
from .sources import diff_source, extract_marker
from .inventory import (
    Entry,
    inventory,
    sections,
)
from .rules import parse_rules, verdict_cap
from .lint import (
    LINT_RULES,
    LintResult,
    hits_line,
    lint_entry,
)
from .dedup import dedup
from .state import load_state, source_due
from .ledger import (
    merge_ledger,
    parse_ledger,
    plan_scan,
    render_ledger_body,
)
from .digest import (
    partition_run,
    render_digest,
    render_ping,
)
from .drift import (
    DRIFT_KIND,
    drift_items,
    in_protected_span,
    merge_drift,
    parse_drift_body,
    render_drift_item,
)
from .cli import main
