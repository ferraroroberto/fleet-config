"""Structure-baseline debt for the weekly `/audit-fleet` digest.

A repo that runs the structure ratchet records its offenders in
`.structure-ratchet-baseline.json`; the size of that file is the debt left to
burn down. `baseline` reads it from a repo's default branch and counts it,
`section` compares those counts with last week's digest state and renders the
digest lines. stdlib + the shared git wrapper only.
"""
