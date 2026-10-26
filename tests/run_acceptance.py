"""Drive each hook with a sample payload and assert the expected exit code.

Run from the repo root (invoke the resolved Python path directly — a bare
``py``/``python`` is not reliably on ``PATH`` on this machine):
    E:/automation/fleet-config/.venv/Scripts/python.exe tests/run_acceptance.py

Exit 0 if all cases pass, 1 otherwise. Prints a single line per case.

The whole run holds the box-wide load lock (`skills/_lib/box_lock.py`) in
shared mode: other gates run alongside it, but a CPU-burn proof waits for it
to finish, and it waits for a running burn (bounded; a timeout fails the run
with the holder named rather than gating on a starved box).

This is the thin dispatcher (fleet-config#502): it sums every check module
under `tests/acceptance/` and prints one summary line. Each module owns one
concern the pre-split 3287-line file used to mix together — the hook-payload
acceptance matrix + foreign-harness parity (`hook_matrix`), architecture/
fleet-map freshness guards (`architecture_guards`), the static AST spawn-flag
scanner (`spawn_scanner`), the hooks/ <-> skills/_lib tree-independence gate
(`tree_boundary`), the duplicate/unused-code ratchet (`static_ratchet_gate`),
the README-size and docs history-marker ratchet (`docs_ratchet_gate`), the
file/function/directory-size and root-layout ratchet (`structure_ratchet_gate`), the
issue-reference-in-comment ratchet (`comment_ratchet_gate`), the instruction-file
lint ratchet (`instruction_ratchet_gate`), the stale-doc-reference ratchet
(`doc_refs_ratchet_gate`), the unreferenced-definition ratchet
(`dead_code_ratchet_gate`), the fleet-convention rule ratchet
(`conventions_ratchet_gate`), the
fleet-scope enumeration guard (`fleet_scope_guard`), and the
standalone pure-logic test-file dispatch layer (`standalone_dispatch`).

The substantive per-hook unit-check functions are the `checks_*` modules, one
per domain (fleet-config#680, finishing the #502 split that left them piled in
a single 2681-line `unit_checks.py`): `checks_context_filter`, `checks_notify`,
`checks_guards`, `checks_session_state`, `checks_cross_agent`,
`checks_capture`, `checks_skill_helpers`, continued in the `guard_checks/`,
`notify_checks/` and `prose_guards/` subpackages. Adding a check means one new function
in the module that owns its domain, plus one `tally.run_unit()` line in the
phase function below that runs its domain.

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
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "_lib"))

import box_lock  # noqa: E402

from acceptance.hook_matrix import run_hook_matrix  # noqa: E402
from acceptance.checks_dispatch import _hook_dispatch_checks  # noqa: E402
from acceptance.architecture_guards import (  # noqa: E402
    _advisory_semantics_check,
    _config_map_check,
    _description_cap_check,
    _fleet_gate_check,
    _fleet_membership_drift_check,
    _fleet_toml_check,
    _fleet_toml_layout_check,
    _mermaid_check,
    _installer_symmetry_check,
    _projects_toml_contract_check,
    _readme_layout_check,
    _docs_links_check,
    _settings_sync_split_check,
    _settings_template_sync_check,
    _system_map_whatchanged_check,
)
from acceptance.prose_guards.skill_rule_checks import (  # noqa: E402
    _acceptance_audit_wiring_check,
    _chief_wait_event_check,
    _gate_evidence_wiring_check,
    _manual_compact_never_gated_check,
    _skill_git_rules_check,
    _unattended_worktree_mandate_check,
)
from acceptance.system_map_coverage import _system_map_coverage_check  # noqa: E402
from acceptance.checks_capture import (  # noqa: E402
    _conversation_capture_unit_checks,
    _conversation_index_unit_checks,
    _work_summary_unit_checks,
)
from acceptance.checks_context_filter import _context_filter_unit_checks  # noqa: E402
from acceptance.checks_cross_agent import (  # noqa: E402
    _codex_hooks_config_check,
    _copilot_statusline_utf8_check,
    _hook_transport_utf8_check,
    _session_state_agent_adapter_unit_checks,
)
from acceptance.checks_git_globals import _git_globals_unit_checks  # noqa: E402
from acceptance.checks_guards import (  # noqa: E402
    _bash_cmdexe_syntax_guard_unit_checks,
    _block_askuserquestion_chief_unit_checks,
    _branch_before_edit_guard_unit_checks,
    _gh_body_file_guard_unit_checks,
    _secret_scan_gh_unit_checks,
    _telegram_file_reply_guard_unit_checks,
    _tier23_hooks_unit_checks,
    _warn_channel_unit_checks,
)
from acceptance.guard_checks.safe_kill_checks import (  # noqa: E402
    _safe_kill_force_push_unit_checks,
    _safe_kill_git_bypass_unit_checks,
)
from acceptance.guard_checks.venv_guard_checks import (  # noqa: E402
    _venv_cd_prefix_guard_unit_checks,
    _venv_git_clean_guard_unit_checks,
    _venv_junction_guard_unit_checks,
    _venv_primary_guard_unit_checks,
)
from acceptance.notify_checks.codex_attention_checks import (  # noqa: E402
    _codex_attention_chief_unit_checks,
    _codex_attention_unit_checks,
    _codex_native_probe_unit_checks,
)
from acceptance.notify_checks.network_isolation_checks import (  # noqa: E402
    _notify_network_isolation_unit_checks,
)
from acceptance.checks_notify import (  # noqa: E402
    _notify_board_link_unit_checks,
    _notify_chief_routing_unit_checks,
    _notify_classify_unit_checks,
    _notify_complete_unit_checks,
    _notify_chunking_unit_checks,
    _notify_media_group_unit_checks,
    _notify_message_ids_unit_checks,
    _notify_send_unit_checks,
    _notify_routing_unit_checks,
)
from acceptance.checks_session_state import (  # noqa: E402
    _chief_handover_sessionstart_unit_checks,
    _chief_steer_convention_unit_checks,
    _index_lock_sessionstart_unit_checks,
    _lib_detect_project_unit_checks,
    _session_state_unit_checks,
)
from acceptance.checks_skill_helpers import (  # noqa: E402
    _learning_log_unit_checks,
    _restart_webapp_unit_checks,
    _scheduled_skill_timeouts_named_check,
)
from acceptance import gate_routing  # noqa: E402
from acceptance.dead_function_gate import _dead_function_unit_check  # noqa: E402
from acceptance.comment_ratchet_gate import _comment_ratchet_check  # noqa: E402
from acceptance.conventions_ratchet_gate import _conventions_ratchet_check  # noqa: E402
from acceptance.dead_code_ratchet_gate import _dead_code_ratchet_check  # noqa: E402
from acceptance.doc_refs_ratchet_gate import _doc_refs_ratchet_check  # noqa: E402
from acceptance.docs_ratchet_gate import _docs_ratchet_check  # noqa: E402
from acceptance.fleet_scope_guard import _fleet_scope_guard_check  # noqa: E402
from acceptance.instruction_ratchet_gate import _instruction_ratchet_check  # noqa: E402
from acceptance.shared import _subprocess_unit_check, seed_fleet_list_env  # noqa: E402
from acceptance.single_home_gate import _single_home_unit_check  # noqa: E402
from acceptance.spawn_scanner import (  # noqa: E402
    _git_wrapper_unit_check,
    _no_window_unit_check,
)
from acceptance.standalone_dispatch import _STANDALONE_UNIT_CHECKS  # noqa: E402
from acceptance.static_ratchet_gate import _static_ratchet_check  # noqa: E402
from acceptance.structure_ratchet_gate import _structure_ratchet_check  # noqa: E402
from acceptance.tree_boundary import _hooks_tree_boundary_check  # noqa: E402

_RATCHET_CHECKS = (
    _static_ratchet_check, _docs_ratchet_check, _structure_ratchet_check, _comment_ratchet_check,
    _instruction_ratchet_check, _doc_refs_ratchet_check, _dead_code_ratchet_check,
    _conventions_ratchet_check,
)
# One named home per shared behaviour, no private copies: the single-home scan and
# the git global-option walk it points at. The dead-function scan keeps the home
# from filling with helpers nothing calls.
_SINGLE_HOME_CHECKS = (_single_home_unit_check, _git_globals_unit_checks, _dead_function_unit_check)


class _Tally:
    """The gate's running Total / Failed / Skipped, summed from the checks that ran."""

    def __init__(self) -> None:
        self.failures = 0
        self.total = 0
        self.skipped = 0

    def add(self, failures: int, total: int, skipped: int = 0) -> None:
        self.failures += failures
        self.total += total
        self.skipped += skipped

    def run_unit(self, check_fn: Callable[[], Tuple[int, int]]) -> None:
        """Call one check function and fold its own `(failures, total)` into
        the running tally — the acceptance-matrix total is summed from real
        checks executed, never a hand-maintained constant that drifts
        silently when a check is added or removed (fleet-config#320)."""
        failures, total = check_fn()
        self.add(failures, total)

    def run_unit3(self, check_fn: Callable[[], Tuple[int, int, int]]) -> None:
        """`run_unit` for the checks that also report a third state — `skipped`.

        A check that *couldn't establish* a fact (no live settings.json to
        compare against) or that *may not gate this repo* (fleet-wide freshness,
        whose inputs are sibling checkouts) must land in neither Total nor
        Failed, so a run that verified less never reads identical to one that
        verified everything (fleet-config#461, #501, #562)."""
        failures, total, skipped = check_fn()
        self.add(failures, total, skipped)


def _run_hook_checks(tally: _Tally) -> None:
    """The per-hook unit checks, in the order the gate has always printed them."""
    # ---- hook-payload acceptance matrix + foreign-harness (Grok) parity ----
    tally.run_unit(run_hook_matrix)

    # ---- hook_dispatch.py: one process answers as its hooks' own did (#1274) ----
    tally.run_unit(_hook_dispatch_checks)

    # ---- context filter hook JSON + fixture eval ----
    # run_unit3: three of its cases probe integrations installed *outside* this
    # repo (~/.codex hooks junction, the copilot hook, the agy plugin). A
    # machine without one of them cannot establish the fact, so it reports as
    # skipped instead of folding into the pass count (fleet-config#679).
    tally.run_unit3(_context_filter_unit_checks)

    # ---- notify_send unit checks (pure / no network) ----
    tally.run_unit(_notify_send_unit_checks)

    # ---- notify_send sendMediaGroup path (fleet-config#976) ----
    tally.run_unit(_notify_media_group_unit_checks)
    tally.run_unit(_notify_message_ids_unit_checks)

    # ---- Codex PermissionRequest + conservative Stop attention routing ----
    tally.run_unit(_codex_attention_unit_checks)
    tally.run_unit(_codex_attention_chief_unit_checks)
    tally.run_unit(_codex_native_probe_unit_checks)

    # ---- notify_on_idle mention-construction unit checks ----
    tally.run_unit(_notify_chunking_unit_checks)

    # ---- notify_on_idle classify / session-link / idle-suppression ----
    tally.run_unit(_notify_classify_unit_checks)

    # ---- notify_on_idle Fleet-Board deep link (fleet-config#242) ----
    tally.run_unit(_notify_board_link_unit_checks)

    # ---- notify_on_idle chief-managed routing (fleet-config#443) ----
    tally.run_unit(_notify_chief_routing_unit_checks)

    # ---- block_askuserquestion_chief: enforce, don't just discourage (fleet-config#463) ----
    tally.run_unit(_block_askuserquestion_chief_unit_checks)

    # ---- telegram_file_reply_guard: files only to the sender's private chat (fleet-config#1478) ----
    tally.run_unit(_telegram_file_reply_guard_unit_checks)

    # ---- _lib.detect_project: worktree-sibling cwd resolution (fleet-config#471) ----
    tally.run_unit(_lib_detect_project_unit_checks)

    # ---- chief_handover_sessionstart pure logic + end-to-end (fleet-config#442) ----
    tally.run_unit(_chief_handover_sessionstart_unit_checks)
    tally.run_unit(_index_lock_sessionstart_unit_checks)

    # ---- chief steer convention: retired `CHIEF - ` marker + what replaced it (fleet-config#622) ----
    tally.run_unit(_chief_steer_convention_unit_checks)

    # ---- session_state board-row persistence (fleet-config#91) ----
    tally.run_unit(_session_state_unit_checks)

    # ---- session_state_codex / session_state_pi adapters (fleet-config#349) ----
    tally.run_unit(_session_state_agent_adapter_unit_checks)

    # ---- notify_complete deterministic message assembly + resolver ----
    tally.run_unit(_notify_complete_unit_checks)

    # ---- work_summary roll-up block + per-file table (pure, no gh) ----
    tally.run_unit(_work_summary_unit_checks)

    # ---- notify category -> chat routing (issue #139) ----
    tally.run_unit(_notify_routing_unit_checks)
    tally.run_unit(_notify_network_isolation_unit_checks)

    # ---- conversation_capture session-dedup logic ----
    tally.run_unit(_conversation_capture_unit_checks)

    # ---- conversation capture/index config-driven routing + indexing ----
    tally.run_unit(_conversation_index_unit_checks)


def _run_guard_checks(tally: _Tally) -> None:
    """The restart helper and the blocking and nudging guard hooks."""
    # ---- restart_and_verify_webapp restart-strategy + recovery hint ----
    tally.run_unit(_restart_webapp_unit_checks)
    tally.run_unit(_scheduled_skill_timeouts_named_check)  # #1306

    # ---- safe_kill_guard: force-push blocks on the pushed ref (#562) ----
    tally.run_unit(_safe_kill_force_push_unit_checks)
    tally.run_unit(_safe_kill_git_bypass_unit_checks)  # #1303
    tally.run_unit(_venv_junction_guard_unit_checks)

    # ---- venv_discipline rule 5 + destructive-action audit trail ----
    tally.run_unit(_venv_primary_guard_unit_checks)
    tally.run_unit(_venv_git_clean_guard_unit_checks)
    tally.run_unit(_venv_cd_prefix_guard_unit_checks)

    # ---- _lib.warn: the nudge lands on a channel the model reads (#681) ----
    tally.run_unit(_warn_channel_unit_checks)

    # ---- gh_body_file_guard: warn-only stdout assertions ----
    tally.run_unit(_gh_body_file_guard_unit_checks)

    # ---- secret_scan_guard: gh issue/PR bodies before they post (#959) ----
    tally.run_unit(_secret_scan_gh_unit_checks)

    # ---- bash_cmdexe_syntax_guard: block + warn assertions (#264, #385) ----
    tally.run_unit(_bash_cmdexe_syntax_guard_unit_checks)

    # ---- Tier 2/3 hooks: docs-guard env override + warn-hook stdout (issue #158) ----
    tally.run_unit(_tier23_hooks_unit_checks)

    # ---- branch_before_edit_guard: real temp git repos/worktrees x launcher env, target-path resolution (fleet-config#464) ----
    tally.run_unit(_branch_before_edit_guard_unit_checks)


def _run_suite_checks(tally: _Tally) -> None:
    """The standalone test-file suites, then the skill helpers that ride with them."""
    # ---- standalone pure-logic test-file dispatch: one row per suite in
    # acceptance/standalone_dispatch.py's table -- new suite = one row added
    # there, no new wrapper/import/registration here (fleet-config#505) ----
    for _label, _test_file, _why in _STANDALONE_UNIT_CHECKS:
        # Three-state like run_unit3: a suite that exits shared.SKIP_EXIT could
        # not establish its facts (missing toolchain) and lands in Skipped, not
        # in a bare `OK` that verified nothing (fleet-config#679).
        tally.add(*_subprocess_unit_check(_label, _test_file, routed_out=gate_routing.routed_out(_label)))

    # ---- learning-log report.py pure helpers (.claude/skills/learning-log) ----
    tally.run_unit(_learning_log_unit_checks)


def _run_repo_guards(tally: _Tally) -> None:
    """Fleet-map freshness, the fleet registry, skill-prose rules and the docs inventory."""
    # ---- system-map: fleet ↔ data ↔ doc coverage (architecture/) ----
    tally.run_unit3(_system_map_coverage_check)

    # ---- system-map: per-repo .fleet.toml aggregation + anti-staleness ----
    # run_unit3: only fleet-config's own card is gated here. The fleet-wide
    # freshness checks read sibling checkouts no commit in this repo controls,
    # so they report as skipped rather than failing this gate (fleet-config#562).
    tally.run_unit3(_fleet_toml_check)
    tally.run_unit(_advisory_semantics_check)

    # ---- system-map: no card description past two lines (#1250) ----
    tally.run_unit(_description_cap_check)
    tally.run_unit(_fleet_toml_layout_check)

    # ---- projects.toml is the fleet-membership list: no repo on disk may be
    # missing from it (fleet-config#640). run_unit3: skipped, not passed, when
    # there is no fleet beside this checkout to compare against.
    tally.run_unit3(_fleet_membership_drift_check)

    # ---- an active repo needs a recognised gate, or it is dormant. Advisory:
    # the inputs are sibling checkouts (fleet-config#562).
    tally.run_unit3(_fleet_gate_check)

    # ---- the machine-local registry and its tracked example agree ----
    tally.run_unit(_projects_toml_contract_check)

    # ---- system-map: Mermaid companion render (render_mermaid.py) freshness ----
    tally.run_unit(_mermaid_check)
    tally.run_unit(_unattended_worktree_mandate_check)

    # ---- no skill deletes an index.lock or merge-commits a PR (#1243) ----
    tally.run_unit(_skill_git_rules_check)

    # ---- acceptance audited per criterion, unverifiable its own state (#958) ----
    tally.run_unit(_acceptance_audit_wiring_check)

    # ---- gate evidence bound to a tree hash, never 'green this session' (#957) ----
    tally.run_unit(_gate_evidence_wiring_check)

    # ---- the chief wakes on worker turn-end events (#999) ----
    tally.run_unit(_chief_wait_event_check)

    # ---- an operator-typed /compact is never gated (#1052) ----
    tally.run_unit(_manual_compact_never_gated_check)

    # ---- system-map: week-over-week 'what changed' diff (whatchanged.py) ----
    tally.run_unit(_system_map_whatchanged_check)

    # ---- config-map: introspected config.data.js freshness + whatchanged ----
    # run_unit3: the freshness half sweeps sibling repos, so it reports as
    # skipped rather than failing this gate — same reason as #562's fleet_toml.
    tally.run_unit3(_config_map_check)

    # ---- install.ps1 / uninstall.ps1 mirror each other (fleet-config#681) ----
    tally.run_unit(_installer_symmetry_check)

    # ---- README Layout tree is an exhaustive inventory (fleet-config#565) ----
    tally.run_unit(_readme_layout_check)

    # ---- relative docs/ markdown links resolve (fleet-config#1515) ----
    tally.run_unit(_docs_links_check)


def _run_wiring_checks(tally: _Tally) -> None:
    """Agent hook wiring and transports, settings sync, spawn conventions and the ratchets."""
    # ---- Codex hook wiring: direct Python commands with bounded timeouts ----
    tally.run_unit(_codex_hooks_config_check)

    # ---- both hook transports keep non-ASCII payloads exact (fleet-config#912) ----
    # run_unit3: without Windows PowerShell the shim half cannot run -- skipped.
    tally.run_unit3(_hook_transport_utf8_check)

    # ---- Copilot hook wiring + statusline keep non-ASCII stdin exact (fleet-config#913) ----
    tally.run_unit3(_copilot_statusline_utf8_check)

    # ---- settings: live ~/.claude/settings.json ⊇ template hook wiring ----
    # run_unit3: this check has a third state (skipped, when the live file is
    # absent, or pending merge when a template hook's module is not live yet)
    # that must never fold into Total/Failed (fleet-config#501, #942).
    tally.run_unit3(_settings_template_sync_check)
    # The pending-merge split it rests on, pinned against a throwaway hooks dir (#942).
    tally.run_unit(_settings_sync_split_check)

    # ---- Windows console suppression on every runtime spawn (#399 / #412) ----
    tally.run_unit(_no_window_unit_check)

    # ---- every runtime `git` spawn routes through run_git (#667 / #677) ----
    tally.run_unit(_git_wrapper_unit_check)

    # ---- baseline ratchets: duplicate/unused code (#1514), README + docs history
    # markers (#1542), file/function/directory size + root layout (#1549) ----
    # run_unit, not run_unit3: exit 3 (could not establish the count) is a failure
    # here, never a skip -- see static_ratchet_gate's docstring.
    for _ratchet in (*_SINGLE_HOME_CHECKS, *_RATCHET_CHECKS):
        tally.run_unit(_ratchet)

    # ---- fleet enumeration routes obey the dormant switch or are allow-listed (#1548) ----
    # run_unit: a dedicated check, never a skip-able standalone_dispatch row.
    tally.run_unit(_fleet_scope_guard_check)

    # ---- hooks/ never imports across into skills/_lib (fleet-config#564) ----
    tally.run_unit(_hooks_tree_boundary_check)


def main() -> int:
    try:
        with box_lock.held(box_lock.SHARED, "fleet-config",
                           report=lambda line: print(line, flush=True)):
            return _run_all()
    except box_lock.BoxLockTimeout as exc:
        print(f"FAIL  box_load_lock: {exc}")
        return 1


def _run_all() -> int:
    seed_fleet_list_env()
    tally = _Tally()
    _run_hook_checks(tally)
    _run_guard_checks(tally)
    _run_suite_checks(tally)
    _run_repo_guards(tally)
    _run_wiring_checks(tally)

    print()
    print(f"Total: {tally.total} | Failed: {tally.failures} | Skipped: {tally.skipped}")
    return 0 if tally.failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
