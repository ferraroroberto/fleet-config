# Chief plan file: format v1

The contract between the fleet chief, which writes the plan, and app-launcher's "Chief's plan" Board card, which reads it (fleet-config#1034, app-launcher#1279). This page is the one definition. The writer (`skills/_lib/chief_plan.py`) and every reader follow it.

## Location and ownership

- `~/.claude/hooks/state/chief-plan.json`, next to `chief-handover.md`. It's machine-local: the state dir is gitignored and the file is never committed. `CLAUDE_HOOKS_STATE_DIR` overrides the directory, which is how tests stay hermetic.
- The chief writes it only through `chief_ops.py plan …`, never by hand or with the Write tool. The helper validates the whole document and replaces the file atomically (a temp file, then `os.replace`, under the hooks-state lock). A mutation that would leave the file invalid writes nothing and exits 2.
- The helper refuses a file it can't parse, and a file with an unsupported `version`. It never overwrites one silently. `plan clear` is the explicit reset.

## Shape

```json
{
  "version": 1,
  "updated_at": "2026-09-26T14:40:00Z",
  "lanes": [
    {"repo": "app-launcher", "session": "<session id>", "item": "#1273", "status": "gate"}
  ],
  "queue": [
    {"repo": "app-launcher", "ref": "#1273", "title": "Code tab: Chat by default on desktop", "status": "gate", "note": ""},
    {"repo": "automation", "ref": "#135", "title": "parking burst trial", "status": "queued", "note": "before Thu 1 Oct 16:00"}
  ],
  "waiting_on_roberto": [
    {"text": "Remember the last tab?", "ref": "app-launcher#1131"}
  ]
}
```

| Field | Rule |
| --- | --- |
| `version` | `1`. |
| `updated_at` | UTC, `YYYY-MM-DDTHH:MM:SSZ`, stamped by the writer on every write. |
| `lanes[]` | One live worker lane per repo: `repo` and `status` required; `item` (`#N`) and `session` optional. |
| `lanes[].status` | `building` \| `gate` \| `idle` \| `waiting`. |
| `queue[]` | The chief's intended order, first to last: `repo`, `ref`, `title` and `status` required; `note` optional. `repo` + `ref` is unique. |
| `queue[].ref` | `#N`, local to `repo`. |
| `queue[].status` | `queued` \| `building` \| `gate` \| `merged` \| `parked` \| `waiting-roberto`. |
| `queue[].note` | Free text, one short line. |
| `waiting_on_roberto[]` | `text` required; `ref` optional and qualified (`repo#N`). |

The writer is stricter than the readers. It writes only these fields, single-line strings of at most 200 characters, and the listed statuses.

## Reader rules

Readers must be tolerant, so the card never errors:

- A missing file or an empty `queue` renders an empty card.
- Unknown fields are ignored.
- An unknown status value renders as a neutral chip.
- An unparseable file, or any `version` other than `1`, renders the empty card with a quiet "plan unreadable" note.
- Show `updated_at` as "updated N min ago". The file outlives the chief session, so a stale stamp is how the card tells a live plan from an abandoned one.

Adding a field to v1 is backwards-compatible, because readers ignore what they don't know. Changing a field's meaning or removing one needs `version: 2`.

## Writer CLI

Each update is one line. Queue items are addressed as `<repo>#<N>`.

```text
chief_ops.py plan show
chief_ops.py plan add app-launcher#1273 "Code tab: Chat by default" [--status gate] [--note "…"] [--at 1]
chief_ops.py plan set app-launcher#1273 --status merged [--note "4190488"] [--title "…"]   # --note "" clears
chief_ops.py plan move app-launcher#1273 --to 1
chief_ops.py plan remove app-launcher#1273
chief_ops.py plan lane app-launcher gate --item "#1273" [--session <sid>]    # upsert, one lane per repo
chief_ops.py plan drop-lane app-launcher
chief_ops.py plan wait "Remember the last tab?" --ref app-launcher#1131
chief_ops.py plan unwait app-launcher#1131      # or the 1-based index, or the exact text
chief_ops.py plan clear
```

Success prints one line, `PLAN=written lanes=… queue=… waiting=… updated_at=…`. A refusal prints `ERROR: <reason>` to stderr and exits 2.
