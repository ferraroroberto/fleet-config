"""The runtime write ratchet stage and its tests.

`runtime_write_ratchet_gate` holds the static check plus the snapshot/probe pair
`run_acceptance.py` runs around the whole acceptance run; each is a
`() -> (failures, total)` function called directly, like the ones in `wire_checks`.
"""
