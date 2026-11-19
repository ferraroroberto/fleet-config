# Tray restart (`/issue-finish` step 6, project runs a tray)

Loaded on demand from `/issue-finish` step 6 when the project's `CLAUDE.md` describes a tray or long-running local process. Follow it, then return to step 6b.

- **Prefer the deterministic restart.** If the project ships a `tray.bat` with
  a `--restart` flag (canonical orphan-proof reclaim-then-start — every fleet
  tray has one), run **`tray.bat --restart`** and nothing else: subtree kill +
  per-`.venv` port reclaim + start atomically. **Do not** hand-roll a
  `Get-NetTCPConnection`/`taskkill` kill — it catches only the listener it
  finds, misses the orphan the reclaim sweep exists to kill, then re-runs a
  start-only script.
- **Invoke it through a real Windows shell — never Git Bash's nested `cmd /c`.**
  Run via the harness PowerShell tool, or
  `C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -Command "& '<repo>/tray.bat' --restart"`
  (forward-slash exe path, per the Git-Bash-strips-backslashes rule). Via the
  Bash tool as `cmd /c "tray.bat --restart"`, Git Bash/MSYS rewrites `/c` to
  `C:/`; `cmd.exe` opens an interactive prompt and the batch never runs — only
  the `cmd` banner, no `Stopping previous…` echoes, old build untouched. Fire
  the PowerShell invocation non-blocking (the tray holds its console — a
  foreground launch never returns), then move to the bounded poll below.
- **Inside a scheduled run (`FLEET_SCHEDULED_RUN=1`), run the recipe through
  `detached_restart.py`.** The runner owns every descendant in a kill-on-close
  job: a tray restarted plainly is counted (the run ends 118) and dies with the
  run. The helper starts the recipe outside that job and runs it to completion in
  the foreground, passing its output and exit code through:
  `E:/automation/fleet-config/.venv/Scripts/python.exe E:/automation/fleet-config/skills/_lib/scheduled/detached_restart.py --cwd <repo> -- C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -Command "& './tray.bat' --restart"`.
  It prints `BREAKAWAY=yes`; `BREAKAWAY=no` means the job refused, the app will not
  outlive the run, and that is reported as not confirmed. Exit 124 = still running
  at the timeout, also not confirmed. Interactively, run the recipe directly.
- **Safety caveat — linked children.** `tray.bat --restart` does a `/T` subtree
  kill, safe only for a tray whose linked-but-independent children (a
  session-host + its PTY-backed shells) are spawned **detached** and
  re-adopted on start (scaffold `docs/windows-tray.md`). Read the target
  repo's `CLAUDE.md` to know which case — don't assume by app name. A tray
  declaring its linked children detach-compliant (e.g. `app-launcher`, per
  `project-scaffolding#35`: its `:8446` session-host is spawned via `cmd /c
  start` and re-adopted, so `--restart` preserves open Coding sessions, safe
  even from inside one) is fine to restart. A tray still hosting such children
  *in its subtree* — or silent on the point — is unsafe: `--restart` would kill
  the user's open Coding sessions (and yours, if inside one) — **confirm with
  the user first**, or use its non-destructive path (kill only the webapp
  port, let the tray re-adopt).
- **Fallback only** for a project with no `--restart`: kill **only** the
  specific process listening on the project's port (`Get-NetTCPConnection
  -LocalPort <port>`, stop that PID — **never** a blanket `python`/`pythonw`
  kill), then relaunch via its start script.
- Confirm the new build is live with a **bounded** poll of the project's
  version endpoint (e.g. `GET /api/version`): a **hard timeout + attempt cap**
  (≤30 s / fixed attempts), then **fail loud** — never an open-ended wait. The
  git SHA must match `HEAD` (a `/healthz` 200 is not enough — a stale process
  passes it) and the asset hash should have changed. Report that build line.
- **On a `git_sha` ≠ `HEAD` mismatch (a silent adopt-stale), stop and surface it
  to the user — do not improvise process kills.** A by-hand `taskkill`/
  `Get-NetTCPConnection` kill during recovery is what the safe-restart rules
  above warn against, and a mistimed single-PID kill can take the server fully
  down. Robust reclaim is the tray's job (`project-scaffolding#54`); the
  finisher's contract is to invoke it correctly and **report** a mismatch, not
  hand-fix it.
