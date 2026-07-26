# Independent-review gate

## Why this exists

A skill that both writes code *and* decides whether to ship it is a single
point of failure: the same context that produced a bug is the context judging
whether the bug is there. `.claude/workflows/cleanup-fleet-all.js` already
solved this for the unattended fleet-cleanup path with a three-role agent
split — **build**, **validate**, **execute** — where the validator is a fresh
agent with no memory of the build, reviewing the diff adversarially but
leniently before anything ships. That pattern was bespoke to one script. This
doc extracts it into a reusable fleet convention so other build-then-ship
skills can adopt the same shape deliberately, rather than each inventing (or
omitting) their own review gate (fleet-config#408).

## The pattern

Three independent agent roles per unit of work, each with its own fresh
context:

1. **Build** — implements the change, runs the project's own verification
   gate, and **stops** before shipping. Reports a schema-validated verdict:
   built/failed, the verification result, and (on failure) whether a retry is
   worth attempting.
2. **Validate** — a *different* agent invocation with no memory of the build.
   Fetches the issue's acceptance criteria fresh (never trusts the builder's
   restatement of them), reads the diff against the base branch, **independently
   re-runs the verification gate** rather than trusting the builder's report of
   PASS, and judges whether the diff plausibly and reasonably satisfies the
   issue **and** conforms to the repo's own `CLAUDE.md` conventions — not just
   "did the mechanical gate pass." Default stance is lenient: fail only on
   something a human reviewer would actually reject (gate genuinely fails, diff
   doesn't touch what the issue asked for, an obvious bug), never on style
   preference.
3. **Execute** — ships an already-validated change: push, PR, CI-advisory wait,
   merge, tray restart. Never guesses past a failure; reports FAILED with a
   reason rather than force-completing.

The gate is a **fixed lookup on each agent's own schema-validated verdict**
(`pass: true/false`), not a fourth LLM call re-interpreting an already-decided
result — that keeps a long or unattended run from drifting or forgetting state
between rounds.

## Failure handling is not one-size-fits-all

`cleanup-fleet-all.js` runs **retry-then-escalate**: a validation rejection
feeds its feedback verbatim into a second build attempt (`MAX_ROUNDS = 2`);
a second failure escalates (branch left in place for a human, never
force-merged). That shape fits its context — a fully unattended, scheduled,
no-human-present run, where "stop and wait for a human" would just wedge the
job.

That is **not** automatically the right shape for every adopter. A skill
invoked interactively — a human typed the slash command and is available in
the same session — should prefer **stop-and-report**: surface the validator's
feedback and let the human decide whether to retry, adjust scope, or abandon,
rather than silently spending a second build round on their behalf. Decide
this per adopting skill based on its actual invocation context (scheduled and
unattended vs. interactively invoked), not by copying `cleanup-fleet-all`'s
choice wholesale.

## Adopting the pattern

A skill is a candidate if it both **builds and ships** in the same run with no
mandatory human checkpoint in between. Concretely:

- **Clear fit: `/issue-yolo`.** Its Phase 3 ("Validate hard") is explicitly
  self-review — the same agent that wrote the code runs every gate, and step
  3g says outright "the reviewer in this run is you." It is the highest-blast-
  radius unattended-*shipping* flow in the fleet workflow set (it merges to
  `main` with no human checkpoint) and currently has zero independent review.
  Decision: **adopt** — see fleet-config#433 for the concrete follow-up scoping
  this change. Failure handling: **stop-and-report**, matching Phase 3's
  existing "if anything fails, stop" ethos — `/issue-yolo` is invoked
  interactively, so there is already a human on the other end of the run to
  decide on a retry, unlike `cleanup-fleet-all`'s unattended context.
- **Already effectively covered: `/issue-finish`'s hard-tier path via
  `/cleanup-fleet` and `/issue-batch`.** These build-and-stop for human review
  before `/issue-finish` ships — the human *is* the independent reviewer here,
  so layering an agent-based validator on top would be redundant with the
  review step that already exists by construction.
- **Not applicable: scheduled read-only skills** (`/audit-fleet`,
  `/design-sweep`, `/system-map`, `/config-map`, `/sota-watch`, etc.). These
  file issues or regenerate data files; they don't merge code changes to a
  repo's `main`, so there is no "ship" step to gate.

## Decision log

- **2026-07-26 (fleet-config#408):** Extracted the build/validate/execute
  split from `cleanup-fleet-all.js` into this reusable convention. Decided
  `/issue-yolo` is the first adopter (see fleet-config#433 for the scoped
  follow-up); `/issue-finish`'s hard-tier path is already effectively covered
  by its existing human-review checkpoint; scheduled read-only skills are out
  of scope entirely. Failure handling for `/issue-yolo` specifically:
  stop-and-report, not retry-then-escalate, because it runs interactively with
  a human already present.
