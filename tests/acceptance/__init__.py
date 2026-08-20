"""tests/acceptance/ -- the verification-gate check package (fleet-config#502).

Split out of the former single 3287-line tests/run_acceptance.py god-module.
`tests/run_acceptance.py` stays the one invoked entry point (the CLAUDE.md
gate command is unchanged); this package holds the concerns that used to be
mixed into it: shared plumbing (`shared`), the hook-payload acceptance matrix
(`hook_matrix`), architecture/fleet-map freshness guards
(`architecture_guards`), the static spawn-flag AST scanner (`spawn_scanner`),
the hooks/ <-> skills/_lib tree-independence gate (`tree_boundary`), and the
standalone pure-logic test-file dispatch layer (`standalone_dispatch`).

The ~25 substantive per-hook unit-check functions live in the `checks_*`
modules, one per domain (fleet-config#680): `checks_context_filter`,
`checks_notify`, `checks_guards`, `checks_session_state`, `checks_cross_agent`,
`checks_capture`, `checks_skill_helpers`. They were the one straggler of the
#502 split -- left as a single 2681-line `unit_checks.py` while every sibling
concern got its own file -- and each is now the obvious home for the next
check in its domain. All of them import `shared`, and nothing else here.
"""
