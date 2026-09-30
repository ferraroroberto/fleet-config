# Dead-code start beacon

Static analysis can't tell whether a script that only a human, a desktop shortcut or a Stream Deck button starts is still used. The Step 1 inventory (`skills/_lib/entry_inventory.py`, fleet-config#961) left 170 of `automation`'s 191 files `unknown` for that reason. The start beacon (`skills/_lib/start_beacon.py`, fleet-config#1114) records which scripts actually run, so a 90-day window can move them to `live` or `cold`. It is report-only: nothing is deleted on its evidence.

## How it works

One file, `fleet_start_beacon.pth`, goes into the target repo's `.venv` site-packages. Python's `site` runs a `.pth` line that starts with `import` at every interpreter start. This covers `python.exe` and `pythonw.exe` from `.venv\Scripts`, whether a script, `-m` or `-c` is run. The line appends one record to a machine-local ledger:

```
<UTC timestamp>\t<repo>\t<abspath of the script | -m <module> | -c | empty>
```

- **The ledger:** `~/.claude/hooks/state/dead_code/<repo>/starts.tsv`, which is gitignored and never committed. `install` adds a `# installed <utc>` header and `uninstall` adds `# uninstalled <utc>`.
- **It never breaks the host.** Everything runs inside `try`. A failure is written once to `%TEMP%\fleet_start_beacon.<repo>.error` (created only when absent), never to the host's stdout or stderr. `tests/test_start_beacon.py` proves an unwritable ledger leaves the host's exit code and output untouched.
- **One record per process.** On Windows with Python 3.14, `site` processes a venv's site-packages twice, so a guard attribute on `sys` stops a second record. Child interpreters still record their own start.
- **It is self-contained.** The `.pth` embeds the code, so it imports nothing from fleet-config and keeps working whatever branch this repo's checkout is on. Re-run `install` after changing the beacon, and `status` reports `CURRENT=False` until you do.
- **No network, no personal data:** only the script path is recorded.

## Probe (2026-09-30, Python 3.14.3, Windows 11)

- **It fires on every start kind:** the `.pth` line ran for `python.exe -c`, `pythonw.exe -c`, `python.exe <script>` and `pythonw.exe -m json.tool`, all with exit 0 and empty stderr. At `site` time, `sys.argv[0]` is the script path, `-c` or `-m`, and `sys.orig_argv` carries the module name.
- **It runs twice per process:** an unguarded `.pth` line fired twice in one PID, which is why the guard exists.
- **Its cost:** interleaved A/B, 30 starts each, three rounds, `python.exe -c pass`. The median went from 41.7-43.2 ms without the beacon to 44.1-45.7 ms with it, a difference of +2.4 to +2.9 ms. Every started process wrote exactly one ledger line.

## Install, check, remove

```powershell
# install: adds the one .pth file (idempotent; refuses when the venv is missing, never creates one)
& E:/automation/fleet-config/.venv/Scripts/python.exe E:/automation/fleet-config/skills/_lib/start_beacon.py install E:/automation/automation
# status: installed / current / ledger headers / last hit / a logged failure
& E:/automation/fleet-config/.venv/Scripts/python.exe E:/automation/fleet-config/skills/_lib/start_beacon.py status E:/automation/automation
# remove: deletes the .pth and records the uninstall header; the ledger stays for the report
& E:/automation/fleet-config/.venv/Scripts/python.exe E:/automation/fleet-config/skills/_lib/start_beacon.py uninstall E:/automation/automation
```

Deleting `E:\automation\automation\.venv\Lib\site-packages\fleet_start_beacon.pth` by hand also removes it. Without the uninstall header, the inventory reads that as `removed` and keeps no-hit entries `unknown`. The `.venv` itself is never created, deleted or rebuilt, and nothing in the target repo's git tree changes. Recreating the venv drops the `.pth` the same way.

Pilot: `automation` only. `/codebase-audit` wiring waits until a full 90-day window has run (#961's verdict).

## Reading it in the inventory

`entry_inventory.py collect <repo>` summarises the ledger into the evidence snapshot: the last hit per repo script, plus counts of distinct targets outside the repo, of `-c`/interactive targets, and of malformed lines. `verdict` then applies these rules to Python entry points:

- **A hit inside the window:** `live` (`last_seen_source: beacon`), and the modules it imports inherit `live`.
- **No hit (or only an older one) while the beacon covered the whole window:** `cold`, `reason: beacon-no-hit`. Covering the whole window means it was installed by the cutoff, and is still installed or was uninstalled no earlier than the as-of date.
- **The beacon is younger than the window:** `unknown`, `beacon-young`.
- **The beacon is uninstalled early, its `.pth` is gone, or its ledger can't be read:** `unknown`, `beacon-inactive`. None of these missing-evidence states is ever folded into `cold`.

The beacon doesn't see launchers (`.bat`/`.ps1`), so they keep their Step 1 reasons.

**Blind spots**, which read as no evidence rather than as a clean bill:

- `python -S`;
- a script run by an interpreter outside this `.venv` (for example a bare `python` or `py` in a launcher);
- concurrent appends that interleave on Windows, which are counted as `malformed` and skipped.

A `cold` entry is a candidate for a human to check, never a deletion.
