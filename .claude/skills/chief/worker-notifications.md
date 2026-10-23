# Incoming worker notifications

Loaded on demand from the `/chief` skill (`SKILL.md`, same section name).

When a **chief-dispatched** worker hits a real "blocked, needs input"
moment (a permission gate or an `AskUserQuestion` — not the routine 💤 idle
nag, which stays silent), it arrives as a `kind=needs-input` event in your
mailbox batch (`chief_ops.py inbox`, or the fallback `wait-event` digest),
never as text typed into your session:

```
- <repo>#<n>: needs input (<agent>) sid=<8> age=<age> kind=needs-input id=<id> dispatch=<id|->
  excerpt (prompt, <n> chars):
  | Codex in <project> awaits approval to edit files
```

The excerpt is the hook's one-line classification, not the worker's words;
`exchange` has the detail. Like every event it waits until your turn ends,
then rings (or wakes the waiter).
A session the user started manually still pings the user directly — this
only reroutes your own dispatches.

On one of these:

- Read that worker's state (`chief_ops.py exchange <sid>`) and decide.
  Most should be absorbed here: nudge it back to work
  (`chief_ops.py say <sid> --file <path>`) with no user-facing notification
  at all.
- Escalate to Roberto (`chief_ops.py escalate`) only when the worker is
  asking something only he can decide — don't relay every absorbed nudge
  upward, and don't let a genuine "I need Roberto" blend in with routine
  status (that's exactly the distinct-priority ping `escalate` exists for).
- With no live chief, or if the mailbox write fails, the notification goes
  to the human Telegram chat instead of being dropped — that fallback is not
  a bug to route around.

A worker's turn ending — finished, a question asked in plain text (its
`AskUserQuestion` is blocked), or gave up — reaches you the same way, as a
mailbox event (see "Waking"). So does a worker that died without firing any
hook: the launcher's reaper records it as a `session-end`. No worker event is
a typed message; the only line ever typed for them is the fixed, data-free
doorbell, and only while your input line is empty, so none collides with
Roberto typing through the same input channel.
