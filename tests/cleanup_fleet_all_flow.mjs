// Control-flow suite for .claude/workflows/cleanup-fleet-all.js (fleet-config#518).
//
// Runs the real workflow script with stubbed agents, so the three properties
// that the 2026-07-30 fleet collapse violated are asserted mechanically rather
// than by reading the source: lanes are strictly serial (never two agents in
// flight, lane N's teardown finishes before lane N+1's build starts), teardown
// runs on every terminal path (merged / escalated / failed), and residue halts
// the run instead of stacking a second worktree on the first. `parallel()` and
// `pipeline()` are stubbed to throw, so re-introducing a fan-out fails here.
//
// Cases 5-7 add fleet-config#534's counterpart property: the three conditions
// found on 2026-08-01 -- a stale `.git/index.lock`, a primary behind origin,
// and a zombie-pinned empty worktree shell -- are reported and never halt, and
// the teardown brief that implements them keeps its repo-scoped glob and its
// "no per-directory zombie attribution" rule. Cases 5b and 8 add
// fleet-config#572's: a branch left by another lane is the fourth condition in
// that family, teardown's checks are scoped to its own lane, and SKILL.md must
// carry the same rules as the prompt.
//
// Driven by tests/test_cleanup_fleet_all_flow.py (which run_acceptance.py owns).
// Run directly with: node tests/cleanup_fleet_all_flow.mjs
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const REPO = dirname(dirname(fileURLToPath(import.meta.url)))
const SRC = readFileSync(join(REPO, '.claude', 'workflows', 'cleanup-fleet-all.js'), 'utf8')
  .replace(/^export const meta/m, 'const meta')

function makeRunner(agentImpl, sink) {
  const body = SRC
  const fn = new Function('agent', 'log', 'phase', 'parallel', 'pipeline', 'args', 'budget',
    `return (async () => { ${body} })()`)
  return fn(agentImpl, m => sink.logs.push(m), () => {}, () => { throw new Error('parallel() called — seriality broken') }, () => { throw new Error('pipeline() called') }, sink.args, {})
}

// Track concurrency: how many agents are "in flight" at once, and the order of labels.
function tracker(responder) {
  const sink = { logs: [], order: [], inflight: 0, maxInflight: 0, args: null }
  const agentImpl = async (prompt, opts) => {
    sink.inflight++
    sink.maxInflight = Math.max(sink.maxInflight, sink.inflight)
    sink.order.push(opts.label)
    await new Promise(r => setTimeout(r, 5))
    const out = responder(opts.label, prompt)
    sink.inflight--
    return out
  }
  return { sink, agentImpl }
}

const ISSUES = {
  documentation: [
    { repo: 'alpha', number: 1, title: 'a' },
    { repo: 'bravo', number: 2, title: 'b' },
  ],
  bug: [
    { repo: 'charlie', number: 3, title: 'c' },
  ],
}

function reply(label, kind) {
  if (label.includes(':build:')) return { status: 'built', branch: 'fix/x', worktree: 'E:\\wt', verification: kind.buildFail ? 'FAIL' : 'PASS', retryable: false, reason: 'gate failed' }
  if (label.includes(':validate:')) return { pass: !kind.validateFail, feedback: 'f', verification: 'PASS' }
  if (label.includes(':execute:')) return { result: 'MERGED', pr: 'pr/1', mergeSha: 'deadbee' }
  if (label.includes(':teardown:')) {
    return {
      residue: kind.residue ? 'RESIDUE' : 'CLEAN',
      detail: kind.residue ? 'worktree dir busy' : 'verified clean',
      indexLock: kind.indexLock || 'none',
      indexLockDetail: kind.indexLockDetail || '',
      behindOrigin: kind.behindOrigin || 'current',
      behindOriginDetail: kind.behindOriginDetail || '',
      zombieShells: kind.zombieShells,
      foreignBranches: kind.foreignBranches,
      foreignWorktrees: kind.foreignWorktrees,
    }
  }
  throw new Error('unknown label ' + label)
}

// Capture the prompt text each agent was handed, keyed by label — the teardown
// brief IS the implementation for fleet-config#534, so its wording is asserted
// here rather than left to a reader's memory.
function promptSpy(responder) {
  const { sink, agentImpl } = tracker(responder)
  sink.prompts = {}
  const wrapped = async (prompt, opts) => {
    sink.prompts[opts.label] = prompt
    return agentImpl(prompt, opts)
  }
  return { sink, agentImpl: wrapped }
}

let failures = 0
const check = (cond, msg) => { console.log((cond ? 'OK   ' : 'FAIL ') + msg); if (!cond) failures++ }

// --- Case 1: happy path, everything merges -------------------------------
{
  const { sink, agentImpl } = tracker(l => reply(l, {}))
  sink.args = { issuesByBucket: ISSUES }
  const res = await makeRunner(agentImpl, sink)
  check(sink.maxInflight === 1, `never more than one agent in flight (saw ${sink.maxInflight})`)
  const labels = sink.order.join('|')
  check(/alpha#1.*bravo#2.*charlie#3/.test(labels), 'lanes run in order, one repo at a time')
  check(sink.order.filter(l => l.includes(':teardown:')).length === 3, 'teardown ran on all 3 merged lanes')
  check(res.halted === null, 'no halt on a clean run')
  const all = res.buckets.flatMap(b => b.results)
  check(all.length === 3 && all.every(r => r.status === 'merged' && r.residue === 'CLEAN'), 'all 3 merged + CLEAN')
  // lane 1 must fully finish (incl. teardown) before lane 2 starts
  const i1 = sink.order.indexOf('documentation:teardown:alpha#1')
  const i2 = sink.order.indexOf('documentation:build:bravo#2')
  check(i1 >= 0 && i2 > i1, 'lane N teardown completes before lane N+1 build starts')
}

// --- Case 2: build fails -> escalated, teardown still runs ----------------
{
  const { sink, agentImpl } = tracker(l => reply(l, { buildFail: l.includes('alpha') }))
  sink.args = { issuesByBucket: { documentation: ISSUES.documentation } }
  const res = await makeRunner(agentImpl, sink)
  const r = res.buckets[0].results[0]
  check(r.status === 'escalated', 'failed build -> escalated')
  check(sink.order.includes('documentation:teardown:alpha#1'), 'teardown runs on an escalated lane (#518)')
  check(!sink.order.includes('documentation:validate:alpha#1'), 'no validate after a failed build')
  check(res.buckets[0].results.length === 2, 'run continues to the next lane after a CLEAN escalation')
}

// --- Case 3: teardown reports RESIDUE -> halt -----------------------------
{
  const { sink, agentImpl } = tracker(l => reply(l, { residue: l.includes('alpha') }))
  sink.args = { issuesByBucket: ISSUES }
  const res = await makeRunner(agentImpl, sink)
  check(res.halted !== null, 'RESIDUE halts the run')
  check(res.halted.repo === 'alpha' && res.halted.issue === 1, 'halt names the offending repo/issue')
  check(res.halted.remainingInBucket === 1, 'halt reports what was left unstarted in the bucket')
  check(!sink.order.some(l => l.includes('bravo')), 'no further lane starts in the halted bucket')
  check(!sink.order.some(l => l.includes('charlie')), 'later buckets never start after a halt')
  check(res.buckets[1].skipped, 'skipped buckets are reported, not silently dropped')
}

// --- Case 4: teardown agent dies -> treated as RESIDUE, not CLEAN ---------
{
  const { sink, agentImpl } = tracker(l => (l.includes(':teardown:') ? null : reply(l, {})))
  sink.args = { issuesByBucket: { bug: ISSUES.bug } }
  const res = await makeRunner(agentImpl, sink)
  check(res.buckets[0].results[0].residue === 'RESIDUE', 'a dead teardown agent is RESIDUE, never CLEAN')
  check(res.halted !== null, 'a dead teardown agent halts the run')
}

// --- Case 5: reported-only probes never gate a lane (#534) ----------------
// A stale-and-cleared index.lock, a fast-forwarded behind-origin primary and a
// zombie-pinned empty shell are all real conditions a human must see, and none
// of them is residue. If any of them ever starts halting the run, this fails.
{
  const { sink, agentImpl } = tracker(l => reply(l, {
    indexLock: 'stale-cleared', indexLockDetail: '4h12m old, no live git',
    behindOrigin: 'fast-forwarded', behindOriginDetail: '11 behind, a1b2c3d->e4f5a6b',
    zombieShells: 'E:\\automation\\alpha-wt-1 (6 zombies, live=0)',
  }))
  sink.args = { issuesByBucket: ISSUES }
  const res = await makeRunner(agentImpl, sink)
  check(res.halted === null, 'stale lock + behind-origin + zombie shell never halt the run')
  const all = res.buckets.flatMap(b => b.results)
  check(all.length === 3 && all.every(r => r.residue === 'CLEAN'), 'reported-only probes leave residue CLEAN')
  const r = all[0]
  check(r.indexLock === 'stale-cleared' && r.behindOriginDetail.includes('11 behind'),
    'index.lock + behind-origin verdicts reach the workflow result')
  check(typeof r.zombieShells === 'string' && r.zombieShells.includes('live=0'),
    'zombie-pinned shells are reported by path and count, not dropped')
  const logs = sink.logs.join('\n')
  check(/index\.lock: stale-cleared/.test(logs) && /behind origin: fast-forwarded/.test(logs)
    && /zombie-pinned shells/.test(logs), 'all three surface in the run log')
}

// --- Case 5b: a foreign branch is reported, never residue (#572) ----------
// A lane that built, merged and tore itself down perfectly reported RESIDUE
// over a stale branch left by an EARLIER lane, halting the run with 41 lanes
// unstarted. Teardown's mandate is its own lane; it is explicitly forbidden to
// delete another lane's ref, so no check may demand that it does.
{
  const { sink, agentImpl } = tracker(l => reply(l, {
    foreignBranches: 'fix/68-harden-upload-path-handling (PR #69 merged, diff vs main empty)',
    behindOrigin: 'fast-forwarded', behindOriginDetail: '2 behind, 4bec16d->c9516a7',
  }))
  sink.args = { issuesByBucket: ISSUES }
  const res = await makeRunner(agentImpl, sink)
  check(res.halted === null, 'a foreign branch never halts the run')
  const all = res.buckets.flatMap(b => b.results)
  check(all.every(r => r.residue === 'CLEAN'), 'a foreign branch leaves residue CLEAN')
  check((all[0].foreignBranches || '').includes('fix/68'),
    'foreign branches reach the workflow result by name')
  check(all[0].behindOrigin === 'fast-forwarded',
    'the fast-forward is not withheld just because another ref exists')
  check(/foreign branches \(not residue\)/.test(sink.logs.join('\n')),
    'foreign branches surface in the run log')
}

// --- Case 6: a teardown that omits the probes reports unknown, not clean ---
{
  const { sink, agentImpl } = tracker(l => (l.includes(':teardown:')
    ? { residue: 'CLEAN', detail: 'verified clean' }   // no probe fields at all
    : reply(l, {})))
  sink.args = { issuesByBucket: { bug: ISSUES.bug } }
  const res = await makeRunner(agentImpl, sink)
  const r = res.buckets[0].results[0]
  check(r.indexLock === 'unknown' && r.behindOrigin === 'unknown',
    'an omitted probe defaults to unknown, never to its passing value')
  check(res.halted === null, 'an unknown probe still does not halt the run')
}

// --- Case 7: the teardown brief still carries #534's rules -----------------
// These are prompt-text assertions on purpose: the teardown agent's brief is
// where checks 5/6 and the by-condition zombie rule actually live, and every
// one of them was a live incident. Deleting a rule must fail a test, not just
// read as a smaller prompt.
{
  const { sink, agentImpl } = promptSpy(l => reply(l, {}))
  sink.args = { issuesByBucket: { bug: ISSUES.bug } }
  await makeRunner(agentImpl, sink)
  const p = sink.prompts['bug:teardown:charlie#3']
  check(!!p, 'teardown prompt captured')
  // The fleet-wide form legitimately appears once, inside the prohibition
  // clause — so assert on the *command*, not on a bare substring.
  check(/ls -d \/e\/automation\/charlie-wt-\*/.test(p), 'check 2 globs only the lane\'s own repo')
  check(!/ls -d \/e\/automation\/\*-wt-\*/.test(p), 'no fleet-wide leftover-directory command')
  check(/never a fleet-wide/i.test(p) && /home-automation/.test(p),
    'the repo-scoped glob carries the why-comment (and the incident) so it is not "simplified"')
  check(/index\.lock/.test(p) && /live-held/.test(p) && /stale-cleared/.test(p),
    'check 5 (stale index.lock, with a live-holder branch) is briefed')
  check(/rev-list --count HEAD\.\.origin/.test(p) && /untrack_guard\.py fast-forward/.test(p) && !/pull --rebase/.test(p),
    'check 6 fast-forwards only, through the guarded merge --ff-only (#1086)')
  check(/never halt/i.test(p), 'checks 5 and 6 are explicitly non-halting')
  check(/dir_holders\.py check/.test(p) && /STATUS=CLEAR/.test(p),
    'the zombie-shell rule names the repo-agnostic live-holder probe and its CLEAR verdict')
  check(!/_browser_sweep\.py '<path>' --dry-run/.test(p),
    'no repo-local Playwright sweeper is required as proof (#571: it ships in 4 of 14 repos)')
  check(/STATUS=UNKNOWN/.test(p) && /STATUS=LIVE/.test(p),
    'both a live holder and an unrunnable probe are still RESIDUE')
  check(/cwd=<unreadable>/.test(p) && /Do not try to match a particular zombie/.test(p),
    'per-directory zombie attribution is explicitly not required')
  check(/number of such shells is irrelevant/i.test(p),
    'nothing keys on how many zombie-pinned shells exist')
  check(/Any one of the five unestablished/.test(p),
    'any unestablished condition is still RESIDUE')
  // #572: check 3 is scoped to the lane, and check 6 no longer depends on it.
  check(/Check 3 — this lane's branch is gone/.test(p),
    'check 3 asserts only this lane\'s branch, not a repo-wide branch list')
  check(!/must show the default branch only/.test(p),
    'the old whole-repo branch assertion is gone')
  check(/Judge only your own branch/.test(p) && /foreignBranches/.test(p),
    'other lanes\' branches are reported, never residue')
  check(/git branch --merged/.test(p) && /squash/.test(p),
    'the brief warns that --merged is unreliable against a squash-merged branch')
  check(/\*\*and check 4 came back clean\*\*/.test(p) && !/checks 3 and 4 both came back clean/.test(p),
    'the fast-forward is gated on the tree, not on the presence of unrelated refs')
}

// --- Case 8: SKILL.md and the teardown prompt must not drift (#572) --------
// They carry the same rules and are edited by different people at different
// times; a rule that lives in only one of them is a rule that will be lost.
{
  const skill = readFileSync(join(REPO, '.claude', 'skills', 'cleanup-fleet-all', 'SKILL.md'), 'utf8')
  check(/foreignBranches/.test(skill), 'SKILL.md documents the foreignBranches field')
  check(/fleet-config#572/.test(skill), 'SKILL.md records why foreign branches stopped halting runs')
  check(/this lane's own branch|this lane's branch/i.test(skill),
    'SKILL.md scopes the branch check to the lane')
}

// --- Case 9: build reports the issue already closed -> no teardown comment (#623) ---
// The orchestrator's own step-5 pre-dispatch check can still miss a closure
// that happens mid-run, hours into a serial sweep. /issue-start's own
// "closed -> stop" check is the last line of defense; the build agent
// surfaces that via alreadyClosed, and teardown must not post the normal
// "unattended lane escalated" comment over an already-resolved thread.
{
  const { sink, agentImpl } = promptSpy(l => {
    if (l.includes(':build:')) {
      return { status: 'failed', verification: 'SKIPPED', retryable: false, reason: 'issue already closed', alreadyClosed: true }
    }
    return reply(l, {})
  })
  sink.args = { issuesByBucket: { bug: ISSUES.bug } }
  const res = await makeRunner(agentImpl, sink)
  const r = res.buckets[0].results[0]
  check(r.status === 'escalated' && r.round === 1, 'alreadyClosed build failure escalates immediately, no retry')
  check(!sink.order.includes('bug:validate:charlie#3'), 'no validate after an alreadyClosed build failure')
  const p = sink.prompts['bug:teardown:charlie#3']
  check(!!p, 'teardown still runs for an alreadyClosed lane (worktree cleanup is still owed)')
  check(!/Post a `gh issue comment/.test(p), 'no escalation-comment instruction for an alreadyClosed lane')
  check(/already closed/i.test(p) && /confusing noise/i.test(p),
    'the teardown brief explains why no comment is posted')
}

// --- Case 10: the handoff artefact is a committed branch (#641) -----------
// In the 2026-08-15 run several build agents read "STOP. Do NOT push, open a
// PR, merge, or run /issue-finish" as also meaning "do not commit", and handed
// off dirty worktrees. Nothing broke only because the execute agent's
// /issue-finish committed for them — the defect was absorbed downstream rather
// than surfaced, so it would have recurred silently until a lane composition
// changed. The fix is wording in the briefs plus one assertion at the lane
// boundary; both are prompt text, so both are asserted here.
{
  const { sink, agentImpl } = promptSpy(l => reply(l, {}))
  sink.args = { issuesByBucket: { bug: ISSUES.bug } }
  await makeRunner(agentImpl, sink)

  const b = sink.prompts['bug:build:charlie#3']
  check(!!b, 'build prompt captured')
  check(b.includes('Commit your work on the branch'),
    'the build brief tells the agent to commit before stopping')
  check(b.includes('**committed branch**, not a dirty working tree'),
    'the committed branch is named as the handoff artefact')
  check(b.includes('Do NOT push, open a PR, merge, or run /issue-finish'),
    'push / PR / merge / issue-finish stay unambiguously forbidden')
  check(b.includes('"Do not ship" does not mean "do not commit"'),
    'the brief closes the exact misreading that caused #641')
  check(/no AI-attribution trailer/i.test(b),
    'the commit instruction carries the no-AI-attribution rule')
  check(b.includes('clean tree with no new commits is a valid report'),
    'a legitimately empty build is still allowed to commit nothing')

  const v = sink.prompts['bug:validate:charlie#3']
  check(!!v, 'validate prompt captured')
  check(v.includes('git status --porcelain` must be empty'),
    'validate asserts a committed handoff at the lane boundary')
  check(v.includes('the leniency rule below does not soften'),
    'the dirty-tree assertion is exempted from the lenient default')
  check(v.includes('Judge the **tree**, not the commit count'),
    'a build that legitimately changed nothing is not failed for having no commits')
}

// --- Case 10b: the same rule in every build-and-stop brief (#641) ----------
// /cleanup-fleet's hard tier is documented as the /issue-batch contract, so a
// rule that lands in one of these files and not the others is drift by
// construction.
{
  for (const [rel, label] of [
    [['.claude', 'skills', 'cleanup-fleet', 'SKILL.md'], '/cleanup-fleet hard-tier prompt'],
    [['skills', 'issue-batch', 'SKILL.md'], '/issue-batch build prompts'],
  ]) {
    // These two files hard-wrap their prompt blocks, so a phrase legitimately
    // spans a line break — match on whitespace-collapsed text, not raw bytes.
    const txt = readFileSync(join(REPO, ...rel), 'utf8').replace(/\s+/g, ' ')
    check(txt.includes('Commit your work on the branch'),
      `${label} tells the agent to commit before stopping`)
    check(txt.includes('committed branch, not a dirty working tree'),
      `${label} names the committed branch as the handoff artefact`)
    check(txt.includes('"Do not ship" does not mean "do not commit"'),
      `${label} closes the #641 misreading`)
    check(/fleet-config#641/.test(txt), `${label} records why the wording changed`)
  }
}

// --- Case 11: a prompt-drift validator runs the preservation gate (#833) ----
// Only that bucket's brief carries it, and a rejection there is an ordinary
// validator rejection: one retry, then escalation.
{
  const { sink, agentImpl } = promptSpy(l => reply(l, { validateFail: l.startsWith('prompt-drift:') }))
  sink.args = { issuesByBucket: { 'prompt-drift': ISSUES.documentation.slice(0, 1), bug: ISSUES.bug } }
  const res = await makeRunner(agentImpl, sink)
  const pd = Object.entries(sink.prompts).find(([l]) => l.startsWith('prompt-drift:validate:'))
  const other = Object.entries(sink.prompts).find(([l]) => l.startsWith('bug:validate:'))
  check(!!pd && /context-purge\/check\.py --base origin\//.test(pd[1]) && /directive/.test(pd[1]),
    'a prompt-drift validator is briefed with check.py --base and the directive-inventory walk')
  check(!!other && !/context-purge\/check\.py/.test(other[1]), 'other buckets are not given the preservation gate')
  const r = res.buckets[0].results[0]
  check(r.status === 'escalated' && r.round === 2 &&
    sink.order.filter(l => l.startsWith('prompt-drift:validate:')).length === 2,
    'a failed preservation gate retries once, then escalates')
}

// --- Case 12: a foreign merged worktree defers its repo, the run continues (#1077) ---
// On 2026-09-27 another lane created a worktree mid-run in a repo this run
// later shipped in. Teardown read it as residue and halted with 41 issues
// unstarted. A foreign, clean, merged worktree now passes teardown, and the
// rest of that repo's issues are deferred -- in later buckets too -- while
// every other repo keeps going.
const ISSUES_1077 = {
  documentation: [
    { repo: 'alpha', number: 1, title: 'a', body: 'x' },
    { repo: 'bravo', number: 2, title: 'b', body: 'x' },
  ],
  bug: [
    { repo: 'alpha', number: 5, title: 'e', body: 'x' },
    { repo: 'charlie', number: 3, title: 'c', body: 'x' },
  ],
}
const FOREIGN_LINE = 'WORKTREE=E:/automation/alpha-wt-nav-41af40a BRANCH=chore/revendor-nav CLASS=foreign-merged REASON=squash-merged'
{
  const { sink, agentImpl } = tracker(l => reply(l, l === 'documentation:teardown:alpha#1' ? { foreignWorktrees: FOREIGN_LINE } : {}))
  sink.args = { issuesByBucket: ISSUES_1077 }
  const res = await makeRunner(agentImpl, sink)
  check(res.halted === null, 'a foreign merged worktree never halts the run (#1077)')
  check(!sink.order.some(l => l.includes('alpha#5')), 'the rest of that repo\'s work is not started, in later buckets too')
  check(sink.order.includes('documentation:teardown:bravo#2') && sink.order.includes('bug:teardown:charlie#3'),
    'every other repo keeps going')
  const d = res.deferred || []
  check(d.length === 1 && d[0].repo === 'alpha' && d[0].number === 5 && d[0].bucket === 'bug',
    'the deferred issue is returned with its repo, number and bucket')
  check(d.length === 1 && d[0].title === 'e' && d[0].body === 'x',
    'a deferred item keeps title and body, so the retry pass can dispatch it')
  check(d.length === 1 && d[0].repo_state === 'foreign-worktree' && d[0].skip_reason.includes('alpha-wt-nav-41af40a'),
    'the deferral names the foreign worktree')
  const a1 = res.buckets[0].results.find(r => r.issue.number === 1)
  check(a1 && a1.residue === 'CLEAN' && (a1.foreignWorktrees || '').includes('CLASS=foreign-merged'),
    'the lane that found it stays CLEAN and reports the foreign worktree')
  const logs = sink.logs.join('\n')
  check(/foreign worktree \(not residue\)/.test(logs) && /deferred alpha#5/.test(logs),
    'the foreign worktree and the deferral both surface in the run log')
}

// --- Case 12b: anything else still halts (#1077) ---------------------------
// The classifier calls the run's own leftover, a dirty foreign worktree and an
// unmerged one residue; teardown then reports RESIDUE. That halts exactly as
// before, even if the agent also filled in foreignWorktrees.
{
  const { sink, agentImpl } = tracker(l => reply(l, l === 'documentation:teardown:alpha#1'
    ? { residue: true, foreignWorktrees: FOREIGN_LINE.replace('foreign-merged', 'foreign-dirty') }
    : {}))
  sink.args = { issuesByBucket: ISSUES_1077 }
  const res = await makeRunner(agentImpl, sink)
  check(res.halted !== null && res.halted.repo === 'alpha', 'a RESIDUE teardown still halts, whatever else it reports')
  check(!sink.order.some(l => l.includes('bravo') || l.includes('charlie') || l.includes('alpha#5')),
    'no lane starts after the halt')
  check(Array.isArray(res.deferred) && res.deferred.length === 0,
    'a halt is not turned into a deferral')
}

// --- Case 13: the teardown brief runs the classifier with the run's own set (#1077) ---
// "Created by this run" is whatever the script passes the classifier: every
// issue's conventional worktree path in that repo, plus every worktree and
// branch a lane of this run reported. Asserted on the brief, since the brief
// is where the classifier gets invoked.
{
  const { sink, agentImpl } = promptSpy(l => {
    const n = (l.match(/#(\d+)$/) || [])[1]
    if (l.includes(':build:')) return { status: 'built', branch: `fix/${n}-x`, worktree: `E:\\automation\\${l.split(':')[2].split('#')[0]}-wt-${n}-lane`, verification: 'PASS' }
    return reply(l, {})
  })
  sink.args = { issuesByBucket: ISSUES_1077 }
  await makeRunner(agentImpl, sink)
  const p1 = sink.prompts['documentation:teardown:alpha#1']
  const p5 = sink.prompts['bug:teardown:alpha#5']
  check(!!p1 && !!p5, 'both alpha teardown prompts captured')
  check(/worktree_residue\.py classify E:\/automation\/alpha /.test(p1), 'check 1 runs the classifier on the lane\'s repo')
  check(p1.includes("--own-worktree 'E:\\automation\\alpha-wt-1'") && p1.includes("--own-worktree 'E:\\automation\\alpha-wt-5'"),
    'every issue the run holds in the repo contributes its conventional worktree path, later buckets included')
  check(p1.includes("--own-worktree 'E:\\automation\\alpha-wt-1-lane'") && p1.includes("--own-branch 'fix/1-x'"),
    'the lane\'s own reported worktree and branch are own')
  check(!p1.includes('bravo-wt') && !p1.includes('charlie-wt'), 'other repos\' paths are never passed')
  check(p5.includes("--own-branch 'fix/1-x'") && p5.includes("--own-worktree 'E:\\automation\\alpha-wt-1-lane'"),
    'an earlier lane of this run in the same repo stays own for later teardowns')
  check(!/must list the primary only/.test(p1), 'the unconditional "primary only" rule is gone')
  check(/VERDICT=foreign-deferred/.test(p1) && /foreignWorktrees/.test(p1) && /Never remove, prune/.test(p1),
    'foreign-deferred passes, is reported in foreignWorktrees, and is never touched')
  check(/VERDICT=residue/.test(p1) && /CLASS=own/.test(p1) && /foreign-dirty/.test(p1) && /foreign-unmerged/.test(p1),
    'own, dirty and unmerged worktrees are still RESIDUE')
  check(/VERDICT=unknown`, or the command could not run → RESIDUE/.test(p1), 'an unknown verdict is RESIDUE, never a pass')
  check(/CLASS=foreign-merged`? is that same foreign worktree and passes check 2/.test(p1),
    'check 2 does not re-flag the registered foreign worktree as a leftover directory')
}

// --- Case 14: SKILL.md carries the #1077 rules too ------------------------
{
  const skill = readFileSync(join(REPO, '.claude', 'skills', 'cleanup-fleet-all', 'SKILL.md'), 'utf8')
  check(/foreignWorktrees/.test(skill) && /fleet-config#1077/.test(skill),
    'SKILL.md documents foreignWorktrees and why a foreign worktree stopped halting runs')
  check(/worktree_residue\.py classify/.test(skill), 'SKILL.md\'s post-flight enumeration uses the same classifier')
  check(/`deferred`/.test(skill) && /7b/.test(skill), 'SKILL.md feeds the workflow\'s deferred list to the retry pass')
}

// --- Case 15: live files an untracking merge would delete are kept and reported (#1086) ---
// Both fast-forwards a lane runs -- land-primary at ship, check 6 at teardown --
// go through untrack_guard, and whatever either kept reaches the lane result
// and the run log. Never a bare `git pull` in the teardown brief.
{
  const { sink, agentImpl } = promptSpy(l => {
    if (l.includes(':execute:')) return { result: 'MERGED', pr: 'pr/1', mergeSha: 'deadbee', restoredUntracked: 'RESTORED_UNTRACKED=config/a.json' }
    if (l === 'documentation:teardown:bravo#2') return { ...reply(l, {}), restoredUntracked: 'KEPT_ASIDE=config/b.json stash=C:/tmp/untrack-guard-x' }
    return reply(l, {})
  })
  sink.args = { issuesByBucket: ISSUES }
  const res = await makeRunner(agentImpl, sink)
  const byNum = n => res.buckets.flatMap(b => b.results).find(r => r.issue.number === n)
  check((byNum(1).restoredUntracked || '') === 'RESTORED_UNTRACKED=config/a.json',
    'a file land-primary kept at ship reaches the lane result')
  check((byNum(2).restoredUntracked || '').includes('RESTORED_UNTRACKED=config/a.json')
    && byNum(2).restoredUntracked.includes('KEPT_ASIDE=config/b.json'),
    'ship and teardown reports are merged, neither dropped')
  check(/live files kept through the fast-forward/.test(sink.logs.join('\n')), 'the kept files surface in the run log')
  const td = sink.prompts['documentation:teardown:alpha#1']
  check(/untrack_guard\.py fast-forward/.test(td) && !/pull --ff-only`/.test(td),
    'teardown check 6 fast-forwards through the guard, never a bare pull')
  check(/restoredUntracked/.test(sink.prompts['documentation:execute:alpha#1']),
    'the ship brief asks for land-primary\'s restore lines')
  check(/untracks a file/.test(sink.prompts['documentation:build:alpha#1']),
    'the build brief makes an untracking change name its paths')
  const skill = readFileSync(join(REPO, '.claude', 'skills', 'cleanup-fleet-all', 'SKILL.md'), 'utf8')
  check(/restoredUntracked/.test(skill) && /fleet-config#1086/.test(skill),
    'SKILL.md documents restoredUntracked')
}

// --- Case 16: ship-gate invariants (fleet-config#1065) ---------------------
// A self-contradictory Build or Validate reply (a PASS claim paired with a
// contradicting field) must never let a lane ship, whatever the agent's own
// `retryable`/`pass` claims say. This is the property the workflow script
// itself must now enforce (previously only the cleanup_workflow.cjs bridge
// checked it, so the native Workflow path could ship past it).
{
  // A build claiming 'PASS' verification but 'failed' status is coerced to a
  // failed build, not trusted as a pass straight to Validate.
  const { sink, agentImpl } = tracker(l => {
    if (l.includes(':build:')) return { status: 'failed', verification: 'PASS', retryable: false }
    return reply(l, {})
  })
  sink.args = { issuesByBucket: { bug: ISSUES.bug } }
  const res = await makeRunner(agentImpl, sink)
  const r = res.buckets[0].results[0]
  check(r.status === 'escalated', 'a PASS/failed build is never trusted as a pass')
  check(!sink.order.includes('bug:validate:charlie#3'), 'no validate after an inconsistent build result')
  check(r.reason === 'failed build cannot pass verification', 'the invariant names itself in the escalation reason')
}
{
  // A verdict claiming pass:true but verification:FAIL is coerced to a fail,
  // never shipped.
  const { sink, agentImpl } = tracker(l => {
    if (l.includes(':validate:')) return { pass: true, feedback: 'looks fine', verification: 'FAIL' }
    return reply(l, {})
  })
  sink.args = { issuesByBucket: { bug: ISSUES.bug } }
  const res = await makeRunner(agentImpl, sink)
  const r = res.buckets[0].results[0]
  check(!sink.order.includes('bug:execute:charlie#3'), 'a pass:true/verification:FAIL verdict is never executed')
  check(r.status === 'escalated', 'the inconsistent verdict escalates instead of shipping')
}

console.log(failures === 0 ? '\nALL CONTROL-FLOW CHECKS PASS' : `\n${failures} CHECK(S) FAILED`)
process.exit(failures === 0 ? 0 : 1)
