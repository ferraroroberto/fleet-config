"""tests/acceptance/ -- the verification-gate check package (fleet-config#502).

Split out of the former single 3287-line tests/run_acceptance.py god-module.
`tests/run_acceptance.py` stays the one invoked entry point (the CLAUDE.md
gate command is unchanged); this package holds the concerns that used to be
mixed into it: shared plumbing (`shared`), the hook-payload acceptance matrix
(`hook_matrix`), architecture/fleet-map freshness guards
(`architecture_guards`), the static spawn-flag AST scanner (`spawn_scanner`),
the ~40 substantive per-hook unit-check functions (`unit_checks`), and the
standalone pure-logic test-file dispatch layer (`standalone_dispatch`).
"""
