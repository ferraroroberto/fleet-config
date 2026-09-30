# Host stall probe

Whole-box stalls on the main fleet machine (7-25 s, every local process stops answering at once, then recovers) turn whichever gate is running red and freeze every live app (fleet-config#1106). `skills/_lib/stall_probe.py` is the always-on probe that catches the next one with timestamps and the machine's state, so the cause can be named from evidence. It is report-only: it never changes a machine setting.

## What it measures

- **Scheduler leg:** the main loop times `sleep(0.1)`. A wake more than the threshold (1 s) late is logged as `kind: sleep` (every process was starved).
- **Network leg:** a thread GETs an in-memory static file from the probe's own loopback HTTP server once a second. A request slower than the threshold is `kind: http`; a failed one is `kind: http-error`.

## What it records

Everything lives under `~/.claude/hooks/state/stall-probe/` (machine-local, never committed):

- `stalls.jsonl`: one line per stall, with `kind`, `start_utc`, `end_utc`, `gap_s` and `threshold_s`, all in UTC per the three-clocks rule. It rotates once to `stalls.jsonl.1` at 5 MB.
- `evidence`: captured on its own thread, at most once per 30 s, just after the stall. It holds:
  - PDH counters by English name: commit, available memory, hard-fault page reads, CPU, disk latency and queue, the Memory Compression and VmmemWSL working sets, and established TCPv4 connections;
  - `GlobalMemoryStatusEx`;
  - TCP state counts from one `netstat`, which rules ephemeral-port exhaustion (fleet-config#440) in or out;
  - the app-launcher's running jobs;
  - scheduled tasks and Defender scans that ran in the last two minutes, from one PowerShell call.

  A source that fails records its error instead of a value. A stall inside the cooldown is logged with `evidence: skipped …`.
- `status.json`: a heartbeat every 5 min (`pid`, `heartbeat_utc`, `started_utc`, `stalls_logged`). A stale heartbeat means the probe is not running, so a quiet log is not evidence of a quiet box.
- `probe.lock`: one probe per state dir. A second `run` prints `ALREADY_RUNNING` and exits 2.

## Commands

```powershell
& E:/automation/fleet-config/.venv/Scripts/python.exe E:/automation/fleet-config/skills/_lib/stall_probe.py start    # background, no window
& E:/automation/fleet-config/.venv/Scripts/python.exe E:/automation/fleet-config/skills/_lib/stall_probe.py status   # heartbeat + stall-line count
& E:/automation/fleet-config/.venv/Scripts/python.exe E:/automation/fleet-config/skills/_lib/stall_probe.py run      # foreground (debugging)
```

`start` launches `pythonw.exe` detached from the starting session's job where the job allows it, so the probe outlives an agent session. It does not survive a reboot. Starting it at logon (an app-launcher `apps.json` row with `autostart`, or a Task Scheduler logon task) is a machine-level choice for Roberto, not something an agent sets up.

## Reading a stall

Correlate `start_utc` with job logs only after converting them to UTC. Sleep and http records within the same second or two are one freeze. The evidence is sampled *after* the stall, so it shows what the machine looked like on the way out: commit near its limit, a large compression working set, or a burst of hard faults point at memory pressure. A matching scheduled task or Defender scan in `recent_activity` points at that task. Thousands of `TIME_WAIT` point at port exhaustion. Machine-level remedies (WSL memory cap, TCP or port-range settings) are Roberto's call and are never applied by an agent.
