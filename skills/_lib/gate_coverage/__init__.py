"""Gate coverage: how much of the fleet the ratchets already enforce, for the weekly audits.

`reader` reads one repo's `.fleet.toml` and every enabled ratchet's baseline from
its default branch: which ratchet tables are enabled, the `[conventions_ratchet]`
rules, and the baselined hit count per ratchet. `section` compares those readings
with last week's digest state and renders the digest lines. `enforced` maps each
enabled ratchet to the audit checks and prompt-audit lint rules it owns, so the
weekly audits skip them. Deterministic: no auditor counts anything here. stdlib + the shared git wrapper only.
"""
