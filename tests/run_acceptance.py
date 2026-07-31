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
scanner (`spawn_scanner`), the ~40 substantive per-hook unit-check functions
(`unit_checks`), and the standalone pure-logic test-file dispatch layer
(`standalone_dispatch`). Shared plumbing (REPO/HOOKS/PYTHON resolution,
`run()`/`assert_exit()`, `_Checker`, `_subprocess_unit_check`) lives in
`tests/acceptance/shared.py`, imported by every module above.
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
    _config_map_check,
    _fleet_toml_check,
    _mermaid_check,
    _unattended_worktree_mandate_check,
    _settings_template_sync_check,
    _system_map_coverage_check,
    _system_map_whatchanged_check,
)
from acceptance.shared import _subprocess_unit_check  # noqa: E402
from acceptance.spawn_scanner import _no_window_unit_check  # noqa: E402
from acceptance.standalone_dispatch import _STANDALONE_UNIT_CHECKS  # noqa: E402
from acceptance.unit_checks import (  # noqa: E402
    _bash_cmdexe_syntax_guard_unit_checks,
    _block_askuserquestion_chief_unit_checks,
    _branch_before_edit_guard_unit_checks,
    _chief_handover_sessionstart_unit_checks,
    _codex_hooks_config_check,
    _context_filter_unit_checks,
    _conversation_capture_unit_checks,
    _conversation_index_unit_checks,
    _gh_body_file_guard_unit_checks,
    _learning_log_unit_checks,
    _lib_detect_project_unit_checks,
    _notify_board_link_unit_checks,
    _notify_chief_routing_unit_checks,
    _notify_classify_unit_checks,
    _notify_complete_unit_checks,
    _notify_mention_unit_checks,
    _pi_usage_stats_unit_checks,
    _restart_webapp_unit_checks,
    _session_state_agent_adapter_unit_checks,
    _session_state_unit_checks,
    _slack_notify_unit_checks,
    _slack_routing_unit_checks,
    _tier23_hooks_unit_checks,
    _work_summary_unit_checks,
)


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

    # ---- hook-payload acceptance matrix + foreign-harness (Grok) parity ----
    run_unit(run_hook_matrix)

    # ---- context filter hook JSON + fixture eval ----
    run_unit(_context_filter_unit_checks)

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

    # ---- session_state board-row persistence (fleet-config#91) ----
    run_unit(_session_state_unit_checks)

    # ---- session_state_codex / session_state_pi adapters (fleet-config#349) ----
    run_unit(_session_state_agent_adapter_unit_checks)

    # ---- notify_complete deterministic message assembly + resolver ----
    run_unit(_notify_complete_unit_checks)

    # ---- work_summary roll-up block + per-file table (pure, no gh) ----
    run_unit(_work_summary_unit_checks)

    # ---- Pi usage collector parses model/provider/token telemetry (pure) ----
    run_unit(_pi_usage_stats_unit_checks)

    # ---- slack category -> channel routing (issue #139) ----
    run_unit(_slack_routing_unit_checks)

    # ---- conversation_capture session-dedup logic ----
    run_unit(_conversation_capture_unit_checks)

    # ---- conversation capture/index config-driven routing + indexing ----
    run_unit(_conversation_index_unit_checks)

    # ---- restart_and_verify_webapp restart-strategy + recovery hint ----
    run_unit(_restart_webapp_unit_checks)

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
        f, t = _subprocess_unit_check(_label, _test_file)
        failures += f
        total_checks += t

    # ---- learning-log report.py pure helpers (.claude/skills/learning-log) ----
    run_unit(_learning_log_unit_checks)

    # ---- system-map: fleet ↔ data ↔ doc coverage (architecture/) ----
    run_unit(_system_map_coverage_check)

    # ---- system-map: per-repo .fleet.toml aggregation + anti-staleness ----
    run_unit(_fleet_toml_check)

    # ---- system-map: Mermaid companion render (render_mermaid.py) freshness ----
    run_unit(_mermaid_check)
    run_unit(_unattended_worktree_mandate_check)

    # ---- system-map: week-over-week 'what changed' diff (whatchanged.py) ----
    run_unit(_system_map_whatchanged_check)

    # ---- config-map: introspected config.data.js freshness + whatchanged ----
    run_unit(_config_map_check)

    # ---- Codex hook wiring: direct Python commands with bounded timeouts ----
    run_unit(_codex_hooks_config_check)

    # ---- settings: live ~/.claude/settings.json ⊇ template hook wiring ----
    # Not run_unit: this check has a third state (skipped, when the live file
    # is absent) that must never fold into total_checks/failures (fleet-config#501).
    _stsc_f, _stsc_t, _stsc_s = _settings_template_sync_check()
    failures += _stsc_f
    total_checks += _stsc_t
    skipped_checks += _stsc_s

    # ---- Windows console suppression on every runtime spawn (#399 / #412) ----
    run_unit(_no_window_unit_check)

    print()
    print(f"Total: {total_checks} | Failed: {failures} | Skipped: {skipped_checks}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
