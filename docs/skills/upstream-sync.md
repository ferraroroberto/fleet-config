# `/upstream-sync`

A local repo is sometimes built from someone else's repo: not a fork, a copy that fits the fleet's structure and carries local changes, while the source keeps shipping. `/upstream-sync` brings what the source merged since a recorded watermark into the local repo, one item at a time, through the normal issue flow. It reuses the source's code (a cherry-pick or a 3-way apply of its diff) and fixes it forward until the local gate passes. Rewriting each feature by hand would throw that code away, and re-forking would lose the local changes.

## The record: `[upstream]` in `.fleet.toml`

The watermark (`synced_sha`, the last source commit already accounted for), a ledger of deliberately skipped source items and the list of local features offered back are the only record of what moved between the two repos. No commit, issue, PR body or comment names the source. The schema is in [architecture/README.md](../../architecture/README.md#optional-per-repo-upstream-table). `/downstream-sync` (hand local-only features back to the source as specs) shares this table and the helper, so neither skill changes the format alone.

## The helper: `skills/_lib/upstream_sync/`

The deterministic half, tested in `tests/repo/upstream_sync/test_upstream_sync.py` against throwaway source and local repos.

- `ledger.py` reads and rewrites the `[upstream]` tables at the end of `.fleet.toml`, keeping every other line, and refuses a write that would not read back. `advance_watermark` is the rule: the watermark moves to the last item of the contiguous run, from the start, where every item is merged or skipped. A failed or unworked item holds it, whatever comes after.
- `source.py` keeps the source as the fetch-only remote `upstream-sync` (its push url is unreachable), lists its first-parent commits after the watermark (one per merged PR, whether the source squashes or merges), marks each one the local ref already carries through `git cherry` patch ids, and classifies the root licence as `yes`, `copyleft`, `no` (none) or `unknown`.
- `downstream.py` is the `/downstream-sync` half. It lists the local first-parent commits the source branch does not reach and flags each one: `synced` when it moved the `[upstream]` table (only sync lanes write that table, so this tells a port from a local feature with no provenance line anywhere), `present` when `git cherry` finds its patch in the source, and `offered` when it is already handed back. It lints a hand-back spec, and it matches a source item to an offer when the item's message references the issue the offer was filed as.
- `cli.py` has seven commands. The `/upstream-sync` four: `init` adds the remote and sets the watermark to the source head; `--dry-run` only reports the head, and a second `init` is refused. `status [--ref]` reads the table, from a ref when given. `survey [--against]` prints the `ITEM` lines. `advance <sha> --outcome merged|skipped` records one item. It refuses a sha that is not a pending item or a skip with no reason, and it exits 1 with `WATERMARK=held before=<sha>` when an earlier item is still open. `--supersedes <local-sha>` retires the offer the item answered, and is refused when it names no single offer. The `/downstream-sync` three: `compare [--against] [--since]` prints the `LOCAL` lines and changes nothing; `offer <local-sha> --feature F [--source-issue N]` records a hand-back and refuses a commit that is not a candidate; `lint-spec <file> [--allow-code] [--forbid W]` refuses a spec missing a section (Problem, Behaviour, Interface, Acceptance criteria, Tests) or carrying a code block, a local path, a private host or address, an email, a secret-shaped token, or this repo's own name or owner.

## Handing work back: `/downstream-sync`

The reverse direction. The source is often a repo the owner cannot push to or see into, so what travels is a feature spec, not code (code only when the source is public). The skill lists the local work the source lacks, the owner picks, it writes one spec per pick, and it files each one on the source only after a per-item OK. A bookkeeping lane then records each hand-back as an `offered` entry. When the source ships its own version, the next `/upstream-sync` survey marks that item `supersedes local` (from the issue reference, or by behaviour against the `OFFERED` lines), and the port replaces the local implementation with the source's, keeping local data and config readable. Its `advance --supersedes` retires the offer. Tests: `tests/repo/upstream_sync/test_downstream_sync.py`.

## How a run moves the watermark

The orchestrating session writes nothing. Each port lane runs `advance` for the skips in front of its item and then for its own item, on its own branch, so the merged PR carries the watermark. If a lane fails, its PR never merges and the watermark stays before that item. The run stops there, and re-running resumes from it. The first-run `init` and any skips after the last port go through one bookkeeping lane by the same path.

## Models and harnesses

The model argument (`sonnet`, `opus`, `luna`, `grok`) goes to `chief_ops.py dispatch --model` for every item, the same launcher path the chief uses. A model the launcher cannot run is refused there, `luna` with no Luna in the Codex catalog included, and the run stops with that line. Dispatch is an HTTP call and the rest is `git`, `gh` and the helper, so the skill runs the same on Claude Code, Codex and Pi. It never needs an in-session sub-agent.
