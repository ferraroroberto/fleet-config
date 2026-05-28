---
name: issue-triage
description: Pull every open GitHub issue across all repos owned by ferraroroberto, score each one Small/Medium/Large by reading title + body, and present a per-project markdown table plus a one-line "suggested start" pick per project so the user can decide where to focus next. Use when the user wants a fleet-wide view of work in flight — e.g. "/issue-triage" or "/issue-triage <repo>" to filter to one repo.
---

# issue-triage

**Goal:** Give the user a phone-readable, fleet-wide overview of open work so they can pick the next thing to tackle. One table per project, sized by complexity, with a recommended starting point per project.

Read-only — this skill **never** creates, closes, labels, comments on, or otherwise mutates any issue. Output goes straight to the chat.

## Arguments

- No args → scan **all repos** owned by `ferraroroberto`.
- One arg that looks like a repo name (e.g. `/issue-triage app-launcher`) → filter to that single repo. Match against the bare repo name, not `owner/name`.

Anything else → treat as no args.

## Execution rules (read before running any command)

- **Do not spawn Python, jq, awk, sed, or any other tool to process the `gh` JSON.** The JSON output of `gh search issues` is read **directly by Claude** — group, score, and render in-conversation. Counting rows, bucketing by repo, picking suggestions are all model-side operations, not shell-side.
- **Shell:** the Bash tool on this machine is **Git Bash**, which does **not** accept PowerShell syntax. The `&` call operator, `$env:VAR`, backtick line continuations, here-strings (`@'…'@`) — all of these are PowerShell-only and will error in Bash with `syntax error near unexpected token '&'` or similar.
  - If you genuinely need PowerShell (e.g. to invoke `.\.venv\Scripts\python.exe`), use the **PowerShell tool**, not Bash.
  - For this skill there is **no reason** to invoke PowerShell — the only shell command needed is `gh`, which works identically in either shell. Stick with Bash + a plain `gh` invocation.
- **One `gh` call total.** Do not re-query per-repo. Do not pipe `gh` into another tool.

## Steps

Run in order. If a step fails, print a short error and stop.

### 1. Pre-flight

- `gh auth status` — must be authenticated as `ferraroroberto`. If not, stop and tell the user to run `gh auth login`.

### 2. Fetch all open issues in one shot

Single GitHub search query — much faster than iterating repos:

```
gh search issues --owner ferraroroberto --state open --include-prs=false --limit 300 \
  --json repository,number,title,body,labels,url,createdAt,updatedAt,assignees
```

Run this **once**, via Bash, as plain `gh …` — no `&`, no pipes, no Python wrapper. Claude reads the JSON output directly from the tool result.

Notes:
- `--include-prs=false` keeps PRs out of the result set.
- `--limit 300` is well above the realistic backlog; if the result is exactly 300, mention that the cap may have been hit.
- If a single-repo filter was passed, **mentally** filter the JSON by `repository.name == <arg>` when grouping in step 3 — don't shell out a second tool to filter it.
- **Drop ledger / metadata issues.** Ignore any issue carrying the `audit-meta` label (the `/codebase-audit` per-repo ledger). It is never actionable work — filter it out model-side when grouping, exactly like the single-repo filter above. Don't add a `gh` query qualifier for it (a leading `-label:` dash trips arg parsing); just skip those rows.

If the result is empty, print "No open issues across the fleet 🎉" and stop.

### 3. Group by repository

Bucket issues by `repository.name`. Sort repositories alphabetically for stable output. Within each repo, hold issues in the order returned (most-recently-updated first is fine).

### 4. Score each issue

For every issue, read `title` + `body` + `labels` and assign:

- **Size:** `S` (Small) / `M` (Medium) / `L` (Large)
- **Why:** ≤ 8 words, concrete — what makes it that size

Calibration:
- **S** — one file or a narrow surface, no design decision, clear acceptance. Bugs with a known repro, typos, small chores.
- **M** — touches a few files or crosses one boundary (e.g. backend + one frontend page), some judgment but the shape is obvious.
- **L** — multi-module, real design choices, or an unbounded body (new integration, architectural change, vague "improve X").

Labels are a signal, not a verdict. A `bug` can be Large if the body describes a refactor; an `enhancement` can be Small if it's a one-line config. The "Why" should reflect what was actually read, not the label.

If the body is empty or 1 line, default to **S** unless the title implies otherwise.

### 5. Pick a "suggested start" per project

For each repo, pick **one** issue as the recommended next step. Preference order:
1. The smallest item that is also the most *tactical* — clear acceptance, narrow blast radius, satisfying to close.
2. If a clear blocker / dependency exists (issue body references "blocks #N" or is itself blocked), prefer the unblocked one.
3. Break ties by most-recently-updated.

Avoid recommending a Large issue unless every issue in the repo is Large — in which case pick the one with the clearest acceptance criteria.

### 6. Render the output

One section per repo. **Repo header**, then a markdown table, then the suggestion line. Phone-readable: short columns, no wrapping inside cells, no extra preamble.

Format exactly like this:

```
## Project 1 — app-launcher  (4 open)

| #  | Title                          | Size | Why                          |
|----|--------------------------------|------|------------------------------|
| 63 | WS handshake retry             | S    | scoped fix, 1 file           |
| 65 | Cache hygiene on tray restart  | M    | crosses tray + webapp        |
| 70 | Edge mirror window refactor    | L    | unbounded; multi-module      |

→ **Suggested start:** #63 — small, tactical, clears one regression.

## Project 2 — photo-ocr  (2 open)

…
```

Rules:
- Truncate title to ~30 chars; never wrap inside a cell.
- Truncate "Why" to ~30 chars.
- Issue `#` column is the raw number — the user can paste it into `/issue-start <N>`.
- If a repo has zero open issues, **omit** it entirely (don't render an empty table).
- Number projects (`Project 1`, `Project 2`, …) in render order.

After all per-project sections, finish with a single line:

```
**Total:** N open issues across M projects. Run `/issue-start <N>` from inside the relevant repo to pick one up.
```

### 7. Stop

No follow-up actions. The user reads the table and decides. Do **not** auto-launch `/issue-start`; do **not** offer to start working on a suggestion unless the user asks.
