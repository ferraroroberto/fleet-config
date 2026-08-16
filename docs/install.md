# Install mechanics

The first-run quickstart lives in [`README.md`](../README.md#install). This is
the durable detail: the two link kinds `install.ps1` creates, the other
surfaces this repo ships beside `hooks/` and `skills/`, and the recipes for
migrating an agent home that already holds real files.

## What `install.ps1` links, and how

`install.ps1` exposes the repo's contents inside the supported **agent homes** — `~/.claude` (Claude Code), `~/.agents` (the cross-agent skills location), `~/.codex` ([Codex](https://developers.openai.com/codex/)'s own home), `~/.pi/agent` ([Pi](https://github.com/parallel-web/pi)'s config dir), and `~/.copilot` ([GitHub Copilot CLI](https://docs.github.com/en/copilot/github-copilot-in-the-cli)) — via two link kinds:

- **Junctions** for the directory entries. Cross-volume OK, no admin. `hooks/` and `commands/` are each junctioned into **both** `~/.claude` and `~/.codex`, `skills/` into `~/.claude/skills` + `~/.agents/skills` (Codex+Pi) + `~/.copilot/skills` (Copilot), `pi/extensions/` into `~/.pi/agent/extensions` for Pi's footer extension, and `tray/` into `~/.claude/tray` — the one machine-local `tray_lifecycle.ps1` every fleet `tray.bat` calls by path (fleet-config#153) — so every agent (and every tray) loads the *same live files* and nothing can drift between them.
- **Symlinks** for the single-file entries (`global-CLAUDE.md` → `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, `~/.pi/agent/AGENTS.md`, and `~/.copilot/copilot-instructions.md`; `codex-hooks.json` → `~/.codex/hooks.json`; `statusline-command.ps1`). Cross-volume file linking on Windows requires admin or Developer Mode, so the installer self-elevates with **one UAC prompt** the first time it needs to create them. Reinstalls that find the symlinks already in place stay UAC-free.

## Other surfaces this repo ships

`tray/tray_lifecycle.ps1` is the ONE machine-local copy of the Windows tray-lifecycle helper (fleet-config#153, `project-scaffolding#153`) — the detect → kill → reclaim → start → verify sequence every fleet tray's `tray.bat` shells out to. It was previously vendored byte-for-byte into six sister repos (`app-launcher`, `home-automation`, `local-llm-hub`, `photo-ocr`, `voice-transcriber`, `whatsapp-radar`), so a single fix cost six-plus mechanical PRs (`project-scaffolding#144`–`#150`); now every `tray.bat` calls it by the one stable, junctioned path (`%USERPROFILE%/.claude/tray/tray_lifecycle.ps1`) exposed by `install.ps1`, and a fix here is live everywhere the moment it merges. `project-scaffolding` still owns the file's canonical source, its behavioral e2e harness, and `docs/windows-tray.md`; `app/tray/single_instance.py` (imported Python, not a shelled helper) stays vendored per-app — the split is "does it ship with the app?" (machine-local infra → this shared channel; app-shipped assets → `project-scaffolding`'s `_vendored/` + `/propagate-vendored`).

`stream-deck/` is a source-controlled Elgato Stream Deck plugin (fleet-config#370) — a self-contained Node/TS project with its own build (`rollup`), tests, and registry — that installs a **Fleet Coding** control surface (the fleet's tray launchers, an **Open Coding** action, a **Back** action) alongside the existing Elgato Stream Deck app, replacing a hand-maintained profile that drifted independently from the repos it launched. See [`docs/stream-deck-plugin.md`](stream-deck-plugin.md) for the why and `stream-deck/README.md` for day-to-day usage/install/maintenance.

## Migrating an existing agent home

If you already have `~/.claude/CLAUDE.md` or `~/.claude/statusline-command.ps1` as real files, the installer refuses with "rename it, then re-run". Move them aside, install, then delete:

```powershell
Move-Item $env:USERPROFILE\.claude\CLAUDE.md              $env:USERPROFILE\.claude\CLAUDE.md.old
Move-Item $env:USERPROFILE\.claude\statusline-command.ps1 $env:USERPROFILE\.claude\statusline-command.ps1.old
.\install.ps1   # UAC prompt
# verify both symlinks resolve to the repo, then:
Remove-Item $env:USERPROFILE\.claude\CLAUDE.md.old
Remove-Item $env:USERPROFILE\.claude\statusline-command.ps1.old
```

Same story for `~/.agents/skills` (the Codex location): if Codex previously *migrated* the skills there as real copies, the installer refuses to clobber the real directory. Move it aside, install, then delete:

```powershell
Move-Item $env:USERPROFILE\.agents\skills $env:USERPROFILE\.agents\skills.old
.\install.ps1   # creates the ~/.agents/skills junction (no UAC — junctions need none)
# verify ~/.agents/skills resolves to the repo, then:
Remove-Item $env:USERPROFILE\.agents\skills.old -Recurse -Force
```

Same for `~/.codex` if Codex was bootstrapped with hand-copied files (a real `AGENTS.md`, a real `hooks/` dir, a hand-written `hooks.json`). The installer refuses to clobber real files, so move them aside, install, then delete:

```powershell
Move-Item $env:USERPROFILE\.codex\AGENTS.md  $env:USERPROFILE\.codex\AGENTS.md.old
Move-Item $env:USERPROFILE\.codex\hooks      $env:USERPROFILE\.codex\hooks.old
Move-Item $env:USERPROFILE\.codex\hooks.json $env:USERPROFILE\.codex\hooks.json.old
.\install.ps1   # one UAC prompt for the AGENTS.md + hooks.json symlinks; the hooks/ + prompts/ junctions need none

# confirm every ~/.codex link resolves back to the repo before deleting the .old copies:
'AGENTS.md','hooks','prompts','hooks.json' | ForEach-Object {
    $p = "$env:USERPROFILE\.codex\$_"; "{0,-11} -> {1}" -f $_, (Get-Item $p -Force).Target
}

Remove-Item $env:USERPROFILE\.codex\AGENTS.md.old, $env:USERPROFILE\.codex\hooks.json.old -Force
Remove-Item $env:USERPROFILE\.codex\hooks.old -Recurse -Force
```

On the first install, keeping `codex-hooks.json` byte-identical to the `hooks.json` Codex already trusts means the symlink swap doesn't re-trigger Codex's hook-trust prompt. Later edits to the hook command strings can still require a one-time Codex trust confirmation, which is expected.
