# Fleet Board session-state capability matrix

`sessions-state.json` (`hooks/session_state.py`, fleet-config#91) is the one
writer behind the app-launcher Board's "what needs me now" column. Three
agents write rows into it today, each through its own event surface — this
is the per-agent map of which semantic states each can actually *prove*
without inferring from process age, transcript text, or directory alone
(explicitly out of scope, fleet-config#349).

| Semantic state | Claude Code | Codex | Pi |
|---|---|---|---|
| `working` | `UserPromptSubmit` hook | `UserPromptSubmit` hook | `input` extension event |
| `needs-you` | `Stop` hook, or the `Notification` hook's permission-prompt piggyback (`notify_on_idle.py`) | `Stop` hook, or `PermissionRequest` hook (the Codex analog of Claude's permission-prompt piggyback) | `agent_settled` extension event ("fires when Pi will not continue running automatically" — the closest available analog to Claude's `Stop`) |
| `idle` | `Notification` hook's idle-nag piggyback (`notify_on_idle.py`) | not available — Codex exposes no idle-nag-shaped event today | not available — Pi's docs state no event distinguishes idle from active work |
| row removal on session end | `SessionEnd` hook | not available — Codex's hook vocabulary (`SessionStart, SubagentStart, PreToolUse, PermissionRequest, PostToolUse, PreCompact, PostCompact, UserPromptSubmit, SubagentStop, Stop`) has no session-end-shaped event; a Codex row ages out via the writer's existing 24h prune, same as any hard-killed session | `session_shutdown` extension event |

## Why the gaps are safe

Any state a writer can't prove is never fabricated. `app-launcher/src/board_sessions.py`
already renders anything outside `{working, needs-you, idle}` as `unknown`
(and a session with no state row at all also renders `unknown`) — so a
missing `idle` signal for Codex/Pi, or a missing shutdown-cleanup hook for
Codex, degrades to the Board's existing fallback rather than requiring new
code on either side of the join.

## Adapters

- **Codex** — `hooks/session_state_codex.py`, wired into `codex-hooks.json`'s
  `UserPromptSubmit` / `Stop` / `PermissionRequest` entries. Codex's hook
  payload shares the same field names as Claude Code's (`session_id`, `cwd`,
  `hook_event_name`, `transcript_path` — confirmed against
  developers.openai.com/codex/hooks), so this is a thin event-map wrapper
  around `session_state.upsert_from_payload()`, not a separate parser.
- **Pi** — `pi/extensions/session_state.ts` (sibling to the existing
  `statusline.ts`), subscribing to `input` / `agent_settled` /
  `session_shutdown`. It shells out to `hooks/session_state_pi.py` on each
  event rather than duplicating the atomic-write/prune logic in TypeScript —
  `session_state.py` documents itself as the sole writer, and this keeps
  that true for all three agents.

Both adapters pass a `default_agent` (`"codex"` / `"pi"`) into
`session_state.upsert_from_payload()`, which only applies when the process
carries no `APP_LAUNCHER_SESSION_ID`/`APP_LAUNCHER_AGENT` (a session opened
outside App Launcher) — a launcher-injected agent value always wins, exactly
as it already does for Claude (fleet-config#345).

## Composing with existing notifications

Codex's separate `notify` mechanism (`~/.codex/config.toml`, occupied by the
CUA `codex-computer-use.exe` "turn-ended" notifier) is untouched by this
work — it only ever fires one event (`agent-turn-complete`) and can't be
extended, but the `hooks.json` events used here run through a completely
separate subsystem, so nothing about the existing notifier changes.

## Verification caveat

Both CLIs skip turn-boundary hooks in their non-interactive/print modes
(`codex exec`, `pi -p --no-session`) — confirmed live during development:
`codex exec` fires `PreToolUse`/`PostToolUse` but never `UserPromptSubmit`/
`Stop`; `pi -p` fires `input` and `session_shutdown` but never
`agent_settled` (there's no "settle" point in a one-shot run). The
`input`/`session_shutdown` path was verified end-to-end live; the
`UserPromptSubmit`/`Stop`/`PermissionRequest` (Codex) and `agent_settled`
(Pi) paths are verified by unit tests plus the officially documented payload
schema, not by a live one-shot trigger — a real interactive session is the
next spot-check if this ever needs re-confirming.
