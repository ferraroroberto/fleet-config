# Global instructions

One file, symlinked by `fleet-config/install.ps1` into every agent's user-scope context path — Claude Code (`~/.claude/CLAUDE.md`), Codex (`~/.codex/AGENTS.md`), Pi (`~/.pi/agent/AGENTS.md`), Copilot CLI (`~/.copilot/copilot-instructions.md`). Hooks, statusline, and tool settings are Claude-Code + Codex-only (matrix: `fleet-config`'s `docs/cross-agent-parity.md`). Agent-specific sections are marked *(… only — skip on other agents)*.

> **Here vs project.** This file owns the **universal**. Shape-specific guidance (Streamlit, tray/daemon, e2e UI testing, GitHub-Actions CI, restart recipes) lives in `project-scaffolding`'s `CLAUDE.md`. Test: *"would this still apply to a bare repo with no app?"* Yes → here, no → the scaffold, never both. (`ferraroroberto/project-scaffolding#68`; `/context-audit` enforces weekly.)

## Working method

### Plan mode is the default

Every non-trivial request starts in plan mode — non-trivial = anything beyond a one-line fix, a typo, or a question answerable without touching code. In plan mode:

- Do NOT edit files, run destructive commands, or commit anything.
- Investigate freely: read files, search, run read-only commands.
- Resolve ambiguity through questions *before* proposing a plan; present it only when confident it matches what the user wants.
- Stay in plan mode across rejections — revise and re-present; don't bail out to execution.

Recommended project setting: `{ "permissions": { "defaultMode": "plan" } }`. Exit plan mode only after explicit approval; approval transitions straight to execution in the same turn.

### Ask before assuming

Ask whenever a decision is expensive to undo or genuinely ambiguous. One sharp question beats three filler ones; multi-choice (2–4 options) when the choice space is bounded. Multiple reasonable approaches → present them as options with tradeoffs, don't pick silently.

Always ask before assuming: file/module location for new code; data shape or schema; data source; error and empty-state handling; whether to add tests, and at what level. Don't ask about things determinable from the code, things already specified, or meta-questions like "is the plan ready?" — that's what plan approval is for.

### Before editing

- Re-read any file before modifying it; for files >500 LOC, read in chunks.
- When renaming a symbol, search separately for: direct calls, type references, string literals, dynamic imports, re-exports, and tests.
- Reproduce before fixing: any non-trivial bug gets a repro (script, failing test, or documented sequence) first.
- Re-verify the issue's premise: confirm the symptom still reproduces and the code matches the issue before starting.
- `git log -- <file>` the area first — prior attempts at the same fix are the cheapest source of truth.

### While fixing

- Empirical proof for retry/timeout/backoff logic — verify the API-semantics assumption with a 10-line probe before shipping.
- A claimed limitation or blocker ("the API can't", "needs a credential", "impossible here") is a material claim — state it only with the verbatim error, the doc statement, or a live probe in hand; run the cheap probe before asking or declaring a step blocked.
- Distinct error messages for distinct conditions ("down" vs "in flight past timeout").
- Don't bundle independently-revertable bugs in one PR — if bug-A reverts without breaking bug-B's fix, ship two PRs.
- Leave info-level log breadcrumbs after a hard bug, in the same commit as the fix — the next occurrence must be diagnosable from logs.
- Test-plan checkboxes are observed, not aspirational: `[x]` means "I ran this and saw it pass."

### Execution: scope up front, then carry it through

- Front-load the questions — settle scope, ambiguity, and hard-to-undo decisions before starting.
- Carry-through applies to agreed work. When the user asks a question, describes a problem, or thinks out loud, the deliverable is your assessment, not a fix — change nothing until they ask for one.
- Once scope is agreed, execute end-to-end to a verified, shippable state. No per-phase approval; "large" is not "stop".
- Checkpoint on risk, not size: pause mid-task only for a real ambiguity, an unforeseen decision, or a finding that contradicts the plan.
- After finishing and verifying a unit, check the related open issues; a natural continuation → state it and proceed, new branch off freshly-merged `main`. Pause for approval only when it's risky, ambiguous, or materially bigger than discussed.
- Keep commits and branches separable so any piece reviews and reverts on its own.

### Verify before declaring done

- Verify every unit with the project's actual tooling (byte-compile, lint, tests). No checker exists → say so explicitly; never claim "tests pass" where there are no tests. Report failures faithfully with the output; never report done on a skipped step.
- A passing suite proves the code behaves as written — not that the symptom is gone or the fix is live in the deployed process. Re-run the original repro against that process and watch it pass before declaring done. (Deploy-coverage — `project-scaffolding#199`, `fleet-config#459` — confirms the fix shipped; this confirms it fixed what was reported.)
- A regression test must first be proven to fail against pre-fix code (`git stash`), or its later pass means nothing.
- If the repo declares a restart/refresh recipe for a long-lived local process, use it after code changes so the verified change is actually live (unless the user opted out). Don't ask a second permission just because the recipe restarts something — the local `CLAUDE.md` owns the command, scope, and build-identity check. No recipe, or the recipe says confirm first → stop and say exactly what's missing; never improvise process kills.
- Any check, gate, health probe, or classifier that can fail to establish a fact must report that as its own state — `unknown` / `not confirmed` — never folded into the passing state. A null, a stale cache, an unresolved probe is not "fine"; a write acknowledged is not an outcome confirmed. Applies to health checks, verification gates, deploy-coverage checks, and delivery/status classifiers alike.

### Senior-dev check

Before finishing, ask: "What would a senior, perfectionist dev reject in review?" Fix duplicated state, inconsistent patterns, or broken architecture *within the file you're already editing* — don't expand scope to unrelated files.

## Conventions

- **Read the README first.** Don't assume `/app/`, `/src/`, `launch_app.bat`, or any path exists.
- **Web-app UI work consults the fleet design system:** `~/.claude/design.md` (light) + `~/.claude/design.dark.md` (dark) — colors, typography, spacing, and the navigation contract (floating bottom-tab pill). `/design-sync` reports drift. Streamlit POC spikes exempt.
- **Config & secrets:** project config in `config.json` or similar; secrets always in `.env`, never committed (`.env` is the env file; `.venv` is the venv directory).
- **Virtual environment:** use the existing `.venv`. Never create `venv`. Never activate — invoke via `& .\.venv\Scripts\python.exe ...` on Windows, `./.venv/bin/python ...` on POSIX.
- **Logging:** the language's logging facility (Python: `logging`, not `print()`). Emojis welcome: ℹ️ ⚠️ ❌ ✅
- **Naming:** snake_case files/functions (Python), PascalCase classes, UPPER_CASE constants. **Imports:** stdlib → third-party → local.
- **Versioning:** follow the file's existing style — `==` where it pins, `>=` where it lower-bounds. Don't change the policy unless asked.
- **Type hints** on all public Python functions; `Optional[T]`, never bare `None` returns.
- **No hardcoded paths or credentials.**
- Implement only what was asked. No nice-to-haves.
- Three similar lines beats a premature abstraction — add a helper on the third caller, not the second; don't wrap framework scaffolds on day one.

## Workflow defaults

### Commit messages — no AI attribution

Never add an AI/Anthropic/OpenAI attribution trailer of any kind — a `Co-Authored-By` line naming Claude or Codex included (the user explicitly rejected this). Conventional `type: subject` line + bullet body only.

### Git discipline

Never auto-commit or push, and never stage files, without being asked — prepare a ready-to-copy commit message; the user runs it. Conventional prefixes (`feat:` `fix:` `refactor:` `docs:` `chore:` `test:` `perf:`). Multi-line body: first line ≤72 chars, blank line, then bullets explaining *why* not *what*.

### Branch & PR pipeline

`main` is always shippable. One issue → one branch → one PR → merge → branch deleted, issue closed. Branch naming: `<type>/<issue-N>-<short-slug>` — e.g. `fix/28-terminal-reconnect`, `feat/30-osc-title`; type matches the commit prefix.

**Lifecycle:** branch off latest `main` → first push opens the PR as **draft** with the issue's acceptance checklist → promote to ready when checks pass → squash-merge + auto-delete branch → `git checkout main && git pull && git branch -d <branch>`, `git fetch --prune`, confirm the issue auto-closed. (Some sister projects use a local-merge flow — follow the project's own pipeline where it differs.)

**Hard rules:** never commit to `main` directly — the one sanctioned exception is the **`/quick` skill** (explicit invocation is the authorization; its SKILL.md owns the size caps, mandatory verification, and escalate-to-issue rule); never force-push a branch someone else or CI might have pulled; never stack a second feature branch on an unmerged first; one feature/fix per branch — an unrelated mid-branch bug gets its own issue and branch. **Never stack hotfixes on hotfixes** — if a fix exposes a new bug, revert before adding a third change; if three same-day PRs interact badly, roll back to last known-good and re-introduce one at a time.

**PR body:** single-commit PR → `Summary` + `Test plan` checklist + `Closes #N`. Multi-commit → per-commit table (`SHA | What | Why`) + `Closed in this PR` + `Still open`. A **cumulative branch** is the exception, allowed only for rapid verified-per-commit rounds — document the policy in the PR body and default back to one-issue-one-branch when the round closes.

**Concurrent same-repo work:** first come, first owns `main` — later sessions build in an isolated `git worktree` (`<repo>-wt-<N>`, venv junctioned) on their own branch. The `issue-*` skills automate this via `fleet-config`'s `skills/_lib/worktree_claim.py`; mechanics + the junction-teardown footgun are in that repo's `docs/skills.md` ("Concurrent same-repo work").

### Planning & documentation

**Plans, roadmaps, proposed features live as GitHub issues**, never as files in the tree. One issue per topic, self-contained enough to hand off cold to a fresh LLM/human with zero session context. The issue + closing PR + `git log` *are* the changelog — no dated `docs/YYYY-MM-DD-*.md` retrospectives.

- **One canonical issue per decision-bearing topic** — reproduce durable content, don't depend on links; other repos get one-line pointer issues.
- **Decision log:** dated distilled bullets inside long-lived issues recording why the plan turned.
- **Supersede explicitly:** comment on the old issue linking the new, then close it — never silently diverge.
- **`gh issue create` defaults:** always `--assignee @me` + at least one type label (`bug`, `enhancement`, `refactor`, `docs`, `chore`, `test`, `perf`; `meta` for cumulative/rollback context). Create the label first if missing.
- **Issue body format** is owned by the `/issue-add` skill (its step 6 is the one canonical template — title style, section list, `file:line` grounding). Invoke it rather than improvising a section list.
- **Decompose:** can't be one PR → "Step N/M" sub-issues, each independently shippable; no "phase 1 of 4" PRs.
- **Cross-repo:** a shared-pattern bug gets the same issue in each affected repo, cross-linked by URL.
- **Closing:** direct-commit closes paste the SHA in a comment; not-planned closes explain the disproof — no zombie issues.
- **On rollback:** file a `meta` issue capturing what was attempted, what worked/didn't, a checkbox list of items still open, and the rollback + base-of-truth SHAs.

**`docs/` is for durable reference** a future reader will re-open (design records, architecture overviews, integration guides, shared playbooks). Topic filenames, never dates. Never plans/TODOs (→ issues) or dated changelogs.

**Feature work:** update `README.md` if usage, config, or output changed; add `docs/<topic>.md` only for a durable concept. One-line fixes: just commit. **Rotation/expiration dates go in README, not memory** — certs, tokens, deprecations get a calendar-anchored README line.

### Markdown that will be rendered — no hard wraps

Markdown headed for a renderer (GitHub issue/PR bodies, comments, Notion via MCP) must **not** hard-wrap paragraphs at 70/80 cols — paragraphs are single long lines; newlines only between paragraphs, between list items, and inside code fences. Does **not** apply to: source code, plain repo `.md` read as source, commit messages (wrap at 72), terminal-only output.

### Issue workflow skills

`/issue-add`, `/issue-start`, `/issue-finish` automate the GitHub-issue workflow in every sister project — each skill's own always-on `description:` is its spec, not repeated here. They ship from one `fleet-config/skills` source junctioned into each agent's auto-scanned skills dir (`~/.claude/skills` Claude; `~/.agents/skills` Codex + Pi; `~/.copilot/skills` Copilot) — same `SKILL.md` format everywhere, no translation. Antigravity has no user-skills dir (plugin-only): documented non-goal (#160).

Two rules that live only here: `/issue-start` takes its mode from the type label — `bug`/`chore`/`documentation` → build straight away, `enhancement` → plan gate, override `now` / `plan`; `/issue-finish` writes no dated changelog files. All three stay generic and read each project's CLAUDE.md for the gate command, ports, and tray procedure.

### Spawning sub-agents — cap concurrent Opus at 3 *(Claude Code only — skip on other agents)*

Keep at most **3 background Opus sub-agents in flight** (sliding window: dispatch up to 3, refill as each returns). **Sonnet sub-agents are exempt** — fan out freely. Works around Anthropic's Opus-specific server-side burst limiter on the 4th–5th+ concurrent bootstrap (anthropics/claude-code#53922, https://code.claude.com/docs/en/errors). It is **not** `CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY` (that bounds parallel tool calls in one session, not sub-agents) — the only place to cap sub-agent count is the orchestrating skill's dispatch logic. Tier vocabulary and per-host model mapping: `fleet-config/docs/model-tiers.md` (single source — don't restate a tier table).

**A sub-agent does not self-resume when its own background task finishes** — only the top-level session gets that wake-up, so one that backgrounds a step and ends its turn just stops (`project-scaffolding#124`). Brief any sub-agent running a long background step up front: it must poll (`BashOutput`/`Monitor`) to completion *within its own turn* before ending.

**A headless top-level `claude -p` session has no wake-up mechanism at all** — the CLI exits on the clean turn-end reporting `exit_code: 0`, false success over a skill that never ran (`fleet-config#314`). Every scheduled fleet skill runs this way: its own `run-weekly.bat` calling `claude -p "/<skill>" ... --permission-mode bypassPermissions`, no human attending. Any command in a skill meant for unattended/scheduled execution must run synchronously (foreground) or poll to completion in the same turn; never fire-and-forget and end the turn expecting to be resumed.

### Project hygiene

- Restart the minimum. Multi-process projects document a one-line restart matrix in README (touched X → restart Y); restarting more loses warm state and breaks siblings. A scope guard, not an opt-out from the repo-declared safe restart.
- Pinned known-good worktree for risky/architectural work — a parallel checkout at the last known-good commit for live A/B; don't touch it until the risky work re-stabilizes.

## Project fleet

### `project-scaffolding` is the canonical master

`E:\automation\project-scaffolding` (`ferraroroberto/project-scaffolding`) is the scaffold repo whose `CLAUDE.md` sister projects derive theirs from; its `docs/playwright-ui-testing.md` is the shared e2e-testing reference. It follows the branch/PR pipeline and issue defaults above exactly.

### Propagate generalizable conventions up to scaffolding

Sister-project work producing a *generalizable convention* (testing pattern, CLAUDE.md rule, workflow) routes up to `project-scaffolding` — ad-hoc per-project divergence was explicitly rejected.

- Per-project *instances* (real script names, paths) stay in the project's own CLAUDE.md; the reusable *concept* goes to scaffolding.
- Check for an existing `project-scaffolding` issue first; otherwise file one (master's template + label + `--assignee @me`).
- If asked, draft the master change on a proper branch via its draft-PR pipeline, one issue per branch.

### Every repo carries a `.fleet.toml`

Each fleet repo declares its architecture-map card in a root `.fleet.toml` (`layer` ∈ governance | enabling | working-web | working-pipe, `icon`, `description`; optional `display_name` / `port` / `chips` / `tag`). `/system-map` aggregates these into the map (repo relationships as text: `fleet-config/architecture/system-map.mmd` — open it when you need the fleet picture); `architecture/fleet.residual.json` is only the fallback for non-adopters plus non-repo structure, so a new repo appears with no central edit. (Schema: `fleet-config/architecture/README.md`; decision `ferraroroberto/fleet-config#148`.)

**Anti-staleness contract:** update `.fleet.toml` in the **same PR** as any material change (port, layer, role, description, exposed services). `fleet-config`'s drift test fails loud if an adopted repo loses its file; `project-scaffolding` ships one so every clone inherits the convention.

## Local infrastructure

### `local-llm-hub` local LLM hub

`E:\automation\local-llm-hub` runs a FastAPI hub on `127.0.0.1:8000` exposing Anthropic-shape `POST /v1/messages` and OpenAI-shape `POST /v1/chat/completions`, routed by `model` name: Claude/Gemini ids reach the local CLI on the user's subscription, open-weight ids reach llama-server backends on their own ports. **Live model ids and ports: that repo's README + `docs/model-comparison.md`, or `GET /v1/models`** — a latest-only policy replaces entries when newer models ship, so never trust a copied list.

Whisper-server at `127.0.0.1:8090` (`ggml-large-v3-turbo.bin`, OpenAI-compatible `/v1/audio/transcriptions`). The hub also proxies audio on `:8000` (`/v1/audio/transcriptions` + `/v1/audio/translations`) so requests land in the observability ring; direct `:8090` POSTs are lower-overhead but invisible to the admin UI. Port 8090 is mutex-shared with `automation/audio/transcribe_voice`.

SDK and `curl` examples, plus the shape limitations (image/document blocks, dropped `image_url` parts and thinking blocks, no `/v1/messages` streaming, open-weight tool-use): `fleet-config`'s `docs/local-llm-hub-usage.md` — read it before building against the hub.

### Don't duplicate hub functionality in downstream apps

Route downstream LLM access — Anthropic- or OpenAI-shape, subscription or open-weight — through the hub via standard SDKs; never re-implement an inline agent-CLI subprocess wrapper (`claude -p`, `codex exec`, `gemini -p`) (the hub owns subprocess management, prompt assembly, multi-turn flattening, host-routing, and observability).

- LLM call → `Anthropic(api_key="local-dummy", base_url="http://127.0.0.1:8000")` or `OpenAI(api_key="local-dummy", base_url="http://127.0.0.1:8000/v1")`.
- Audio → POST directly to `http://127.0.0.1:8090/v1/audio/transcriptions`.
- Hub lacks a feature → write a plan for `local-llm-hub` to add it; don't bypass the hub.

### Prefer scripts over session-injected MCP connectors for automation

For unattended/automation workflows, prefer a thin Python script (standard SDK or REST) over a session-injected MCP connector — a fleet audit (`ferraroroberto/fleet-config#128`, 945 transcripts) found 97% of injected tool surface unused, and every enabled connector is a fleet-wide, every-session context cost.

- New automation → script via SDK/REST first; connectors only for genuinely interactive, exploratory, one-off use.
- Keep the default connector set minimal; toggle one on per session that needs it.
- A connector that becomes a recurring automation dependency is the signal to scriptify it and disable the connector by default.

## Recurring gotchas

Each entry is the rule; its diagnosis, examples, commands and incident history live under the same title in `fleet-config`'s `docs/recurring-gotchas.md` (`E:/automation/fleet-config/docs/recurring-gotchas.md`) — read that section before working in the area.

- **Git Bash strips backslashes in `settings.json` commands** *(Claude Code only — skip on other agents)* — statusLine/hook command strings run through Git Bash, so Windows paths in them use forward slashes (`C:/Windows/...`); Codex invokes the hook modules directly.
- **A trailing backslash before a closing double-quote escapes it, not closes it** — never end a double-quoted Windows path argument with a bare trailing backslash; drop it or use a forward slash (`fleet-config#800`).
- **Windows PowerShell in spawned commands (any agent)** — use the absolute `C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe`, never the 0-byte `pwsh` stub; never native `cmd.exe /c` from Git Bash (`cmd.exe //c` if Bash must call cmd, fleet-config#385); PowerShell scripts read the agent's stdin JSON whole and as UTF-8 on both legs, never `$input` or bare `[Console]::In` (fleet-config#912); cast `[math]::Round(x)` to `[string]` before concatenating.
- **PYTHONPATH for out-of-tree Python scripts** — a script outside the project that imports project packages needs `PYTHONPATH` set to the repo root; better, keep it in-tree and run `python -m <module.path>` from the repo root.
- **Windows Python: UTF-8 stdout under capture** — set `PYTHONUTF8=1` under capture (durable fix: `sys.stdout.reconfigure(encoding="utf-8")` and stderr at entry points); inversely, in a `PYTHONUTF8` process pin `encoding="oem", errors="replace"` when decoding native Windows console tools (`app-launcher#743`); a helper that returns `None` on failure must log the failure.
- **Browser automation must not look like a bot** — automation against a third-party site presents as a real human Chrome session (stealth args, `navigator.webdriver` undefined, real Chrome, persistent profile, 1280×900, `chromium_sandbox=True`), with launch kwargs + init-script in one per-project helper every module imports; a captcha or "unusual activity" report means suspect a stealth regression first. Narrow first-party-screenshot exception and the `FLEET_BROWSER_HOST` browser-URL rule: in the doc.
- **Shared Chrome profiles: serialize access, never kill a live holder** — wait with exponential backoff (60→120→240→480 s) in one shared helper and raise a precise error only after the schedule; on Windows the lock is a live-process kernel object, so deleting `SingletonLock`/`Cookie`/`Socket` files does nothing.
- **GitHub's `Closes #N` keyword matches on substrings, not standalone clauses** — a PR that advances a multi-PR issue without finishing it says "Part of #N" / "Progresses #N"; reserve `Closes #N` / `Fixes #N` for the one PR that finishes it (`app-launcher#355`).
- **Three clocks — normalise to UTC before correlating GitHub state with local logs** — `gh` timestamps are UTC, this host's offset changes with the season (read it, never hardcode it), and an app-launcher job log's `[h:mm:ss]` prefix is elapsed since run start; convert first and state the conversion, and reconstruct what a tool could observe at time T instead of re-running it now (`fleet-config#633`).
- **Subprocess spawns must suppress the console window (Windows)** — every `subprocess` spawn of an external executable passes `creationflags=subprocess.CREATE_NO_WINDOW` on Windows unless the window is meant to be visible; never combine it with `DETACHED_PROCESS`; repos with 3+ call sites use one `NO_WINDOW` helper (`fleet-config#399`).
- **A fleet-wide `git status` sweep strands 0-byte index locks unless it sets `GIT_OPTIONAL_LOCKS=0`** — a stale lock is invisible to every read (`status`, `fetch`, `rev-list`, an up-to-date `pull --ff-only` all exit 0 with correct output) while `add`/`commit`/`pull`/`stash` refuse, so a failed `merge --ff-only` leaves the next command reading the old HEAD: 15 days hidden across 9 repos (`fleet-config#667`), 23 repos at one timestamp (`fleet-config#939`). Every `git` spawn goes through the tier's `run_git`, and any repo reading git state fleet-wide needs that static gate over its own tree. Report a lock, never delete one — it is another process's file.
- **Windows ephemeral port exhaustion takes down the whole fleet at once** — all local web apps dead 1–4 min at once, then self-healing, is a drained dynamic port range, not one app's diff (`fleet-config#440`): run the doc's one-minute diagnosis, then fix the leaking burst and pool connections first. `TcpTimedWaitDelay` and any dynamic-port-range change are machine-level and Roberto's call, never applied unattended by an agent — and never narrow the range downward.
