# Machine critical

Loaded on demand from the `/chief` skill (`SKILL.md`, same section name).

The machine itself being in trouble reaches you as a `kind=machine-critical` event in the same mailbox batch, never as typed text: the local-llm-hub health watch reached `act` on a critical metric (ephemeral ports above all). Contract: `docs/chief-mailbox.md`, "Machine events".

```
- machine <host> critical metric=<metric> value=<v> threshold=<t> [repeats=<n>] kind=machine-critical id=<id> dispatch=-
  first_seen=<utc> verdict=<hub verdict url>
  offender <name> pid=<pid> owner=<fleet|non-fleet|unknown> repo=<repo|-> value=<v>
```

A field the publisher could not fill reads `-`. The event is a host, not a worker: it has no `sid` and no `exchange`. `repeats=<n>` means later events of the same incident were folded in; the `ACK=` line names them all.

## First, for every event

1. **Dedupe by incident.** The incident key is `<host>/<metric>/<first_seen>`. Run `chief_ops.py note "machine-critical <host> <metric> incident <first_seen>: handling" --kind note` when you start, and read your recent journal before acting: an event for a key you already journaled and have not seen clear is the same incident, so ack it with no new work. A new `first_seen` on the same metric is a new incident.
2. **Read the verdict yourself.** `curl -s <verdict url>` (the hub's own page, plain `http://127.0.0.1:8000`), then confirm the top offender against it. The event's `owner` is the publisher's classification: trust it only when the verdict agrees. A verdict you cannot read is **not confirmed**: say so, never treat it as cleared and never as still critical.
3. **Classify the top offender** by `owner`, then act only as the matching case below allows.

## Fleet offender (`owner=fleet`, `repo=<repo>`)

The offender is a process of a fleet app. The only action allowed is that repo's own restart recipe, run exactly as its `CLAUDE.md` declares it (for example `tray.bat --restart` through a real Windows shell):

- No declared recipe, or one that says confirm first: do not improvise. `escalate` and `plan ask` below, and file the missing recipe as a `chore` in that repo.
- Run the recipe once, confirm the new build through the repo's own build-identity check, then re-read the verdict after the hub's next sample (never sooner than its sampling interval) and confirm the metric **fell**: the same offender gone or its `value` clearly lower. A reading that has not changed, or a verdict that cannot be read, is "not confirmed", never "fixed".
- Fell: journal the outcome (`chief_ops.py note "machine-critical <host> <metric> incident <first_seen>: restarted <repo>, <metric> fell <before> to <after>" --kind note --ref <repo>#<n>`), then ack.
- Did not fall, or the same offender is back within the hour: no second restart. `escalate` and `plan ask` below.
- **File a deduped bug for a leak.** A fleet process that grew a resource count past the threshold is a defect in the owning repo, whatever the restart did. `gh issue list --repo <owner>/<repo> --state open --search "<metric>"` first: an open issue that covers it gets a dated comment (host class, metric, the readings, the offender name) and nothing new is filed. Otherwise file a `bug` with `--assignee @me`, a self-contained public-safe body (metric, readings, the mechanism you can name, never a hostname or a private path) and queue it through the normal plan and worker cap.

## Non-fleet offender (`owner=non-fleet`)

A process the fleet does not own, a machine-level setting, a driver or a reboot. **Never act**: do not stop or signal the process, do not change a machine setting (dynamic port range, `TcpTimedWaitDelay`, services), do not reboot. `chief_ops.py escalate --file <path>` with the metric, readings, the offender's name and `first_seen`, then `plan ask` (below). Record `note` and ack.

## Unknown offender (`owner=unknown`, or the verdict disagrees)

Treat it as non-fleet: the allowed action is the same, none. Never restart a fleet app on a guess, because a restart of the wrong one costs warm state and fixes nothing. `escalate`, `plan ask`, `note`, ack. If the verdict names a fleet offender the event did not, handle it as a fleet offender from the verdict, and say so in the journal line.

## Escalate and ask

- `chief_ops.py escalate --file <path>`: one short message, host, metric, value against threshold, the offender and what you did or did not do. Once per incident, never per repeat.
- `plan ask "<question>" --ref <repo>#<n>` (or `--repo fleet-config`) with `--detail` (the readings, the offender, what was tried, what the verdict says now), `--recommend`, and up to four `--option "Label::description"`: the options are Roberto's actions (for example reboot at a time he picks, stop the process himself, wait one more cycle), never yours. Mechanical steps are not a question.

## Never

- Never touch the session-host on :8446: a restart kills every session. If the verdict points at it, print the command for Roberto in the `plan ask` detail and do nothing else.
- Never change a machine setting or a network parameter, and never kill a process the fleet does not own.
- Never run a restart you cannot name a recipe for, and never restart the same app twice for one incident.
- Never touch a `telegram:*` channel session.
- Keep issues and comments public-safe: no hostnames, private paths, process command lines or session links. A `note` line names the host only by the hub's short label (the incident key needs it) and carries the same other limits.

Ack the event (every id on the `ACK=` line) once handled, like any other.
