export const meta = {
  name: 'cleanup-fleet-all',
  description: 'Build, independently validate, ship and tear down fleet cleanup issues across all eight queued audit buckets — one bucket at a time, one repo at a time, with a bounded build/validate retry loop',
  phases: [
    { title: 'Build' },
    { title: 'Validate' },
    { title: 'Execute' },
    { title: 'Teardown' },
  ],
}

// See fleet-config/.claude/skills/cleanup-fleet-all/SKILL.md for the invoking
// skill and the "decision gate" rationale (fleet-config plan tingly-kindling-crayon).
// This script owns zero Bash/filesystem access by design — every repo-mutating
// action happens inside a spawned agent, never here. The gate below is a fixed
// lookup on each agent's own schema-validated verdict, not a re-interpretation
// of their reasoning.
//
// STRICT SERIALITY (fleet-config#518). One bucket at a time, and one issue at a
// time inside it. The 2026-07-30 run fanned every repo in a bucket out with
// `parallel(...)`; combined with an escalation path that never tore anything
// down, that produced 11 simultaneous stray worktrees, two primaries left off
// `main`, and a fleet Roberto had to wipe by hand the next morning. Serial lanes
// bound the blast radius to exactly one repo: at most one worktree can exist at
// any instant, so a lane that cannot be returned to clean is one command to
// recover, not eleven.
//
// TERMINAL TEARDOWN. Teardown is not the escalation path's job, it is the last
// step of *every* lane — merged, escalated, or failed alike. A lane is only
// finished once its repo is verified back on a clean default branch with no
// worktree and no leftover branch.
//
// HALT ON RESIDUE. If teardown cannot get a repo back to clean, the run stops
// there rather than starting the next lane. Continuing past an unclean lane is
// precisely how one forgotten worktree became a cascade.
//
// REPORTED BUT NOT HALTING (fleet-config#534). Three conditions found on
// 2026-08-01 are real, must reach a human, and are *not* residue: a stale
// `.git/index.lock` left by a crashed git (which silently blocks every pull, so
// a later verification reads a stale working copy and calls shipped work
// missing), a primary many commits behind `origin/main` (clean is not current),
// and an empty `<repo>-wt-<N>` shell pinned by exited-but-still-handled WebKit
// process objects on a host that has not rebooted (inert, undeletable until
// reboot, and it halted four runs in one day). Teardown reports all three; only
// `residue` gates the run.

const MAX_ROUNDS = 2

const BUILD_RESULT_SCHEMA = {
  type: 'object',
  required: ['status', 'verification'],
  properties: {
    status: { type: 'string', enum: ['built', 'failed'] },
    branch: { type: 'string' },
    worktree: { type: 'string' },
    verification: { type: 'string', enum: ['PASS', 'FAIL', 'SKIPPED'] },
    retryable: { type: 'boolean' },
    reason: { type: 'string' },
    summary: { type: 'string' },
    // The orchestrator's own step-5 pre-dispatch check (fleet-config#623) can
    // still miss a closure that happens mid-run, hours into a serial sweep.
    // /issue-start's own "closed → stop" check is the last line of defense,
    // and this flag is how that specific reason reaches teardown -- so it can
    // skip the "unattended lane escalated" comment, which reads as confusing
    // noise on a thread that is already resolved.
    alreadyClosed: { type: 'boolean' },
  },
}

const VALIDATE_RESULT_SCHEMA = {
  type: 'object',
  required: ['pass', 'feedback', 'verification'],
  properties: {
    pass: { type: 'boolean' },
    feedback: { type: 'string' },
    verification: { type: 'string', enum: ['PASS', 'FAIL'] },
  },
}

const EXECUTE_RESULT_SCHEMA = {
  type: 'object',
  required: ['result'],
  properties: {
    result: { type: 'string', enum: ['MERGED', 'FAILED'] },
    pr: { type: 'string' },
    mergeSha: { type: 'string' },
    reason: { type: 'string' },
  },
}

// fleet-config#534. The last four properties are *reported*, never gating: a
// stale index.lock, a primary behind origin, and inert zombie-pinned empty
// shells are all real conditions a human must see, and none of them is residue
// in the worktree/branch sense. They deliberately do not feed the halt gate in
// processIssue -- only `residue` does. Each has an explicit `unknown` member
// because a probe that could not establish its fact must say so rather than
// fold into the passing value (global CLAUDE.md, and #526's principle applied
// to a second check).
const TEARDOWN_RESULT_SCHEMA = {
  type: 'object',
  required: ['residue', 'detail'],
  properties: {
    residue: { type: 'string', enum: ['CLEAN', 'RESIDUE'] },
    detail: { type: 'string' },
    commented: { type: 'boolean' },
    wipSha: { type: 'string' },
    indexLock: { type: 'string', enum: ['none', 'stale-cleared', 'live-held', 'unknown'] },
    indexLockDetail: { type: 'string' },
    behindOrigin: { type: 'string', enum: ['current', 'fast-forwarded', 'unknown'] },
    behindOriginDetail: { type: 'string' },
    zombieShells: { type: 'string' },
    foreignBranches: { type: 'string' },
  },
}

// Every dispatched agent gets these two rules verbatim. Both are live-incident
// scars, not style preferences (fleet-config#515).
const ISOLATION_RULES = `HARD RULES — both are live-incident scars, never work around them:

- **Never work a primary checkout.** Build in an isolated sibling worktree, always, for every repo. \`/issue-start\` claims the primary whenever nothing else holds the claim, but a *running* app is not a claim holder: on 2026-07-30 a build agent legitimately won MODE=primary in E:\\automation\\app-launcher and live-edited the files the running launcher webapp was serving, breaking it mid-run. Force worktree mode explicitly:
  \`E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/worktree_claim.py acquire E:\\automation\\<repo> --issue <N> --force-worktree\`
  then \`setup-worktree\` and \`cd\` into the printed \`WORKTREE=\` path before doing anything else. Everything after that — /issue-start, the build, the gate — happens inside the worktree.
- **A live-e2e guard refusal is a hard STOP.** If a repo's test suite refuses to run because a live instance is up, report that and stop. Setting \`E2E_LIVE=1\` or any equivalent override is FORBIDDEN for every agent; e2e never targets a live production instance.`

function buildPrompt(issue, priorFeedback) {
  const retryNote = priorFeedback
    ? `\n\nThis is a retry on the same branch, in the same worktree. Your previous attempt was rejected:\n"${priorFeedback}"\nFix this specific problem — do not start over from scratch.`
    : ''
  return `You are building GitHub issue #${issue.number} in the ${issue.repo} repo, then STOPPING before shipping it. Repo root: E:\\automation\\${issue.repo}. You are the only agent touching this repo right now.

${ISOLATION_RULES}

1. Force worktree mode and create the worktree per the isolation rules above, then \`cd\` into it.
2. Invoke /issue-start ${issue.number} now — handles pre-flight, issue read, CLAUDE.md read, main sync, branch cut, hand-off to fast-mode implementation. It will see it is already in a worktree; do not let it move you back to the primary.
   If /issue-start's own pre-flight reports the issue is already closed, STOP immediately — do not force scope onto a closed issue. Report status: "failed", verification: "SKIPPED", retryable: false, reason: "issue already closed", and alreadyClosed: true, then skip straight to the report at the end (do not attempt steps 3-4).
3. Build the change.
4. Run the project's verification gate per its CLAUDE.md (e.g. \`C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -File scripts/verify-before-ship.ps1\`). It must exit 0. If the project has no checker, say so explicitly in your report and treat verification as SKIPPED, not PASS.
5. Commit your work on the branch — \`git add\` the files you changed and \`git commit\` them (conventional \`type: subject\` message, no AI-attribution trailer). Your handoff artefact is a **committed branch**, not a dirty working tree: uncommitted work has no SHA, so an escalation or a crash between here and the next agent loses it outright instead of parking it recoverably in the reflog (fleet-config#641). If you genuinely changed nothing, commit nothing and say so — a clean tree with no new commits is a valid report, a dirty tree never is.
6. STOP. Do NOT push, open a PR, merge, or run /issue-finish — a separate agent validates this before anything ships. "Do not ship" does not mean "do not commit": step 5 is required, and only the four actions named here are forbidden.${retryNote}

Issue #${issue.number}: ${issue.title}
The full issue text is already read by /issue-start. If needed, fetch the current text with \`gh issue view ${issue.number} --repo ferraroroberto/${issue.repo}\`.

Report via the required schema, including \`branch\` and the absolute \`worktree\` path you worked in (a later agent needs it to tear down; report it even when the build failed, and report an empty string only if no worktree was ever created). If verification is FAIL or SKIPPED, judge \`retryable\` yourself: true only if a second attempt has a real chance of fixing it (e.g. a straightforward bug in your own change); false for anything structural (no verification gate exists for this repo, the issue itself is unclear or unreproducible, or the real scope is bigger than one retry can close).`
}

function validatePrompt(issue, build) {
  return `You are independently validating GitHub issue #${issue.number} in the ${issue.repo} repo. It was built in the worktree ${build.worktree || `E:\\automation\\${issue.repo}-wt-${issue.number}`}, on branch ${build.branch}. You did NOT write this change — you have no memory of building it, review it fresh and adversarially, but leniently.

${ISOLATION_RULES}

1. \`cd\` into that worktree (never the primary checkout at E:\\automation\\${issue.repo}) and confirm you're on branch ${build.branch}.
2. Check the handoff is committed: \`git status --porcelain\` must be empty. If it is not, the build agent left its work in a dirty tree — stop reviewing and report \`pass: false\` with feedback "uncommitted changes at handoff — commit your work on the branch before stopping". This is the one lane-boundary assertion the leniency rule below does not soften; absorbing it silently is what let it recur (fleet-config#641). Judge the **tree**, not the commit count: a build that legitimately changed nothing leaves a clean tree with no commits ahead, and that is not a failure.
3. Fetch the current issue text with \`gh issue view ${issue.number} --repo ferraroroberto/${issue.repo}\` and use it as the acceptance-criteria source.
4. Read the diff against the repo's default branch (e.g. \`git diff origin/main...${build.branch}\`).
5. Independently re-run the project's verification gate yourself per its CLAUDE.md — do not just trust the builder's report of PASS.
6. Judge whether this diff plausibly and reasonably addresses the fetched acceptance criteria.

Issue #${issue.number}: ${issue.title}

Be LENIENT — this is a sanity check that a human reviewer would rubber-stamp, not a nitpicky code review. Default to pass=true unless something is clearly broken, incomplete, or wrong (verification actually fails, the diff doesn't touch what the issue asked for, an obvious bug). Never fail an issue over style preferences, naming, or anything you'd only raise as an optional PR comment.

Report via the required schema. Always fill in \`feedback\` — briefly even on a pass — since a rejection's feedback is fed verbatim to the next build attempt.`
}

function executePrompt(issue, build) {
  return `You are shipping an already-built, already-validated GitHub issue #${issue.number} in the ${issue.repo} repo, on branch ${build.branch}, in the worktree ${build.worktree || `E:\\automation\\${issue.repo}-wt-${issue.number}`}. You are the only agent touching this repo right now.

${ISOLATION_RULES}

1. \`cd\` into that worktree, confirm you're on branch ${build.branch}.
2. Run the /issue-finish flow for this branch: push, gh pr create, CI-advisory wait (unless the diff is provably CI-unrelated per /issue-yolo's rule), then merge + land per /issue-finish step 5's WORKTREE branch — you are in a worktree, so \`gh pr merge <PR> --merge\` with NO --delete-branch (it fails its local half) and NO \`git checkout main\`; then remove-worktree, then \`worktree_claim.py land-primary <repo> ${issue.number}\`, then delete the branch refs explicitly. Report its PRIMARY=live/PRIMARY=stale line — a merged PR that never reached the primary is not live. Tray restart per the repo's CLAUDE.md. /issue-finish owns the worktree teardown for a successful ship — let it run its own teardown rather than hand-rolling one.
3. Fire the /issue-finish completion ping via notify_complete.py --kind finish — do NOT use any MCP Slack tool to pick a channel yourself, the helper resolves it from projects.toml.

If anything fails, do not force it through — leave the branch and PR (if any) as-is and report FAILED with the reason. Never guess-fix a shipping failure. A dedicated teardown agent runs after you either way.

Report via the required schema.`
}

// The terminal step of every lane. fleet-config#518: the durable artifact of a
// lane that did not ship is the GitHub issue plus a comment on it, never a
// branch or a worktree left lying around for a human to find days later.
function teardownPrompt(issue, lane) {
  const wt = lane.worktree || `E:\\automation\\${issue.repo}-wt-${issue.number}`
  const shipped = lane.status === 'merged'
  const commentStep = shipped
    ? `1. No issue comment needed — this one merged (${lane.pr || 'PR recorded'}).`
    : lane.alreadyClosed
    ? `1. No issue comment needed — the issue was already closed by the time /issue-start reached it (fleet-config#623). Posting an "unattended lane escalated" comment on an already-resolved thread is confusing noise, not a useful record; just tear the workspace down below.`
    : `1. Post a \`gh issue comment ${issue.number} --repo ferraroroberto/${issue.repo}\` recording, in plain prose (no hard-wrapped paragraphs, no AI attribution): that an unattended /cleanup-fleet-all lane ended as **${lane.status}** after round ${lane.round}; the verbatim reason below; and — only if branch \`${lane.branch}\` has commits ahead of the default branch — that WIP SHA plus a note that the branch and worktree were torn down and the commit is recoverable from the reflog for ~90 days. This comment is the durable record; the branch is not.

   Reason to quote verbatim: "${(lane.reason || 'no reason reported').replace(/"/g, "'")}"`

  return `You are the teardown agent for GitHub issue #${issue.number} in the ${issue.repo} repo (${issue.title}). An unattended /cleanup-fleet-all lane just finished with status **${lane.status}**. Your only job is to leave this repo in exactly the state a human would want to find it in tomorrow morning, and to report honestly whether you achieved that.

Repo primary: E:\\automation\\${issue.repo}. Lane branch: ${lane.branch || '(none — build never cut one)'}. Lane worktree: ${wt}

${commentStep}
2. If a PR was opened for this branch and is still OPEN, leave the remote branch and the PR alone (deleting the branch would orphan the PR) — say so in \`detail\`. Otherwise, delete the remote branch if it exists: \`git push origin --delete ${lane.branch || '<branch>'}\`.
3. Tear down the worktree — always via the helper, never by hand (\`rm -rf\` on a worktree whose \`.venv\` is a junction destroys the primary's real venv):
   \`E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/worktree_claim.py remove-worktree ${wt}\`
   It is safe and idempotent if the worktree is already gone (a merged lane's /issue-finish removes its own).
4. Release the claim (idempotent, harmless if never held):
   \`E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/worktree_claim.py release E:\\automation\\${issue.repo}\`
5. From the primary checkout, delete the local lane branch if it still exists (\`git branch -D ${lane.branch || '<branch>'}\`) and make sure the primary is on its default branch, clean.
6. VERIFY — do not assume any of the above worked. Run all six checks in the primary checkout and read the output. Checks 1–4 decide \`residue\`; checks 5 and 6 are **reported and never halt the run**.

   **Check 1 — worktree registration.** \`git -C E:\\automation\\${issue.repo} worktree list\` → must list the primary only.

   **Check 2 — leftover sibling directory.** \`ls -d /e/automation/${issue.repo}-wt-* 2>/dev/null\`. Glob **only this lane's own repo name**, exactly as written — never a fleet-wide \`/e/automation/*-wt-*\`. Sweeps run concurrently across repos, so a fleet-wide glob makes this lane report another repo's in-flight worktree as its own residue; that happened on 2026-08-01 (app-launcher#709's lane flagged home-automation's live worktree). Keep it repo-scoped; this is not a thing to "simplify" later.

   A hit is **residue by default**. It is not residue only when all five conditions below hold, each proved by running the command and reading its output — never inferred:

   1. **Empty** — zero children, recursively: \`find '<path>' -mindepth 1 -print -quit\` prints nothing.
   2. **A real directory, not a reparse point/junction** — read the attribute bit explicitly: \`C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -Command "(Get-Item -Force '<path>').Attributes"\` must not contain \`ReparsePoint\`. Do not infer this from the listing.
   3. **Git has already deregistered it** — the path is absent from check 1's \`git worktree list\` output.
   4. **No live holder** — \`E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/dir_holders.py check '<path>'\` prints \`STATUS=CLEAR\` with \`LIVE=0\`. \`STATUS=LIVE\` means a running process names this path (each holder's pid and command line are printed, so you can say *which*) — residue. \`STATUS=UNKNOWN\` means the probe could not run — also residue, because an unestablished condition is never a pass.

      This probe is deliberately **repo-agnostic**: it runs from fleet-config's own venv against the Windows process table and needs nothing whatsoever from the target repo — no \`tests/e2e/\`, no Playwright, no venv. The old requirement was the repo's own \`tests/e2e/_browser_sweep.py\`, which exists in **4 of 14 fleet repos**; in the other ten this condition was unprovable by construction, so every leftover directory was guaranteed RESIDUE and guaranteed to halt the run whatever was actually in it — the exception written to stop inert shells halting runs only worked where a Playwright sweeper happened to ship (fleet-config#571). Where \`tests/e2e/_browser_sweep.py\` *does* exist it is still the better instrument for classifying leaked browser helpers and running it as well is welcome; its **absence** proves nothing either way.
   5. **The helper was still run and still refused** — \`worktree_claim.py remove-worktree '<path>'\` (step 3) was actually executed against this path and did not remove it.

   Why the exception exists: on a Windows host that has not rebooted since an e2e run leaked WebKit helpers, those helpers exit and Windows keeps their *process objects* alive while any handle to them remains. The empty directory shell they pin cannot be deleted, renamed, or moved until reboot. It holds nothing, is git-deregistered, is not a junction, and is inert. Treating it as residue halted four separate runs in one day.

   Any one of the five unestablished — a non-empty directory, a junction, a still-registered worktree, a \`STATUS=LIVE\` holder, or a probe that could not run at all (\`STATUS=UNKNOWN\`: a crash, no PowerShell, unreadable output) — makes this **RESIDUE**. An unverifiable state is residue, never a convenient pass.

   **Zombies cannot be attributed to a directory, and nothing here asks you to.** An exited process is absent from the process table entirely and reports \`cwd=<unreadable>\` to anything that goes looking, so which shell it pins is information that does not exist anywhere. An exited process that still pins a shell is exactly the case this exception was written for. \`STATUS=CLEAR\` is the entire requirement and it is sufficient, because a live process is the only kind that could still be doing something. Do not try to match a particular zombie to a particular shell, and do not report an inability to do so as a failed condition — that information does not exist.

   Report every leftover that satisfies all five in \`zombieShells\`, by **path and probe verdict** (e.g. \`E:\\automation\\alpha-wt-1 (empty, deregistered, STATUS=CLEAR, remove refused)\`), and do not let it make this lane RESIDUE. Judge each directory on its own five conditions. **The number of such shells is irrelevant** — several, left by earlier lanes, are the expected state on a host that has not rebooted; never key on a count, and never on which path you were expecting.

   **Check 3 — this lane's branch is gone.** \`git -C E:\\automation\\${issue.repo} branch\` → \`${lane.branch || '(none — build never cut one)'}\` must not appear. **Judge only your own branch.** Every *other* local branch is reported, never residue, and never halts: your mandate is this lane (step 5 deletes your branch, and another lane's ref is explicitly not yours to remove), so this check may not assert a whole-repo property you are forbidden to bring about. On 2026-08-07 a lane that built, merged, and tore down perfectly reported RESIDUE over a stale branch from an *earlier* lane whose PR was already merged, and halted the run with 41 lanes unstarted (fleet-config#572).

   Report every branch that is neither the default nor yours in \`foreignBranches\`, one entry each, with its name and — cheaply, best-effort — whether its PR already merged (\`gh pr list --repo ferraroroberto/${issue.repo} --head <branch> --state all --json number,state\`) and whether \`git -C E:\\automation\\${issue.repo} diff <default>..<branch>\` is empty. Do **not** delete them. Note for whoever reads it: \`git branch --merged\` is unreliable here — the fleet squash-merges, so the original tip is not an ancestor of the default branch and a fully-absorbed branch still reports as unmerged (the same squash-destroys-the-SHA behaviour as fleet-config#567). An empty \`diff\` or the PR's merge state is the reliable test, and \`git branch -d\` will refuse where \`-D\` is what's actually correct.

   **Check 4 — tree.** \`git -C E:\\automation\\${issue.repo} status --porcelain\` → must be empty, on the default branch.

   **Check 5 — stale \`.git/index.lock\` (reported, never halts).** A crashed or killed git leaves \`.git/index.lock\` behind and every later \`git pull\` fails with *"Another git process seems to be running"*. Checks 1–4 pass anyway, so the lane reports "primary on main, clean" — true, and read as current when it is four commits behind. On 2026-08-01 three repos held hours-old locks with no git process alive, and a verification consequently recorded a merged, shipped file as MISSING.
   - \`ls -l E:\\automation\\${issue.repo}/.git/index.lock\` → absent: \`indexLock: "none"\`, nothing to do.
   - Present → look for a **live** \`git.exe\` whose command line names this repo: \`C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process -Filter \\"Name='git.exe'\\" | Select-Object ProcessId,CommandLine | Format-List"\`.
   - A live git naming this repo → \`indexLock: "live-held"\`. Someone is mid-operation: say so in \`indexLockDetail\` (with the pid) and **touch nothing** — no delete, no pull, no retry. \`live-held\` is an *unknown*-class verdict, never a pass: it is a named, more useful spelling of "could not establish that this primary is current", and it must be reported as such.
   - No live git **and** the lock's mtime is older than 5 minutes → \`indexLock: "stale-cleared"\`. Put its age in \`indexLockDetail\`, delete the lock, and let check 6 do the pull. This is the **only** condition under which the lock may be removed.
   - Anything you could not establish — the process query failed, the mtime is unreadable, or the lock is younger than 5 minutes with no identifiable owner → \`indexLock: "unknown"\`, and leave it exactly where it is.

   **Check 6 — primary current with origin (reported, never halts).** Clean is not current: a primary sitting eleven commits behind \`origin/main\` passes checks 1–4, and any later verification reading that working copy misreports shipped work as absent.
   - \`git -C E:\\automation\\${issue.repo} fetch origin\`, then \`git -C E:\\automation\\${issue.repo} rev-list --count HEAD..origin/<default-branch>\`.
   - \`0\` → \`behindOrigin: "current"\`.
   - Non-zero, **and check 4 came back clean** (empty porcelain, HEAD on the default branch) → \`git -C E:\\automation\\${issue.repo} pull --ff-only\`. On success \`behindOrigin: "fast-forwarded"\`, with the commit delta and the before/after SHAs in \`behindOriginDetail\`. **Never a merge, never a rebase, never a reset, never \`--force\`.**
   - Gate this on check 4 alone — what makes a pull unsafe is a dirty tree or HEAD off the default branch, not the mere existence of other refs. Gating it on check 3 too meant one foreign branch withheld the fast-forward from a perfectly healthy primary and left it two commits behind, which is precisely the "clean is not current" failure this check exists to prevent (fleet-config#572).
   - Never pull over an unclean primary. If check 4 failed (wrong branch, dirty tree), do **not** attempt the fast-forward at all → \`behindOrigin: "unknown"\` naming the count and the reason. This lane is already RESIDUE; mutating the tree on top of that would destroy the evidence a human needs.
   - The fast-forward is refused (diverged history), the fetch failed, or check 5 left the lock in place (\`live-held\`/\`unknown\`) → \`behindOrigin: "unknown"\` with the reason. Do not escalate to any other kind of pull.

If a directory refuses to delete because a process holds it (a leaked Playwright browser helper is the usual culprit — project-scaffolding#203), say exactly that in \`detail\`; do NOT kill processes you cannot identify and do NOT retry destructively.

Report via the required schema. \`residue\` is **CLEAN** only when checks 1–4 came back exactly as described — with a leftover directory that satisfies all five zombie-shell conditions counting as passing check 2, and another lane's branch counting as passing check 3 — and if any of those checks could not be run, or came back ambiguous, that is RESIDUE, not CLEAN. A run-halting decision is made from this field, so a false CLEAN is far worse than an honest RESIDUE. Checks 5 and 6 never touch \`residue\` and never halt the run; report them in \`indexLock\`/\`indexLockDetail\` and \`behindOrigin\`/\`behindOriginDetail\`, along with \`zombieShells\` and \`foreignBranches\`, so they reach the human-facing summary. Narrowing what counts as *your* mess is not lowering the bar for it — your own leftover branch, worktree, or dirty tree is still RESIDUE and still halts the run.`
}

async function processIssue(bucket, issue) {
  let feedback = null
  let lane = {
    bucket, issue, status: 'escalated', round: MAX_ROUNDS,
    branch: null, worktree: null, reason: 'exhausted retries', alreadyClosed: false,
  }

  for (let round = 1; round <= MAX_ROUNDS; round++) {
    const build = await agent(buildPrompt(issue, feedback), {
      phase: 'Build',
      label: `${bucket}:build:${issue.repo}#${issue.number}`,
      schema: BUILD_RESULT_SCHEMA,
    })

    if (build) {
      lane.branch = build.branch || lane.branch
      lane.worktree = build.worktree || lane.worktree
    }

    if (!build || build.verification !== 'PASS') {
      const reason = build ? (build.reason || `verification ${build.verification}`) : 'build agent returned no result'
      if (build && build.retryable && round < MAX_ROUNDS) {
        feedback = reason
        continue
      }
      lane = { ...lane, status: 'escalated', round, reason, alreadyClosed: !!(build && build.alreadyClosed) }
      break
    }

    const verdict = await agent(validatePrompt(issue, build), {
      phase: 'Validate',
      label: `${bucket}:validate:${issue.repo}#${issue.number}`,
      schema: VALIDATE_RESULT_SCHEMA,
    })

    if (verdict && verdict.pass) {
      const shipped = await agent(executePrompt(issue, build), {
        phase: 'Execute',
        label: `${bucket}:execute:${issue.repo}#${issue.number}`,
        schema: EXECUTE_RESULT_SCHEMA,
      })
      if (shipped && shipped.result === 'MERGED') {
        lane = { ...lane, status: 'merged', round, pr: shipped.pr, mergeSha: shipped.mergeSha, reason: null }
      } else {
        lane = { ...lane, status: 'failed', round, reason: shipped ? shipped.reason : 'execute agent returned no result' }
      }
      break
    }

    const vFeedback = verdict ? verdict.feedback : 'validator returned no result'
    if (round < MAX_ROUNDS) {
      feedback = vFeedback
      continue
    }
    lane = { ...lane, status: 'escalated', round, reason: vFeedback }
    break
  }

  // Terminal step of every lane, no exceptions (fleet-config#518).
  const teardown = await agent(teardownPrompt(issue, lane), {
    phase: 'Teardown',
    label: `${bucket}:teardown:${issue.repo}#${issue.number}`,
    schema: TEARDOWN_RESULT_SCHEMA,
  })

  // fleet-config#534: the two reported-only probes default to 'unknown', never
  // to their passing value, when the teardown agent died or omitted them — a
  // check that could not establish its fact must surface as unknown.
  return {
    ...lane,
    residue: teardown ? teardown.residue : 'RESIDUE',
    residueDetail: teardown ? teardown.detail : 'teardown agent returned no result',
    wipSha: teardown ? teardown.wipSha : undefined,
    indexLock: (teardown && teardown.indexLock) || 'unknown',
    indexLockDetail: teardown ? teardown.indexLockDetail : 'teardown agent returned no result',
    behindOrigin: (teardown && teardown.behindOrigin) || 'unknown',
    behindOriginDetail: teardown ? teardown.behindOriginDetail : 'teardown agent returned no result',
    zombieShells: teardown ? teardown.zombieShells : undefined,
    foreignBranches: teardown ? teardown.foreignBranches : undefined,
  }
}

// Workaround: in this environment, an object passed as Workflow's `args` has
// been observed arriving here as a JSON-stringified value rather than the
// parsed object the tool docs describe (reproduced with a trivial payload,
// so it's not content-dependent) — handle both shapes defensively.
const rawArgs = typeof args === 'string' ? JSON.parse(args) : args
const issuesByBucket = (rawArgs && rawArgs.issuesByBucket) || {}
const bucketNames = Object.keys(issuesByBucket)
const allResults = []
let halted = null

for (const bucket of bucketNames) {
  if (halted) {
    allResults.push({ bucket, results: [], skipped: 'run halted before this bucket started' })
    continue
  }

  const issues = issuesByBucket[bucket] || []
  if (!issues.length) {
    log(`${bucket}: no open issues`)
    allResults.push({ bucket, results: [] })
    continue
  }

  phase('Build')
  log(`${bucket}: ${issues.length} issue(s), one repo at a time`)

  const results = []
  for (let i = 0; i < issues.length; i++) {
    const issue = issues[i]
    log(`${bucket} [${i + 1}/${issues.length}]: starting ${issue.repo}#${issue.number}`)
    const r = await processIssue(bucket, issue)
    results.push(r)
    log(`${bucket} [${i + 1}/${issues.length}]: ${issue.repo}#${issue.number} → ${r.status}, teardown ${r.residue}`)

    // Reported-only probes (#534) — surfaced in the stream so an overnight run
    // is diagnosable from the log alone, never folded into the halt decision.
    if (r.indexLock !== 'none') log(`  index.lock: ${r.indexLock} — ${r.indexLockDetail || 'no detail reported'}`)
    if (r.behindOrigin !== 'current') log(`  behind origin: ${r.behindOrigin} — ${r.behindOriginDetail || 'no detail reported'}`)
    if (r.zombieShells) log(`  zombie-pinned shells (not residue): ${r.zombieShells}`)
    if (r.foreignBranches) log(`  foreign branches (not residue): ${r.foreignBranches}`)
    if (r.alreadyClosed) log(`  already closed mid-run (fleet-config#623) — no teardown comment posted`)

    // Anti-cascade gate. A lane that could not be returned to clean stops the
    // whole run — serial lanes mean exactly one repo is affected, and starting
    // the next lane on top of it is how 2026-07-30 turned one stray worktree
    // into eleven.
    if (r.residue !== 'CLEAN') {
      halted = {
        bucket,
        repo: issue.repo,
        issue: issue.number,
        status: r.status,
        detail: r.residueDetail,
        remainingInBucket: issues.length - (i + 1),
      }
      log(`HALT: ${issue.repo}#${issue.number} left residue — ${r.residueDetail}`)
      break
    }
  }

  const merged = results.filter(r => r.status === 'merged').length
  const escalated = results.filter(r => r.status === 'escalated').length
  const failed = results.filter(r => r.status === 'failed').length
  log(`${bucket}: ${merged} merged, ${escalated} escalated, ${failed} failed`)

  allResults.push({ bucket, results })
}

return { buckets: allResults, halted }
