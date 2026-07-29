# Fleet Board session-state capability matrix

`sessions-state.json` (`hooks/session_state.py`, fleet-config#91) is the one
writer behind the app-launcher Board's "what needs me now" column. Four
agents write rows into it today, each through its own event surface — this
is the per-agent map of which semantic states each can actually *prove*
without inferring from process age, transcript text, or directory alone
(explicitly out of scope, fleet-config#349).

| Semantic state | Claude Code | Codex | Pi | Grok |
|---|---|---|---|---|
| `working` | `UserPromptSubmit` hook | `UserPromptSubmit` hook | `input` extension event | `UserPromptSubmit` hook, reached through Grok's own Claude-settings compat scan (see below) — **verified live** |
| `needs-you` | `Stop` hook, or the `Notification` hook's permission-prompt piggyback (`notify_on_idle.py`) | `Stop` hook, or `PermissionRequest` hook (the Codex analog of Claude's permission-prompt piggyback) | `agent_settled` extension event ("fires when Pi will not continue running automatically" — the closest available analog to Claude's `Stop`) | `Stop` hook — **verified live** |
| `idle` | `Notification` hook's idle-nag piggyback (`notify_on_idle.py`) | not available — Codex exposes no idle-nag-shaped event today | not available — Pi's docs state no event distinguishes idle from active work | **not proven.** Grok *does* expose `Notification` and `PermissionDenied`, so the signal exists in principle — but `notify_on_idle.py`'s classifier is written against Claude's notification sub-type vocabulary and has not been verified against Grok's payload, so nothing is written. Degrades to the Board's `unknown` rather than being guessed. |
| row removal on session end | `SessionEnd` hook | not available — Codex's hook vocabulary (`SessionStart, SubagentStart, PreToolUse, PermissionRequest, PostToolUse, PreCompact, PostCompact, UserPromptSubmit, SubagentStop, Stop`) has no session-end-shaped event; a Codex row ages out via the writer's existing 24h prune, same as any hard-killed session | `session_shutdown` extension event | `SessionEnd` hook — **verified live** |

"Verified live" above means exactly that: a real `grok -p` session against
grok 0.2.114, polled at 60 ms, produced the transition
`working` → `needs-you` → row deleted, every row carrying `agent: "grok"`.
The `idle` cell stays unproven on purpose — an unverified state is recorded as
unavailable, never inferred.

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
- **Grok** — *no adapter module at all*, deliberately. Grok scans
  `~/.claude/settings.json` for hooks by default (`[compat.claude] hooks`), so
  `session_state.py` is already wired into Grok's `UserPromptSubmit` / `Stop` /
  `SessionEnd` through Claude's own entry. What was missing was not plumbing but
  translation: Grok's stdin envelope is camelCase with lower_snake event values
  (`hookEventName: "user_prompt_submit"`) where Claude's is snake_case with
  PascalCase (`hook_event_name: "UserPromptSubmit"`), so the writer saw an
  unrecognized event and silently wrote nothing. `_lib.normalize_payload()`
  translates once, at the single `read_stdin_json()` entry point every hook
  shares (fleet-config#491). A fourth adapter would have duplicated wiring that
  already existed.

The Codex and Pi adapters pass a `default_agent` (`"codex"` / `"pi"`) into
`session_state.upsert_from_payload()`; Grok instead carries its identity in the
normalized payload itself. Attribution precedence is `APP_LAUNCHER_AGENT` →
payload harness hint → `default_agent` → `claude`, so a launcher-injected agent
value always wins, exactly as it already does for Claude (fleet-config#345), and
a session opened outside App Launcher still reports its real harness. That middle
term is load-bearing precisely because Grok arrives through *Claude's* own
wiring: without it, every external Grok row would confidently claim to be a
Claude session — a fabricated identity of exactly the kind this matrix exists to
prevent.

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

**Grok is the exception**: unlike Codex and Pi, it fires the full turn-boundary
set in headless `grok -p` mode, so every claimed cell above was confirmed
end-to-end against a live grok 0.2.114 session rather than from the schema.
Two behaviours only that live run could have surfaced:

- Grok fires a second, observe-only `Stop` at session end (`reason` is
  `channel_closed`/`shutdown`) **after** `SessionEnd` has already fired. Taken at
  face value it re-creates the row `SessionEnd` just deleted, stranding a dead
  session on the Board as `needs-you` until the 24h prune. `normalize_payload()`
  maps that fire to a name no hook matches, so it stays inert.
- Grok's hook runner reported our exit code `2` as `1`, and it fails *open* on any
  code other than 2 — so a guard printed its refusal and the dangerous command
  ran anyway. Grok's documented escape hatch (a `deny` decision on stdout is
  honored regardless of exit code) is what `_lib.block()` now also emits for a
  Grok-sourced payload. A green unit test would never have caught this; only
  watching a real session get blocked did.
