# Interactive workflow capabilities

This is the canonical capability contract for shared issue/audit skills (fleet-config#749). It adapts workflow intent to tools exposed by the current session. It does not install tools, switch permission/plan mode, launch a scheduled process, or grant shipping authorization. Project skill discovery and scope remain owned by project-scaffolding’s `docs/agents/project-skills.md`; scheduled spawning and stream parsing are separate (fleet-config#750).

## Bind before dispatch

Record the host **and surface** (for example Codex app/API collaboration versus Codex CLI), version when observable, execution mode, callable tool names and accepted parameters. Bind the six operations below from the active tool schema. A CLI name, discovery link, earlier session or documentation claim does not establish a callable tool. Report each capability as verified, unsupported (tested absent), or unknown (not established). Unknown is never a passing capability.

| Operation | Required behavior | Missing capability |
| --- | --- | --- |
| Spawn | Independent task, concrete ID, workspace, fresh/inherited context choice, accepted model/effort and slot limit. | Run eligible work serially, explicitly reporting serial mode. Role-separated review cannot become self-review. |
| Collect | Terminal success/failure/cancel status and actual result for the specific ID; schema validation where the skill requires it. | Do not dispatch when no reliable collection path exists. A launch acknowledgement is not completion. |
| Wait/resume | Wait on concrete IDs/mailbox; distinguish timeout, user steering, progress and terminal result. | Use bounded status/result polling within this turn. Never end the turn expecting an unverified wake-up. |
| Cancel | Stop further dispatch, cancel/interrupt only owned workers, confirm terminal outcome before teardown. | Stop dispatch, request cooperative stop, drain running work; report cancellation unconfirmed. Never kill unrelated processes. |
| Model/effort | Resolve easy/hard/extreme intent using current supported metadata; record requested versus observed controls. | Inherit only when acceptable for the task; report effort unconfirmed. An explicit unsupported request needs clarification. |
| User input | Use only a callable tool allowed in the current mode and for this kind of question. | Ask a concise plain-text question and stop dependent work when an answer is required. In unattended mode report blocked; silence/time elapsed is never approval. |

Read `docs/model-tiers.md` for tier intent; do not duplicate a permanent host mapping in a skill. Host slot limits also apply when a tier has no fleet cap. Lack of spare slots queues work; it is not proof that spawn is unsupported.

## Dispatch and collect

Maintain a ledger for the selected work: pending, running (ID, checkout, requested model/effort), completed (actual result), failed, cancelled, or unknown. Launch at most the minimum of free host slots, the skill’s window and any applicable model cap. Setup shared Git metadata sequentially before dispatch. One writer per checkout; use `worktree_claim.py` and active-issue markers exactly as the adopting skill requires. Finishes against one primary repo are serialized even when worktree builds can overlap.

After each wait, consume all delivered terminal results, validate their task identity and result schema, record them, then refill eligible slots. A timeout, interrupted wait, progress message, missing output or pruned agent-list entry never invents a result. Keep already-collected terminal mailbox results even if the host later prunes the worker from its list. Complete only when every selected item is accounted for and every launched worker has a terminal result. Report pending/unknown separately if collection is lost; never print all-success or run a dependent ship step.

Failed workers follow the adopting skill’s existing policy. `/issue-yolo` rejection stops and reports verbatim, with no automatic retry. `cleanup-fleet-all` uses its fixed two-round policy and terminal teardown. Never silently respawn a worker whose write outcome is unknown: it may have committed or shipped. Inspect actual branch/PR state and establish the old worker has stopped first.

On cancellation, stop dispatching immediately. Native interruption is not proof that an OS child or write stopped. Collect or inspect owned work until its outcome is confirmed; preserve the branch/worktree when uncertain. Run only authorized teardown after the writer has stopped. Do not convert cancelled/unknown work into successful completion. A user who requests cancellation has not authorized shipping pending work.

No-spawn fallback is explicit and workflow-specific: read-only audits and permitted builds can run serially in the same session, with the same per-item gates and isolation. Skills forbidding orchestrator writes (`cleanup-fleet`, `issue-finish-batch`, `cleanup-fleet-all`) instead hand off one standalone worker at a time; if no separate worker can be launched, report blocked or return the concrete sequential commands for the user. Never override that role boundary by editing or shipping in the orchestrator. Independent review requires a fresh context or independent human; `/issue-batch` and hard-tier cleanup still hand off for human review before shipping.

## Observed bindings

### Claude Code CLI 2.1.261

Native initialization listed `Task`, `TaskOutput`, `TaskStop`; the actual model called its accepted `Agent` alias with `prompt`, `description`, `subagent_type`, `model`, and `run_in_background`. Inspect the current schema rather than assuming the inventory spelling. Foreground calls return terminal content; background IDs require terminal notification/result collection or `TaskOutput` polling. `TaskStop` is available for owned task cancellation; cancellation side effects remain unverified by the smoke below. No per-Agent effort control was established. `Workflow` is an optional specialization only when actually exposed, with its own result/wait path.

### Codex app/API native collaboration (2026-09-05 session)

`collaboration.spawn_agent` returns a canonical task ID. `fork_turns: "none"` gives fresh context; full-history forks inherit the parent model/effort and cannot override them. Use fresh context for independent review. `collaboration.wait_agent` waits for mailbox activity, not necessarily task completion; consume delivered `FINAL_ANSWER` results. `list_agents` is an inventory aid, and completed entries can be pruned. `send_message` queues a message without starting an idle turn; `followup_task` can start an idle worker. `interrupt_agent` interrupts the current turn and leaves the agent addressable; it does not promise to kill OS children.

This session exposed four active slots including the root, and accepted the probe’s `gpt-5.6-luna`, `reasoning_effort: "low"`. Other exposed names included Astra/Sol/Terra; they are session metadata, not permanent tier IDs or evidence of provider execution. The Default-mode session had Plan-only `request_user_input` unavailable for use; use `request_user_input_async` only when actually exposed and permitted, otherwise plain text. Do not switch modes or invent an input tool to satisfy a skill. This proof does **not** certify Codex CLI’s tool names, model menu, or wake-up semantics.

### Other surfaces

Pi, Grok and Copilot interactive delegation/collection remain **unknown** until the conformance scenarios below pass on that exact host. Grok advertising `spawn_subagent` alone is not proof of a completed workflow. Scheduled process support, hook parity and discovery are separate facts.

## Cleanup without Workflow

`.claude/workflows/cleanup-fleet-all.js` remains the single source of prompts, schemas and fixed build/validate/execute/teardown decisions. If callable `Workflow` and its result path exist, use the skill’s Claude specialization. Otherwise use the narrow interactive bridge `skills/_lib/cleanup_workflow.cjs` with native fresh workers. It never launches a process, sends a tool call or performs repository operations.

Create an ignored local JSON state file containing `{"issuesByBucket": {"bug": [{"repo":"synthetic","number":1,"title":"probe","body":"synthetic"}]}, "results": []}` (replace the fixture with the preflighted issue set only during an authorized real run). Run `node <fleet-config>/skills/_lib/cleanup_workflow.cjs <state-file>`. The output is either `request` (prompt, schema, phase, label, request ID) or `complete` (the original workflow aggregate). Save the printed `workflowHash` into the state before accepting results. Dispatch the exact prompt/schema through a fresh native worker, collect its terminal result, then append `{"id":"<request ID>","result":<verdict>}` to `results` and repeat. Use `null` only for a confirmed failed worker with no result, never a timeout or missing output. Preserve the ledger’s worker ID and real evidence separately; hashes detect mismatched replay, not whether a worker ran.

The bridge re-evaluates only this repository’s fixed, side-effect-free decision script against collected results. It rejects changed workflow hashes, mismatched request IDs, malformed schemas and unused results. It does not trust external executable paths or evaluate worker text as code. Before dispatching Execute, re-confirm the validated branch SHA and clean tree still match the reviewer’s evidence; a changed branch requires fresh validation. Cancellation stops bridge dispatch; do not feed a fabricated result to advance it. Review is always a fresh worker; if spawn is unavailable this path stops before work begins. The existing serial-lane, two-round retry, residue halt and post-flight checks remain in force.

## Conformance and evidence

Repeat in an empty synthetic repository with no private content, normal supported authentication, and no worker filesystem/network tools. Record exact host/surface/version, cwd, tool schema, launch IDs, result IDs/statuses and order, requested/observed models and efforts. Never use real issue shipping as a smoke test.

1. Launch two bounded arithmetic-marker workers before collecting either; collect both terminal results before final completion. Record actual native events rather than only the parent’s claim.
2. Exercise timeout/progress before success, one failed worker, cancellation requested versus confirmed, missing result and slot exhaustion using capability fixtures. Preserve pending/unknown states and never ship on them. If native cancellation is unprobed, label it unknown even when the fixture passes.
3. Remove spawn from the fixture: serial read-only work still collects each result; independent review and orchestrator-forbidden writes block. Remove input or select a mode disallowing it: required questions wait in text, unattended runs block; no inferred approval.
4. For a new Pi/Grok surface, inspect installed tool metadata and repeat step 1 natively before changing unknown to verified. Probe model/effort acceptance, fresh context, wait and cancellation independently; list any unproven capabilities instead of certifying the whole harness.
5. Run `tests/test_workflow_capabilities.py` and the full acceptance gate. Fixtures prove decision boundaries; they do not replace native model evidence.

Observed on 2026-09-05: Claude synthetic session `4e5b0bef-efc3-4a61-919f-c0c9fc8fa3b4` launched workers `a8f821b2fe67b0212` and `a102eb93132c132e1` at 13:34:43/44 UTC before terminal results at 13:34:44/46 UTC; both used `claude-sonnet-5`, zero worker tool calls, markers `PROBE_749_CLAUDE_ALPHA result=42 status=complete` and `PROBE_749_CLAUDE_BETA result=63 status=complete` (Beta repeated its marker). CLI terminal stats: spawned 2, completed 2, failed/killed 0. Calls were foreground and overlapped; background lifecycle was not needed for this proof.

Codex native collaboration launched `/root/smoke_749_alpha` and `/root/smoke_749_beta` with `fork_turns: none`, `gpt-5.6-luna`, `low`, before collection. Actual terminal mailbox results were `PROBE_749_ALPHA result=42 status=complete` and `PROBE_749_BETA result=63 status=complete`; `wait_agent(timeout_ms:60000)` returned `timed_out: false` between them. Both were collected before reporting pass. No tools/files/network in either worker. A separate fresh worker `/root/smoke_749_cancel` was interrupted: `interrupt_agent` returned previous status running, then `list_agents` showed interrupted. A new `followup_task` on that agent produced `PROBE_749_FOLLOWUP status=complete` after a mailbox wait; the original task remains interrupted. No OS-child termination or provider-level model attestation was tested.

A separate fresh Codex Luna worker (`/root/contract_749_cases`, no tools) applied the instruction contract to eight synthetic absent-feature cases: audit/no-spawn → serial; YOLO/no-fresh-review → block shipping; finish-batch/no-spawn → handoff/block; Default-mode/Plan-only input → ask in text and wait; unattended required input → blocked; timeout → pending/unknown; cancellation acknowledgement without terminal proof → unconfirmed; hard-tier built branch → human review before ship. All eight matched the contract. This is instruction-fixture interpretation evidence, not proof that any missing native tool exists.
