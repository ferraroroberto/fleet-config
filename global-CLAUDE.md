# Global instructions

User-level memory loaded into every Claude Code session on this machine.

## Workflow defaults

### Commit messages — no Claude/AI attribution

Do not add `Co-Authored-By: Claude ...` or any other Claude/Anthropic/AI attribution trailer to git commit messages. The user explicitly rejected this. Use only the conventional `type: subject` line and bullet body.

### Markdown that will be rendered — no hard wraps

When writing markdown for a renderer (GitHub issue/PR bodies, GitHub comments, Notion pages via MCP, anything that flows through a markdown renderer), do **not** hard-wrap paragraphs at 70/80 cols. Paragraphs must be single long lines. Only insert newlines between paragraphs (blank line), between list items, and inside fenced code blocks.

**Why:** the user reads on a vertical PTI terminal — hard-wrapped paragraphs render as awkward forced line breaks that fight the terminal's own wrapping, producing very short ragged lines.

Does **not** apply to: source code, plain `.md` files viewed as source in a repo, commit messages (wrap at 72), terminal-only output.

### Planning artifacts lifecycle

- **Future-work plan** → a GitHub *issue* (never a `docs/` file). The issue + the PR that closes it + `git log` are the changelog — do **not** also drop a dated `docs/YYYY-MM-DD-*.md` retrospective per merge. Triplicating the same information into `docs/` is busywork that ages badly.
- **What `docs/` is for:** durable *reference* material a future reader will actually re-open — design records, architecture overviews, integration guides, shared playbooks (e.g. `project-scaffolding/docs/playwright-ui-testing.md`, `local-llm-hub/docs/model-comparison.md`). Filenames should describe the topic, not a date. If you wouldn't re-read it next quarter, it doesn't belong here.
- **One canonical issue per decision-bearing topic.** Make it self-contained (reproduce durable content rather than depending on links). Other repos get one-line *pointer* issues to the canonical one.
- **Decision log:** inside long-lived issues, keep dated distilled bullets recording why the plan turned (not raw transcripts).
- **Supersede explicitly:** when a new decision overrides an old issue, comment on the old one linking the new, then close it — never silently diverge.
- **Cold-handoff test:** every planning issue must be executable by a fresh LLM/human with zero session context.

### Issue workflow skills

Three global skills in `~/.claude/skills/` automate the user's standard GitHub-issue workflow across all sister projects:

- **`/issue-add`** — paste a rough idea/transcript; researches the codebase and files one well-formed self-contained issue (senior-dev style), labelled from the repo's existing label set, self-assigned. Creates directly, no checkpoint.
- **`/issue-start`** — pick issue (number, or list-and-pick for `next`/no-arg), sync `main`, cut branch, load context. Mode chosen from the issue's type label: `bug`/`chore`/`documentation` → **fast mode** (build straight away); `enhancement` → **plan mode** (plan-approval gate). Override with `now` (force fast) or `plan` (force plan).
- **`/issue-finish`** — confirm acceptance, update README if it changed, verification gate, push, PR with `Closes #N`, wait for CI, auto-merge (merge commit + delete branch), land on main, safe tray restart. Does **not** write dated changelog files into `docs/`.

Both stay generic and read each project's CLAUDE.md for the gate command, ports, and tray procedure.

### Spawning sub-agents — cap concurrent Opus at 3

When a skill or session fans out **background sub-agents that run on Opus**, keep at most **3 in flight** at once (a sliding window): dispatch up to 3, and each time one returns, dispatch the next pending one until the queue drains. Fewer than 3 pending → just spawn that many (1 item = 1 agent). Every fleet fan-out skill — `audit-fleet`, `cleanup-fleet`, `issue-batch` — references this cap rather than re-hardcoding the number, so the limit lives in exactly one place.

- **Sonnet sub-agents are exempt** — they may fan out freely. In a mixed run only the Opus agents count against the window of 3 (Sonnet agents run alongside, uncounted). Sonnet is the "smaller model" half of the documented mitigation.
- **Why:** Anthropic's server-side burst limiter rejects the 4th–5th+ simultaneous Opus sub-agent bootstrap with `Server is temporarily limiting requests (not your usage limit) · Rate limited`. The empirical ceiling is 3–4 concurrent (anthropics/claude-code#53922 — the first 3–4 succeed, the rest fail); there is no officially published number, so 3 is the conservative floor. The official 429 guidance is to "avoid running many parallel subagents" or switch to a smaller model for high-volume runs (https://code.claude.com/docs/en/errors). Two unattended runs lost most of their work to this — the 2026-06-03 `/audit-fleet` (only 3 of 27 repos completed) and a later `/cleanup-fleet`.
- **Not** `CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY` — that env var bounds parallel *tool calls within a single session*, not the number of sub-agents in flight. The only place to cap sub-agent count is the orchestrating skill's dispatch logic (a bounded window), which is what this rule mandates.

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

## Recurring gotchas

### Git Bash strips backslashes in `settings.json` commands

Claude Code on this Windows machine executes `settings.json` commands (statusLine, hooks) through **Git Bash**, not cmd or PowerShell directly. Git Bash treats `\` as an escape character, so any Windows path in a command string must use **forward slashes** — `C:/Windows/...`, not `C:\Windows\...` — or bash strips the backslashes (`C:\Windows` → `C:Windows`) and the command silently fails.

Working statusLine / hook command form (in `settings.json`):

```
C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File C:/Users/rober/.claude/<script>.ps1
```

Related sub-gotchas:

- **Avoid `pwsh`** in spawned commands. The default `pwsh` on PATH is a WindowsApps execution alias (a 0-byte reparse stub) that fails when spawned non-interactively. Use the absolute Windows PowerShell 5.1 path above instead.
- **PowerShell scripts that read Claude Code's stdin JSON** should use `[Console]::In.ReadToEnd()` — `$input` is unreliable across the bash → powershell.exe pipe.
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
