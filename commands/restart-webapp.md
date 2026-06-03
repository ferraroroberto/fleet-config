---
description: "Safely restart the current project's webapp and verify the new build is live."
---

# /restart-webapp

Restart the webapp for the **current project** (based on `cwd`), then confirm the new build is serving.

## What this does

This invokes `~/.claude/hooks/restart_and_verify_webapp.py`, which:

1. Detects the project from the current working directory (looks it up in `~/.claude/hooks/projects.toml`).
2. Reads the project's `webapp_port`, `restart_cmd`, and `tray_cmd` from `projects.toml`.
3. Kills **only** the PID listening on `webapp_port` — never blanket-kills `python.exe`. Sister hubs on `[global].never_kill_ports` (8000 LLM hub, 8090 whisper, 8446 session-host) are never touched.
4. Brings the webapp back. For the **tray-owned apps** (app-launcher, photo-ocr, voice-transcriber) the webapp is a uvicorn *child* of a long-lived `launcher.py tray`, and an idempotent `tray.bat` no-ops while that tray lives — so those projects declare a `restart_cmd` that respawns a fresh uvicorn through the app's `WebappManager` (which the tray adopts), never touching the `:8446` session-host. A just-killed port can linger in `TIME_WAIT`, so the respawn settles and retries. Projects without a `restart_cmd` fall back to running `tray_cmd` (`tray.bat`).
5. Polls `<api_version_path>` (default `/api/version`) until `git_sha` matches `git rev-parse HEAD`.
6. Reports the new `asset_hash` so I know the build I'm looking at is the one I just shipped. On an unrecoverable failure it prints the manual-recovery commands instead of a bare `nothing listening`.

## How to invoke it

Run this from the project root (so `cwd` resolves to the right project in `projects.toml`):

```powershell
& py "C:/Users/rober/.claude/hooks/restart_and_verify_webapp.py"
```

Report the script's output verbatim — the user wants to see the build line (`git_sha=... asset_hash=...`) before considering the restart done. If the script exits non-zero, surface the stderr and stop.

## When NOT to use this

- During a long debug loop where the user is iterating on backend code and watching logs — restarting yanks their session.
- For projects that aren't registered in `projects.toml` — add them first (or use the manual ritual for one-off projects).
