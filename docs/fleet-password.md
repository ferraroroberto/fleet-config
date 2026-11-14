# One password for every fleet app

`skills/_lib/fleet_password/cli.py` sets the same sign-in password in every fleet app that carries project-scaffolding's vendored password module (`src/password.py`, manifest key `password`). The owner runs it by hand from a terminal on the PC; no skill or scheduled job calls it.

## Running it

```powershell
# 1. See which apps it would set, without prompting or writing anything
& E:/automation/fleet-config/.venv/Scripts/python.exe E:/automation/fleet-config/skills/_lib/fleet_password/cli.py --dry-run

# 2. Set it: prompts twice, never echoed
& E:/automation/fleet-config/.venv/Scripts/python.exe E:/automation/fleet-config/skills/_lib/fleet_password/cli.py

# Limit a run to some apps
& E:/automation/fleet-config/.venv/Scripts/python.exe E:/automation/fleet-config/skills/_lib/fleet_password/cli.py --only app-launcher,task-os
```

Any non-empty password is accepted, a digits-only PIN or a passphrase alike. An empty value or two entries that differ stop the run before any app is touched (exit 2).

## What it does per app

- **Which apps.** Every repo in the machine-local fleet list (`hooks/projects.toml`) whose `.fleet.toml` `[vendored]` table declares `password`. Linked worktrees are skipped. Apps without that component have no password to set, so they never appear.
- **How it sets.** It runs the app's own setter with the app's own `.venv`: `python -m src.password set --stdin` in the repo root, with the password on stdin. The password never goes in argv, output or a log. An inherited `APP_PASSWORD_FILE` is dropped from the child's environment, so each app writes its own `config/app_password.json`.
- **Proof.** The app's module then checks the password back (`password.check_password`). If the check fails, the previous hash file is restored and the app is reported `FAILED`.
- **Backups.** An existing `config/app_password.json` is copied first to `~/.claude/hooks/state/fleet-password/<UTC stamp>/<repo>/`, outside every repo. It holds only a hash.
- **Output.** One line per app: `OK`, `SKIPPED (<why>)`, `FAILED (<why>)`, or `WOULD-SET (<what changes>)` under `--dry-run`. Exit 0 when nothing failed, 1 when any app failed.

## Restarts and sessions

No app needs a restart: the vendored module reads the hash file on every sign-in. Signed-in browsers and issued tokens are left alone; the vendored module has no session store to revoke. Once an app holds a hash, its old password (`auth_password`, `FOLDER_EXPLORER_PASSWORD`, the task-os owner's config hash) no longer works.

Apps on other machines (a satellite's own checkout) are not reached: run the script there too.
