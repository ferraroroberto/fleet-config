"""Acceptance checks that pin rules living in skill prose.

A rule an agent follows only because a SKILL.md says so can be dropped by a
context purge or a well-meaning rewrite without any code changing.
`skill_rule_checks` asserts each such rule is still stated where the agent
reads it. Each check is a `() -> (failures, total)` function that
`run_acceptance.py` calls directly.
"""
