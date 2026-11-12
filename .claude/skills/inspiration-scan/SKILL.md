---
name: inspiration-scan
description: Daily digest of ideas from followed newsletters, YouTube channels and X accounts — drop seen items, score the rest through the local hub against what the fleet does, deliver an HTML report with an /issue-add line per idea. E.g. "/inspiration-scan", "scan my sources for ideas". Also runs daily unattended.
---

# inspiration-scan

**Capability preflight:** read [workflow-capabilities](E:/automation/fleet-config/docs/workflow-capabilities.md) and bind dispatch, results, waits, cancellation, model tiers and questions to this session’s actual tools before proceeding. Tool names below are conditional examples; the contract governs adaptation.

**Goal:** one pass a day over the owner's sources, keeping only items likely to give an idea worth trying in the fleet. The owner reads the digest, picks an idea, and files it with the card's `/issue-add` line.

**The deterministic work is `scan.py`.** It reads every source, dedupes against earlier days, scores through the hub, writes the HTML digest and prints a manifest. This skill runs it, delivers the digest and reports. Never re-score, re-rank or rewrite the digest by hand.

## Execution rules (read first)

- **Run from the `fleet-config` repo root** (`E:/automation/fleet-config`) so helper paths resolve.
- **Private data stays local.** The source list, the seen ledger, `ideas.json`, the ideas ledger and every digest live in the machine-local state root (`~/.claude/inspiration-scan/`, or `INSPIRATION_SCAN_ROOT`). Never post a source name, an item or a digest to GitHub. The manifest names source types and counts only.
- **Read-only on every source.** Feeds are plain GETs. X is read through the dedicated Chrome profile by `x_reader.py`, which only opens profile pages: it never posts, likes, follows or clicks.
- **At most one pass a day.** A second run on the same day re-reads the sources, so never loop or retry a run to fill a quiet digest.
- **Run everything in the foreground** and wait for it in this turn. A scheduled run has no wake-up.
- **No source is ever "nothing new" by default.** Each one is `ok`, `not-read` (with a reason) or `unknown`, and the report says which.

## Steps

### 1. Run the scan

```
E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/inspiration-scan/scan.py run
```

This can take up to about twenty minutes when the X profile is held by another Chrome, because the reader waits with backoff instead of killing the holder. Capture every manifest line:

- `SOURCES=<type>|total=|ok=|not-read=|unknown=|login-needed=|items=`: one line per source type. `login-needed` counts X sources skipped because the X profile is not logged in.
- `COUNTS=new=|seen_before=|scored=|unscored=|deferred=|kept=`.
- `SCORING_ERROR=<batch>: <error>`: a batch the hub did not score. Its items are listed as unscored and come back tomorrow.
- `IDEAS=<n>`: ideas now in `ideas.json` (today's kept ones plus earlier undecided ones), for the Board.
- `DIGEST=<path>`: the HTML digest.
- `SOURCES_FILE=created|unfilled|invalid`, when the source list is missing, still the untouched example, or unparseable.
- `STATUS=ok|degraded|no-sources|bad-config`.

`STATUS=no-sources` (exit 2) means the source list was missing (`run` created it from the example) or is still the untouched example. It still writes a `DIGEST=` that says what to fill in and where, so deliver that digest in step 2. `bad-config` (exit 3) means the list does not parse: report the line `scan.py` printed and stop, there is no digest.

### 2. Deliver the digest

In a launcher session, send the file to the owner's chat view:

```
E:/automation/fleet-config/.venv/Scripts/python.exe skills/_lib/send_file.py <DIGEST> --caption "Inspiration digest: <kept> ideas"
```

`SENT=` means it was delivered. On `UNAVAILABLE=` (a scheduled run has no launcher session), upload it to the owner's private bot chat as **activity-log** traffic instead:

```
E:/automation/fleet-config/.venv/Scripts/python.exe hooks/notify_send.py --category log --file <DIGEST> --title "Inspiration digest <YYYY-MM-DD>" --text "<kept> ideas; <one clause per SOURCES= line that is not fully ok>"
```

On `REFUSED=`, relay the reason and still try the Telegram upload. Either helper failing is reported, never retried in a loop.

### 3. Report

Print a few lines: the `STATUS=`, ideas kept, sources not read and why (X skipped for no login is reported as that, never as quiet), scoring errors, and which channel delivered the digest (or that none did). No item titles or source names.

## Ideas on the Board

Each run also writes `ideas.json` and drops any idea the chief has recorded in `ideas-ledger.jsonl`. The run never writes the ledger: only `scan.py ideas apply|filter` does, and only the chief calls it ([docs/inspiration-ideas.md](E:/automation/fleet-config/docs/inspiration-ideas.md)).

## Owner setup

- `scan.py run` creates the source list from `sources.example.toml` when it is missing (`scan.py init` does the same on its own); fill in your own sources.
- `scan.py login-x` opens the dedicated X profile headed: log in once, then close the window. Until then the digest reports X as skipped for no login.
- The app-launcher Job runs `.claude/skills/inspiration-scan/run-weekly.bat` daily at 03:00; the entry shape is in [docs/skills/inspiration-scan.md](E:/automation/fleet-config/docs/skills/inspiration-scan.md).
