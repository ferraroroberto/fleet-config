"""Gate coverage for the weekly `/audit-fleet` digest: how much of the fleet the ratchets already enforce.

`reader` reads one repo's `.fleet.toml` and every enabled ratchet's baseline from
its default branch: which ratchet tables are enabled, the `[conventions_ratchet]`
rules, and the baselined hit count per ratchet. `section` compares those readings
with last week's digest state and renders the digest lines. Deterministic: no
auditor counts anything here. stdlib + the shared git wrapper only.
"""
