# Project Instructions

This repo is the versioned home for user-scope Claude Code configuration. After `install.ps1`, the contents of `hooks/` are visible at `~/.claude/hooks/` (and vice-versa) via a Windows junction — there is no copy step, no sync ritual.

## Plan mode is the default

Every non-trivial request starts in plan mode. Non-trivial = anything beyond a one-line fix, a typo, or a question I can answer without touching code.

In plan mode:
- Do NOT edit files, run destructive commands, or commit anything
- Investigate the codebase as needed (read files, search, run read-only commands)
- Resolve ambiguity through questions before proposing a plan
- Present the plan only when you're confident it reflects what I actually want

Exit plan mode only after I explicitly approve.

## Repo-specific conventions

- **Hooks are user-scope, fleet-wide.** Don't write a hook tuned to a single project's quirk — put the quirk in `hooks/projects.toml` and keep the hook code generic. Project keys in `projects.toml` are detected by `cwd` prefix.
- **Hooks are wired into `~/.claude/settings.json` via `.ps1` shims** that call into Python (the user's system Python, not a `.venv`). The `.ps1` shim path uses **forward slashes** — `C:/Users/rober/.claude/hooks/<name>.ps1` — because Claude Code on this Windows machine routes hook commands through Git Bash, which strips backslashes. Never write Windows-style backslashes into a `settings.json` command string.
- **Always use the absolute Windows PowerShell 5.1 path** in `settings.json` commands: `C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe`. The default `pwsh` on PATH is a 0-byte WindowsApps reparse stub that fails non-interactively.
- **Hooks block by exit-code 2** with a single short reason on stderr. Non-blocking hooks print a single nudge line on stdout and exit 0.
- **Hooks read stdin as JSON** via `_lib.stdin_json()`. PowerShell shims use `[Console]::In.ReadToEnd()` (per the global gotcha) and pipe straight to the Python module.

## Adding a new fleet project

When a new repo is created under `E:/automation/`, **always** add a minimal entry to `hooks/projects.toml` before the `[global]` block:

```toml
[my-new-project]
cwd_prefix = "E:/automation/my-new-project"
```

This is required for `notify_on_idle` to show the correct project name in Slack pings. Without it the hook falls back to `[claude]`, making it impossible to tell which project needs attention. Add port/gate/tray fields only if the project has a tray app or a verification gate.

## Verification

```powershell
# 1. Byte-compile every hook and shared skill helper
& py -m py_compile hooks/*.py skills/_lib/*.py

# 2. Run the acceptance matrix (drives each hook with a sample stdin payload;
#    the final case runs the audit-issue helper's pure-logic unit tests)
& py tests/run_acceptance.py
```

If a hook regresses, the matrix fails loudly. Don't claim a hook works without driving it through `tests/run_acceptance.py`.

## Git

Never auto-commit or push, never stage files without being asked. When a task is done, prepare a relevant commit message ready to copy. Never add `Co-Authored-By: Claude` (or any other AI attribution trailer) — the very first hook in this repo blocks that anyway, so you'll trip your own wire.

```bash
git add <files>
git commit -m "type: short description

- detail 1
- detail 2"
```

## Senior-dev check

Before finishing, ask: "What would a senior, perfectionist dev reject in review?" If the answer points at duplicated state, inconsistent patterns, or broken architecture *within the file you're already editing*, fix it. Don't expand scope to unrelated files.

---

See `README.md` for install, layout, and the Tier 1 hook list.
