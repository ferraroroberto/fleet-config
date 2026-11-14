# Chief journal file: format

The running list of what changed, written by the fleet chief and read by app-launcher's "since you last looked" Board card. The plan (`docs/chief-plan.md`) holds the current state; this file holds what happened. This page is the one definition. The writer is `skills/_lib/chief/journal.py`.

## Location and ownership

- `~/.claude/hooks/state/chief-journal.jsonl`, beside `chief-plan.json`. It's machine-local: the state dir is gitignored and the file is never committed. `CLAUDE_HOOKS_STATE_DIR` overrides the directory, which is how tests stay hermetic.
- The chief writes it only through `chief_ops.py`: `note` by hand, and every `plan` transition below by itself. Never by hand or with the Write tool. Each write is a locked read-modify-write that trims the file and replaces it atomically, so a reader never sees a half-written file.

## Shape

One JSON object per line, oldest first:

```json
{"ts": "2026-11-14T09:00:00Z", "kind": "merged", "ref": "app-launcher#1273", "text": "merged: Code tab: Chat by default (4190488)"}
```

| Field | Rule |
| --- | --- |
| `ts` | UTC, `YYYY-MM-DDTHH:MM:SSZ`, stamped by the writer. |
| `kind` | `merged` \| `dispatched` \| `filed` \| `parked` \| `asked` \| `answered` \| `failed` \| `note`. |
| `ref` | `<repo>#<N>` the line is about, or `""` when it is about nothing in particular. |
| `text` | One short public-safe line, at most 200 characters: no secrets, no session links, no private paths. |

## What logs itself

| Command | Entry | When |
| --- | --- | --- |
| `plan add <ref> "<title>"` | `filed` | always |
| `plan lane <repo> <status> --item "#N"` | `dispatched` | the lane's item is new or changed, not on a status change |
| `plan set <ref> --status merged` | `merged` | the row was not already merged |
| `plan set <ref> --status parked` | `parked` | the row was not already parked |
| `plan ask` and `plan wait` | `asked` | always |
| `plan unwait <key>` | `answered` | one entry per item removed |

A command the writer refuses writes nothing to either file. `plan prune`, `move`, `remove`, `drop-lane` and `clear` are not journalled.

`chief_ops.py note "<text>" [--kind K] [--ref repo#N]` covers a change no plan command makes (default kind `note`). It prints `NOTE=appended kind=… ref=…` or `NOTE=skipped reason=duplicate`; a bad kind, ref or text is refused with exit 2, and a journal that cannot be written prints `NOTE=failed reason=…` with exit 1.

## Dedupe

An entry whose `kind` + `ref` + `text` matches the newest entry for that `kind` + `ref` is skipped, so a chief that restates itself every turn adds nothing. The same text after a different one for that `kind` + `ref` is a new event and is kept.

## Bounds

Every write drops entries older than 14 days, then keeps the newest 500: whichever bound is tighter wins.

## A journal failure never fails `plan`

The plan is written before its journal line. A journal error (a locked or unwritable file) is logged to stderr as `journal write skipped …` and the `plan` command keeps its own exit code.

## Reader rules

- A missing file is an empty journal.
- Skip a line that does not parse, and ignore fields you do not know.
- Entries are in time order; show newest first. A reader that remembers when Roberto last looked shows the entries after that time.
