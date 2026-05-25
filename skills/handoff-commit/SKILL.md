---
name: handoff-commit
description: Generate a copy-paste markdown prompt that hands off a specific GitHub commit to another LLM, instructing it to apply the same logical change to a sister project. Use when the user works on a public repo and wants the same change replicated in a private fork (or vice versa) without copy-pasting code. Defaults to HEAD; accepts a commit-ish argument (SHA, HEAD~N, branch).
---

# handoff-commit

**Goal:** Produce a markdown handoff prompt for another LLM. The other LLM will fetch a commit from a public GitHub repo and replicate its logical changes in a sister project. The user then copy-pastes the markdown elsewhere.

**This skill produces text only.** Never edit files in the working directory, never commit, never push, never call other tools beyond Bash for git plumbing. Output the markdown block and stop.

## Arguments

- No argument → target commit is `HEAD`.
- One argument → treat as a git commit-ish (`<sha>`, `HEAD~1`, `main`, `<branch>`, etc.).
- More than one argument → tell the user only one is accepted and stop.

## Steps

Run these in order. If any step fails, print a short error and stop — do not produce a partial prompt.

### 1. Resolve the commit and verify the repo

Run in parallel:
- `git rev-parse --is-inside-work-tree` — must print `true`. If not, error: "Not inside a git repository."
- `git rev-parse <ref>` — full 40-char SHA. If it fails, error: "Could not resolve <ref>."

### 2. Verify the commit is pushed to a remote on `origin`

```
git branch -r --contains <full-sha>
```

If the output contains **no** line starting with `origin/`, refuse:

> ❌ Commit `<short-sha>` isn't reachable on `origin`. Run `git push` first, then re-run `/handoff-commit`.

Stop. Do not generate the prompt — the URL would be dead.

### 3. Gather commit + repo metadata

Run in parallel:
- `git remote get-url origin` — capture the remote URL.
- `git log -1 --format=%H%n%h%n%s%n%an <%ae>%n%ai <full-sha>` — captures full SHA, short SHA, subject, author + email, author date in one call. (Use a separator scheme that won't collide with commit content — e.g. use `--format='%H%x00%h%x00%s%x00%an%x00%ae%x00%ai'` and split on `\0`.)
- `git log -1 --format=%b <full-sha>` — body (may be empty).
- `git diff-tree --no-commit-id --name-status -r <full-sha>` — list of files with A/M/D/R status.
- `git diff-tree --no-commit-id --numstat -r <full-sha>` — +/- line counts per file, for the summary line.

### 4. Normalize the remote URL to an `https://github.com/USER/REPO` form

Accept the common shapes and strip `.git`:
- `git@github.com:USER/REPO.git` → `https://github.com/USER/REPO`
- `https://github.com/USER/REPO.git` → `https://github.com/USER/REPO`
- `ssh://git@github.com/USER/REPO.git` → `https://github.com/USER/REPO`

If the remote isn't a GitHub URL, warn the user but still produce the prompt (the patch URL trick won't work for non-GitHub hosts — note that limitation in the warning, don't fabricate a URL).

Derive:
- `commit_url = <https-base>/commit/<full-sha>`
- `patch_url  = <https-base>/commit/<full-sha>.patch`

### 5. Render the handoff prompt

Print **exactly one** fenced markdown block (use four backticks to fence so embedded triple-backticks survive), with the populated template below. Do not print anything else before or after besides a single line saying `Copy the block below:`.

````
Apply the changes from this upstream commit to the current project.

  Repo:        <https-base>
  Commit URL:  <commit_url>
  SHA:         <full-sha>  (short: <short-sha>)
  Author:      <author> <<email>>
  Date:        <author-date>
  Subject:     <subject>

Commit message body:
<body — indented two spaces, or "(no body)" if empty>

Files changed (<N> file(s), +<additions> / -<deletions>):
  <status>  <path>     (+<adds> / -<dels>)
  ... one line per file ...

YOUR TASK
  1. Fetch the patch without adding a remote:
       curl -L <patch_url> -o /tmp/handoff.patch
     Or with git directly:
       git fetch <https-base>.git <full-sha>
       git show <full-sha> > /tmp/handoff.patch
  2. Inspect the patch so you understand what it does. Do NOT cherry-pick blindly.
  3. Apply the same LOGICAL changes to THIS project's copies of the listed files.
     The local files likely have unrelated modifications, so `git apply` will
     probably fail — apply hunks manually and resolve conflicts by preserving
     local edits while still introducing the new functionality.
  4. Touch ONLY the files listed above. Do not refactor, reformat, or "improve"
     anything else. If a function signature change in the upstream commit
     breaks call sites in OTHER files in this repo, surface those call sites
     and ask before editing them.
  5. Verify it still works using whatever the project's existing checks are
     (py_compile, pytest, npm test, type checker, etc.). If none exist, say so
     explicitly rather than claiming success.
  6. Do NOT commit, push, or stage anything. Leave the working tree dirty so
     I can review the diff with `git diff` and commit on my own terms.

HARD RULES
  - Plan-mode first: investigate the upstream patch and the local state, then
    present a short plan before editing. Wait for my approval to proceed.
  - Ask before assuming anything about file locations, call sites, or intent.
  - Never run destructive git commands. Never auto-commit. Never push.
  - If the upstream commit touches files that don't exist in this project,
    stop and ask me how to handle them — don't invent equivalents.
````

### 6. Done

After printing the prompt, output exactly one short sentence: `Generated handoff prompt for <short-sha>. Push status: ✅ on origin.`

## Notes

- This skill never writes to disk, never commits, never pushes. It only reads git state and prints markdown.
- Commit-ish resolution is done with `git rev-parse`, so any reference git understands works: tag names, branch names, `HEAD~N`, partial SHAs.
- If the commit's remote is on a non-GitHub host (GitLab, Bitbucket, internal Gitea), still produce the prompt but replace the `curl <patch>.patch` line with a note pointing the other LLM at whatever the host's equivalent is, and warn the user inline.
