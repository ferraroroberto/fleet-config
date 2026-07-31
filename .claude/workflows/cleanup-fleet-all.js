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

const TEARDOWN_RESULT_SCHEMA = {
  type: 'object',
  required: ['residue', 'detail'],
  properties: {
    residue: { type: 'string', enum: ['CLEAN', 'RESIDUE'] },
    detail: { type: 'string' },
    commented: { type: 'boolean' },
    wipSha: { type: 'string' },
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
3. Build the change.
4. Run the project's verification gate per its CLAUDE.md (e.g. \`pwsh -File scripts/verify-before-ship.ps1\`). It must exit 0. If the project has no checker, say so explicitly in your report and treat verification as SKIPPED, not PASS.
5. STOP. Do NOT push, open a PR, merge, or run /issue-finish — a separate agent validates this before anything ships.${retryNote}

Issue #${issue.number}: ${issue.title}
The full issue text is already read by /issue-start. If needed, fetch the current text with \`gh issue view ${issue.number} --repo ferraroroberto/${issue.repo}\`.

Report via the required schema, including \`branch\` and the absolute \`worktree\` path you worked in (a later agent needs it to tear down; report it even when the build failed, and report an empty string only if no worktree was ever created). If verification is FAIL or SKIPPED, judge \`retryable\` yourself: true only if a second attempt has a real chance of fixing it (e.g. a straightforward bug in your own change); false for anything structural (no verification gate exists for this repo, the issue itself is unclear or unreproducible, or the real scope is bigger than one retry can close).`
}

function validatePrompt(issue, build) {
  return `You are independently validating GitHub issue #${issue.number} in the ${issue.repo} repo. It was built in the worktree ${build.worktree || `E:\\automation\\${issue.repo}-wt-${issue.number}`}, on branch ${build.branch}. You did NOT write this change — you have no memory of building it, review it fresh and adversarially, but leniently.

${ISOLATION_RULES}

1. \`cd\` into that worktree (never the primary checkout at E:\\automation\\${issue.repo}) and confirm you're on branch ${build.branch}.
2. Fetch the current issue text with \`gh issue view ${issue.number} --repo ferraroroberto/${issue.repo}\` and use it as the acceptance-criteria source.
3. Read the diff against the repo's default branch (e.g. \`git diff origin/main...${build.branch}\`).
4. Independently re-run the project's verification gate yourself per its CLAUDE.md — do not just trust the builder's report of PASS.
5. Judge whether this diff plausibly and reasonably addresses the fetched acceptance criteria.

Issue #${issue.number}: ${issue.title}

Be LENIENT — this is a sanity check that a human reviewer would rubber-stamp, not a nitpicky code review. Default to pass=true unless something is clearly broken, incomplete, or wrong (verification actually fails, the diff doesn't touch what the issue asked for, an obvious bug). Never fail an issue over style preferences, naming, or anything you'd only raise as an optional PR comment.

Report via the required schema. Always fill in \`feedback\` — briefly even on a pass — since a rejection's feedback is fed verbatim to the next build attempt.`
}

function executePrompt(issue, build) {
  return `You are shipping an already-built, already-validated GitHub issue #${issue.number} in the ${issue.repo} repo, on branch ${build.branch}, in the worktree ${build.worktree || `E:\\automation\\${issue.repo}-wt-${issue.number}`}. You are the only agent touching this repo right now.

${ISOLATION_RULES}

1. \`cd\` into that worktree, confirm you're on branch ${build.branch}.
2. Run the /issue-finish flow for this branch: push, gh pr create, CI-advisory wait (unless the diff is provably CI-unrelated per /issue-yolo's rule), gh pr merge --delete-branch, land on main, tray restart per the repo's CLAUDE.md. /issue-finish owns the worktree teardown for a successful ship — let it run its own teardown rather than hand-rolling one.
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
6. VERIFY — do not assume any of the above worked. In the primary checkout run all four and read the output:
   - \`git -C E:\\automation\\${issue.repo} worktree list\` → must list the primary only.
   - \`ls -d /e/automation/${issue.repo}-wt-* 2>/dev/null\` → must find nothing.
   - \`git -C E:\\automation\\${issue.repo} branch\` → must show the default branch only.
   - \`git -C E:\\automation\\${issue.repo} status --porcelain\` → must be empty, on the default branch.

If a directory refuses to delete because a process holds it (a leaked Playwright browser helper is the usual culprit — project-scaffolding#203), say exactly that in \`detail\`; do NOT kill processes you cannot identify and do NOT retry destructively.

Report via the required schema. \`residue\` is **CLEAN** only when all four verification commands above came back exactly as described — if any check could not be run, or came back ambiguous, that is RESIDUE, not CLEAN. A run-halting decision is made from this field, so a false CLEAN is far worse than an honest RESIDUE.`
}

async function processIssue(bucket, issue) {
  let feedback = null
  let lane = {
    bucket, issue, status: 'escalated', round: MAX_ROUNDS,
    branch: null, worktree: null, reason: 'exhausted retries',
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
      lane = { ...lane, status: 'escalated', round, reason }
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

  return {
    ...lane,
    residue: teardown ? teardown.residue : 'RESIDUE',
    residueDetail: teardown ? teardown.detail : 'teardown agent returned no result',
    wipSha: teardown ? teardown.wipSha : undefined,
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
