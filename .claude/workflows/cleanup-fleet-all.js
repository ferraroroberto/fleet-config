export const meta = {
  name: 'cleanup-fleet-all',
  description: 'Build, independently validate, and ship fleet cleanup issues across all seven queued audit buckets, serially, with a bounded build/validate retry loop',
  phases: [
    { title: 'Build' },
    { title: 'Validate' },
    { title: 'Execute' },
  ],
}

// See fleet-config/.claude/skills/cleanup-fleet-all/SKILL.md for the invoking
// skill and the "decision gate" rationale (fleet-config plan tingly-kindling-crayon).
// This script owns zero Bash/filesystem access by design — every repo-mutating
// action happens inside a spawned agent, never here. The gate below is a fixed
// lookup on each agent's own schema-validated verdict, not a re-interpretation
// of their reasoning.

const MAX_ROUNDS = 2

const BUILD_RESULT_SCHEMA = {
  type: 'object',
  required: ['status', 'verification'],
  properties: {
    status: { type: 'string', enum: ['built', 'failed'] },
    branch: { type: 'string' },
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

function buildPrompt(issue, priorFeedback) {
  const retryNote = priorFeedback
    ? `\n\nThis is a retry on the same branch. Your previous attempt was rejected:\n"${priorFeedback}"\nFix this specific problem — do not start over from scratch.`
    : ''
  return `You are building GitHub issue #${issue.number} in the ${issue.repo} repo, then STOPPING before shipping it. Repo root: E:\\automation\\${issue.repo}. You are the only agent touching this repo right now.

1. cd to E:\\automation\\${issue.repo}.
2. Invoke /issue-start ${issue.number} now — handles pre-flight, issue read, CLAUDE.md read, main sync, branch cut, hand-off to fast-mode implementation.
3. Build the change.
4. Run the project's verification gate per its CLAUDE.md (e.g. \`pwsh -File scripts/verify-before-ship.ps1\`). It must exit 0. If the project has no checker, say so explicitly in your report and treat verification as SKIPPED, not PASS.
5. STOP. Do NOT push, open a PR, merge, or run /issue-finish — a separate agent validates this before anything ships.${retryNote}

Issue #${issue.number}: ${issue.title}
The full issue text is already read by /issue-start. If needed, fetch the current text with \`gh issue view ${issue.number} --repo ferraroroberto/${issue.repo}\`.

Report via the required schema. If verification is FAIL or SKIPPED, judge \`retryable\` yourself: true only if a second attempt has a real chance of fixing it (e.g. a straightforward bug in your own change); false for anything structural (no verification gate exists for this repo, the issue itself is unclear or unreproducible, or the real scope is bigger than one retry can close).`
}

function validatePrompt(issue, build) {
  return `You are independently validating GitHub issue #${issue.number} in the ${issue.repo} repo. Repo root: E:\\automation\\${issue.repo}, branch: ${build.branch}. You did NOT write this change — you have no memory of building it, review it fresh and adversarially, but leniently.

1. cd to E:\\automation\\${issue.repo}, confirm you're on branch ${build.branch}.
2. Fetch the current issue text with \`gh issue view ${issue.number} --repo ferraroroberto/${issue.repo}\` and use it as the acceptance-criteria source.
3. Read the diff against the repo's default branch (e.g. \`git diff origin/main...${build.branch}\`).
4. Independently re-run the project's verification gate yourself per its CLAUDE.md — do not just trust the builder's report of PASS.
5. Judge whether this diff plausibly and reasonably addresses the fetched acceptance criteria.

Issue #${issue.number}: ${issue.title}

Be LENIENT — this is a sanity check that a human reviewer would rubber-stamp, not a nitpicky code review. Default to pass=true unless something is clearly broken, incomplete, or wrong (verification actually fails, the diff doesn't touch what the issue asked for, an obvious bug). Never fail an issue over style preferences, naming, or anything you'd only raise as an optional PR comment.

Report via the required schema. Always fill in \`feedback\` — briefly even on a pass — since a rejection's feedback is fed verbatim to the next build attempt.`
}

function executePrompt(issue, build) {
  return `You are shipping an already-built, already-validated GitHub issue #${issue.number} in the ${issue.repo} repo, on branch ${build.branch}. Repo root: E:\\automation\\${issue.repo}. You are the only agent touching this repo right now.

1. cd to E:\\automation\\${issue.repo}, confirm you're on branch ${build.branch}.
2. Run the /issue-finish flow for this branch: push, gh pr create, CI-advisory wait (unless the diff is provably CI-unrelated per /issue-yolo's rule), gh pr merge --delete-branch, land on main, tray restart per the repo's CLAUDE.md.
3. Fire the /issue-finish completion ping via notify_complete.py --kind finish — do NOT use any MCP Slack tool to pick a channel yourself, the helper resolves it from projects.toml.

If anything fails, do not force it through — leave the branch and PR (if any) as-is and report FAILED with the reason. Never guess-fix a shipping failure.

Report via the required schema.`
}

async function processIssue(bucket, issue) {
  let feedback = null
  let lastBranch = null

  for (let round = 1; round <= MAX_ROUNDS; round++) {
    const build = await agent(buildPrompt(issue, feedback), {
      phase: 'Build',
      label: `${bucket}:build:${issue.repo}#${issue.number}`,
      schema: BUILD_RESULT_SCHEMA,
    })

    if (!build || build.verification !== 'PASS') {
      const reason = build ? (build.reason || `verification ${build.verification}`) : 'build agent returned no result'
      lastBranch = build ? build.branch : lastBranch
      if (build && build.retryable && round < MAX_ROUNDS) {
        feedback = reason
        continue
      }
      return { bucket, issue, status: 'escalated', round, branch: lastBranch, reason }
    }

    lastBranch = build.branch

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
        return { bucket, issue, status: 'merged', round, branch: build.branch, pr: shipped.pr, mergeSha: shipped.mergeSha }
      }
      return { bucket, issue, status: 'failed', round, branch: build.branch, reason: shipped ? shipped.reason : 'execute agent returned no result' }
    }

    const vFeedback = verdict ? verdict.feedback : 'validator returned no result'
    if (round < MAX_ROUNDS) {
      feedback = vFeedback
      continue
    }
    return { bucket, issue, status: 'escalated', round, branch: build.branch, reason: vFeedback }
  }

  return { bucket, issue, status: 'escalated', round: MAX_ROUNDS, branch: lastBranch, reason: 'exhausted retries' }
}

// Workaround: in this environment, an object passed as Workflow's `args` has
// been observed arriving here as a JSON-stringified value rather than the
// parsed object the tool docs describe (reproduced with a trivial payload,
// so it's not content-dependent) — handle both shapes defensively.
const rawArgs = typeof args === 'string' ? JSON.parse(args) : args
const issuesByBucket = (rawArgs && rawArgs.issuesByBucket) || {}
const bucketNames = Object.keys(issuesByBucket)
const allResults = []

for (const bucket of bucketNames) {
  const issues = issuesByBucket[bucket] || []
  if (!issues.length) {
    log(`${bucket}: no open issues`)
    allResults.push({ bucket, results: [] })
    continue
  }

  phase('Build')
  log(`${bucket}: processing ${issues.length} issue(s)`)

  const results = (await parallel(issues.map(issue => () => processIssue(bucket, issue)))).filter(Boolean)

  const merged = results.filter(r => r.status === 'merged').length
  const escalated = results.filter(r => r.status === 'escalated').length
  const failed = results.filter(r => r.status === 'failed').length
  log(`${bucket}: ${merged} merged, ${escalated} escalated, ${failed} failed`)

  allResults.push({ bucket, results })
}

return allResults
