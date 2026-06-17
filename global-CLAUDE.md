# Global instructions

User-level memory loaded into every coding-agent session on this machine. This one file is the single source of truth for **both** agents: Claude Code reads it as `~/.claude/CLAUDE.md`, Codex reads it as `~/.codex/AGENTS.md` (same file, linked). Guidance applies to whichever agent is reading it; the few sections that are genuinely specific to one agent are marked *(… only — skip on other agents)*.

> **What lives here vs in a project.** This file owns everything **universal** — true for *every* repo on the machine, including a one-off with no UI, no tray, no launcher. **Shape-specific** guidance (Streamlit, tray/daemon, end-to-end UI testing, GitHub-Actions CI, the per-project restart recipe) lives in `project-scaffolding`'s `CLAUDE.md` and is inherited only by projects of that shape. The test for any directive: *"would this still apply to a bare repo with no app?"* Yes → here. No → the scaffold. Nothing belongs in both. (Boundary recorded in `ferraroroberto/project-scaffolding#68`; the `/context-audit` skill enforces it weekly.)

## Working method

How to approach any task, regardless of project shape.

### Plan mode is the default

Every non-trivial request starts in plan mode. Non-trivial = anything beyond a one-line fix, a typo, or a question answerable without touching code.

In plan mode:
- Do NOT edit files, run destructive commands, or commit anything.
- Investigate as needed (read files, search, run read-only commands).
- Resolve ambiguity through questions *before* proposing a plan.
- Present the plan only when confident it reflects what the user actually wants.
- Stay in plan mode across rejections — if the user pushes back, revise and re-present; don't bail out to execution.

Recommended in a project's `.claude/settings.json`: `{ "permissions": { "defaultMode": "plan" } }`. Exit plan mode only after explicit approval; approval transitions straight to execution in the same turn.

### Ask before assuming

Ask whenever a decision would be expensive to undo or is genuinely ambiguous. One sharp question beats three filler ones. Use multi-choice (2–4 options) when the choice space is bounded — much faster to answer than prose. If multiple reasonable approaches exist, present them as options with tradeoffs; don't pick silently.

Always ask before assuming: file/module location for new code; data shape or schema; data source (upload, local file, DB via secrets); error and empty-state handling; whether to add tests, and at what level.

Don't ask about things determinable by reading the code, things already specified, or process meta-questions like "is the plan ready?" — that's what plan approval is for.

### Before editing

- Re-read any file before modifying it. Don't trust memory across long sessions. For files >500 LOC, read in chunks.
- When renaming a symbol, search separately for: direct calls, type references, string literals, dynamic imports, re-exports, and tests.
- Reproduce before fixing: for any non-trivial bug, write a repro (script, failing test, or documented sequence) before the fix. Forces real understanding; stops "I think this fixes it" → ship → rollback.
- Re-verify the issue's premise: spend a few minutes confirming the symptom still reproduces and the code matches the issue before starting. Stale briefs waste PRs.
- `git log -- <file>` the area first. Prior attempts at the same fix are the cheapest source of truth.

### While fixing

- Empirical proof for retry/timeout/backoff logic. Loops that react to return values encode assumptions about API semantics — verify the assumption with a 10-line probe before shipping.
- Distinct error messages for distinct conditions. "Down" and "in flight past timeout" need different messages — same-message-different-cause is how users stack orphan state.
- Don't bundle independently-revertable bugs in one PR. If bug-A's commit can revert without breaking bug-B's fix, ship two PRs.
- Leave log breadcrumbs after a hard bug. The next occurrence should be diagnosable from logs, not screenshots. Add the info-level log in the same commit as the fix.
- Test-plan checkboxes are observed, not aspirational. `[x]` means "I ran this and saw it pass." If a box can't be checked now, the PR isn't ready now.

### Execution: scope up front, then carry it through

- Front-load the questions. Settle scope, ambiguity, and hard-to-undo decisions *before* starting — that is the main control point.
- Once scope is agreed, execute end-to-end to a verified, shippable state. Don't stop for per-phase approval; "large" is not "stop".
- Checkpoint on risk, not size. Pause mid-task only for what the agreed scope didn't cover: a real ambiguity, an unforeseen decision, or a finding that contradicts the plan.

### Chaining connected work

- Issues are split for tracking but are often sequential. After finishing and verifying a unit, check the related open issues.
- If the next step is a natural continuation, state it and proceed — new branch off freshly-merged `main`. Pause for approval only when it's risky, ambiguous, or materially bigger than discussed.
- One branch per coherent unit. Keep commits and branches separable so any piece reviews and reverts on its own.

### Verify before declaring done

Verify every unit before calling it done, using the project's actual tooling (byte-compile, lint, tests). If no checker exists for a project, say so explicitly — don't claim "tests pass" when there are no tests. Report failures faithfully with the output; never report done on a step that was skipped.

### Senior-dev check

Before finishing, ask: "What would a senior, perfectionist dev reject in review?" If the answer points at duplicated state, inconsistent patterns, or broken architecture *within the file you're already editing*, fix it. Don't expand scope to unrelated files.

## Conventions

Universal code & config conventions (project-specific layout and stack live in the project's own README/CLAUDE.md).

- **Read the README first.** Don't assume `/app/`, `/src/`, `launch_app.bat`, or any specific path exists — the project's layout is documented in its own `README.md`.
- **Config & secrets:** project config in `config.json` or similar; secrets always in `.env`, never committed. The canonical env filename is `.env` (`.venv` is the venv directory, not the env file).
- **Virtual environment:** use the existing `.venv`. Never create `venv`. Never activate — invoke via `& .\.venv\Scripts\python.exe ...` on Windows, `./.venv/bin/python ...` on POSIX.
- **Logging:** use the language's logging facility (Python: `logging`, not `print()`). Emojis welcome in log messages: ℹ️ ⚠️ ❌ ✅
- **Naming:** snake_case for files/functions (Python), PascalCase for classes, UPPER_CASE for constants. **Imports:** stdlib → third-party → local.
- **Versioning policy:** follow the existing style in `requirements.txt` / `package.json` — keep `==` where the file pins, `>=` where it uses lower bounds. Don't change the policy unless asked.
- **Type hints** on all public Python functions. Use `Optional[T]`, never bare `None` returns.
- **No hardcoded paths or credentials.**
- Implement only what was asked. No nice-to-haves.
- Three similar lines beats a premature abstraction. Add a helper on the third caller, not the second. Don't wrap framework scaffolds on day one.

## Workflow defaults

### Commit messages — no AI attribution

Do not add `Co-Authored-By: Claude …`, `Co-Authored-By: Codex …`, or any other AI/Anthropic/OpenAI attribution trailer to git commit messages. The user explicitly rejected this. Use only the conventional `type: subject` line and bullet body.

### Git discipline

Never auto-commit or push, and never stage files, without being asked. When a task is done, prepare a ready-to-copy commit message; the user runs it. Use conventional prefixes (`feat:` `fix:` `refactor:` `docs:` `chore:` `test:` `perf:`) — makes `git log --oneline` scannable and PR-body commit tables possible. Multi-line body: first line ≤72 chars, blank line, then bullets explaining *why* not *what*.

### Branch & PR pipeline

`main` is always shippable. One issue → one branch → one PR → merge → branch deleted, issue closed.

Branch naming: `<type>/<issue-N>-<short-slug>` — e.g. `fix/28-terminal-reconnect`, `feat/30-osc-title`. Type matches the commit prefix.

**Lifecycle:** open the branch off latest `main` → first push opens the PR as **draft** with the issue's acceptance checklist in the body → commits land freely while in draft → promote to ready when acceptance checks pass → squash-merge by default + auto-delete branch → `git checkout main && git pull && git branch -d <branch>`, `git fetch --prune`, confirm the issue auto-closed. (Some sister projects use a local-merge flow instead of squash-merge — follow the project's own pipeline where it differs.)

**Hard rules:** never commit to `main` directly; never force-push a branch someone else or a CI run might have pulled; never stack a second feature branch on an unmerged first (rebase or wait); one feature/fix per branch — if mid-branch you find an unrelated bug, file an issue and start a new branch, don't smuggle it in. **Never stack hotfixes on hotfixes** — if a fix exposes a new bug, revert before adding a third change; if three same-day PRs interact badly, roll back to last known-good and re-introduce one at a time.

**PR body discipline:** single-commit PR → `Summary` + `Test plan` checklist + `Closes #N`. Multi-commit / cumulative PR → per-commit table (`SHA | What | Why`) + `Closed in this PR` + `Still open`. A **cumulative branch** (branch stays alive across rounds) is the exception, not the default — allowed only for rapid iterative rounds where each commit is verified end-to-end; document the policy in the PR body and default back to one-issue-one-branch when the round closes.

**Concurrent same-repo work:** two sessions sharing one checkout collide (a `git checkout` in one rewrites the other's tree mid-build). The convention is **first come, first owns `main`**: the first session to start work on a repo claims its primary checkout; every session after builds in an isolated `git worktree` (`<repo>-wt-<N>`, venv junctioned) on its own branch. The `issue-*` skills automate this via `fleet-config`'s `skills/_lib/worktree_claim.py` (claim on `/issue-start`, release/teardown on `/issue-finish`); mechanics + the junction-teardown footgun are documented in that repo's README ("Concurrent same-repo work").

### Planning & documentation

**Plans, roadmaps, proposed features live as GitHub issues** on the relevant repo, never as files in the tree. One issue per topic, self-contained enough to hand off cold (the cold-handoff test: executable by a fresh LLM/human with zero session context). The issue + the PR that closes it + `git log` *are* the changelog — do not also drop a dated `docs/YYYY-MM-DD-*.md` retrospective per merge.

- **One canonical issue per decision-bearing topic.** Reproduce durable content rather than depending on links. Other repos get one-line *pointer* issues to the canonical one.
- **Decision log:** inside long-lived issues, keep dated distilled bullets recording why the plan turned (not raw transcripts).
- **Supersede explicitly:** when a new decision overrides an old issue, comment on the old one linking the new, then close it — never silently diverge.

**`gh issue create` defaults:** always pass `--assignee @me` and at least one type label (`bug`, `enhancement`, `refactor`, `docs`, `chore`, `test`, `perf` — mirroring commit prefixes; `meta` for cumulative/rollback context). Create the label first if missing. No untagged, unassigned issues.

**Issue template (non-trivial work):** **Why** (or **Symptom** + **Root cause** for bugs) · **Scope** (what's in) · **Out of scope** (explicit non-goals) · **How to verify** (concrete acceptance steps) · **Constraints worth knowing** (env, gotchas, file refs not obvious from code).

**Decompose:** if it can't be one PR, split into "Step N/M" sub-issues, each independently shippable. Don't ship "phase 1 of 4" PRs. **Cross-repo:** if a bug lives in a shared script/pattern, file the same issue in each affected sister repo and cross-link by URL. **Closing:** `Closes #N` in the PR body for auto-close; for issues closed by direct commit, paste the SHA in a closing comment; close not-planned with a comment explaining the empirical disproof — no zombie issues. **On rollback:** file a `meta`-labelled issue capturing what was attempted, what worked/didn't, why, plus a checkbox list of items "conceptually still open"; reference rollback SHA + base-of-truth SHA explicitly.

**What `docs/` is for:** durable *reference* material a future reader will actually re-open — design records, architecture overviews, integration guides, shared playbooks (e.g. `project-scaffolding/docs/playwright-ui-testing.md`, `local-llm-hub/docs/model-comparison.md`). Filenames describe the topic, not a date. If you wouldn't re-read it next quarter, it doesn't belong here. **Never** put plans/roadmaps/TODOs (→ issues) or dated per-PR changelog files in `docs/`.

**For feature work:** update `README.md` if usage, config, or output changed; add a topic-named `docs/<topic>.md` only when the change introduces a durable concept worth re-reading. For one-line fixes and typos: just commit. **Rotation / expiration dates go in README, not memory** — certs, tokens, API deprecations, vendor deadlines get a calendar-anchored line in README (memory decays; READMEs get read).

### Markdown that will be rendered — no hard wraps

When writing markdown for a renderer (GitHub issue/PR bodies, GitHub comments, Notion pages via MCP, anything that flows through a markdown renderer), do **not** hard-wrap paragraphs at 70/80 cols. Paragraphs must be single long lines. Only insert newlines between paragraphs (blank line), between list items, and inside fenced code blocks.

**Why:** the user reads on a vertical PTI terminal — hard-wrapped paragraphs render as awkward forced line breaks that fight the terminal's own wrapping, producing very short ragged lines.

Does **not** apply to: source code, plain `.md` files viewed as source in a repo, commit messages (wrap at 72), terminal-only output.

### Issue workflow skills

Three global skills automate the user's standard GitHub-issue workflow across all sister projects (in `~/.claude/skills/` for Claude Code, reaching Codex via the `~/.agents/skills/` junction):

- **`/issue-add`** — paste a rough idea/transcript; researches the codebase and files one well-formed self-contained issue (senior-dev style), labelled from the repo's existing label set, self-assigned. Creates directly, no checkpoint.
- **`/issue-start`** — pick issue (number, or list-and-pick for `next`/no-arg), sync `main`, cut branch, load context. Mode chosen from the issue's type label: `bug`/`chore`/`documentation` → **fast mode** (build straight away); `enhancement` → **plan mode** (plan-approval gate). Override with `now` (force fast) or `plan` (force plan).
- **`/issue-finish`** — confirm acceptance, update README if it changed, verification gate, push, PR with `Closes #N`, wait for CI (skipped when the change is provably CI-unrelated), auto-merge (merge commit + delete branch), land on main, safe tray restart. Does **not** write dated changelog files into `docs/`.

Both stay generic and read each project's CLAUDE.md for the gate command, ports, and tray procedure.

### Spawning sub-agents — cap concurrent Opus at 3 *(Claude Code only — skip on other agents)*

When a skill or session fans out **background sub-agents that run on Opus**, keep at most **3 in flight** at once (a sliding window): dispatch up to 3, and each time one returns, dispatch the next pending one until the queue drains. Fewer than 3 pending → just spawn that many (1 item = 1 agent). Every fleet fan-out skill — `audit-fleet`, `cleanup-fleet`, `issue-batch` — references this cap rather than re-hardcoding the number, so the limit lives in exactly one place.

- **Sonnet sub-agents are exempt** — they may fan out freely. In a mixed run only the Opus agents count against the window of 3 (Sonnet agents run alongside, uncounted). Sonnet is the "smaller model" half of the documented mitigation.
- **Why:** Anthropic's server-side burst limiter rejects the 4th–5th+ simultaneous Opus sub-agent bootstrap with `Server is temporarily limiting requests (not your usage limit) · Rate limited`. The empirical ceiling is 3–4 concurrent (anthropics/claude-code#53922 — the first 3–4 succeed, the rest fail); there is no officially published number, so 3 is the conservative floor. The official 429 guidance is to "avoid running many parallel subagents" or switch to a smaller model for high-volume runs (https://code.claude.com/docs/en/errors). Two unattended runs lost most of their work to this — the 2026-06-03 `/audit-fleet` (only 3 of 27 repos completed) and a later `/cleanup-fleet`.
- **Not** `CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY` — that env var bounds parallel *tool calls within a single session*, not the number of sub-agents in flight. The only place to cap sub-agent count is the orchestrating skill's dispatch logic (a bounded window), which is what this rule mandates.

### Project hygiene

- Restart the minimum. If a project runs multiple long-lived processes, document a one-line restart matrix in its README (touched X → restart Y). Restarting more than necessary loses warm state and breaks sister processes.
- Pinned known-good worktree for risky work. For architectural changes, keep a parallel checkout pinned at the last known-good commit for live A/B comparison; don't touch the pinned tree until the risky work re-stabilizes on main.

## Project fleet

### `project-scaffolding` is the canonical master

`E:\automation\project-scaffolding` (GitHub `ferraroroberto/project-scaffolding`) is the canonical scaffold repo. Its `CLAUDE.md` is the source of truth that sister projects (`app-launcher`, `photo-ocr`, `voice-transcriber`, etc.) derive their own from. `docs/playwright-ui-testing.md` there is the shared didactic e2e-testing reference.

Its own pipeline differs from the local-merge flow some sister projects use: branch off `main` → push → **draft PR** → promote → **squash-merge + delete branch**. Branch name `<type>/<issue-N>-<slug>`. Issues require `--assignee @me` + a type label.

### Propagate generalizable conventions up to scaffolding

When work in a sister project produces a *generalizable convention* (a testing pattern, a CLAUDE.md rule, a workflow), route the knowledge up to `project-scaffolding` so every project inherits it — don't leave it as a one-off local change.

**Why:** the user said this explicitly and called it "very important" — "I don't want to diverge, I don't want to create ad hoc stuff." Per-project CLAUDE.md edits that encode a reusable idea cause drift across the project fleet.

**How to apply:**
- Per-project *instances* (real script names, actual test file paths) belong in the project's own CLAUDE.md — that is not divergence.
- The reusable *concept* behind them belongs in `project-scaffolding`.
- Check for an existing `project-scaffolding` issue first; comment/pin if one covers the area, otherwise file a new issue (master's issue template + label + `--assignee @me`).
- If the user asks, also draft the master CLAUDE.md/doc change — on a proper branch via `project-scaffolding`'s draft-PR pipeline, one issue per branch.

### Every repo carries a `.fleet.toml`

Every fleet repo declares its own architecture-map card in a root **`.fleet.toml`** (`layer` ∈ governance | enabling | working-web | working-pipe, `icon`, `description`; optional `display_name` / `port` / `chips` / `tag`). The `/system-map` generator in `fleet-config` *aggregates* these per-repo declarations into the map — the central `architecture/fleet.residual.json` is only the fallback for repos that haven't adopted one yet, plus the non-repo structure. A new repo thus appears on the map automatically, with no central edit. (Schema + reasoning: `fleet-config/architecture/README.md`; decision record `ferraroroberto/fleet-config#148`.)

**Keep it current — this is the anti-staleness contract:** update `.fleet.toml` in the **same PR** as any material change to the repo (port, layer, role, one-line description, exposed services). It lives in the repo precisely so it's right there when you change the thing it describes. `fleet-config`'s drift test fails loud if an adopted repo loses its `.fleet.toml`, so metadata can't silently rot. `project-scaffolding` ships a `.fleet.toml` so every cloned repo inherits the convention by default.

## Local infrastructure

### `claude-local-calls` local LLM hub

`E:\automation\claude-local-calls` is a sibling repo running a FastAPI hub on `127.0.0.1:8000` that exposes both Anthropic-shape `POST /v1/messages` and OpenAI-shape `POST /v1/chat/completions`, routed by `model` name:

- `claude-haiku-4-5`, `claude-sonnet-4-6`, `claude-opus-4-7` → forwarded to the local `claude -p` CLI using the user's Claude Code subscription
- `qwen3.5-9b` → llama-server on `:8081`
- `glm-4.5-air` → llama-server on `:8082` (MoE, CPU offload)
- `gemma4-e4b-it` → llama-server on `:8086` (8 B, edge tier)
- `gemma4-26b-a4b-it` → llama-server on `:8087` (25 B / 3.8 B-active MoE, quality tier)

Plus a whisper-server at `127.0.0.1:8090` (`ggml-large-v3-turbo.bin`, OpenAI-compatible `/v1/audio/transcriptions`). The hub **also proxies audio** on `127.0.0.1:8000` (`/v1/audio/transcriptions` + `/v1/audio/translations`, see `src/server.py`) so requests land in the observability ring; clients can still POST directly to `:8090` for lower overhead, but those direct hits are invisible to the admin UI. Port 8090 is mutex-shared with `automation/audio/transcribe_voice`.

**Calling it:**

```python
from anthropic import Anthropic
client = Anthropic(api_key="local-dummy", base_url="http://127.0.0.1:8000")
client.messages.create(model="claude-haiku-4-5", ...)
```

```bash
curl -F file=@clip.wav http://127.0.0.1:8090/v1/audio/transcriptions
```

**Limitations to verify before using** (as of late April 2026): image / document / extended-thinking content blocks are dropped at the shape boundary; no streaming; all local backends are text-only (no vision-capable local model); Anthropic-shape tool-use to qwen/glm is not implemented (OpenAI-shape works via llama-server `--jinja`). Re-read the repo's README and `docs/model-comparison.md` before relying on a specific model id — the latest-only policy means model entries get replaced when newer ones ship.

### Don't duplicate hub functionality in downstream apps

When a downstream app needs Claude/local-LLM access, route through the hub via standard SDKs — **do not** re-implement `claude -p` subprocess wrappers inline.

**Why:** the hub already does subprocess management, prompt assembly, multi-turn flattening, host-routing. Duplicating that in a downstream app means two implementations to keep in sync and bypasses the hub's central LAN-access / observability story. The user called an inline `claude -p` wrapper "overengineering two times the same stuff" and rejected it.

**How to apply:**
- New feature needs an LLM call → default to `Anthropic(api_key="local-dummy", base_url="http://127.0.0.1:8000")` (Anthropic shape) or `OpenAI(api_key="local-dummy", base_url="http://127.0.0.1:8000/v1")` (OpenAI shape).
- Audio → POST directly to `http://127.0.0.1:8090/v1/audio/transcriptions`.
- Hub lacks a feature you need → write a plan for `claude-local-calls` to add it; don't bypass the hub from the downstream app.

### Prefer scripts over session-injected MCP connectors for automation

For unattended/automation workflows, prefer a **thin Python script** (standard SDK or REST) over a **session-injected MCP connector**. Reserve connectors for genuinely interactive, exploratory, one-off use. Keep the **default** session connector set minimal — every enabled connector is a fleet-wide, every-session context cost regardless of whether a given session uses it.

**Why:** a fleet-wide MCP audit (`ferraroroberto/fleet-config#128`) measured real connector usage across 945 session transcripts: 97% of the injected tool surface was unused, and five connectors (Google Drive, Spotify, Supabase, Uber Eats, Zoom — 53 schemas) were invoked zero times ever. The pattern already proven in the fleet: Slack automation runs through `fleet-config/hooks/slack_notify.py`, not the Slack connector; `inspiration-system/src/notion_client.py` hits the Notion REST API directly. Connectors were the convenient default, not the efficient one. (`ferraroroberto/life-os#29` is the first migration off a connector onto a REST script.)

**How to apply:**
- New automation needs an external service → reach for a script via the standard SDK/REST first; only use a connector if the work is genuinely interactive/exploratory and one-off.
- Keep the account-level default connector set minimal; toggle a connector on for the session that needs it rather than leaving it on for the whole fleet.
- When a connector becomes a recurring automation dependency, that is the signal to scriptify it and then disable the connector by default.

## Recurring gotchas

### Git Bash strips backslashes in `settings.json` commands *(Claude Code only — skip on other agents)*

Claude Code on this Windows machine executes `settings.json` commands (statusLine, hooks) through **Git Bash**, not cmd or PowerShell directly. Git Bash treats `\` as an escape character, so any Windows path in a command string must use **forward slashes** — `C:/Windows/...`, not `C:\Windows\...` — or bash strips the backslashes (`C:\Windows` → `C:Windows`) and the command silently fails. (Codex does **not** route hooks through Git Bash — its `hooks.json` command paths may safely use backslashes, which is why the two agents share the same `run-hook.ps1` shim but wire it up through different config files.)

Working statusLine / hook command form (in `settings.json`):

```
C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File C:/Users/rober/.claude/<script>.ps1
```

### Windows PowerShell in spawned commands (any agent)

- **Avoid `pwsh`** in spawned commands. The default `pwsh` on PATH is a WindowsApps execution alias (a 0-byte reparse stub) that fails when spawned non-interactively. Use the absolute Windows PowerShell 5.1 path (`C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe`) instead.
- **PowerShell scripts that read the agent's stdin JSON** (hooks on either agent) should use `[Console]::In.ReadToEnd()` — `$input` is unreliable across the shell → powershell.exe pipe.
- **Avoid arithmetic-vs-string ambiguity in PowerShell.** `[math]::Round(x) + '%'` is parsed as arithmetic and throws on `'%'`; cast first: `[string][math]::Round(x) + '%'`.

### PYTHONPATH for out-of-tree Python scripts

When invoking `& .\.venv\Scripts\python.exe <path-outside-project>` (e.g. a script in `E:\tmp\`) that imports project packages (`app.*`, `src.*`, etc.), prepend `$env:PYTHONPATH = (Get-Location).Path;` on Windows / `PYTHONPATH=$(pwd)` on POSIX.

**Why:** Python sets `sys.path[0]` to the *script's* directory, not CWD, so `cd`-ing into the project before invocation does not help. Without PYTHONPATH the run fails with `ModuleNotFoundError: No module named 'app'` (or `'src'`).

**Better when possible:** if the script *can* live inside the repo (a gitignored scratch dir is fine), use `& .\.venv\Scripts\python.exe -m <module.path>` from the project root — `-m` adds CWD to `sys.path` and no env var is needed. Only reach for `PYTHONPATH` when the script genuinely must live elsewhere.

### Windows Python: UTF-8 stdout under capture

Piped/redirected stdout (every captured run) makes Python fall back to cp1252, so any emoji/box-drawing `print()` throws `UnicodeEncodeError` and exits 1 — even though it works in the real terminal. Set `$env:PYTHONUTF8 = "1"` before invoking python under capture. Durable code fix: `sys.stdout.reconfigure(encoding="utf-8")` (and stderr) at entry points.

### Browser automation must not look like a bot

Every Playwright / automated-browser launch in any project must present as a real human Chrome session. Concretely:

- **No "Chrome is being controlled by automated test software" infobar** — strip `--enable-automation` via `ignore_default_args=["--enable-automation", "--enable-blink-features=IdleDetection"]`.
- **`navigator.webdriver` must read as `undefined`** — add init script: `Object.defineProperty(navigator, 'webdriver', {get: () => undefined});` via `add_init_script` (not just CLI flag).
- **Use real Chrome** (`channel="chrome"`), not bundled Chromium — anti-bot heuristics fingerprint Chromium fast.
- **Persistent profile** with viewport 1280×900 and `--disable-blink-features=AutomationControlled`.
- Also pass `--disable-features=Translate`, `--no-default-browser-check`, `--no-first-run`.

**Why:** past incidents on Mercadona / Casa Melier surfaced captchas the moment automation was detected. LinkedIn, X, Instagram, Threads run more aggressive anti-bot checks; getting flagged risks account warnings or lockouts.

**Single source of truth per project:** put the launch kwargs + init-script in one helper (e.g. `config/chrome_launch.py` in `reporting`, `automation/browser.py` in `grocery-shopping`). All session modules and bootstrap scripts must import from there — never re-inline launch args in a new module. If the user reports a captcha or "unusual activity" notice, suspect a stealth regression first.

### Shared Chrome profiles: serialize access, never kill a live holder

A persistent Chrome profile allows only **one live instance**. When two unattended jobs share one profile (e.g. a scrape + a reporting job on the same `chrome_user_data`), the second to launch gets Playwright's *"Opening in existing browser session"* and dies. Do **not** "self-heal" by killing the holder — it's usually a legitimately-running sibling, and killing it corrupts that run. Instead **wait** with exponential backoff (60→120→240→480 s), re-attempting the launch each cycle, and raise a precise error only if still held after the schedule (a >15-min holder is genuinely hung). On Windows the profile lock is a live-process kernel object, **not** the POSIX `SingletonLock`/`Cookie`/`Socket` files — deleting those does nothing. Put the detect-holder + wait-with-backoff wrapper in one helper every session imports (e.g. `config/chrome_profile_lock.py`); never re-inline a launch-with-retry.
