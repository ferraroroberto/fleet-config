---
name: fleet-health
description: Weekly hardware health checkup of every fleet machine — capture each box's resource envelope through its own hub, diff against last week's entry, append to one incremental ledger. E.g. "/fleet-health", "check the fleet hardware health", "what's running on my machines", "what crept in since last week". Also runs weekly unattended.
---

# fleet-health

**Goal:** answer the question a one-off capture cannot — **"what crept in?"** A snapshot says what is running now; a *ledger* says what changed, keeping a powerful box from silently degrading. Each run captures every reachable machine through its own hub, analyses the numbers against what was written last time, and appends one entry per machine to a single incremental ledger.

**The previous entry is loaded before anything is analysed** — load-bearing, not a nicety. A run that analyses without reading the prior entry has produced a snapshot, not a checkup.

**No machine name appears anywhere in this skill.** The fleet is read from the hub's own inventory each run and each machine is routed by what it *answers*, never by what it is called — a newly enrolled box appears in the next checkup with zero edits here. Grepping this directory for a machine id is the test.

## Execution rules (read first)

- **Run from the `fleet-config` repo root** (`E:/automation/fleet-config`) so helper paths resolve.
- **Never commit health data.** Everything lands under `~/.claude/fleet-health/`, entirely outside this repo — the skill and its runner are the only public artefacts. Don't copy results in, don't paste machine inventories into issues or PRs.
- **Every machine is accounted for, every run.** A machine is either captured or listed as **not covered with a reason**. Silently omitting an unreachable box is the one failure mode that makes the ledger lie.
- **Poll synchronously; never background-and-wait.** Per fleet-config#314, a scheduled headless `claude -p` that ends its turn expecting to be resumed exits `0` having done nothing, and the job reports false success. `capture.py` blocks in bounded chunks and returns only when the captures are done — let it.
- **Partial failure degrades one entry, never the run.** One unreachable machine is a "not covered" line; it does not abort the other captures.
- **A `409` is "busy", not "broken".** The hub refuses a second concurrent capture with `{"detail": "a capture is already running"}`; `capture.py` adopts the in-flight run instead of failing.
- **Degrade gracefully, never block on a prompt** (unattended): hub down → report it and skip the ping rather than hang; first run → write a baseline entry and say so.

## Steps

Run in order. A failure on one step prints a short error and stops.

### 1. Read the previous ledger entry first

```
E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/fleet-health/ledger.py previous
```

Prints the most recent run's entries (or `NO_PRIOR_ENTRY` on a first run). **Read this into context before step 2** — every recommendation still open here must be explicitly carried forward in step 4, marked as *still open*, never silently re-derived as if newly discovered.

### 2. Capture every reachable machine

Three calls: **start once, poll until done, collect once.** A tool call is capped well under an hour, so the capture cannot block in a single call — the chunk boundary is the point.

```
E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/fleet-health/capture.py start
```

Discovers machines from `/admin/api/machines/status`, classifies each by probing its own hub's `/admin/api/diagnostics/status`, and starts **all** captures before polling **any**, so they run concurrently. Default one hour at 30 s ticks — override with `--duration-s` / `--interval-s` for an on-demand run. Prints `POLL_CHUNKS_EXPECTED=<n>`, run state is persisted, stdout carries `LEDGER=` / `OUT_DIR=` / `RUN_DATE=` plus one `MACHINE=` line per machine.

Then poll — **repeatedly, in the same turn**, until a call prints `DONE=yes`:

```
E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/fleet-health/capture.py poll
```

Each call blocks for up to nine minutes then returns, printing per-machine `samples=` progress and `DONE=yes|no`. **fleet-config#314 discipline made concrete:** the blocking happens *inside* a tool call that returns, so the turn stays alive. Never background this and never end the turn waiting to be resumed — a scheduled headless `claude -p` that does so exits `0` having captured nothing while the job reports success.

Finally:

```
E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/fleet-health/capture.py collect
```

Stops anything still running past its deadline, fetches every artefact, and emits the manifest the rest of the skill reads:

- `MACHINE=<id>|status=captured|run_id=…|verdict=…|samples=…|report=…|export=…|drift=…`
- `MACHINE=<id>|status=not-covered|detail=<kind>|reason=<human reason>`
- `CAPTURED=` / `NOT_COVERED=` totals

Exit `3` (start) = inventory unreachable, the local hub is down — report and stop. Exit `4` = inventory fine but nothing could be captured; still write a ledger entry recording *why* every machine was skipped, because that is a real finding, not an empty run. Pass a matching `--out-dir`/`--date` to all three calls if you override either.

### 3. Analyse each captured machine — adversarially

Read each machine's `report=` markdown (the generated health report: verdict, findings, resource envelope, load by app, heaviest processes, listening ports, drift vs baseline) and its `drift=` json. Pull from `export=` json only when a number the report doesn't carry is needed.

**Cross-check the numbers rather than transcribing them.** The 2026-07-20 dry run surfaced two engine bugs precisely because the reading was adversarial — per-process CPU that contradicted the system-wide figure, and a `hostname` that disagreed with the `machine_id`. An analysis that repeats the report back without sanity-checking it is decorative. Always ask:

- Do the per-app CPU figures sum to something compatible with the system-wide average, or do they contradict it?
- Is peak RSS per app plausible against total RAM, and does the sum overshoot the machine?
- Does the verdict follow from the findings, or is a `healthy` hiding a saturated resource the rules don't cover?
- **What is resident but idle** — memory or VRAM held by something the machine's role never actually uses? This is the highest-value finding the ledger produces.
- Is the real constraint the one the verdict names? (VRAM was the binding constraint on the dry run, not RAM or CPU.)

Flag anything that looks like an engine bug rather than a machine problem, and say which you think it is.

### 4. Append this run to the ledger

```
E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/fleet-health/ledger.py append --date <RUN_DATE> --file <path to the markdown you wrote>
```

Write one entry per machine — **including the not-covered ones** — newest run on top, in exactly this structure:

```markdown
### <machine-id> — <YYYY-MM-DD>

**Findings** — what the data says: envelope, per-app load, and what changed vs the last run
**Annex** — `runs/<YYYY-MM-DD>/<machine>.{json,md}`
**Recommendation** — concrete and actionable; if it was already open last run, say so explicitly
```

A not-covered machine gets the same three headings, with **Findings** stating the reason it could not be measured and **Annex** reading `— (not covered)`.

Keep entries diffable: same headings, same order, no prose drift between runs.

### 5. Report out

Post the digest to Telegram as the caption of the ledger file — **activity-log** traffic, so `--category log` (the helper resolves `coding log` from `hooks/projects.toml`; never hardcode a chat id):

```
cat <<'EOF' | E:/automation/fleet-config/.venv/Scripts/python.exe hooks/notify_send.py --category log \
   --file <absolute LEDGER path from step 2> \
   --title "Fleet health — <YYYY-MM-DD>"
🩺 Fleet health checkup — <YYYY-MM-DD> · <N> captured, <M> not covered

<one line per machine: id · verdict · the single most interesting change>
EOF
```

The helper never raises; a missing token just logs and exits non-zero.

Then publish the run as a **private Artifact** so it reads well on a phone. **Best effort** — unproven from a headless run, so a failure here is a logged warning, never a failed run. Never publish results anywhere public: the ledger is a hardware inventory of the user's own machines.

### 6. Report

Print: how many machines were captured, how many not covered and why, each machine's verdict, the headline change vs last run, and the Telegram result. A few lines.

## Notes

- **Why each machine's own hub, not one central capture:** the diagnostics engine is deliberately local-only — `local-llm-hub`'s `docs/diagnostics.md` states triggering a capture on a peer is not offered, each host's hub owns its own sampler. No `machine_id` parameter on any route to abuse.
- **Why addresses come from `models.yaml`:** the inventory API returns no LAN address, so a peer's hub cannot be dialled from the inventory alone. `capture.py` resolves `id → address` from the hub's own `hosts:` block, keyed by the ids the inventory already returned — still zero hardcoded names. If that file moves, set `FLEET_HEALTH_MODELS_YAML`.
- **Why one hour at 30 s, not the dry run's two hours:** a weekly unattended job wants the idle-resident picture (what is *always* loaded), which settles well inside an hour. Override per-run when investigating something specific.
- **Known coverage gaps**, reported as not-covered rather than hidden: a peer hub that answers but 404s on the diagnostics API is on an older build and needs a host sync (engine landed in `ferraroroberto/local-llm-hub#315`); an SSH-only machine with no hub at all needs the portable sampler from `ferraroroberto/local-llm-hub#316`.
- **A `healthy` verdict on a lightly-attributed platform means "unmeasured", not "fine".** The rules lean on app attribution and listening ports; where those are thin, the verdict is weak evidence. Say so in the entry rather than reporting a clean bill of health — the first cross-platform run found macOS at 99% unattributed with zero ports collected.
- **This skill reports; a human decides.** It never acts on its own recommendations — no killing processes, no uninstalling, no config changes.

## Wiring the weekly schedule

Add an **app-launcher Jobs** entry (Windows Task Scheduler under `\AppLauncher\`) that runs weekly, overnight. Target `.claude/skills/fleet-health/run-weekly.bat`; it preserves `/fleet-health` plus bypass permissions and streams filtered milestones through `claude_progress.py`. cwd = `E:/automation/fleet-config`. Same executor as every other scheduled job (`/system-map`, `/audit-fleet`).
