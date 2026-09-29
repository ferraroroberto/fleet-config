// Interactive request/result bridge for ONE fixed fleet cleanup workflow.
// No worker/process spawning; the caller owns native tools and observed results.
const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const workflowPath = path.resolve(__dirname, '../../.claude/workflows/cleanup-fleet-all.js')
const hash = value => crypto.createHash('sha256').update(value).digest('hex')

function validate(value, schema, name = 'result') {
  if (schema.type === 'object') {
    if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error(`${name}: expected object`)
    for (const key of schema.required || []) {
      if (!Object.hasOwn(value, key)) throw new Error(`${name}: missing ${key}`)
    }
    for (const [key, field] of Object.entries(schema.properties || {})) {
      if (Object.hasOwn(value, key)) validate(value[key], field, `${name}.${key}`)
    }
  } else if (typeof value !== schema.type) {
    throw new Error(`${name}: expected ${schema.type}`)
  }
  if (schema.enum && !schema.enum.includes(value)) throw new Error(`${name}: invalid enum value`)
}

async function next(state) {
  if (!state || !state.issuesByBucket || typeof state.issuesByBucket !== 'object' || Array.isArray(state.issuesByBucket) || !Array.isArray(state.results)) {
    throw new Error('state requires issuesByBucket object and results array')
  }
  for (const issues of Object.values(state.issuesByBucket)) {
    if (!Array.isArray(issues)) throw new Error('bucket must contain an issue array')
    for (const issue of issues) {
      if (!issue || !/^[a-zA-Z0-9_.-]+$/.test(issue.repo || '') || !Number.isInteger(issue.number) || issue.number <= 0 || typeof issue.title !== 'string' || typeof issue.body !== 'string') {
        throw new Error('invalid issue identity/title/body')
      }
    }
  }
  const source = fs.readFileSync(workflowPath, 'utf8')
  const workflowHash = hash(source)
  if ((state.workflowHash && state.workflowHash !== workflowHash) || (state.results.length && !state.workflowHash)) {
    throw new Error('workflow hash missing or changed; do not replay old results')
  }
  const sourceBody = source.replace('export const meta =', 'const meta =')
  const execute = new AsyncFunction('args', 'agent', 'log', 'phase', sourceBody)
  const pending = Symbol('pending')
  let cursor = 0
  let request
  const agent = async (prompt, opts) => {
    const id = hash(JSON.stringify([workflowHash, state.issuesByBucket, cursor, prompt, opts]))
    const prior = state.results[cursor++]
    if (!prior) {
      request = { id, prompt, ...opts }
      throw pending
    }
    if (prior.id !== id || !Object.hasOwn(prior, 'result')) throw new Error('result does not match the next request')
    if (prior.result !== null) {
      validate(prior.result, opts.schema)
      // The two ship-gate invariants beyond schema shape (pass requires
      // verification PASS; a PASS build requires status built) are enforced
      // *twice*, deliberately, not duplicated by accident: the workflow
      // script's own `inconsistentBuild`/`inconsistentValidate` protect the
      // native Workflow path, which never runs through this bridge's agent()
      // at all, by self-healing a contradiction into a normal escalation.
      // This check protects the *other* call path — `node cleanup_workflow.cjs
      // <state.json>`, the standalone CLI fallback SKILL.md documents for
      // when native Workflow isn't available — by failing loud (non-zero
      // exit) the moment a replayed result is self-contradictory, rather than
      // letting bad input silently reach business logic. The workflow script
      // has no module system (it must stay a bare, pasteable script body for
      // the native Workflow tool), so the two checks cannot share code; keep
      // both conditions identical to `inconsistentBuild`/`inconsistentValidate`
      // if either ever changes (fleet-config#1065).
      if (opts.phase === 'Validate' && (!prior.result.feedback.trim() || (prior.result.pass && prior.result.verification !== 'PASS'))) {
        throw new Error('validator requires feedback and a consistent verification verdict')
      }
      if (opts.phase === 'Build' && prior.result.verification === 'PASS' && prior.result.status !== 'built') {
        throw new Error('failed build cannot pass verification')
      }
    }
    return prior.result
  }
  try {
    const result = await execute({ issuesByBucket: state.issuesByBucket }, agent, () => {}, () => {})
    if (cursor !== state.results.length) throw new Error('unused results after workflow completion')
    return { status: 'complete', workflowHash, result }
  } catch (error) {
    if (error !== pending) throw error
    return { status: 'request', workflowHash, request }
  }
}

module.exports = { next }
if (require.main === module) {
  Promise.resolve().then(() => {
    if (process.argv.length !== 3) throw new Error('usage: node cleanup_workflow.cjs <state.json>')
    const state = JSON.parse(fs.readFileSync(process.argv[2], 'utf8').replace(/^\uFEFF/, ''))
    return next(state)
  }).then(result => process.stdout.write(`${JSON.stringify(result)}\n`)).catch(error => {
    process.stderr.write(`WORKFLOW=unknown ${error.message}\n`)
    process.exitCode = 2
  })
}