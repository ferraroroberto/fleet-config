"""The structural check a recurring audit bucket's cleanup ships, and where it goes next.

`scope` decides the `structural-gate` verdict for a cleanup branch: whether a
valid `Structural-check:` declaration exists, and the check's scope (repo-specific,
generalizable, scaffold-owned or n/a). `promotion` files one deduped
project-scaffolding issue per generalizable check, so a check written in one repo
can become a fleet-wide ratchet rule. The intake side of that issue is
project-scaffolding's `docs/conventions-ratchet.md`, "Adding a rule from a
promotion issue". stdlib + `git` (through `git_run`) + `gh` (injected) only.
"""
