# Project Instructions

Versioned home for user-scope coding-agent configuration. After `install.ps1`, `hooks/` is visible at `~/.claude/hooks/` (and vice-versa) via a Windows junction. This repo's own `global-CLAUDE.md` supplies the global instructions and applies here in full; below is only what's repo-specific.

## Repo-specific conventions

- **Hooks are user-scope, fleet-wide.** Don't write a hook tuned to a single project's quirk — put the quirk in `hooks/projects.toml` (project keys detected by `cwd` prefix) and keep the hook code generic.
- **Hooks are wired into `~/.claude/settings.json` via the shared `run-hook.ps1` shim** dispatching to the named Python module through this repo's own project `.venv` (fleet-config#350; the shim falls back to a system Python if the venv is ever absent). The shim path uses **forward slashes** — `C:/Users/rober/.claude/hooks/run-hook.ps1` — because Claude Code routes hook commands through Git Bash, which strips backslashes. Never write backslashes into a `settings.json` command string.
- **Always the absolute Windows PowerShell 5.1 path** in `settings.json` commands: `C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe` (`pwsh` on PATH is a 0-byte WindowsApps stub that fails non-interactively).
- **Hooks block by exit-code 2** with a single short reason on stderr; non-blocking hooks exit 0 with a single short nudge. Never hand-roll either — `_lib.block()` and `_lib.warn()` own the wire format, because both are per-event/per-harness: plain exit-0 stdout only reaches the model on `SessionStart`/`UserPromptSubmit`/`UserPromptExpansion`, so `warn()` emits `hookSpecificOutput.additionalContext` on `PostToolUse`-class events and `systemMessage` on `PreToolUse` (fleet-config#681). A nudge printed to a channel the model never reads is a guard that only reports.
- **Hooks read stdin as JSON** via `_lib.read_stdin_json()`; PowerShell shims use `[Console]::In.ReadToEnd()` and pipe straight to the Python module.
- **Foreign-harness payloads are translated in exactly one place** — `_lib.normalize_payload()`, called from `read_stdin_json()` (fleet-config#491). A hook must never learn a second harness's key names, event vocabulary, or tool ids; if a harness sends something new, extend the maps there. Claude-shaped payloads are returned as the *same object*, and `tests/test_payload_normalization.py` asserts that, because `hooks/` is junctioned live into `~/.claude/hooks` — a merge is fleet-wide against running sessions the moment it lands. Likewise `_lib.block()` owns the per-harness refusal dialect (Claude: exit 2 + stderr; Grok additionally needs a `deny` decision on stdout, because it reports our 2 as 1 and fails **open** on anything else). Adding a guard that only *reports* a block is worse than adding no guard: verify a real refusal live in the target harness. Procedure: `docs/adding-a-coding-harness.md`.
- **Every `subprocess` spawn passes `creationflags=NO_WINDOW`** — `_lib.NO_WINDOW` in `hooks/`, `from no_window import NO_WINDOW` in `skills/_lib/` and `.claude/skills/*` (fleet-config#412). Never re-inline the `sys.platform == "win32"` ternary. This repo's scheduled skills *are* the console-less parent of the global convention, so an unflagged spawn flashes a console window at whoever is at the machine. `tests/run_acceptance.py` parses `hooks/`, `skills/`, and `.claude/skills/` and fails on any spawn missing the flag; `tests/` itself is exempt.
- **Every `git` call goes through `run_git`** — `_lib.run_git` in `hooks/`, `git_run.run_git` (text) / `git_run.run_git_bytes` (byte-exact reads) in `skills/_lib/` and `.claude/skills/*` (fleet-config#677). Never hand-roll `subprocess.run(["git", …])`: the wrapper is where `git_env()`'s `GIT_OPTIONAL_LOCKS=0` lives — the fix for the stranded 0-byte `index.lock` (fleet-config#667) — and a raw spawn opts back out of it invisibly, still exiting 0 with the right answer. Same static gate as the flag above: `tests/run_acceptance.py`'s `git_wrapper` check scans the three runtime trees and fails on any literal `git` argv outside the two wrapper files themselves.
- **Scheduled-skill launchers live *with* the skill:** `skills/<skill>/run-weekly.bat`, never at the repo root. Body is exactly `cd /d <working-dir>` then this repo's `.venv` Python invoking `skills/_lib/claude_progress.py "/<skill>" [flags] --permission-mode bypassPermissions` — the adapter owns `claude -p --output-format stream-json --verbose`, emits flushed human-readable milestones, and preserves Claude's exit code. The working dir is the skill's repo (`cd /d E:\automation\fleet-config`), or `E:\automation` for genuinely fleet-wide skills (`/audit-fleet`); `bypassPermissions` is required because a scheduled run has no human to answer prompts; add `--model`/`--effort` only when needed. Sister repos follow the same shape in their own tree (e.g. life-os `.claude/skills/_recap/run-weekly.bat`). The app-launcher Job's `script_path` points at this file; live `jobs.json` is machine-local, `jobs.sample.json` carries the committed example.

## Internal architecture

[`docs/architecture.mmd`](docs/architecture.mmd) is a hand-authored Mermaid diagram of this repo's internal structure (hooks/, the two skill tiers, `architecture/`, `tests/`). Update it in the **same PR** as any material structural change — same anti-staleness contract as a `.fleet.toml` `description`. Not auto-generated, not covered by `tests/run_acceptance.py`.

## Adding a new fleet project

New repo under `E:/automation/` → **always** add a minimal entry to `hooks/projects.toml` before the `[global]` block:

```toml
[my-new-project]
cwd_prefix = "E:/automation/my-new-project"
```

Required for `notify_on_idle` to name the right project in Slack pings (else it falls back to `[claude]`). `tests/run_acceptance.py`'s `fleet_membership` check enforces this: any repo on disk under `E:/automation/` that is neither declared here nor listed in `[global] architecture_ignore` fails the gate by name (fleet-config#640). Add port/gate/tray fields only if the project has a tray app or verification gate.

**That block is also the fleet-membership list** — `fleet_repos()` reads it (minus `[global] architecture_ignore`), so a new entry expands `/system-map`, `/config-map`, and `/context-audit`'s skill-description cap gate too. Same PR, or `tests/run_acceptance.py` fails:

```powershell
& E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/system-map/build_data.py     # fleet.data.js
& E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/system-map/render_mermaid.py # system-map.mmd + global-CLAUDE.md block
& E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/config-map/build_data.py     # config.data.js
```

Then add the repo's row to `architecture/ARCHITECTURE.md`. A repo that ships its own root `.fleet.toml` also belongs in the residual's `_adopted` registry (so a deleted declaration fails loud); one that doesn't needs a fallback card in `architecture/fleet.residual.json`, or it is in the fleet but absent from the map. A repo that genuinely should stay off the map goes in `[global] architecture_ignore` — a recorded decision, not silence.

## Verification

```powershell
# 1. Byte-compile every hook and shared skill helper (recursive — PowerShell does
#    not glob-expand arguments to a native exe, so `py_compile hooks/*.py` fails)
& E:/automation/fleet-config/.venv/Scripts/python.exe -m compileall -q hooks skills/_lib

# 2. Run the acceptance matrix (sample stdin payloads per hook + the helper unit tests)
& E:/automation/fleet-config/.venv/Scripts/python.exe tests/run_acceptance.py
```

Invoke this repo's `.venv` interpreter directly by its absolute path (as above) — a bare `py`/`python` is not reliably on `PATH` here, so it silently fails wherever a skill or doc uses it (fleet-config#256). Create the venv once with `py -m venv .venv` (or any Python ≥3.12); it is stdlib-only, nothing to install. Don't claim a hook works without driving it through `tests/run_acceptance.py`.

## Git

Global git discipline applies (never auto-commit/push/stage unasked; prepare a ready-to-copy conventional commit message). Repo nuance: the very first hook here blocks AI attribution trailers, so you'd trip your own wire.

---

See `README.md` for install and layout; the reference catalogues live in `docs/` — `docs/hooks.md` (Tier 1 hooks), `docs/skills.md` (both skill tiers + the `_lib/` primitives), `docs/cross-agent-parity.md` (the per-agent matrix).
