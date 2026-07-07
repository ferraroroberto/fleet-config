# Project Instructions

This repo is the versioned home for user-scope coding-agent configuration. After `install.ps1`, the contents of `hooks/` are visible at `~/.claude/hooks/` (and vice-versa) via a Windows junction — no copy step, no sync ritual.

The global instructions (plan-mode default, git discipline, no AI attribution) are this repo's own `global-CLAUDE.md` and apply here in full — below is only what's specific to this repo.

## Repo-specific conventions

- **Hooks are user-scope, fleet-wide.** Don't write a hook tuned to a single project's quirk — put the quirk in `hooks/projects.toml` (project keys detected by `cwd` prefix) and keep the hook code generic.
- **Hooks are wired into `~/.claude/settings.json` via the shared `run-hook.ps1` shim** dispatching to the named Python module (the user's system Python, not a `.venv`). The shim path uses **forward slashes** — `C:/Users/rober/.claude/hooks/run-hook.ps1` — because Claude Code routes hook commands through Git Bash, which strips backslashes. Never write backslashes into a `settings.json` command string.
- **Always the absolute Windows PowerShell 5.1 path** in `settings.json` commands: `C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe` (`pwsh` on PATH is a 0-byte WindowsApps stub that fails non-interactively).
- **Hooks block by exit-code 2** with a single short reason on stderr; non-blocking hooks print a single nudge line on stdout and exit 0.
- **Hooks read stdin as JSON** via `_lib.stdin_json()`; PowerShell shims use `[Console]::In.ReadToEnd()` and pipe straight to the Python module.
- **Scheduled-skill launchers live *with* the skill:** `skills/<skill>/run-weekly.bat`, never at the repo root. Body is exactly `cd /d <working-dir>` then `claude -p "/<skill>" [flags] --permission-mode bypassPermissions` — working dir is the skill's repo (`cd /d E:\automation\fleet-config`), or `E:\automation` for genuinely fleet-wide skills (`/audit-fleet`); `bypassPermissions` because a scheduled run has no human to answer prompts; add `--model`/`--effort`/`--verbose` only when needed. Sister repos follow the same shape in their own tree (e.g. life-os `.claude/skills/_recap/run-weekly.bat`). The app-launcher Job's `script_path` points at this file; live `jobs.json` is machine-local, `jobs.sample.json` carries the committed example.

## Internal architecture

[`docs/architecture.mmd`](docs/architecture.mmd) is a hand-authored Mermaid diagram of this repo's internal structure (hooks/, the two skill tiers, `architecture/`, `tests/`). Update it in the **same PR** as any material structural change — same anti-staleness contract as a `.fleet.toml` `description`. Not auto-generated, not covered by `tests/run_acceptance.py`.

## Adding a new fleet project

New repo under `E:/automation/` → **always** add a minimal entry to `hooks/projects.toml` before the `[global]` block:

```toml
[my-new-project]
cwd_prefix = "E:/automation/my-new-project"
```

Required for `notify_on_idle` to name the right project in Slack pings (else it falls back to `[claude]`). Add port/gate/tray fields only if the project has a tray app or verification gate.

## Verification

```powershell
# 1. Byte-compile every hook and shared skill helper
& C:/Users/rober/AppData/Local/Python/bin/python.exe -m py_compile hooks/*.py skills/_lib/*.py

# 2. Run the acceptance matrix (sample stdin payloads per hook + the helper unit tests)
& C:/Users/rober/AppData/Local/Python/bin/python.exe tests/run_acceptance.py
```

Invoke the resolved Python path directly — a bare `py`/`python` is not reliably on `PATH` on this machine, so it silently fails wherever a skill or doc uses it (fleet-config#256). Don't claim a hook works without driving it through `tests/run_acceptance.py`.

## Git

Global git discipline applies (never auto-commit/push/stage unasked; prepare a ready-to-copy conventional commit message). Repo nuance: the very first hook here blocks AI attribution trailers, so you'd trip your own wire.

```bash
git add <files>
git commit -m "type: short description

- detail 1
- detail 2"
```

---

See `README.md` for install, layout, and the Tier 1 hook list.
