"""Guard-hook acceptance checks that outgrew `acceptance.checks_guards`.

One module per guard family: `safe_kill_checks` (force-push and bypass-flag
refusals) and `venv_guard_checks` (`venv_discipline.py` rules 4 and 5 and the
destructive-action audit trail). Each check is a `() -> (failures, total)`
function that `run_acceptance.py` calls directly, like the ones in `checks_guards`.
"""
