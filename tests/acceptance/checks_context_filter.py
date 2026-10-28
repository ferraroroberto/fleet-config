"""Acceptance checks for the context-filter surface (fleet-config#680).

Split out of the former 2681-line `unit_checks.py`, which was the one member of
this package still holding every concern at once while its siblings
(`architecture_guards`, `hook_matrix`, `spawn_scanner`, `standalone_dispatch`,
`tree_boundary`) had each been given their own file. The functions were always
independent -- nothing couples them but `shared.py` -- so this is the straggler
finally following the package's own convention, not an untangling.

One concern, run as a fixed sequence of small section functions under
`context_filter_sections/`: the compressor's JSON contract, its fixture eval,
and the *foreign* wirings (the Codex hooks junction, the Copilot CLI hook, the
`agy` plugin) that must not drift from the source of truth in this repo. Those three probe integrations installed outside this tree, which is why
this is the package's only `run_unit3` (three-state) check: a machine missing
one cannot establish the fact, and reports it as skipped rather than folding it
into a pass (fleet-config#679).
"""
from __future__ import annotations

from typing import Tuple

from acceptance.context_filter_sections import (
    compress_cmd,
    compressor,
    foreign_wiring,
    hook_rewrite,
    telemetry,
    wrapper_cli,
)
from acceptance.shared import _Checker

# The sections in the order their cases print. A section left out of this tuple stops running
# silently, so the acceptance run's case total is the guard against that, not this list.
_SECTIONS = (
    hook_rewrite.rewrite_output_shape,
    wrapper_cli.fixture_eval,
    wrapper_cli.timeout_kills_process_tree,
    wrapper_cli.timeout_carries_truncation_marker,
    compressor.faithfulness_never_reads_less,
    compressor.diffs_and_json_are_payload,
    compressor.background_command_not_wrapped,
    wrapper_cli.filter_fault_fails_open,
    wrapper_cli.banner_reports_withheld_lines,
    wrapper_cli.whole_output_carries_no_banner,
    hook_rewrite.skill_helper_passthrough,
    hook_rewrite.rewrite_decisions,
    hook_rewrite.cd_prefix_runs_in_outer_shell,
    hook_rewrite.cwd_reaches_wrapper_verbatim,
    wrapper_cli.survives_non_cp1252_output,
    wrapper_cli.bash_body_runs_non_login,
    wrapper_cli.import_paths_defer_heavy_modules,
    hook_rewrite.mode_file_resolution,
    hook_rewrite.grok_payload_short_circuits,
    telemetry.row_schema_is_pinned,
    hook_rewrite.attribution_flags_forwarded,
    foreign_wiring.codex_wired_hook_fails_open,
    foreign_wiring.agy_payload_dialect,
    foreign_wiring.copilot_payload_dialect,
    foreign_wiring.copilot_installed_copy_matches_source,
    foreign_wiring.agy_installed_copy_matches_source,
    compress_cmd.compress_modes_and_skips,
    compress_cmd.compress_malformed_stdin_fails_open,
    compress_cmd.compress_keeps_non_ascii_output,
    telemetry.shadow_log_rotates_at_cap,
    telemetry.command_field_is_redacted,
    telemetry.blob_gc_prunes_stale_entries,
)


def _context_filter_unit_checks() -> Tuple[int, int, int]:
    """Returns `(failures, total, skipped)` -- run via `run_unit3`.

    Three of these cases probe integrations that live *outside* this repo
    (the ~/.codex hooks junction, the installed copilot hook, the installed
    agy plugin). On a machine without one of them the fact cannot be
    established, so it reports as `skipped` via `check.advisory(..., False)`
    -- never folded into the pass count, which is what made a machine that
    verified nothing read identical to one that verified all three
    (fleet-config#679).
    """
    check = _Checker()
    for section in _SECTIONS:
        section(check)
    return check.failures, check.total, check.skipped
