# Chief diagnostics run

Follow this when the owner asks for a diagnostics run. It checks this machine's chief setup the same way on every computer and returns one redacted Markdown report that can be brought back and turned into fixes. `chief_ops.py` is `E:/automation/fleet-config/.venv/Scripts/python.exe skills/_lib/chief_ops.py`, run from the fleet-config checkout. The report shape matches app-launcher-lite's `chief-diagnostics.md`, so one template reads both.

## Rules for the whole run

- Read-only, except the optional round trip below: no `git` writes, no restarts, no deletes, no `sweep --apply`, no `plan`, `note` or `proposals` writes, no `say` to other sessions.
- A fact you could not establish is `unknown`. Never write `pass` for it, and never fill it from memory.
- Redact before you write: the home folder is `<home>`, the user name `<user>`, the host name `<host>`, any other absolute path `<path>/<last part>`, a session id `<sid>` (an 8-character prefix is fine inside this terminal, never in the report). No transcripts, job output, tokens or issue bodies go into the report.

## Step 1: doctor

Run `chief_ops.py doctor`. It prints one `CHECK <area>.<name>=pass|fail|unknown ...` line per fact (install, verbs, mailbox, logs), up to 10 `LOG ...` groups, and a last line `DOCTOR=pass|fail|unknown fails=<n> unknowns=<n>`. Its output is already redacted. Keep every line for the report. A missing last line means the run did not finish: the verdict is `unknown`.

## Step 2: the live round trip (only on the owner's OK)

Ask first, with `plan ask` and in this turn's reply: "The round trip dispatches one throwaway lane on `<repo>#<N>` that does no work, and stops only that lane. OK?" `<repo>` has no live session (`chief_ops.py sessions`); `<N>` is one of its open issues with no owner-check label. Run it only when the owner says yes to that question. Otherwise mark every round-trip row `skipped (no OK)`.

Write this brief to a scratch file: `Diagnostics round trip. Do no work on this issue: no branch, no worktree, no edits, no comments, no gate. Reply with the single word OK and end your turn.`

1. **Chief-managed lane.** `chief_ops.py dispatch <repo> <N> --model sonnet --effort low --brief-file <file>`. `dispatch` marks it chief-managed, so its turn end must reach the mailbox. Record `DISPATCHED session=<sid>` and `DISPATCH_ID=`. A `REFUSED=` line ends the round trip as `fail` with that line; `DISPATCH=unconfirmed` follows the skill's rule (poll `sessions`, never dispatch again).
2. **Turn-end and doorbell.** Every 15 s for up to 3 minutes run `chief_ops.py inbox --count`. When the doorbell is on (`wake` printed `doorbell=on`), the ring arrives in your terminal as a mailbox notice: note whether it came. When `PENDING` is above 0, run `chief_ops.py inbox`, find the turn-end event for `<sid>` with the same `dispatch_id`, and run its `ACK=` command (ack other events only after handling them as usual). No event in 3 minutes is a fail.
3. **The card.** `chief_ops.py board --json`: find the row for `<sid>` and record its column and status. A finished turn reads as your turn with an idle status.
4. **Stop and session-end.** `chief_ops.py stop <sid>`. Then poll `inbox --count` again for up to 2 minutes for that session's session-end event; ack it. No event is a fail.
5. **Plain session (expected silence).** Your own session is a plain one: it is never chief-managed, so none of the events in steps 2 and 4 may carry your own sid (`chief_ops.py chief-sid`). An event from it is a fail; silence is a pass.

Stop only the lane this step dispatched. If it will not stop, report its sid prefix and leave it.

## Step 3: your own failed calls

Look back over this session's own tool calls (the transcript as you see it, not others' files): every call that exited non-zero, printed `REFUSED=`, `UNCONFIRMED=`, `UNREACHABLE=` or `ERROR:`, or that you retried with other arguments. For each, name the chief skill line that led you to it and say what was misleading. Count them.

## Step 4: the report

Reply with one Markdown report, in this order:

1. **Verdict:** the `DOCTOR=` line, then the round trip as `pass`, `fail`, `unknown` or `skipped (no OK)`.
2. **Counts:** checks passed, failed, unknown; log failure groups; your own failed tool calls.
3. **Top 5 problems**, worst first, each with its evidence (the `CHECK` or `LOG` line) and a suggested fix (for example "run `install.ps1`", "fast-forward the checkout", "restart the launcher through its recipe", "file an issue on fleet-config: ...").
4. **Per-check table:** `| Area | Check | Result | Detail |`, one row per `CHECK` line and one per round-trip step.
5. **Log groups:** the `LOG` lines as a table.
6. **Skill lines that misled you**, from step 3.

End the turn after the report. Filing issues from it is the owner's call.
