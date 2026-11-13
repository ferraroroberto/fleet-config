# `/upstream-sync`

A local repo is sometimes built from someone else's repo: not a fork, a copy that fits the fleet's structure and carries local changes, while the source keeps shipping. `/upstream-sync` brings what the source merged since a recorded watermark into the local repo, one item at a time, through the normal issue flow. It reuses the source's code (a cherry-pick or a 3-way apply of its diff) and fixes it forward until the local gate passes. Rewriting each feature by hand would throw that code away, and re-forking would lose the local changes.

## The record: `[upstream]` in `.fleet.toml`

The watermark (`synced_sha`, the last source commit already accounted for) and a ledger of deliberately skipped source items are the only record of what came in. No commit, issue, PR body or comment names the source. The schema is in [architecture/README.md](../../architecture/README.md#optional-per-repo-upstream-table). `/downstream-sync` (hand local-only features back to the source as specs) shares this table and the helper, so neither skill changes the format alone.

## The helper: `skills/_lib/upstream_sync/`

The deterministic half, tested in `tests/repo/test_upstream_sync.py` against throwaway source and local repos.

- `ledger.py` reads and rewrites the `[upstream]` tables at the end of `.fleet.toml`, keeping every other line, and refuses a write that would not read back. `advance_watermark` is the rule: the watermark moves to the last item of the contiguous run, from the start, where every item is merged or skipped. A failed or unworked item holds it, whatever comes after.
- `source.py` keeps the source as the fetch-only remote `upstream-sync` (its push url is unreachable), lists its first-parent commits after the watermark (one per merged PR, whether the source squashes or merges), marks each one the local ref already carries through `git cherry` patch ids, and classifies the root licence as `yes`, `copyleft`, `no` (none) or `unknown`.
- `cli.py` has four commands. `init` adds the remote and sets the watermark to the source head; `--dry-run` only reports the head, and a second `init` is refused. `status [--ref]` reads the table, from a ref when given. `survey [--against]` prints the `ITEM` lines. `advance <sha> --outcome merged|skipped` records one item. It refuses a sha that is not a pending item or a skip with no reason, and it exits 1 with `WATERMARK=held before=<sha>` when an earlier item is still open.

## How a run moves the watermark

The orchestrating session writes nothing. Each port lane runs `advance` for the skips in front of its item and then for its own item, on its own branch, so the merged PR carries the watermark. If a lane fails, its PR never merges and the watermark stays before that item. The run stops there, and re-running resumes from it. The first-run `init` and any skips after the last port go through one bookkeeping lane by the same path.

## Models and harnesses

The model argument (`sonnet`, `opus`, `luna`, `grok`) goes to `chief_ops.py dispatch --model` for every item, the same launcher path the chief uses. A model the launcher cannot run is refused there, `luna` with no Luna in the Codex catalog included, and the run stops with that line. Dispatch is an HTTP call and the rest is `git`, `gh` and the helper, so the skill runs the same on Claude Code, Codex and Pi. It never needs an in-session sub-agent.
