# Telling a quiet lane from a hung one (fleet-config#638)

Loaded on demand from the `/chief` skill (`SKILL.md`, same section name). Point
numbers like "dispatch brief point 2" refer to that file's standard dispatch brief.

A job log that stops growing is the most misread signal on a long unattended
run — one lane once went silent for 19 minutes and, from outside the process,
was indistinguishable from a hang. Both wrong calls cost: a false stall
triggers intervention in a run that halts on residue, a missed one wastes
hours of an unattended night. Work these in order — cheap deterministic
checks first, the process probe last.

1. **Read the clock correctly before measuring any silence.** The `[h:mm:ss]`
   prefix in `E:\automation\app-launcher\webapp\jobs\<job>\<run_id>\output.log`
   is **elapsed since the run started**, not wall-clock
   (`claude_progress.py:282`, off `time.monotonic`). A last line reading
   `[06:08]` says nothing about the time of day. The `<run_id>` directory is a
   `YYYYMMDDTHHMMSS` wall-clock start stamp, so a line's real time is
   `run_id + elapsed`; the log file's mtime against now is the true silence.
2. **Silence shorter than 45 minutes is not a stall, by construction.**
   `claude_progress.py` runs a watchdog (`DEFAULT_STALL_TIMEOUT_SECONDS`,
   2700s) that kills a run whose stream has genuinely gone quiet, emits `⏱ no
   stream activity for …` into the log, and exits `124`. A permanently hung
   lane is therefore not a failure mode you have to catch by hand — had it
   truly stalled, the adapter would have ended it and said so. (Overridable
   per-run via `--stall-timeout` or `CLAUDE_PROGRESS_STALL_TIMEOUT`; `0`
   disables it, so confirm the bound holds before leaning on it.)
3. **The normal shape of a long silence is one slow tool call.** An agent
   running a repo's verification gate sits inside a single call for several
   minutes and emits no milestone until it returns. Suspect that before
   suspecting a hang — the same reflex as "suspect buffering before a hang"
   (dispatch brief point 2), one level up.
4. **For positive proof, sample the right process.** The work happens in the
   `claude.exe` **child** of `claude_progress.py`. Sample its CPU twice, ~20s
   apart; a rising counter means the lane is working:

   ```powershell
   Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq <adapter-pid> }
   # claude.exe CPU 287.78 -> 288.06 across 20s => working, not hung
   ```

   **The adapter's own idleness proves nothing.** `claude_progress.py` sits
   idle by design between milestone boundaries, so an idle adapter is the
   normal resting state of a healthy lane — the intuitive check is the
   misleading one.
