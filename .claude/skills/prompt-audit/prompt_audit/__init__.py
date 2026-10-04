"""Deterministic half of `/prompt-audit`, as a package (fleet-config#831, #832, #931).

`audit.py` beside this package is the CLI entry the skill invokes; its
docstring lists the subcommands. This was one 1462-line module carrying eleven
concerns behind a single dispatcher, the same shape `skills/_lib/design_lint`
recorded fixing (fleet-config#564), and `rules.md` grows it the same way. The
code moved verbatim; only imports changed.

Layout, in dependency order (no module imports a later one):

  common.py     paths, fleet constants, the `skills/_lib` imports, small pure helpers
  sources.py    vendor-guide freshness gate
  inventory.py  audiences, skill references + the scan-surface inventory
  rules.py      `rules.md` parsing and per-audience verdicts
  lint.py       the lint engine (R-01..R-17, R-30, R-34..R-37; assists for R-39, R-43)
  dedup.py      findings shared with the scaffolding master / lite global
  state.py      cadence state
  coverage.py   tracked-guide sections no `Source:` line cites (+ the page cache)
  leads.py      hand-fed outside claims (`leads.toml`) and their trace results
  evals.py      the skill-eval job's aggregates, folded into the digest and R-44
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
    references,
    sections,
)
from .rules import parse_rules, verdict_cap
from .leads import LeadsError, claim_id, load_leads, resolve as resolve_leads, suggestions_comment
from .leads import mark as mark_lead, digest_lines as lead_digest_lines
from .evals import digest_lines as eval_digest_lines, fold as fold_evals, latest_two
from .coverage import (
    cache_page,
    cited_sections,
    coverage,
    coverage_comment,
    digest_line,
    item_id,
    known_items,
    page_sections,
    pages_dir,
)
from .lint import (
    ASSIST_RULES,
    LINT_RULES,
    LintResult,
    mcp_vocabulary,
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
    provisional_rules,
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
