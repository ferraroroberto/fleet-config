"""Drive each hook with a sample payload and assert the expected exit code.

Run from the repo root (invoke the resolved Python path directly — a bare
``py``/``python`` is not reliably on ``PATH`` on this machine):
    E:/automation/fleet-config/.venv/Scripts/python.exe tests/run_acceptance.py

Exit 0 if all cases pass, 1 otherwise. Prints a single line per case.

This is the thin dispatcher (fleet-config#502): it sums every check module
under `tests/acceptance/` and prints one summary line. Each module owns one
concern the pre-split 3287-line file used to mix together — the hook-payload
acceptance matrix + foreign-harness parity (`hook_matrix`), architecture/
fleet-map freshness guards (`architecture_guards`), the static AST spawn-flag
scanner (`spawn_scanner`), the hooks/ <-> skills/_lib tree-independence gate
(`tree_boundary`), and the standalone pure-logic test-file dispatch layer
(`standalone_dispatch`).

The substantive per-hook unit-check functions are the `checks_*` modules, one
per domain (fleet-config#680, finishing the #502 split that left them piled in
a single 2681-line `unit_checks.py`): `checks_context_filter`, `checks_notify`,
`checks_guards`, `checks_session_state`, `checks_cross_agent`,
`checks_capture`, `checks_skill_helpers`. Adding a check means one new function
in the module that owns its domain, plus one `run_unit()` line below.

Shared plumbing (REPO/HOOKS/PYTHON resolution, `run()`/`assert_exit()`,
`_Checker`, `_subprocess_unit_check`) lives in `tests/acceptance/shared.py`,
imported by every module above.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from acceptance.hook_matrix import run_hook_matrix  # noqa: E402
from acceptance.architecture_guards import (  # noqa: E402
    _advisory_semantics_check,
    _config_map_check,
    _fleet_membership_drift_check,
    _fleet_toml_check,
    _mermaid_check,
    _readme_layout_check,
    _unattended_worktree_mandate_check,
    _settings_template_sync_check,
    _system_map_coverage_check,
    _system_map_whatchanged_check,
)
from acceptance.checks_capture import (  # noqa: E402
    _conversation_capture_unit_checks,
    _conversation_index_unit_checks,
    _work_summary_unit_checks,
)
from acceptance.checks_context_filter import _context_filter_unit_checks  # noqa: E402
from acceptance.checks_cross_agent import (  # noqa: E402
    _codex_hooks_config_check,
    _session_state_agent_adapter_unit_checks,
)
from acceptance.checks_guards import (  # noqa: E402
    _bash_cmdexe_syntax_guard_unit_checks,
    _block_askuserquestion_chief_unit_checks,
    _branch_before_edit_guard_unit_checks,
    _gh_body_file_guard_unit_checks,
    _safe_kill_force_push_unit_checks,
    _tier23_hooks_unit_checks,
)
from acceptance.checks_notify import (  # noqa: E402
    _notify_board_link_unit_checks,
    _notify_chief_routing_unit_checks,
    _notify_classify_unit_checks,
    _notify_complete_unit_checks,
    _notify_mention_unit_checks,
    _slack_notify_unit_checks,
    _slack_routing_unit_checks,
)
from acceptance.checks_session_state import (  # noqa: E402
    _chief_handover_sessionstart_unit_checks,
    _chief_steer_convention_unit_checks,
    _lib_detect_project_unit_checks,
    _session_state_unit_checks,
)
from acceptance.checks_skill_helpers import (  # noqa: E402
    _learning_log_unit_checks,
    _restart_webapp_unit_checks,
)
from acceptance.shared import _subprocess_unit_check  # noqa: E402
from acceptance.spawn_scanner import (  # noqa: E402
    _git_wrapper_unit_check,
    _no_window_unit_check,
)
from acceptance.standalone_dispatch import _STANDALONE_UNIT_CHECKS  # noqa: E402
from acceptance.tree_boundary import _hooks_tree_boundary_check  # noqa: E402


def main() -> int:
    failures = 0
    total_checks = 0
    skipped_checks = 0

    def run_unit(check_fn: Callable[[], Tuple[int, int]]) -> None:
        """Call one check function and fold its own `(failures, total)` into
        the running tally — the acceptance-matrix total is summed from real
        checks executed, never a hand-maintained constant that drifts
        silently when a check is added or removed (fleet-config#320)."""
        nonlocal failures, total_checks
        f, t = check_fn()
        failures += f
        total_checks += t

    def run_unit3(check_fn: Callable[[], Tuple[int, int, int]]) -> None:
        """`run_unit` for the checks that also report a third state — `skipped`.

        A check that *couldn't establish* a fact (no live settings.json to
        compare against) or that *may not gate this repo* (fleet-wide freshness,
        whose inputs are sibling checkouts) must land in neither Total nor
        Failed, so a run that verified less never reads identical to one that
        verified everything (fleet-config#461, #501, #562)."""
        nonlocal failures, total_checks, skipped_checks
        f, t, s = check_fn()
        failures += f
        total_checks += t
        skipped_checks += s

    # ---- hook-payload acceptance matrix + foreign-harness (Grok) parity ----
    run_unit(run_hook_matrix)

    # ---- context filter hook JSON + fixture eval ----
    # run_unit3: three of its cases probe integrations installed *outside* this
    # repo (~/.codex hooks junction, the copilot hook, the agy plugin). A
    # machine without one of them cannot establish the fact, so it reports as
    # skipped instead of folding into the pass count (fleet-config#679).
    run_unit3(_context_filter_unit_checks)

    # ---- slack_notify unit checks (pure / no network) ----
    run_unit(_slack_notify_unit_checks)

    # ---- notify_on_idle mention-construction unit checks ----
    run_unit(_notify_mention_unit_checks)

    # ---- notify_on_idle classify / session-link / idle-suppression ----
    run_unit(_notify_classify_unit_checks)

    # ---- notify_on_idle Fleet-Board deep link (fleet-config#242) ----
    run_unit(_notify_board_link_unit_checks)

    # ---- notify_on_idle chief-managed routing (fleet-config#443) ----
    run_unit(_notify_chief_routing_unit_checks)

    # ---- block_askuserquestion_chief: enforce, don't just discourage (fleet-config#463) ----
    run_unit(_block_askuserquestion_chief_unit_checks)

    # ---- _lib.detect_project: worktree-sibling cwd resolution (fleet-config#471) ----
    run_unit(_lib_detect_project_unit_checks)

    # ---- chief_handover_sessionstart pure logic + end-to-end (fleet-config#442) ----
    run_unit(_chief_handover_sessionstart_unit_checks)

    # ---- chief steer convention: retired `CHIEF - ` marker + what replaced it (fleet-config#622) ----
    run_unit(_chief_steer_convention_unit_checks)

    # ---- session_state board-row persistence (fleet-config#91) ----
    run_unit(_session_state_unit_checks)

    # ---- session_state_codex / session_state_pi adapters (fleet-config#349) ----
    run_unit(_session_state_agent_adapter_unit_checks)

    # ---- notify_complete deterministic message assembly + resolver ----
    run_unit(_notify_complete_unit_checks)

    # ---- work_summary roll-up block + per-file table (pure, no gh) ----
    run_unit(_work_summary_unit_checks)

    # ---- slack category -> channel routing (issue #139) ----
    run_unit(_slack_routing_unit_checks)

    # ---- conversation_capture session-dedup logic ----
    run_unit(_conversation_capture_unit_checks)

    # ---- conversation capture/index config-driven routing + indexing ----
    run_unit(_conversation_index_unit_checks)

    # ---- restart_and_verify_webapp restart-strategy + recovery hint ----
    run_unit(_restart_webapp_unit_checks)

    # ---- safe_kill_guard: force-push blocks on the pushed ref (#562) ----
    run_unit(_safe_kill_force_push_unit_checks)

    # ---- gh_body_file_guard: warn-only stdout assertions ----
    run_unit(_gh_body_file_guard_unit_checks)

    # ---- bash_cmdexe_syntax_guard: block + warn assertions (#264, #385) ----
    run_unit(_bash_cmdexe_syntax_guard_unit_checks)

    # ---- Tier 2/3 hooks: docs-guard env override + warn-hook stdout (issue #158) ----
    run_unit(_tier23_hooks_unit_checks)

    # ---- branch_before_edit_guard: real temp git repos/worktrees x launcher env, target-path resolution (fleet-config#464) ----
    run_unit(_branch_before_edit_guard_unit_checks)

    # ---- standalone pure-logic test-file dispatch: one row per suite in
    # acceptance/standalone_dispatch.py's table -- new suite = one row added
    # there, no new wrapper/import/registration here (fleet-config#505) ----
    for _label, _test_file, _why in _STANDALONE_UNIT_CHECKS:
        # Three-state like run_unit3: a suite that exits shared.SKIP_EXIT could
        # not establish its facts (missing toolchain) and lands in Skipped, not
        # in a bare `OK` that verified nothing (fleet-config#679).
        f, t, s = _subprocess_unit_check(_label, _test_file)
        failures += f
        total_checks += t
        skipped_checks += s

    # ---- learning-log report.py pure helpers (.claude/skills/learning-log) ----
    run_unit(_learning_log_unit_checks)

    # ---- system-map: fleet ↔ data ↔ doc coverage (architecture/) ----
    run_unit(_system_map_coverage_check)

    # ---- system-map: per-repo .fleet.toml aggregation + anti-staleness ----
    # run_unit3: only fleet-config's own card is gated here. The fleet-wide
    # freshness checks read sibling checkouts no commit in this repo controls,
    # so they report as skipped rather than failing this gate (fleet-config#562).
    run_unit3(_fleet_toml_check)
    run_unit(_advisory_semantics_check)

    # ---- projects.toml is the fleet-membership list: no repo on disk may be
    # missing from it (fleet-config#640). run_unit3: skipped, not passed, when
    # there is no fleet beside this checkout to compare against.
    run_unit3(_fleet_membership_drift_check)

    # ---- system-map: Mermaid companion render (render_mermaid.py) freshness ----
    run_unit(_mermaid_check)
    run_unit(_unattended_worktree_mandate_check)

    # ---- system-map: week-over-week 'what changed' diff (whatchanged.py) ----
    run_unit(_system_map_whatchanged_check)

    # ---- config-map: introspected config.data.js freshness + whatchanged ----
    # run_unit3: the freshness half sweeps sibling repos, so it reports as
    # skipped rather than failing this gate — same reason as #562's fleet_toml.
    run_unit3(_config_map_check)

    # ---- README Layout tree is an exhaustive inventory (fleet-config#565) ----
    run_unit(_readme_layout_check)

    # ---- Codex hook wiring: direct Python commands with bounded timeouts ----
    run_unit(_codex_hooks_config_check)

    # ---- settings: live ~/.claude/settings.json ⊇ template hook wiring ----
    # run_unit3: this check has a third state (skipped, when the live file is
    # absent) that must never fold into total_checks/failures (fleet-config#501).
    run_unit3(_settings_template_sync_check)

    # ---- Windows console suppression on every runtime spawn (#399 / #412) ----
    run_unit(_no_window_unit_check)

    # ---- every runtime `git` spawn routes through run_git (#667 / #677) ----
    run_unit(_git_wrapper_unit_check)

    # ---- hooks/ never imports across into skills/_lib (fleet-config#564) ----
    run_unit(_hooks_tree_boundary_check)

    print()
    print(f"Total: {total_checks} | Failed: {failures} | Skipped: {skipped_checks}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
