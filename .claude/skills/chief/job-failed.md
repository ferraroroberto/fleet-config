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
- **Never re-run a Job to test it.** A Job may send mail, touch live stores or drive real devices. A re-run is Roberto's "Run now", not yours; verify a fix by reading the next scheduled run.
- **Private data stays out of issues, comments and briefs.** Job output can hold private data (Life OS above all) and this repo is public. Name the job, the exit code and the mechanism; never quote the output.

Ack the event once handled, like any other.
