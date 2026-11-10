# CI failed

Loaded on demand from the `/chief` skill (`SKILL.md`, same section name).

A red GitHub Actions run on a fleet repo's default branch reaches you as a `kind=ci-failed` event in the same mailbox batch, never as typed text:

```
- ci <workflow> failed repo=<repo> sha=<sha7> run=<run_id> kind=ci-failed id=<id> dispatch=-
  conclusion=<failure|timed_out|startup_failure> event=<push|schedule|…> at=<created_at> jobs=<failed job names> url=<run_url>
  excerpt (ci-log, <n> chars):
  | <the failed step's log tail>
```

A field the producer could not fill reads `-`. The event is a CI run, not a worker: it has no `sid` and no `exchange`. CI is advisory in this fleet, never a shipping gate, but a red default branch is still worth acting on.

- **Investigate.** Read the excerpt, then `gh run view <run_id> --repo <owner>/<repo> --log-failed` for the full failed-step log and `gh run list --repo <owner>/<repo> --branch <default> --workflow <workflow> --limit 5` for what came after.
- **Transient or defect.** A later green run of the same workflow on the same branch means transient (a network blip, a runner hiccup): one drawer line, no issue. A run still red, or a failure that repeats, is a real defect or gap.
- **Dedupe.** An open issue that already covers the failure gets a dated comment (workflow, `sha7`, the mechanism) and nothing new is filed. Several events for one workflow and repo before the first is handled are one failure.
- **Fix through the normal plan.** Otherwise file a `bug` in the owning repo and follow the lane cap, the plan gate and `plan ask` exactly as for any other work: a `bug` gets a build lane, a shape decision gets a plan-only lane and a `plan ask`. Queue or dispatch it per the plan and the worker cap.
- **Never re-run a workflow** (`gh run rerun`, `gh workflow run`), and never edit a workflow file as a reflex: the fix ships through a normal lane and its proof is the next CI run on the merge commit, read with `gh run list`. A run that is still going is "not confirmed", never green.
- **Keep issues and comments public-safe.** Read the log for the mechanism; name the workflow, job and error class, and never paste secrets, tokens or private paths from it.
- **Pull-request runs** are out of scope for the event (a lane's own PR is the lane's business); if one reaches you anyway, treat it as a lane's CI note, not a default-branch failure.

## Self-heal ledger

With Settings > Chief `chief_job_self_heal` on, record every investigated failure with the same cause classes as a Job: `chief_ops.py heal record ci:<repo>:<workflow> --run <run_id> --cause transient|defect|config|dependency|environment|unknown [--issue <repo>#<n>]`, then `--pr <repo>#<n>` once the fix merges and is verified. A `HEAL_REPEAT=` line means the same cause class hit this workflow again within 30 days: say so in the drawer and propose a structural fix (`plan ask`) instead of another patch. When `heal` prints `HEAL=off`, record nothing. Never call `heal rerun` for a `ci:` key: a workflow is not a Job, and the command refuses it (`job-not-found`).

Ack the event once handled, like any other.
