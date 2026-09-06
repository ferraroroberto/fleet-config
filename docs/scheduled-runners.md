# Scheduled runners

`skills/_lib/scheduled_runner.py` owns one scheduled-child lifecycle: elapsed progress, UTF-8 output, stall detection, cancellation, terminal evidence, delivery checks and eligible Claude retries. `runner_adapters.py` translates native commands/events into `ProgressEvent`; it never schedules work or duplicates the lifecycle. `claude_progress.py` remains the compatible command/import facade. App-launcher Jobs still executes the existing scripts; all eleven checked-in production launchers still use Claude.

## Select a provider explicitly

Existing callers keep their prompt-first arguments unchanged:

```powershell
& E:/automation/fleet-config/.venv/Scripts/python.exe skills/_lib/claude_progress.py '/skill-name' --permission-mode bypassPermissions
```

A new, separately authorized Codex caller uses an explicit model available to its account and an explicit permission choice:

```powershell
& E:/automation/fleet-config/.venv/Scripts/python.exe skills/_lib/scheduled_runner.py --harness codex '/skill-name arguments' --model '<supported-model-id>' --approve-for-me --delivery-check './delivery_check.py'
```

`--harness claude` selects the compatible Claude adapter; omission also retains Claude. Pi, Grok and other names fail with usage exit `2`. Harness selection precedes the prompt. `--stall-timeout <seconds>` and `--delivery-check <python-script>` belong to the outer runner and never reach either CLI. `CLAUDE_PROGRESS_STALL_TIMEOUT` remains the compatible default; `0` disables the watchdog. Cwd is inherited by both the native child and its delivery check. Stdin is closed so a scheduled child cannot wait for an absent human.

The Codex adapter supports `--model`/`-m`, `--effort`, `--sandbox`/`-s` (`read-only` or `workspace-write`), `--approve-for-me`, `--ephemeral` and `--ignore-user-config`. An explicit model and permission choice are required. `--approve-for-me` uses the installed CLI's automatic approval review; it is not unconditional permission. Denied or missing tools must be resolved through the workflow's failure/delivery contract. The selected model must support the requested effort; the runner does not invent an alias or downgrade it. Native model rejection is reported distinctly.

Codex runs `exec --json --color never`, forces the OpenAI provider and ChatGPT login method for that process, and removes inherited `OPENAI_API_KEY`/`CODEX_API_KEY` from its child environment. It does not log in, copy credentials, use an OSS provider, or fall back to another provider or metered key. The existing saved subscription authentication is used. Claude retains its existing auth/permission configuration and command semantics; the runner never adds fallback, and rejects `--fallback-model`.

For disposable conformance only, Codex also accepts explicit `--disable hooks`, `-c project_doc_max_bytes=0`, and `-c check_for_update_on_startup=false`. Those process overrides are not added by default and never rewrite live configuration. Arbitrary config overrides, profiles, resume/fork modes, output overrides, alternate providers and bypass-sandbox flags are rejected by this bounded initial Codex adapter. Verify installed help before extending it. Official [non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode) documents the JSONL surface; the [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference) documents authentication restriction and instruction-size controls.

## Completion and failure evidence

The shared runner requires a valid terminal event and observed tool work, with no malformed/unknown records or pending tools/children. Claude's `result` must establish success; Codex's `turn.completed` must have its native usage object. A prose promise, tool start, failed terminal result with process exit zero, or unknown future event never establishes completion. Known reasoning, usage/status and hook metadata is ignored without exposing raw payloads; tool summaries are allowlisted and secret-redacted. Individual failed tools may recover within the same native run, as the Codex smoke demonstrates. A real artifact still requires the caller's delivery check.

| Exit | Meaning |
| --- | --- |
| `0` | Complete stream and tool work; delivery confirmed if a check was supplied |
| `114` | Cancellation requested, descendant termination unconfirmed |
| `115` | Required tools/MCP unavailable |
| `116` | Model unavailable or unsupported |
| `117` | Authentication unavailable |
| `118` | Unfinished tools or children |
| `119` | Claude upstream 5xx failure; eligible pre-tool attempts may be retried |
| `120` | Complete turn invoked no tools |
| `121` | Delivery postcondition not confirmed |
| `122` | Missing terminal event, malformed or unknown stream |
| `123` | Skill asserted `SCHEDULED-RUN-FAILED` |
| `124` | Stalled stream |
| `125` | Interrupted/failed Claude child or native background-kill signature |
| `127` | Native executable could not start |
| `130` | Requested cancellation of the owned process tree confirmed |

Existing nonzero child exits remain nonzero and normally retain their value. Explicit provider failure classifications and the existing Claude upstream/stall rules name established causes. Stream/no-work/incomplete detectors override clean process exits; they do not hide an existing child failure. A delivery check runs once after the final attempt, regardless of the native exit, and only replaces a clean exit with `121`. Missing, failed or timed-out checks are unconfirmed. The final log reports failed delivery separately from successful process completion.

Claude retains the native background-wait override `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0` and its established 60s/180s outer retry schedule, capped at three attempts. Eligibility additionally requires no tools, children, malformed/unknown records or killed-child evidence. A side effect, including a dispatched task, closes that gate. The existing native 529 incident and CLI wait-ceiling probe are recorded in the inherited implementation comments and regression fixtures. This change adopts no new native API retry rule. Codex has **no outer replay**: its JSONL item lifecycle is not a proof that an interrupted attempt made no side effect, and no empirical Codex pre-effect replay guarantee has been established. Native request-level retries remain the CLI's responsibility.

Cancellation is supported through `KeyboardInterrupt` or `run_process(cancel_event=...)`. `ProcessScope.launch(command, env=...)` owns process creation and release; callers must not create a process and attach it afterwards. On Windows it creates the venv launcher with `CREATE_SUSPENDED`, assigns its retained handle to a private kill-on-close Job Object, writes and closes the bootstrap's stdin handshake while still suspended, then resumes the owned primary thread. This puts even the venv redirector's base-interpreter child inside the job before provider startup. Assignment or handshake failure never resumes the launcher. Main-thread SIGINT delivery is deferred only across `Popen` construction until the process object is retained, then the original handler is restored and invoked. Failed or interrupted launch cleans up the launcher and job, observes the retained process handle, and raises; unconfirmed cleanup explicitly raises `owned cleanup unknown`. The retained job owns ordinary CreateProcess descendants after the native parent exits; cancellation never targets process names/ports. POSIX keeps the same separate process group.

Both stdout and stderr use nonblocking reads, including partial UTF-8 records. Root exit starts a one-second drain grace; remaining owned processes make an otherwise clean run unfinished (`118`) and are terminated. Remaining or unqueryable ownership also closes the pre-effect retry gate, even if both pipes reached EOF and the parent returned a transient API error. Cancellation/stall stops the owned scope, waits at most two seconds to observe its active count reach zero, and bounds pipe draining. Cancellation is confirmed (`130`) only when termination was observed and both pipes reached EOF; failed/unknown queries, failed termination, or unclosed pipes stay unconfirmed (`114`). Deadline logs name the active count (or unknown) and each pipe's EOF state. Final job closure is an ownership-scoped safety net, never evidence that upgrades an unconfirmed result. Windows code uses stdlib `ctypes`, `_winapi` and `msvcrt`, with shared `NO_WINDOW` flags; no additional dependency is required.

The Windows ownership contract covers processes inherited into this job, including nested normal child jobs. External brokers such as WMI can launch processes outside it; this runner does not claim ownership of those or terminate them. An outside pipe holder still cannot force an endless drain or produce confirmed cancellation. Windows job inheritance and accounting follow Microsoft's [Job Objects contract](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects); the launch barrier follows its [suspended thread contract](https://learn.microsoft.com/en-us/windows/win32/procthread/suspending-thread-execution). Since Python closes the initial thread handle, Toolhelp finds the single thread of the retained, still-live suspended launcher, and `GetProcessIdOfThread` checks the reopened handle before `ResumeThread`. POSIX creation/group/error behavior has deterministic contract coverage; these native process proofs were run on Windows only. Arbitrary external hard-killing of the wrapper is not the verified cancellation interface. No live PTYs or unrelated process trees are targeted.

## Native proof and reproducibility

Observed 2026-09-05 on Windows, using synthetic temp git repositories containing only the disposable `scheduled-smoke` SKILL.md, a UTF-8 marker file, output and delivery checker. No private source, credential copying, live hook rewiring, production schedule changes, notifications or metered fallback were used. Both runs called `run_with_transient_retry` and `run_delivery_check` from the same outer runner. Sanitized native parser fields are committed as `tests/fixtures/scheduled_claude.jsonl` and `scheduled_codex.jsonl`; commands, tool payloads, absolute paths and reasoning are omitted.

| Adapter | Runtime / model / mode | Observed outcome |
| --- | --- | --- |
| Claude | CLI `2.1.261`, `claude-sonnet-5`, effort `low`; `-p --output-format stream-json --verbose --safe-mode --tools Read,Write --permission-mode bypassPermissions` | Read skill/input, Write output, terminal success, exit `0`, deterministic LF UTF-8 byte postcondition passed |
| Codex | CLI `0.153.3`, advertised `gpt-5.6-luna`, effort `low`; `exec --json --ignore-user-config --ephemeral --approve-for-me --disable hooks`, process overrides above | Discovered project skill, paired command/file-change items, corrected its first newline mismatch, terminal `turn.completed`, exit `0`, byte postcondition passed |

Claude safe mode disables custom skill discovery as well as private context; its prompt explicitly asked it to read the disposable SKILL.md and execute the steps with built-in tools. This proves the bounded scheduled execution path, not Claude safe-mode discovery. Codex used the normal `/scheduled-smoke` prompt normalization and `.agents/skills/scheduled-smoke/SKILL.md` discovery. Requested effort was accepted by each CLI; independent provider-side effort attestation was not performed.

An earlier Claude probe with a CRLF input produced LF output and claimed success. The unchanged byte-level delivery checker returned `1`, correctly making delivery unconfirmed. The passing repeat used an explicitly LF input; its checker still required exact bytes. This is evidence for the distinction between native completion and delivered postcondition, not a claim that Claude reliably preserves CRLF.

Run `tests/probe_scheduled_runner.py --harness <claude|codex> --model <supported-id> --effort low` with this repo's existing venv to repeat a bounded native smoke. It uses supported saved authentication, creates no production job, and prints the temp root, native milestones and the postcondition verdict. This opt-in probe is never part of the offline acceptance gate.

For ownership changes, run `tests/probe_scheduled_runner.py --ownership-only` through the real Windows venv. It runs the harmless process controls without any provider/model request: a 500 ms delay before assignment, exact private-job membership of the launcher/base interpreter/provider, retained-handle terminal checks, assignment/handshake/resume failures, pre-release and post-resume interruption, unknown cleanup, parent-exited cancellation and the orphan pipe matrix. The delay regression failed against the pre-fix helper with `venv base interpreter escaped the exact private job`; it passes with suspended creation. These controls also run in the acceptance suite. Provider compatibility evidence above is unchanged and was not repeated for this ownership fix.

Offline conformance is `tests/test_scheduled_runner.py` plus the existing `tests/test_claude_progress.py`, both registered in `tests/run_acceptance.py`. Fixtures cover native progress, unavailable auth/model/tools, missing/malformed/unknown terminal evidence, no tools, pending/interrupted children, provider selection and retry barriers. Real fake-child processes cover cwd/UTF-8, delivery failure, stall/cancel ownership and survival of an unrelated child. The parent-exited regression first failed against the reviewed candidate: cancellation returned `114` after 4.002 seconds and the pipe-holding descendant wrote its completion marker. The repaired runner returned `130` in under 0.1 seconds with no marker. The Windows matrix independently holds stdout, stderr, both, or neither; termination/query failures remain bounded and unconfirmed. Retained descendant handles prove actual exit, including the kill-on-close safety-net cases. An assignment-rejection probe proves that provider side effects never begin without ownership.

## Adding Pi/Grok or another event surface

Pi and Grok are extension targets, not implemented adapters. Add only an explicit command/environment adapter and normalization into the existing `ProgressEvent` kinds: start, text, tool start/end, child start/progress/end, result, error, malformed and unknown. Keep the scheduler, workflow, delivery checks and lifecycle shared. Unsupported native events must remain unknown; never translate an unfamiliar item into a completed tool or terminal success.

Before advertising support, inspect that exact installed CLI's argv, authentication, model/effort and JSONL schema; add sanitized success/failure fixtures and run the same conformance matrix. Native smoke must execute a disposable skill and prove a deterministic artifact with normal auth. Verify missing tools, child completion, stall and owned cancellation independently. Any proposed outer retry requires a harmless empirical probe of the native API's pre-effect semantics first. Preserve unknown states until each fact is proven.

Codex delegated-child items currently remain unknown and fail conformance; this unit proves a bounded scheduled skill using local native tools. Interactive collaboration verified in [workflow-capabilities.md](workflow-capabilities.md) does not certify Codex CLI scheduled-child semantics. A delegated scheduled workflow must pass its own native child collection/cancellation scenarios before adoption.
