# Declared state machines

Each lifecycle in this repo that had been spelled as scattered status strings is declared once: a closed `StrEnum`, the one function that derives it (where the facts need ordering), and a `[[state_machines]]` entry in `.fleet.toml`. The conventions ratchet's `state-literal`, `state-set-repeat` and `state-write` rules then see a plain string compare as a suspect, so the baseline is empty and stays empty. The convention is project-scaffolding's `docs/implicit-state-machines.md`.

Every enum is a `StrEnum`, so the value stored in a manifest, plan file or JSON report is the same string it always was.

| Machine (`.fleet.toml` name) | Type and module | Derived by | Read by |
|---|---|---|---|
| `scheduled-run-outcome` | `RunState` in `skills/_lib/scheduled/run_outcome.py` | `decide` | the exit code, verdict line, retry gate and delivery override ([scheduled-runners.md](scheduled-runners.md)) |
| `acceptance-audit-state` | `AuditState` in `skills/_lib/acceptance_audit.py` | `audit_state` | the `ACCEPTANCE=` line and exit code `/issue-finish` reads |
| `steer-delivery` | `DeliveryState` in `skills/_lib/chief/steer_delivery.py` | `finalize_delivery` | `chief_ops say --verify` and its verdict line |
| `proposal-answer` | `ProposalAnswer` in `skills/_lib/chief/proposals.py` | `effective_answer` | the proposal screen, accept rates and the digest |
| `chief-queue-row` | `QueueStatus` in `skills/_lib/chief_plan.py` | `validate` | `chief_plan`, `chief/drain.py`, `chief/migrate_parked.py` and the Board |
| `probe-status` | `ProbeStatus` in `skills/_lib/discovery_probe.py` | the probe functions that build each report | the probe's exit code |
| `backup-leg-status` | `LegStatus` in `hooks/backup/config.py` | `leg_status` | the leg manifest, the exit-code aggregation and the freshness check |

Closed vocabularies that are not this repo's lifecycle are a named constant or enum, not a machine: a Claude `task_notification` and a Codex tool item's terminal status (`ChildStatus`, `ToolItemStatus` in `skills/_lib/scheduled/runner_adapters.py`), a fleet-health machine's capture coverage (`Coverage` in `.claude/skills/fleet-health/capture.py`), a context-purge repo's `REPO_STATUSES`, and a cleanup lane's `LANE_STATUS` in `.claude/workflows/cleanup-fleet-all.js`.

## Proof

Each machine has a test marked `state-machine-proof: <name>` that shows an illegal transition refused: a verdict that must not be reached from a given set of facts, an unrecognised value, and that the set of states is closed. project-scaffolding's state scoreboard counts the markers.

## Adding a state

Add the member to the enum, handle it in the one derive function, and add a row to that machine's proof test. A reader that needs a fact the state does not carry gets the fact added to the derive function's inputs; it never re-derives the mode itself.
