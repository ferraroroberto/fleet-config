# Inspiration ideas: the file, the ledger and the Board message

`/inspiration-scan` ends each run by writing the ideas it kept to a machine-readable file, so the app-launcher Board can show them. The owner turns each one into an issue or drops it; the chief records either outcome in a ledger, and the next scan never offers a decided idea again. This page is the contract between the scan, the chief ([`ideas.md`](../.claude/skills/chief/ideas.md)) and the Board. All of it lives in the machine-local state root (`~/.claude/inspiration-scan/`, or `INSPIRATION_SCAN_ROOT`), never in the repo.

## `ideas.json`

Rewritten by every `scan.py run`: the ideas kept today, then the earlier ones the owner has not decided on yet (an undecided idea is already in `seen.json`, so dropping it from the file would lose it; they are carried for 30 days). An idea in the ledger is never in the file.

```json
{"version": 1, "digest": "2026-11-07", "ideas": [ { ...idea... } ]}
```

| Field | Meaning |
| --- | --- |
| `id` | The item id: a hash of the normalised link. Stable across runs. |
| `title`, `link` | The item's title and canonical link. |
| `source` | `{"name": "<source name>", "kind": "newsletter" \| "youtube" \| "x"}`. |
| `author` | The feed's author, or `""` when the reader has none. |
| `published` | ISO-8601 publication time, or `""`. |
| `excerpt` | At most 25 words of the item text, ending in `…` when cut. The link carries the rest. |
| `digest` | The date of the digest the idea came from. |
| `score`, `model` | The 0-10 relevance score and the id of the model that gave it. |
| `why_fits`, `experiment` | The scorer's one-line answers. |
| `suggested_repo` | A fleet repo name, or `""`. Only a repo that exists on this machine survives; the scorer's guess otherwise is dropped. |
| `prompt` | The long `/issue-add` text: the idea in plain prose, why it fits, the experiment, then a `## Source` section (source and kind, author, date, link, digest date, score and model). |

## `ideas-ledger.jsonl`

Append-only, one JSON object per line, written only by `scan.py ideas apply|filter`.

- `id`, `state` (`applied` or `filtered`), `at` (UTC, `YYYY-MM-DDTHH:MM:SSZ`).
- `issue` (`<repo>#<n>`) on an applied row; `note` (the owner's words, possibly `""`) on a filtered row.
- A copy of the attribution: `title`, `link`, `source`, `author`, `published`, `digest`, `score`, `model`. A row stands on its own after `ideas.json` is rewritten.

An id has at most one row. A ledger line that does not parse stops the scan and the writer (`IDEAS=unknown`), because skipping it would offer a decided idea again.

## The writer

```
scan.py ideas list [--state new|applied|filtered]
scan.py ideas apply <id> --issue <repo>#<n>
scan.py ideas filter <id> [--note "..."]
```

`list` prints one `IDEA=<id> state=... score=... repo=... title=...` line per idea (an undecided idea is `new`), then `IDEAS_ENTRIES=<n>`. A successful write prints `IDEA_APPLIED=<id> issue=<ref>` or `IDEA_FILTERED=<id>`.

Refusals are final, exit 2, nothing written, each with its own reason:

- `IDEAS_REFUSED=<id> reason=unknown-id`: the id is in neither the ledger nor `ideas.json`.
- `IDEAS_REFUSED=<id> reason=already-applied` / `already-filtered`: a second outcome for the same id.
- `IDEAS_REFUSED=<id> reason=bad-ref (...)`: `--issue` is not `<repo>#<n>`.

`IDEAS=unknown reason=...` (exit 1) means `ideas.json` or the ledger could not be read or written; it is not an empty ledger.

## The Board message

The Board sends one message to the chief's terminal, the same channel as "Answers from the Board". The first line is exactly one of:

```
Idea from the Board: <id> -> file in <repo>
Idea from the Board: <id> -> drop
```

Then the body: the owner's edited prompt after a `file` line, or the owner's note after a `drop` line (it may be empty). Then one line per attached file, `Attached: <path>`. An attachment is for the chief to read; it is never posted to GitHub.

The `<repo>` is a fleet repo name; the chief files in `ferraroroberto/<repo>`.
