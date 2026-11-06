# A Job failed

Loaded on demand from the `/chief` skill (`SKILL.md`, same section name).

A failed app-launcher Job reaches you as a `kind=job-failed` event in the same mailbox batch, never as typed text:

```
- job <job_name> failed exit=<n|-> run=<run_id> repo=<repo|-> kind=job-failed id=<id> dispatch=-
  job_id=<id> trigger=<schedule|manual|chain|webhook|…> script=<path>
  excerpt (job-output, <n> chars):
  | <last lines of the run's output>
```

`exit=-` is a reaped run with no exit code; `outcome=not-confirmed` on the head line means the launcher could not establish the result, so say "not confirmed", never "failed". The event is a job, not a worker: it has no `sid` and no `exchange`.

- **Investigate.** Read the excerpt, then the run's full `output.log`, the job definition and the owning script. Decide whether it is transient (a one-off network or device blip, already green on a later run) or a real defect or gap.
- **Dedupe.** An open issue that already covers the failure gets a dated comment and nothing new is filed. Investigate a job at most once per day: a red daily job does not restart the investigation every morning, so later events for the same `job_id` that day are acked with no new work.
- **Fix through the normal plan.** File the issue in the owning repo (`repo=`), then follow the lane cap, the plan gate and `plan ask` exactly as for any other work: a `bug` gets a build lane, a shape decision gets a plan-only lane and a `plan ask`. A transient failure is one drawer line and no issue.
- **Never re-run a Job yourself.** A Job may send mail, touch live stores or drive real devices. A re-run is Roberto's "Run now"; verify a fix by reading the next scheduled run. The one exception is `chief_ops.py heal rerun` under self-heal, below: never `POST /api/jobs/<id>/run` by any other route, and never edit a job definition (`jobs.json` is Roberto's; `rerun_safe` is his to set).
- **Private data stays out of issues, comments and briefs.** Job output can hold private data (Life OS above all) and this repo is public. Name the job, the exit code and the mechanism; never quote the output.

## Self-heal (Settings > Chief `chief_job_self_heal`)

`chief_ops.py heal` reads the switch on every call. When it prints `HEAL=off reason=...` (off, missing or unreadable), the rules above are the whole story: record nothing, re-run nothing. When the switch is on:

- **Record every investigated failure** with its cause class: `heal record <job_id> --run <run_id> --cause transient|defect|config|dependency|environment|unknown [--issue <repo>#<n>]`. A `HEAL_REPEAT=` line means the same cause class hit this job again within 30 days: say so in the drawer and propose a structural fix (`plan ask`) instead of another patch.
- **Dispatch the fix at once.** A real defect is filed and dispatched as a build lane straight away, within the worker cap, with no `plan ask` first. A fix that is a shape decision or destructive keeps the plan-only lane and `plan ask`. A transient failure stays one drawer line (still recorded).
- **Link the fix.** Once the fix lane's PR merges and you have verified it, `heal record <job_id> --run <run_id> --pr <repo>#<n>`.
- **Re-run once, only through `heal rerun`.** `heal rerun <job_id> --run <run_id>` fires the job only when the job carries `rerun_safe: true`, the PR is merged and its merge commit is in the primary checkout's `HEAD`, and this failure was never re-run before. `HEAL_REFUSED=` and `HEAL=unknown` are final for this turn: never route around them. A `reason=not-rerun-safe` (or `rerun-safe-absent`) refusal gets one Board line: the fix is live, run it when ready. `HEAL=unconfirmed` means the POST may have started a run: check the Jobs tab, never retry.
- **Read the result.** `heal outcome <job_id> --run <run_id>` after the re-run finishes. `needs_owner=yes`, or a `HEAL_NEEDS_OWNER=` line from `heal record` when a `job-failed` event is the re-run itself, means the fix did not hold: put it to Roberto with `plan ask`, file nothing new and re-run nothing.

Ack the event once handled, like any other.
