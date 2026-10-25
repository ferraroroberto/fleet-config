"""Notification acceptance checks that outgrew `acceptance.checks_notify`.

`codex_attention_checks` covers Codex's approval and Stop routing: the
notification, the chief-managed hand-off and the native-probe wiring.
`network_isolation_checks` proves the suite cannot reach Telegram. Each
check is a `() -> (failures, total)` function that `run_acceptance.py` calls
directly, like the ones in `checks_notify`.
"""
