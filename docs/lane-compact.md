# Lane handoff and compaction

A session the fleet chief dispatches (a *lane*) can run long enough to fill most of a 1M-token window. Claude Code compacts on its own only near the limit, at whatever moment that falls (possibly mid-gate), and keeps a generic summary; accuracy drops well before then. So a lane stops at a turn end once its context passes a per-model threshold, writes a handoff of its own, compacts, and gets the handoff back afterwards. It is the lane form of the chief's own threshold hold and handover re-injection, and rides the same two hook entries, so it needs no extra `settings.json` wiring.

## How it runs

1. **Reading.** `statusline-command.ps1` writes `hooks/state/session-context/<session_id>.json` on every render: `used_percentage`, `context_window_size` and `model_id`, straight from Claude Code's statusline payload. Files older than three days are swept by the same script.
2. **Hold.** At every turn end `hooks/chief_waiter_stop.py` calls `lane_compact.stop_refusal()` for any session that is not the chief. It acts only for a launcher session listed in `chief-managed.json` whose snapshot is fresh (15 minutes) and at or over its threshold, and only when no gate is running in that lane's tree. It then holds the turn end once with the instruction to write the handoff to `hooks/state/lane-handoffs/<launcher-sid>.md` and run `chief_ops.py lane-compact`.
3. **Compact.** `chief_ops.py lane-compact` refuses for the chief, outside a launcher session, without a handoff written in the last 10 minutes, or while a gate runs in the caller's tree; otherwise it posts `/compact <focus>` to the lane's own input, which runs when the turn ends.
4. **Resume.** On the `compact` session start, `hooks/chief_handover_sessionstart.py` hands the handoff back through `additionalContext` (cut at 6,000 characters with a pointer to the file), in whatever repo the lane works. A handoff older than an hour is not re-injected, so an unrelated later `/compact` does not resurrect it.

The hold is debounced: one per 10 minutes per lane (`lane-compact-nudge/<sid>`), and none within 10 minutes of a queued compaction (`lane-compact-queued/<sid>`), because the percentage still reads high until the compaction runs.

## No compaction during a gate

`skills/_lib/gate_evidence.py run` keeps `hooks/state/gate-inflight/<pid>.json` (its pid and repo path) for the gate's lifetime. Both the hold and `lane-compact` treat a live marker whose repo contains the lane's working directory as a running gate; a marker whose pid is gone is ignored (and removed by the next gate), and an unreadable one counts as running, so the lane never compacts on a guess. A gate run outside the evidence helper is not seen.

## Thresholds

`hooks/lane_compact/thresholds.toml` is the one config, tracked so every machine gets the same values:

| Key | Meaning |
|---|---|
| `enabled` | `false` turns the hold off entirely |
| `default` | percentage used when nothing more specific matches |
| `[windows]` | by context-window size in tokens (`1000000 = 60`, `200000 = 75`) |
| `[models]` | by model id, exact first, then the longest matching prefix; wins over the window |

Values are percentages from 1 to 99; anything else is skipped and the next rule applies. `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` is not used: it can only lower Claude Code's own trigger and is not reliable on Windows, so the threshold check is ours.

## Failure modes

Every path fails open: no snapshot, a stale one, a null percentage (early in a session and right after `/compact`), an unreadable registry or config, or an exception in the check all allow the turn end and log why. The chief keeps its own Settings-driven threshold and `self-compact`; this path never runs for it.

Claude Code only. Other harnesses have no statusline snapshot, so their lanes are always allowed; see the parity gap in [cross-agent-parity.md](cross-agent-parity.md).
