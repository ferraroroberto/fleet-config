"""Per-unit run history of the acceptance gate.

`gate_evidence.py run` appends one row per unit per gate run to a ledger in the hooks-state
directory, so a unit's time, outcome and process spawns can be compared across runs. The unit
is a whole check function or suite file, never one `check()` call.
"""
