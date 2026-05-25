---
name: screen
description: Attach recent screenshots from E:\downloads\snaps to the conversation without manual copy-paste. Invoke whenever the user's message contains the literal token `/screen` — at the start OR anywhere mid-sentence (e.g. "check this /screen 1" or "/screen 3 what changed?"). Forms: `/screen` (just the latest screenshot), `/screen N` (last N screenshots), `/screen Nm` (screenshots from the last N minutes). Any other text in the user message is their actual prompt about the attached screenshots.
---

# screen

**Goal:** Pull recent screenshots from `E:\downloads\snaps`, load them into the conversation via the Read tool (which renders images visually), and then handle any trailing text the user typed as their real question about those images.

This skill must stay lightweight: parse, glob, read, respond. No editing, no commits, no extra tools.

## Source

- Directory: `E:\downloads\snaps`
- Eligible extensions: `.jpg`, `.jpeg`, `.png`, `.webp` (case-insensitive)
- "Recency" is determined by **file modification time** (`LastWriteTime`), not filename.

## Argument parsing

Identify the spec token by scanning the **user's full message** (not just the skill arg field — when `/screen` is mid-sentence, the harness may not auto-route, and you're invoking the skill yourself; the args field can be empty). Find the literal `/screen` in the message; the spec is the next whitespace-separated token immediately after it.

The spec is one of:

- *(absent — `/screen` is the last token, or followed only by non-numeric words)* → **default to `1`**. Take just the most-recently-modified eligible file.
- `N` — integer ≥ 1. Take the **N most-recently-modified** eligible files.
- `Nm` — integer ≥ 1 followed by literal `m`. Take **all eligible files modified within the last N minutes**.

Everything else in the user's message (text before `/screen`, and text after the spec token) is the user's actual prompt about the screenshots. Concatenate it (preserving order) and remember it as `user_prompt`.

Examples:
- `/screen` → spec `1`, prompt empty
- `/screen 3` → spec `3`, prompt empty
- `/screen 5m what changed?` → spec `5m`, prompt `what changed?`
- `check this /screen 1` → spec `1`, prompt `check this`
- `compare these /screen 4 - is the alignment fixed?` → spec `4`, prompt `compare these  - is the alignment fixed?`

If the token immediately after `/screen` exists but doesn't match `^\d+m?$`, treat it as the start of `user_prompt` and use spec `1` (default). Don't error — be forgiving.

## Steps

### 1. Resolve the file list

Run **one** command via the **PowerShell tool** (not Bash — Bash mangles `$_`). Pick the form based on whether the spec ends in `m`. Substitute `N` with the parsed integer.

**Count form** (`N`):
```
Get-ChildItem -LiteralPath 'E:\downloads\snaps' -File | Where-Object { $_.Extension -match '^\.(jpg|jpeg|png|webp)$' } | Sort-Object LastWriteTime -Descending | Select-Object -First N | Sort-Object LastWriteTime | ForEach-Object { $_.FullName }
```

**Minutes form** (`Nm`):
```
$cutoff = (Get-Date).AddMinutes(-N); Get-ChildItem -LiteralPath 'E:\downloads\snaps' -File | Where-Object { $_.Extension -match '^\.(jpg|jpeg|png|webp)$' -and $_.LastWriteTime -gt $cutoff } | Sort-Object LastWriteTime | ForEach-Object { $_.FullName }
```

The double sort in the count form (descending to pick the newest N, then ascending) yields a **chronological oldest → newest** order in the output — that is the order the images should be attached.

If the command returns zero lines:

- For `N`: error `❌ No screenshots found in E:\downloads\snaps.` and stop.
- For `Nm`: error `❌ No screenshots modified in the last N minutes.` and stop.

### 2. Read each file as an image

For each absolute path printed by step 1, call the **Read tool** on it. The Read tool renders images visually into the conversation, which is what makes them visible to the model.

Issue all the Read calls **in parallel** in a single tool-use block — they're independent.

### 3. Acknowledge and proceed

After the Read calls return, print exactly one short line summarizing what was attached. Format:

> Attached N screenshot(s) (oldest → newest): `<basename1>`, `<basename2>`, ...

(Truncate to the first 4 basenames + `, ...` if N > 4.)

Then:

- If `user_prompt` is non-empty: address it directly using the now-visible images. Do **not** restate the prompt — just answer it. Follow normal project rules (plan mode for non-trivial work, etc.).
- If `user_prompt` is empty: stop. Do not speculate about what the user wants — wait for their next message.

## Notes

- The skill never edits files, never commits, never moves or deletes screenshots — it only reads them.
- If a path contains characters that confuse the Read tool, prefer the exact `FullName` from PowerShell output verbatim; do not re-quote or re-escape.
- The skill is Windows-specific by design (the source path is a Windows path). It will not work from a POSIX shell.
