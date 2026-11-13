"""Track an outside source repo past a watermark: the `[upstream]` table in a repo's
`.fleet.toml`, the contiguous-advance rule, and the fetch-only source remote.

Shared by `/upstream-sync` (bring the source's merged work in) and `/downstream-sync`
(hand local-only features back as specs): both read and write the same table through
`ledger`, so neither may change its format alone. Contract: `docs/skills/upstream-sync.md`
and the `[upstream]` section of `architecture/README.md`.
"""
