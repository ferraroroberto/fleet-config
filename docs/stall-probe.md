# Host stall probe

Whole-box stalls on the main fleet machine (7-25 s, every local process stops answering at once, then recovers) turn whichever gate is running red and freeze every live app (fleet-config#1106). `skills/_lib/stall_probe.py` is the always-on probe that catches the next one with timestamps and the machine's state, so the cause can be named from evidence. It is report-only: it never changes a machine setting.

## What it measures

- **Scheduler leg:** the main loop times `sleep(0.1)`. A wake more than the threshold (1 s) late is logged as `kind: sleep` (every process was starved).
- **Network leg:** a thread GETs an in-memory static file from the probe's own loopback HTTP server once a second. A request slower than the threshold is `kind: http`; a failed one is `kind: http-error`.
- **Suspend:** a machine sleep stalls both legs, because `time.monotonic()` runs through it, but it is not a stall (fleet-config#1123). Both legs also read `QueryUnbiasedInterruptTime`, which stops while the machine sleeps; the difference across the gap is how much of it was sleep (`suspended_s`). A gap more than half sleep, and at least the threshold, is logged as `kind: suspend` (`seen_by` names the leg) and is left out of the stall count. A gap that was mostly awake stays a stall, with its `suspended_s` visible. If the unbiased clock can't be read, `suspended_s` is `null` and the gap stays a stall: unknown is never read as asleep or as awake.

## What it records

Everything lives under `~/.claude/hooks/state/stall-probe/` (machine-local, never committed):

- `stalls.jsonl`: one line per stall or suspend, with `kind`, `start_utc`, `end_utc`, `gap_s`, `suspended_s` and `threshold_s`, all in UTC per the three-clocks rule. It rotates once to `stalls.jsonl.1` at 5 MB.
- `evidence`: captured on its own thread, at most once per 30 s, just after the stall. It holds:
  - PDH counters by English name: commit, available memory, hard-fault page reads, CPU, disk latency and queue, the Memory Compression and VmmemWSL working sets, and established TCPv4 connections;
  - `GlobalMemoryStatusEx`;
  - TCP state counts from one `netstat`, which rules ephemeral-port exhaustion (fleet-config#440) in or out;
  - the app-launcher's running jobs;
  - scheduled tasks and Defender scans that ran in the last two minutes, from one PowerShell call.

  A source that fails records its error instead of a value. A stall inside the cooldown is logged with `evidence: skipped …`.
- `power` (suspend records only, instead of `evidence`): the System log's sleep and resume events around the gap, read about 8 s after the wake because Power-Troubleshooter logs it a few seconds late. It holds `sleep_reason` (Kernel-Power 42, e.g. `Battery` for a critical-battery trigger), `wake_source` (Power-Troubleshooter 1, e.g. a scheduled task's wake timer) and the raw `events` (Kernel-Power 42, 107, 187, 524; Power-Troubleshooter 1; the Kernel-General 1 clock correction), each with provider, id, time and message. A field no event carries is `null`, and an empty `events` list means the log held none: a sleep nobody asked for stays visible rather than hidden. A suspend skips the heavy `evidence` capture: it would only show the wake-up catch-up, and it would spend the 30 s cooldown that a real stall right after wake needs.
- `status.json`: a heartbeat every 5 min (`pid`, `heartbeat_utc`, `started_utc`, `stalls_logged`, `suspends_logged`). A stale heartbeat means the probe is not running, so a quiet log is not evidence of a quiet box.
- `probe.lock`: one probe per state dir. A second `run` prints `ALREADY_RUNNING` and exits 2.

## Commands

```powershell
& E:/automation/fleet-config/.venv/Scripts/python.exe E:/automation/fleet-config/skills/_lib/stall_probe.py start    # background, no window
& E:/automation/fleet-config/.venv/Scripts/python.exe E:/automation/fleet-config/skills/_lib/stall_probe.py status   # heartbeat + stall-line count
& E:/automation/fleet-config/.venv/Scripts/python.exe E:/automation/fleet-config/skills/_lib/stall_probe.py run      # foreground (debugging)
```

`start` launches `pythonw.exe` detached from the starting session's job where the job allows it, so the probe outlives an agent session. It is idempotent: when a probe already holds `probe.lock` it prints `ALREADY_RUNNING` and spawns nothing. It does not survive a reboot on its own; see the next section.

## Starting it at logon

`install-logon` drops `FleetStallProbe.bat` into the current user's Startup folder (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`). It is a plain file write in the user's own profile: no admin rights, no Task Scheduler (the same mechanism as app-launcher's `src/boot_autostart.py`; app-launcher's own `apps.json` autostart launches only tray-kind rows, so it can't start this). The wrapper runs this repo's `.venv` Python with `stall_probe.py start` and appends the result to `stall-probe/startup.log`, so a logon launch that fails is diagnosable.

```powershell
& E:/automation/fleet-config/.venv/Scripts/python.exe E:/automation/fleet-config/skills/_lib/stall_probe.py install-logon
& E:/automation/fleet-config/.venv/Scripts/python.exe E:/automation/fleet-config/skills/_lib/stall_probe.py uninstall-logon
```

Run `install-logon` from the primary checkout (the wrapper records the paths of the checkout that ran it). Outcomes: `INSTALLED`, `UPDATED` (a stale wrapper was rewritten), `ALREADY_INSTALLED` and `WRITE_FAILED` (exit 1); each write is read back. `uninstall-logon` prints `UNINSTALLED`, `NOT_INSTALLED` or `REMOVE_FAILED`. Neither touches a probe that is already running.

## Reading a stall

`status` prints `stall_lines` (stalls only) and `suspend_lines` apart. A suspend is not a freeze: the box was asleep, and the question is why (read `power`). The wall clock stands still during a sleep and is corrected by a hardware-clock sync minutes after the wake, so a suspend's `start_utc` and `end_utc` can be off by up to the gap: trust `gap_s` and `suspended_s`, and take real times from the `power` events. A wake can also arrive in two steps (a 13 s gap, then a 97 s one) within one sleep, which gives two suspend lines.

Correlate `start_utc` with job logs only after converting them to UTC. Sleep and http records within the same second or two are one freeze. The evidence is sampled *after* the stall, so it shows what the machine looked like on the way out: commit near its limit, a large compression working set, or a burst of hard faults point at memory pressure. A matching scheduled task or Defender scan in `recent_activity` points at that task. Thousands of `TIME_WAIT` point at port exhaustion. Machine-level remedies (WSL memory cap, TCP or port-range settings) are Roberto's call and are never applied by an agent.
