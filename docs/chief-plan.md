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
    {"id": "q1", "text": "Remember the last tab?", "ref": "app-launcher#1131"},
    {"id": "q2", "text": "Merge the two capture copies?", "repo": "life-os", "ref": "life-os#171",
     "question": "Merge the two capture copies?", "detail": "One session was captured twice and the copies diverged.",
     "recommendation": "Merge: one index, no lost turns.",
     "options": [{"label": "Merge", "description": "keep one copy", "recommended": true}, {"label": "Leave"}]}
  ]
}
```

| Field | Rule |
| --- | --- |
| `version` | `1`. |
| `updated_at` | UTC, `YYYY-MM-DDTHH:MM:SSZ`, stamped by the writer on every write. |
| `lanes[]` | One live worker lane per repo: `repo` and `status` required; `item` (`#N`), `session` and `model` optional. |
| `lanes[].status` | `building` \| `gate` \| `idle` \| `waiting`. |
| `queue[]` | The chief's intended order, first to last: `repo`, `ref`, `title` and `status` required; `note` optional. `repo` + `ref` is unique. |
| `queue[].ref` | `#N`, local to `repo`. |
| `queue[].status` | `queued` \| `building` \| `gate` \| `merged` \| `parked` \| `waiting-roberto`. |
| `queue[].note` | Free text, one short line. |
| `lanes[].model` / `queue[].model` | The model the lane or build item runs on: `opus` \| `sonnet`, picked per task by `docs/model-tiers.md` (fleet-config#1101). Optional, so older files stay valid. |
| `waiting_on_roberto[]` | `text` required: the short card line. `ref` optional and qualified (`repo#N`). Everything below is optional too (fleet-config#1049). |
| `waiting_on_roberto[].id` | Stable, unique within the list: a lowercase letter, then up to 31 letters, digits or hyphens. The writer assigns the next `q<N>` when none is given. Older files may have items without one. |
| `waiting_on_roberto[].repo` | The fleet repo the question is about. It matches `ref`'s repo when both are set; the writer derives it from `ref`. |
| `waiting_on_roberto[].question` / `detail` / `recommendation` | The full question, descriptive context, and the chief's recommendation. One line each, at most 1000 characters. |
| `waiting_on_roberto[].options[]` | 0–4 choices: `label` required (at most 80 characters, unique), `description` optional, `recommended` optional bool. At most one is recommended unless `multi`. |
| `waiting_on_roberto[].multi` | `true` when more than one option may be chosen; needs `options`. |

The writer is stricter than the readers. It writes only these fields, single-line strings (at most 200 characters unless a row above says otherwise), and the listed statuses.

## Questions for Roberto

A waiting item with `question` is one entry on the Board's one-shot answer sheet (app-launcher). Roberto answers the whole sheet at once; the answers reach the chief's terminal as one "Answers from the Board" message (app-launcher#1295). The chief acts on each answer and removes the item with `plan unwait <id>`. The Board never writes the plan file.

Every decision waiting on Roberto is an answerable question, and the writer enforces it (fleet-config#1102):

- `plan set <repo#N> --status waiting-roberto`, and `plan add … --status waiting-roberto`, are refused (exit 2, naming `plan ask`) unless an open `waiting_on_roberto` item with a `question` has that `ref`. Ask first, then set the status.
- `plan wait "<text>"` files its text as the question, the short form of `plan ask`. It never writes an item with nothing to answer.
- `validate()` does not enforce either rule, so a file written before them still loads.

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
chief_ops.py plan add app-launcher#1273 "Code tab: Chat by default" [--status gate] [--note "…"] [--at 1] [--model sonnet]
chief_ops.py plan set app-launcher#1273 --status merged [--note "4190488"] [--title "…"] [--model opus]   # --note "" / --model "" clear
chief_ops.py plan move app-launcher#1273 --to 1
chief_ops.py plan remove app-launcher#1273
chief_ops.py plan lane app-launcher gate --item "#1273" [--session <sid>] [--model opus]    # upsert, one lane per repo; an omitted --model keeps the lane's
chief_ops.py plan drop-lane app-launcher
chief_ops.py plan wait "Remember the last tab?" --ref app-launcher#1131      # = plan ask with only the question
chief_ops.py plan ask "<question>" [--repo R] [--ref R#N] [--detail "…"] [--recommend "…"] \
    [--option "Label::description" …(≤4)] [--recommended "Label"] [--multi] [--id ID] [--text "card line"]
chief_ops.py plan unwait q2                     # or the 1-based index, the repo#N ref, or the exact text
chief_ops.py plan clear
```

Success prints one line, `PLAN=written lanes=… queue=… waiting=… updated_at=…`; `wait` and `ask` append the new item's ` id=…`. `show` prints the plan as JSON with one row per line. A refusal prints `ERROR: <reason>` to stderr and exits 2.
