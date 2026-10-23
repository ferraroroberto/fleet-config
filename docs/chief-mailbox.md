# Chief mailbox

The durable channel from chief-managed workers to the fleet chief. Every worker turn-end, session exit and permission gate becomes one small JSON file in a directory. The chief reads those files; nothing else carries the data. A doorbell (implemented in app-launcher) may later tell the chief to read. It carries no data.

This document is the cross-repo contract. A second implementation (app-launcher counts pending events every ~3 s) follows this file alone. The reference implementation is `hooks/chief_inbox.py` (writer) and `skills/_lib/chief/mailbox.py` (readers: `count_pending`, `claim_mailbox`, `ack_events`, `claim_batch`, behind the `chief_ops.py` CLI). The tests named under [Conformance](#conformance) pin every rule below.

## Directory layout

```
<state_dir>/chief-inbox/
    <published_ms>-<id>.json          published, unclaimed events   (pending)
    .claimed/<claimed_at_ms>-<id>.json  v2 claims awaiting ack        (pending once stale)
    .event.<random>.tmp               a writer's temp file            (ignore)
    .claim-<pid>-<ns>/                wait-event's private claim folder (ignore)
```

- `<state_dir>` is `CLAUDE_HOOKS_STATE_DIR` when set, else `~/.claude/hooks/state` (`hooks/_lib.state_dir()`).
- `<published_ms>` and `<claimed_at_ms>` are Unix epoch **milliseconds**, decimal digits, no padding.
- `<id>` is the event id. A v2 writer uses `uuid4().hex`: 32 lowercase hex characters. A v1 file (written before the v2 id format) has an 8-hex suffix instead; readers take whatever follows the first `-` as its id.

## Publishing (writers)

- **One file per event.** Never append to a shared file: on Windows a file another process holds open cannot be renamed, so a shared log would make atomic claims impossible.
- **Atomic publish.** Write the JSON to a temp file in the same directory whose name starts with `.` and ends with `.tmp`, then `os.replace` it to `<published_ms>-<id>.json`. A reader therefore never sees a partially written `.json`.
- **A writer in another repo publishes into the directory. It never imports from `hooks/`.** The file format below is the whole interface.
- Writers never touch `.claimed/`.

## Event schema (v2)

```json
{
  "v": 2,
  "id": "16cb7df4e6f54b578c56885b936cee25",
  "kind": "turn-end",
  "event": "Stop",
  "launcher_sid": "<APP_LAUNCHER_SESSION_ID of the worker>",
  "repo": "fleet-config",
  "number": 1455,
  "agent": "claude",
  "ts": "2026-10-16T07:47:58Z",
  "dispatch_id": "<uuid hex from chief_ops.py dispatch, or null>",
  "excerpt": "<last ≤1500 chars of the worker's final reply, or null>",
  "excerpt_source": "payload",
  "excerpt_unavailable": "<reason; present only when excerpt is null>"
}
```

| Field | Meaning |
|---|---|
| `v` | Schema version, `2`. Absent on v1 events. |
| `id` | Equals the file name's id. The dedupe key: a re-offered event keeps its `id`. |
| `kind` | `turn-end`, `session-end`, `needs-input` or `job-failed`. A `needs-input` is a worker blocked on a permission gate (Claude `permission_prompt`, Codex `PermissionRequest`), written only while a chief is live. A `job-failed` is an app-launcher Job run that failed, see [Job events](#job-events-kind-job-failed). |
| `event` | v1 compatibility: `Stop` or `SessionEnd`. Kept so the v1 reader (`wait-event`) still names the event. A `needs-input` event carries its harness's own event name (`Notification` or `PermissionRequest`); readers name it by `kind`. |
| `launcher_sid`, `repo`, `number`, `agent`, `ts` | Unchanged from v1. `ts` is UTC, second precision, `Z` suffix. |
| `dispatch_id` | The per-dispatch handle `chief_ops.py dispatch` prints as `DISPATCH_ID=` and stores in the `chief-managed.json` row. `null` for a worker dispatched without one (for example straight from the launcher). |
| `excerpt` | The last 1,500 characters of the worker's final reply. For `needs-input`, the hook's one-line prompt instead (at most 300 characters). Claude names the pending tool call, found in the tail of the parent transcript and of recently active sub-agent transcripts: `permission: Bash <command head> (parent)` or `(sub-agent <id8>)`; no pending call found reads `pending call: unknown (payload message: ...)` or `(default text: ...)`, never a bare idle line. Codex: `Codex in fleet-config awaits approval to edit files`. It is never an empty string: no readable reply means `null`. |
| `excerpt_source` | `payload` (the harness's own last-assistant field), `transcript` (the tail of `transcript_path`), `prompt` (a `needs-input` event's classified prompt), or `null`. |
| `excerpt_unavailable` | Why `excerpt` is `null`. Present only then. |

Excerpt sources per harness, verified live:

| Harness | Source |
|---|---|
| Claude Code (2.1.301) | `Stop` payload `last_assistant_message`; fallback: the last assistant record in the bounded tail (256 KiB) of `transcript_path` |
| Codex (0.154.0) | `Stop` payload `last_assistant_message`; same transcript fallback (rollout `response_item` records) |
| Pi (0.84.4) | The `pi/extensions/session_state.ts` extension reads the session branch's newest assistant text and sends it as `last_assistant_message`; Pi sends no `transcript_path` |

Readers must ignore fields they do not know, and must accept v1 events (no `v`, `id`, `kind`, `dispatch_id` or excerpt fields). A reader that does not know a `kind` still counts and claims the event; the counting rule below never looks at `kind`.

### Job events (`kind: "job-failed"`)

An app-launcher Job run that failed is published as one `job-failed` event, so a failing Job wakes the chief the way a worker does. A Job has no worker session: `launcher_sid`, `number` and `dispatch_id` are always `null`. The launcher writes the file and never imports from `hooks/`.

```json
{
  "v": 2,
  "id": "6b1f0c2e9d3a4f58a7c4e1d2b3a49586",
  "kind": "job-failed",
  "event": "JobFailed",
  "job_id": "<the Job's id>",
  "job_name": "<the Job's display name>",
  "run_id": "<the failed run's id>",
  "exit_code": 1,
  "outcome": "failed",
  "trigger": "schedule",
  "script_path": "<the Job's script_path>",
  "repo": "life-os",
  "launcher_sid": null,
  "number": null,
  "agent": null,
  "dispatch_id": null,
  "ts": "2026-10-22T05:00:00Z",
  "excerpt": "<last ≤1500 chars of the run's output.log, or null>",
  "excerpt_source": "job-output"
}
```

| Field | Meaning |
|---|---|
| `job_id`, `job_name`, `run_id` | Which Job and which run. One event per failed run. |
| `exit_code` | The run's exit code; `null` for a reaped run that never reported one. |
| `outcome` | `failed` or `not-confirmed`, as `src.jobs_outcome.run_outcome` in app-launcher classifies the run. `not-confirmed` is a run whose result could not be established, never folded into `failed`. |
| `trigger` | `schedule`, `manual`, `chain` or `webhook`. The launcher passes any other trigger its run record holds through unchanged, so a reader renders an unknown value as-is and never rejects the event. |
| `script_path`, `repo` | The Job's script and the fleet repo that owns it, or `null` when no fleet repo does. |
| `excerpt`, `excerpt_source` | The last 1,500 characters of the run's `output.log`, with `excerpt_source: "job-output"`. `excerpt` and `excerpt_source` are `null`, with `excerpt_unavailable`, when the log is missing or empty. Job output can hold private data: it is for the chief's eyes only and never copied into an issue. |

The event `id` is the dedupe key, as for every event. Readers render the event as `job <job_name> failed exit=<n> run=<run_id> repo=<repo>` (`-` for a null `exit_code` or `repo`; ` outcome=not-confirmed` appended when `outcome` is not `failed`), then a `job_id=… trigger=… script=…` line, then the excerpt lines.

## Pending: the counting rule

A reader that only needs to know *whether there is work* (the doorbell) counts by **file name alone**. It never opens, parses or locks a file.

`pending = R + S`, where:

- **R**: entries directly in `chief-inbox/` that are regular files, whose name ends in `.json` and does **not** start with `.`.
- **S**: entries directly in `chief-inbox/.claimed/` that are regular files, whose name ends in `.json` and does not start with `.`, **and** whose claim is stale. A claim is stale when its name matches `^(\d+)-(.+)\.json$` and `now_ms - int(group 1) >= CLAIM_STALE_MS`, **or** when the name does not match that pattern at all (a claim with no readable time counts as stale, erring toward waking the chief).
- `CLAIM_STALE_MS` = **900,000** (15 minutes, `chief.mailbox.CLAIM_STALE_SECONDS`).

Never counted:

- anything whose name starts with `.` in either directory (temp files `.event.<random>.tmp`, and `.claimed` itself);
- names not ending in `.json` (`*.tmp`, notes);
- directories, including `wait-event`'s `.claim-<pid>-<ns>/` folders and anything inside them;
- subdirectories of `.claimed/`;
- a `.claimed/` claim younger than `CLAIM_STALE_MS`: a live reader holds it.

Edge cases, decided:

- **A `.json` that does not parse still counts.** The name rule cannot see content, and publishing is atomic, so this is junk rather than a half-written file. The next `inbox` run drops it, and the count falls by one.
- **An event older than the 24 h TTL still counts by name.** `inbox` drops it on the next claim, so the count self-heals after one ring.
- **`chief-inbox/` missing** means `pending = 0`; it is created by the first event.
- **`chief-inbox/` or `.claimed/` present but not listable** (permissions, or a file where the directory should be) means pending is **unknown**, never `0`. `chief_ops.py inbox --count` prints `PENDING=unknown reason=…` and exits 1.
- A file can move between the two listings mid-count (claimed, re-offered or acked). The count is advisory: it decides whether to ring, and the reader re-establishes the facts. A one-off miscount at that instant is harmless.

`chief_ops.py inbox --count` implements exactly this rule and prints `PENDING=<n>`.

## Reading (the chief)

Two readers coexist. The chief reads with `inbox` / `ack` when the doorbell rings, and arms `wait-event` only while `chief_ops.py wake` reports the launcher's `chief_doorbell` switch off or unknown. `wait-event` is retired after a soak.

**v2: `chief_ops.py inbox`, then `chief_ops.py ack <id…>`** (at-least-once):

1. **Re-offer.** Every stale claim in `.claimed/` (same staleness test as the counting rule) is moved back to the root under its same basename. `<claimed_at_ms>-<id>.json` is already a valid root name.
2. **Claim.** Each root event file, oldest name first, is renamed to `.claimed/<now_ms>-<id>.json`. The rename *is* the claim: a concurrent reader that loses the race skips the file.
3. **Read.** Unparseable files and events past the 24 h TTL are deleted (`dropped=`). A claimed file that cannot be *read* is never deleted (see [A failed read is not junk](#a-failed-read-is-not-junk)). The rest are printed with their `id` and excerpt, followed by an `ACK=chief_ops.py ack <ids>` line. Ids that came back through step 1 are marked `re-offered`.
4. **Ack.** After handling, `ack` deletes each id's file, from `.claimed/` or (if it was re-offered meanwhile) from the root. An id with no file prints `ACK_MISSING=<id>` and exits 1.

A chief that dies between claim and ack leaves stale claims behind. They count as pending again after 15 minutes, so a doorbell rings and the next `inbox` re-offers the same ids. The chief dedupes by `id`.

**v1: `chief_ops.py wait-event`** (exactly once, unchanged): it renames each root `*.json` into its private `.claim-<pid>-<ns>/` folder, reads it, deletes it, and exits on the first batch. It never looks in `.claimed/`. For v2 events its digest line gains ` kind=<kind>` and indented excerpt lines; the header, v1 lines and closing `Next:` line are unchanged. While both readers run, each event is read by exactly one of them, because both claim by rename and neither deletes a file it could not read.

### A failed read is not junk

On Windows, two racing renames of the same file can **both** succeed. The rename opens the source by name, then moves it through that handle, so a reader that opened it just before another's rename landed moves it again, out of the first reader's claim. Until that second move finishes, the first reader's read fails with a sharing violation; after it, the file is gone from the first reader's path. So a reader must never treat a failed read of a just-claimed file as junk and delete it:

- **Sharing violation / access denied:** retry briefly (five attempts, 10 ms doubling).
- **File not found:** the other reader holds it now and delivers it. Skip it and delete nothing.
- **Still unreadable after the retries:** `inbox` leaves it in `.claimed/`, so it is re-offered once stale. `wait-event` moves it back to the root for its next batch.

Each of these paths logs a line to stderr. Stdout is unchanged.

## Doorbell line

While the launcher's `chief_doorbell` switch (Settings > Chief > Doorbell) is on, the chief is idle with an empty input line, and pending is above zero, the doorbell types this fixed line, which carries no count and no data, and repeats it on later ticks until pending reaches zero:

```
⟳ chief-inbox: pending — run chief_ops.py inbox
```

Everything the chief acts on comes from `inbox`, never from the ring.

## Conformance

- `tests/test_chief_mailbox.py`: the counting rule case by case (temps, dot-files, non-`.json`, directories, `wait-event`'s claim folder, fresh vs stale vs unstamped claims, the exact stale boundary, an unlistable directory being unknown), claim, ack, re-offer with an injected clock, the two readers racing, and a claimed file that cannot be read yet (an injected read failure) never being dropped by either reader.
- `tests/probe_claim_race.py` (opt-in, not in the gate): the real two-claimer race, 60 events × 400 iterations, for both reader pairs. It fails if any event is lost, which is what a reader deleting a file after a failed read would cause on Windows.
- `tests/test_chief_wait_event_compat.py`: `wait-event`'s output and exactly-once claim on v1 and v2 events. It passes unchanged on the pre-v2 tree.
- `tests/test_chief_inbox.py`: the v2 writer through the real Claude, Codex and Pi adapter hooks, including every excerpt source and the `null`-with-reason case.
- `tests/test_chief_needs_input.py`: the `needs-input` writer through Claude's and Codex's attention hooks (one event, the pending-call excerpt over synthetic transcripts, no typed message, Telegram on no chief or a failed write), and the real `wait-event` digest line for it.
- `tests/test_chief_job_failed.py`: a synthetic `job-failed` event through the real `inbox` and `wait-event` (the head line, the detail line, the excerpt, a null `exit_code` and `repo`, `not-confirmed`), the event counted as pending by file name, claimed and acked like any kind, and v1, v2 and `needs-input` events rendering unchanged beside it.
