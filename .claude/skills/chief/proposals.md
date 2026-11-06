# Weekly proposals

Loaded on demand from the `/chief` skill (`SKILL.md`, same section name).

The weekly learning-log run offers candidate workflow issues as one `kind=proposals` event in your mailbox batch, never as typed text. The run writes it only while Settings > Chief `chief_weekly_proposals` is on, so no event means the switch is off and there is nothing to do.

```
- weekly proposals n=<k> week=<YYYY-Www> kind=proposals id=<id> dispatch=-
  [<pid8>] <repo>: <title> (<type>) -- <reason>
```

- **Check it first.** `chief_ops.py proposals show --week <week>` lists the rows with their `pid` prefix and answer. A candidate already `accepted` or `declined` was handled before a re-offer: skip it. Nothing pending → ack the event and stop.
- **Ask once.** Post one `plan ask "File which of these?" --multi --repo fleet-config`, one `--option "<repo>: <short title>::<reason>"` per candidate (at most four, the Board's limit; each label at most 80 characters), `--recommended` on the ones you would file, and `--detail` naming the week. Put the pids in `--detail` so the answer maps back to the rows. Set the plan item waiting on that ask like any other decision.
- **Record every answer.** On the Board's answer, for each candidate: picked → file it with `/issue-add` (public repos only, public-safe text, the reason and evidence refs as the body's grounding), queue the issue in the plan, then `chief_ops.py proposals answer <pid> --accepted --issue <repo>#<n>`. Not picked → `chief_ops.py proposals answer <pid> --declined [--note "<the owner's words, public-safe>"]`. The ledger is what next week's run reads, so an unrecorded answer makes the run propose the same idea again.
- **Ack and unwait.** `plan unwait <id>` once every candidate is answered, then ack the event like any other.
- **Never invent.** Do not add, reword or re-rank candidates, and do not file one the owner did not pick. A candidate whose repo is not public, or whose reason quotes private data, is not filed: decline it with the note `private-content` and say so in one drawer line.
- **Switch or ledger trouble.** `PROPOSALS=off`, `PROPOSALS=unknown` or `PROPOSALS_REFUSED` is final for this turn: report it in one drawer line and never route around it by writing the ledger by hand.
