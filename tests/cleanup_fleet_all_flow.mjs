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
// "no per-directory zombie attribution" rule.
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
  check(/rev-list --count HEAD\.\.origin/.test(p) && /--ff-only/.test(p) && !/pull --rebase/.test(p),
    'check 6 fast-forwards with --ff-only only')
  check(/never halt/i.test(p), 'checks 5 and 6 are explicitly non-halting')
  check(/_browser_sweep\.py/.test(p) && /live=0/.test(p),
    'the zombie-shell rule names the sweep and its live=0 requirement')
  check(/cwd=<unreadable>/.test(p) && /Do not try to match a particular zombie/.test(p),
    'per-directory zombie attribution is explicitly not required')
  check(/number of such shells is irrelevant/i.test(p),
    'nothing keys on how many zombie-pinned shells exist')
  check(/Any one of the five unestablished/.test(p),
    'any unestablished condition is still RESIDUE')
}

console.log(failures === 0 ? '\nALL CONTROL-FLOW CHECKS PASS' : `\n${failures} CHECK(S) FAILED`)
process.exit(failures === 0 ? 0 : 1)
