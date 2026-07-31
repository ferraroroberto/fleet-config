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
  if (label.includes(':teardown:')) return { residue: kind.residue ? 'RESIDUE' : 'CLEAN', detail: kind.residue ? 'worktree dir busy' : 'verified clean' }
  throw new Error('unknown label ' + label)
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

console.log(failures === 0 ? '\nALL CONTROL-FLOW CHECKS PASS' : `\n${failures} CHECK(S) FAILED`)
process.exit(failures === 0 ? 0 : 1)
