"""Static checks on the hooks' multi-harness wire translation layer (`hooks/hook_wire/`).

One module per concern: `tool_id_leak_scanner` (no hook outside `edit_events.py` branches on a foreign harness's
own tool id directly). Each check is a `() -> (failures, total)` function `run_acceptance.py` calls directly, like
the ones in `guard_checks`/`notify_checks`.
"""
