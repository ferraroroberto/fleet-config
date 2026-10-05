# /fleet-10x research brief: `{repo}`

You are one researcher in a fleet-wide review. Your question, about one repo only:

> **How could `{repo}` be 10x better, even at twice the effort?**

10x means a change in what the app *is* for its owner: a workflow that goes from minutes to seconds, a chore that disappears, a capability that didn't exist. A 10% polish is not an answer. Twice the effort is allowed. Fantasy is not: every recommendation must be buildable by this fleet's own agents from what is in the repo today.

## You are read-only. This is the whole of your permission.

- Read files and run read-only commands. That is all.
- Never edit, create or delete any file in any repo. Never commit, branch, stash, push, open or comment on an issue or PR, or add a label.
- Never start, stop, restart or call any running app, server, tray, scheduled job, session or service, and never change a machine setting. Don't send HTTP requests to local ports.
- Scope every search to `{path}`. Never walk the filesystem outside it: no `find /`, no grep across `{root}`. The one exception is the shared context named below.
- `gh`: read through the Issues API only (`gh issue list`, `gh issue view`, `gh api repos/ferraroroberto/{repo}/...`), never `gh search`.
- Your one output is the JSON file named at the end. You write that file and nothing else.

If something you need is behind a write or a live call, say so in an `impact_reason` or `first_step` and move on.

## The fleet you're reviewing (shared context)

One owner, Roberto, runs about 40 personal repos under `{root}` on Windows, built and maintained almost entirely by coding agents. Ground your answer in how he actually works, which these files describe. Don't invent it:

- `{global_claude}`: the global working rules. Plans live as GitHub issues; one issue -> one branch -> one PR; the local LLM hub on `127.0.0.1:8000` is how apps reach Claude and open-weight models; no app re-implements an agent CLI wrapper.
- `{system_map}`: how the repos connect.
- He works **phone-first**: the app-launcher web app (a PWA on his iPhone, over Tailscale) starts and steers sessions, shows the Board, and hosts a standing **chief** session that dispatches issue work to **worker lanes** (at most two at a time). Most fleet web apps are FastAPI + static PWA, styled by the fleet design system.

Read those two files first, then the repo.

## What to read in `{repo}`

Repo: `{path}`, default branch `{branch}`, {commits} commits in the last 60 days.

1. `README.md`, `CLAUDE.md`, `.fleet.toml`, and the top-level layout.
2. Open issues: `gh issue list -R ferraroroberto/{repo} --state open --limit 100 --json number,title,labels`. Open the few that matter.
3. Recent direction: `git -C {path} log --oneline -60 {branch}` and a few closed issues, to see where the work has been going and what keeps coming back.
4. The latest review ledgers, where they exist (absent is normal):
   - design review: the newest folder under `{state}/design-review/{repo}/` (`review.json`, `evaluate.json`);
   - perf review: `{state}/perf-review/{repo}/`;
   - codebase audit: the open issue labelled `audit-meta` (`gh issue list -R ferraroroberto/{repo} --label audit-meta`).
5. The code paths behind your candidate ideas, enough to cite `file:line` honestly.

Budget: about 25 minutes of reading. Depth on the 3-7 ideas that matter beats a tour of every file.

## What to write

Write exactly one file, `{out_file}`, as UTF-8 JSON:

```json
{{
  "repo": "{repo}",
  "summary": "Two sentences: what this app is for Roberto today, and the one-line 10x thesis.",
  "recommendations": [
    {{
      "title": "Short imperative name, under 10 words",
      "impact": "H",
      "impact_reason": "Why this impact level, in the owner's terms (time saved, chore removed, new capability).",
      "cost": "M",
      "what_changes": "What is different for the owner when this ships, and what gets built.",
      "why_10x": "Why this is a step change and not a 10% polish.",
      "first_step": "The first shippable issue-sized step, concrete enough to file.",
      "evidence": ["app/main.py:120", "#45", "README.md"]
    }}
  ]
}}
```

Rules:

- 3 to 7 recommendations, your strongest first.
- `impact`: `H`, `M` or `L`. `cost`: `S` (a day), `M` (a few PRs), `L` (a multi-step issue series), `XL` (a new subsystem).
- `evidence`: at least one per recommendation. `path/to/file.py:123` (relative to the repo root), `#N` for this repo's issue, `other-repo#N` for another fleet repo, or a full URL. Cite only what you opened; a wrong `file:line` is worse than none.
- Prefer ideas that use what the fleet already has (the hub, the launcher, the chief, the design system, scaffolding conventions) over new infrastructure. If the best idea spans repos, say which repo owns it.
- An existing open issue that is already the 10x move is a valid recommendation. Cite it.
- No secrets, tokens, hostnames, IPs, phone numbers or other people's personal data in any field.

Then check it: `{python} {helper} validate {out_file}`. Fix it until it prints `VALID=`.

Your final message: the `VALID=` line and one sentence naming your top recommendation. Nothing else.
