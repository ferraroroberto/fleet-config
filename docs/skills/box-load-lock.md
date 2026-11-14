# Box load lock (burn proofs never overlap a gate)

A red-under-load proof runs CPU burners across the whole machine. Below-normal priority keeps scheduled jobs and live sessions responsive, but it does not protect another lane's browser e2e gate: the burn still starves the dev server and the browser enough to break 15-second page-load timeouts, so an unrelated gate goes red on tests that pass alone. Burns and gates therefore never overlap.

`skills/_lib/box_lock.py` is a box-wide advisory lock with two modes. A gate takes it **shared**: any number of gates run together. A burn takes it **exclusive**: it waits while any gate holds the lock, and a gate that starts during a burn waits for the burn to finish.

## How it works

- The lock is the directory `box-load-lock/` under the hooks-state root (`~/.claude/hooks/state/`, or `CLAUDE_HOOKS_STATE_DIR`). Each holder writes one marker, `<gate|burn>.<pid>.<token>.json`, holding pid, repo, mode and start time. It is machine-local and never committed.
- Mode and pid are in the filename, so who holds the lock is decided from a directory listing, never from reading a file another process may be writing.
- An acquirer writes its own marker first and then looks for a conflicting holder. If it finds one it removes its marker and retries after a jittered pause, so two racing acquirers can both back off but never both proceed.
- A marker whose pid is confirmed gone is reclaimed on the next look, so a crashed or hard-killed burn or gate frees the lock by itself.
- **Unknown is held.** A pid the probe cannot settle, or a lock directory that cannot be listed, counts as a holder. It is never treated as free.
- **The queue is fair.** An acquirer that finds the lock busy takes a ticket, `wait.<gate|burn>.<pid>.<ns>.<token>.json`, and keeps it until it holds the lock or gives up. Everyone defers to an older ticket whose mode conflicts with theirs, and an acquirer with no ticket counts as newest. So a holder that releases and at once asks again, as the perf sweep does between apps, finds the waiting gate ahead of it: the gate takes the gap, and the sweep's next app waits for that gate. A ticket whose pid is confirmed gone is reclaimed like a dead holder; an unsettled pid keeps its place.
- **Waiting is bounded** (30 minutes by default). On timeout the caller gets the holders named, never an indefinite hang.
- **A timeout is its own state.** The lock staying busy says nothing about the command it guarded, so every caller reports it as `UNKNOWN box_load_lock` with exit 3 and the command never runs. It is never a red gate.

## Who takes it

- `tests/_lib/cpu_burn.py` holds it exclusively for the whole burn. If it times out it exits 3 before starting any burner and prints the holders (`--lock-timeout-minutes` sets the wait).
- `tests/run_acceptance.py` holds it shared for the whole run. A timeout ends it with an `UNKNOWN  box_load_lock` line naming the holder and exit 3: no check ran, so nothing failed.
- A gate in another repo wraps its command:

```
E:/automation/fleet-config/.venv/Scripts/python.exe E:/automation/fleet-config/skills/_lib/box_lock.py run --mode shared --repo <name> -- <gate command>
```

`run` exits with the command's own code, or 3 (`UNKNOWN box_load_lock`, the command did not run) when the lock was not free in time. `box_lock.py status` lists the live holders.

## Scheduling rule

The lock is the backstop; the chief's dispatch brief is the schedule. A brief that asks for a burn proof also tells the lane to hold the proof while another lane is gating (see the chief skill's `dispatch-brief.md`).
