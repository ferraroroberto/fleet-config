# Token-reduction tooling evaluation (chop, RTK, Context Mode, token-optimizer-mcp)

Evaluation spike for [#219](https://github.com/ferraroroberto/fleet-config/issues/219): can any third-party "context/token reduction" tool beat the fleet's own native `context_filter` hook layer on **native Windows + PowerShell**, without re-adding the standing MCP surface the fleet deliberately trimmed? All numbers below are measured on this machine (2026-07-20); no vendor claims are reproduced as data.

## Method

- **Shared static corpus:** raw outputs of representative commands captured once (git status/log/diff/show from `fleet-config`, `gh issue list`/`gh pr list`, `npm ls --all` from `app-launcher`, and a full `pytest tests/` run from `home-automation` with 85 failures / 611 passes — 5,222 lines). Every candidate compressed the same commands at the same repo state.
- **Token metric:** `context_filter.estimate_tokens` (chars ÷ 4, ceiling) applied uniformly to raw and compressed output.
- **Hook firing test:** per candidate, hook installed globally, then a headless `claude -p` session (Sonnet, `bypassPermissions`) instructed to run `git log --oneline -3` via the Bash tool; firing was judged by the candidate's own tracking DB recording the interception AND the session returning correct output. `~/.claude/settings.json` backed up before and hash-verified restored after every test.
- **Trust gate:** pinned-version source audit + all-runs network capture through a local logging proxy (`HTTP(S)_PROXY` → 127.0.0.1 listener recording every CONNECT; Go/Rust HTTP clients honor proxy env). No live `curl|sh` / `irm|iex` was ever executed.
- **Live A/B session benchmark:** one fixed, revertible task ([home-automation#489](https://github.com/ferraroroberto/home-automation/issues/489) — add `started_at` to `/api/version` + test) implemented end-to-end by a fresh headless Sonnet session per condition, full `/issue-start 489 now` ceremony through the green backend gate, stopping before ship, fully reverted between conditions. Per-session tokens isolated via `OTEL_RESOURCE_ATTRIBUTES=project.name=ab-<condition>` and read from local-llm-hub's telemetry (`/admin/api/telemetry/claude-code/usage`). Identical prompt file for every condition (recorded below). An independent judge agent scored the outcomes afterward.

## Static corpus results

| command | raw tk | native tk | chop tk | rtk tk |
|---|---:|---:|---:|---:|
| git status (clean) | 22 | 22 | 2 | 17 |
| git log --oneline -30 | 549 | 549 | 549 | 549 |
| git log -5 | 628 | 319 | 95 | 299 |
| git diff HEAD~3..HEAD | 5,655 | 1,216 | 87 | 4,927 |
| git show --stat HEAD | 125 | 124 | 125 | 125 |
| gh issue list --limit 30 | 232 | 231 | 179 | 162 |
| gh pr list --state all --limit 20 | 608 | 608 | 483 | 428 |
| pytest tests/ (85F/611P, 5,222 lines) | 64,076 | 1,512 | 64,148 | 707 |
| npm ls --all | 79 | 79 | 79 | 79 |
| **total** | **71,974** | **4,660 (93.5%)** | **65,747 (8.7%)** | **7,293 (89.9%)** |

Native fixture benchmark (the repo's own reproducible eval, `context_filter_cli.py eval`): median 90.0%, total 82.7% across its 5 fixtures.

Reading the table honestly:

- **The distribution is everything.** Small outputs (status, short logs, npm) are near-free either way; the one huge case — a failing test run — dominates the total. Native (97.6%) and RTK (98.9%) both crush it; **chop compressed the failing pytest run by 0%** (its own tracker agrees: `pytest … 0.0%`), which collapses its corpus total to 8.7% despite excellent per-command wins elsewhere.
- **Compression ratio is not signal preservation.** chop's 98.5% on `git diff` comes from replacing the diff with per-file `+n -m` stats — an agent that needed to *read* the change gets nothing. Native kept 1,216 tokens of real hunks; RTK barely touched it (4,927). The right amount of compression for `git diff` depends on why the agent ran it — a strong argument for owning the filter rules.
- RTK's pytest summary keeps per-failure file:line plus a truncated first traceback frame — good signal density; native keeps the failure names + summary line.

## Hook firing on native Windows (the make-or-break question)

| candidate | auto-installs hook? | hook fires when wired? | conditions |
|---|---|---|---|
| **chop** | yes (`chop init --global`, forward-slash path) | **yes — verified empirically** | binary dir must be on PATH for Claude Code's Git Bash (official installer does this; an audited manual install must add it). Fired despite 5 competing fleet Bash hooks — claude-code#15897 did **not** manifest on the current build. |
| **RTK** | **no** — non-interactive `rtk init -g` prompts to patch settings.json and defaults to No (rtk-ai/rtk#671); instead it **silently appends `@RTK.md` to the global CLAUDE.md** (which on this machine dirties `fleet-config/global-CLAUDE.md` through the symlink) | **yes — verified empirically** when the `rtk hook claude` block is added to settings.json by hand (v0.43.0's hook is a native subcommand, not the old `.sh`) | rtk on PATH; same manual-wiring caveat |
| native `context_filter` | already wired (dormant) in settings.json via `run-hook.ps1`; enabled per-process by `FLEET_CONTEXT_FILTER_MODE=rewrite` | yes (fleet-proven mechanism) | none |

The empirical result supersedes the issue's expectation: **both** third-party hooks mechanically work on native Windows — RTK's #671 gap is only its *installer* (no auto-patch + CLAUDE.md injection), not the hook mechanism.

## Trust gate

| dimension | chop v1.38.7 | RTK v0.43.0 |
|---|---|---|
| Pinned + audited | source cloned at tag (`fc6c2782`), Go, ~30 files audited | binary from tagged release; lighter audit (Rust, not fully read) |
| Release integrity | **Ed25519-signed** checksums.txt (key embedded in audited source) + SHA256 verified | SHA256 vs unsigned checksums.txt only |
| Network in source | only `updater/` (api.github.com + github.com release downloads, signed-update verify) | telemetry module, **consent "never asked" / disabled by default** (verified live) |
| Network observed | **only** `api.github.com` — the daily background update check; zero per-command traffic. (A stray `cafe.github.com` CONNECT during a mixed batch was attributed to **gh.exe** — the hostname is embedded in GitHub CLI's own binary, absent from chop's.) | **zero connections observed** across all corpus runs |
| Phone-home you cannot turn off | daily version check to api.github.com fires regardless of the auto-update flag (only the *download* is opt-in; auto-update itself is off by default — flag-file) | none observed; telemetry opt-in |
| Local data | savings DB in `%LOCALAPPDATA%\chop` (pure-Go SQLite); output only touches disk locally | savings DB under `%APPDATA%\rtk` |
| Install side effects | appends hook to settings.json (clean, detected, uninstallable via `chop uninstall`) | `rtk init -g` writes `~/.claude/RTK.md` and edits global CLAUDE.md without asking — **invasive**; creates `%APPDATA%\rtk\filters.toml` |
| Supply-chain posture if adopted | must pin + disable/firewall the update check, and re-audit per upgrade — auto-update would silently replace an audited binary with an unaudited one | same pinning requirement; no self-update observed in v0.43.0 CLI surface |

## MCP-based candidates (Context Mode, token-optimizer-mcp)

Measured from source (`git clone --depth 1`, tool schemas extracted and token-estimated at chars÷4; nothing installed, no postinstall executed):

| | Context Mode | token-optimizer-mcp |
|---|---|---|
| MCP tools registered | 11 (matches its claim) | **74** (README variously claims 61 and 65) |
| Standing tool-schema cost, every session | **~6,100 tokens** | **~29,200 tokens** |
| Hooks installed | 6 (PreToolUse, PostToolUse, PreCompact, SessionStart, UserPromptSubmit, Stop) | 4 (PreToolUse, PostToolUse, UserPromptSubmit, PreCompact) |
| Telemetry / network | none found in source; the hosted "Insight dashboard" is an explicit opt-in tool call that opens a browser, not a beacon | no telemetry SDK; its summarization features call api.anthropic.com / Google APIs with the user's own key; generated HTML reports load Google Charts from CDN |
| Install behavior | plugin-marketplace or explicit CLI step; bare `npm install` does not rewrite settings.json | **red flag:** `npm install -g` postinstall auto-writes 4 hook entries into `~/.claude/settings.json` with no opt-in step |

Reconciliation with the minimal-MCP doctrine (global CLAUDE.md; [#128](https://github.com/ferraroroberto/fleet-config/issues/128) measured 97% of injected tool surface unused across 945 transcripts): both candidates charge a standing per-session context tax to *maybe* save output tokens later — the exact trade the fleet already rejected. token-optimizer-mcp's ~29k/session is larger than the entire raw static corpus above and disqualifying on its own, before the settings.json-mutating postinstall. Context Mode is the honest version of the approach (verified counts, no phone-home, real bundle-integrity checks) but still costs ~6k/session against a native filter whose standing cost is ~0. **Both: pass on doctrine, now with numbers.**

## Live A/B session benchmark

Fixed task: [home-automation#489](https://github.com/ferraroroberto/home-automation/issues/489). Worker prompt (verbatim, identical per condition):

```
/issue-start 489 now

Session constraints (benchmark run — follow exactly):
- Implement issue #489 fully and get the declared backend verification gate green: & .\.venv\Scripts\python.exe -m pytest tests -p no:cacheprovider --ignore=tests/e2e
- Commit your work on the feature branch with a proper conventional commit message (committing on the feature branch is explicitly authorized for this session).
- Then STOP: do not run /issue-finish, do not push, do not open a PR, do not merge, do not delete the branch, do not restart the tray, and do not close or comment on the issue.
- End with a short summary: files changed, and the last 5 lines of the gate output.
```

All three sessions completed the full ceremony (claim → branch → implement → commit) and reported the gate green (484 passed); every branch was verified unpushed and then discarded, the claim released and the Board marker removed between conditions, and [home-automation#489](https://github.com/ferraroroberto/home-automation/issues/489) closed not-planned at the end.

Sonnet main-loop telemetry per condition (hub OTel rows, `project=ab-<condition>`):

| condition | wall | output tk | cache-creation tk | cache-read tk | cost |
|---|---:|---:|---:|---:|---:|
| baseline (no filter) | 125s | 7,306 | 57,307 | 1,481,383 | $0.898 |
| native `context_filter` (rewrite) | 156s | 6,647 | **19,029 (−67%)** | 1,898,656 | $0.784 (−13%) |
| chop (hook active, verified intercepting) | 118s | 5,178 | 40,818 (−29%) | 1,868,779 | $0.883 (−2%) |

Reading it: **cache-creation tokens — new context entering the window — is where output filtering lives**, and the native filter cut it 3× harder than chop. chop's DB confirms why: it intercepted the session's git commands but they were all tiny (0% saved each), and the pytest gate ran as `& .\.venv\Scripts\python.exe -m pytest …`, a venv-path invocation outside chop's command map — while the native filter wraps the whole Bash/PowerShell surface. Caveat, recorded honestly: **one run per condition; single-sample session variance is real** (the three sessions also wrote slightly different diffs), so treat the ranking (native > chop > baseline on context economy) as directional and the corpus table as the load-bearing evidence.

### Independent judge scores

A separate judge agent (no involvement in the runs) scored each condition's diff, test, and summary against the spec:

| condition | correctness | code quality | filter-induced degradation |
|---|---:|---:|---|
| baseline | 9/10 | 7.5/10 | n/a |
| native | 9/10 | 8.5/10 | none found |
| chop | 10/10 | 8/10 | none found |

All three converged on the same structural fix and a green gate; variation was polish, not substance (baseline refactored the adjacent `built_at` line; native also updated the route docstring; chop wrote the only test asserting tz-awareness). Judge's verdict verbatim: *"No evidence output filtering degraded outcome quality — both filtered conditions matched or slightly exceeded the unfiltered baseline."* Caveat: the judge saw final artifacts + telemetry, not full tool-call logs, so mid-session confusion could only be inferred, not observed.

## Verdicts (native-Windows, each vs the native-filter baseline)

- **Native `context_filter` — the winner; graduate it.** Zero standing context cost, zero delegated trust, best-in-class on the dominant corpus case (97.6% on a failing pytest run), the largest live-session context reduction (−67% cache-creation), already wired and acceptance-tested. It is dormant today — the follow-up adoption issue is to run it in `shadow` mode fleet-wide, review the shadow log, then flip to `rewrite`. Its measured gaps (0% on `git log --oneline`, `gh` lists, `npm`) are exactly where chop's and RTK's per-command filter rules are good — port *ideas* from their open source, not their binaries.
- **chop — pass.** The spike's headline empirical result is in its favor — the hook genuinely auto-intercepts on native PowerShell/Claude Code, and it cleared the trust gate at v1.38.7 (signed releases, clean network capture, local-only data) — but it is not better than the baseline where it matters: 0% on failing pytest (its own tracker agrees), 8.7% corpus total vs native's 93.5%, −29% live cache-creation vs native's −67%, `Bash`-matcher-only (misses this fleet's PowerShell tool), blind to venv-path Python invocations, and its `git diff`→stats compression destroys signal an agent may need. Adopting it would add a third-party binary in the most secret-exposed position on the machine, a daily non-disableable version check, and a re-audit obligation per upgrade — to be *worse* than what we own.
- **RTK — pass.** Best pytest summarizer measured (98.9%, good signal density) and zero network traffic observed, and — contrary to the issue's expectation — its v0.43.0 hook (`rtk hook claude`) works on native Windows when wired manually. But the installer is disqualifyingly invasive (silently appends `@RTK.md` to the global CLAUDE.md — which on this machine dirties `fleet-config/global-CLAUDE.md` through the symlink), releases ship unsigned checksums, the Rust source got only a light audit, and its corpus total (89.9%) still trails the native filter. Same conclusion: steal its pytest-summary shape for the native filter instead.
- **Context Mode — pass on doctrine.** The honest MCP implementation (verified 11 tools, no phone-home), but ~6,100 standing tokens every session against a baseline that costs ~0.
- **token-optimizer-mcp — hard pass.** ~29,200 standing tokens/session (74 tools; README undercounts), plus an `npm install -g` postinstall that silently rewrites `~/.claude/settings.json` — a supply-chain red flag independent of any token math.

## Cleanup state (leave-the-machine-as-found)

chop and RTK binaries, data dirs (`%LOCALAPPDATA%\chop`, `%APPDATA%\rtk`), `RTK.md`, and the CLAUDE.md injection all removed; `~/.claude/settings.json` restored and hash-verified against the pre-spike backup after every mutation; home-automation back on pristine `main` (no branches, no remote pushes, claim released, Board marker removed, benchmark issue closed not-planned).
