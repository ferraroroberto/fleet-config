---
name: retro-fleet
description: Mine every Claude Code, Codex and Pi session log on this machine for errors that keep coming back, propose a check for each recurring one, render the scored proposal page and post one Board ask per proposal; nothing is filed or built. E.g. "/retro-fleet", "which agent mistakes keep recurring". Also runs weekly unattended.
disable-model-invocation: true
---

# retro-fleet

**Goal:** find agent mistakes that recur across sessions and propose how to stop each one, preferring a mechanical check (a ratchet rule, a hook, a test) over a written rule. This skill reads and proposes. Its only writes are the machine-local cache, the page and the Board asks; the owner decides each ask, and an accepted one is filed later through `/issue-add`. Contract: [docs/skills/retro-fleet.md](E:/automation/fleet-config/docs/skills/retro-fleet.md).

## 1. Mine

```
E:/automation/fleet-config/.venv/Scripts/python.exe E:/automation/fleet-config/skills/_lib/retro/fleet.py scan
```

Run it in the foreground; the first run on a machine is a full backfill and can take many minutes, later runs read only the logs that changed. Keep the manifest lines `LOGS=`, `DIGESTED=`, `UNMEASURED=`, `SIGNATURES=` and the `CANDIDATES=` path. `recurring:0` means nothing recurred: report the counts and stop.

## 2. Classify

Hand `candidates.json` to one read-only Sonnet sub-agent. It judges each entry in `candidates` (the most recurring signatures, counts only) by searching this repo's `hooks/`, `tests/`, ratchet config and the scaffold's equivalents, and returns a JSON list with one object per signature it judged:

- `key`: the candidate's `key`, unchanged.
- `verdict`: `enforced` (a check already catches it; reported only), `unenforced` (a program could decide it and nothing does: name the hook, ratchet rule or test and its target file), `unwired` (a check exists but the gate or the hook wiring does not run it), or `judgement` (needs intent read: name one line in the narrowest instruction file).
- `title`, `summary`, `home`: its own words about the check, no quoted prompt, command or tool output; at most 400 characters each.
- `impact` and `effort`: whole numbers 1-5. Impact rests on sessions in the window and the trend; effort on the size of the check.
- `repo` (optional): defaults to `project-scaffolding` when the signature is seen in two or more repos, else its one repo.

Write the list to `verdicts.json` next to `candidates.json`. A signature the agent cannot place is left out, not guessed.

## 3. Render

```
E:/automation/fleet-config/.venv/Scripts/python.exe E:/automation/fleet-config/skills/_lib/retro/fleet.py report <candidates.json> <verdicts.json>
```

Prints `PAGE=<path> items=<n> quick_wins=<n>` and `REPORTED_ONLY=<n>`. A verdict naming a key the mine did not produce, or an over-long text, is refused (exit 2): fix the verdict, never the counts.

## 4. Publish, then ask

Publish the page where the owner's phone opens it (an Artifact when that tool is callable) and post the asks against its URL:

```
E:/automation/fleet-config/.venv/Scripts/python.exe E:/automation/fleet-config/skills/_lib/proposal_report/cli.py ask <report.json> --page-url <url>
```

A proposal asked before is skipped (`already-asked`), so a weekly run asks only about new signatures. With no way to publish (an unattended run without the Artifact tool), do not post asks: send the page file through `skills/_lib/send_file.py` or `hooks/notify_send.py --category log --file <page>`, and report the asks as not posted.

## 5. Report

A few lines: the manifest counts, unmeasured logs by reason (never folded into clean), proposals by quadrant, the already-enforced signatures reported only, and the ask outcome line. No signature text beyond what the page shows.
