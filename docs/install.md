# Install mechanics

The first-run quickstart lives in [`README.md`](../README.md#install). This is
the durable detail: the two link kinds `install.ps1` creates, the other
surfaces this repo ships beside `hooks/` and `skills/`, and the recipes for
migrating an agent home that already holds real files.

## What `install.ps1` links, and how

`install.ps1` exposes the repo's contents inside the supported **agent homes** — `~/.claude` (Claude Code), `~/.agents` (the cross-agent skills location), `~/.codex` ([Codex](https://developers.openai.com/codex/)'s own home), `~/.pi/agent` ([Pi](https://github.com/parallel-web/pi)'s config dir), and `~/.copilot` ([GitHub Copilot CLI](https://docs.github.com/en/copilot/github-copilot-in-the-cli)) — via two link kinds:

- **Junctions** for the directory entries. Cross-volume OK, no admin. `hooks/` and `commands/` are each junctioned into **both** `~/.claude` and `~/.codex`, `skills/` into `~/.claude/skills` + `~/.agents/skills` (Codex+Pi) + `~/.copilot/skills` (Copilot), `pi/extensions/` into `~/.pi/agent/extensions` for Pi's footer extension, and `tray/` into `~/.claude/tray` — the one machine-local `tray_lifecycle.ps1` every fleet `tray.bat` calls by path (fleet-config#153) — so every agent (and every tray) loads the *same live files* and nothing can drift between them.
- **Symlinks** for the single-file entries (`global-CLAUDE.md` → `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, `~/.pi/agent/AGENTS.md`, and `~/.copilot/copilot-instructions.md`; `codex-hooks.json` → `~/.codex/hooks.json`; `statusline-command.ps1`). Cross-volume file linking on Windows requires admin or Developer Mode, so the installer self-elevates with **one UAC prompt** the first time it needs to create them. Reinstalls that find the symlinks already in place stay UAC-free.

## Scoped project discovery

`install.ps1` also inventories the registered checkouts in `hooks/projects.toml` and reconciles individual skill-directory links. The layout follows [project-scaffolding's portable project skills contract](https://github.com/ferraroroberto/project-scaffolding/blob/main/docs/agents/project-skills.md): keep the maintained source, link the complete directory into the missing native root at the same scope, and preserve existing real parents. `.claude/skills/<name>` gets a `.agents/skills/<name>` link for Codex/Pi; a real `.agents` source gets an inverse `.claude` link. Grok's native compatibility needs no `.grok` mirror. Existing user-home junctions remain compatible, including the `_lib` helper path used by global workflows; new project discovery never mirrors their parent containers.

Only direct, valid `SKILL.md` children are selected. `_lib`, `_private`, dot directories, runtime trees and helper containers receive no discovery link. The inventory compares frontmatter names and resolved sources across both roots, visible ancestors and user skills. Different sources sharing one name are collisions even when folder names differ. Matching user-created links are used without claiming ownership; real files/directories, changed targets and broken unowned links are preserved. A collision report calls for an explicit source selection or rename after comparing content. In particular, the real copies in `content-management/.agents/skills` are never replaced automatically.

```powershell
# Read-only: no model call, writes, trust changes or source-content output.
& E:/automation/fleet-config/.venv/Scripts/python.exe skills/_lib/scoped_discovery.py diagnose --registered
# Limit installation/removal to one checkout; user homes stay untouched.
.\install.ps1 -ProjectRoot E:/automation/fleet-config
.\uninstall.ps1 -ProjectRoot E:/automation/fleet-config
```

Diagnosis reports `needs-install`, `blocked`, or `unknown` separately from filesystem `ok`. Native discovery and instruction-reading remain unknown until independently probed; byte counts and a resolving pointer are not proof. `--repo <checkout>` limits the Python diagnostic; `--home <temporary-home>` supports isolated fixtures. Ordinary installation continues independent safe links while returning nonzero for any collision/unknown result. No setting or instruction fallback is silently rewritten.

Each checkout records only links it creates in its Git metadata directory, in `.fleet-config-discovery.json`; linked worktrees use their own `gitdir`, not the primary's manifest. Exact generated routes get an owned block in Git's local `info/exclude`, preserving user lines and existing private-context ignores. Because linked worktrees share that exclude file, each checkout has a separate block and edits take an exclusive lock. Removing one checkout's links leaves sibling blocks intact. An occupied lock, changed block or malformed manifest reports unknown and requires inspection. Reinstallation is idempotent. Source removal removes only the unchanged owned link; altered entries retain their ownership record. Uninstall removes the link itself without recursion and keeps all real parent directories and sources. User-home uninstall now also checks recorded source identity before unlinking.

Every fresh clone/worktree needs its own installation against the sources actually present there. Run `install.ps1 -ProjectRoot <worktree>` after creation and `uninstall.ps1 -ProjectRoot <worktree>` before `worktree_claim.py remove-worktree`. Never link ignored primary private sources into a worktree. Running either PowerShell script from a linked worktree defaults to that checkout only. A moved checkout whose absolute link still reaches its old source is a collision: inspect and remove the unchanged owned old link with scoped uninstall, then reinstall against the moved source. Generated routes stay local through owned exact-path Git excludes; the installer never changes adopters' tracked ignore files.

`AGENTS.md` remains the short committed pointer to maintained `CLAUDE.md`, both at root and where package-specific instructions exist. Global instruction links remain single-source. The read-only report includes all discovered root/package instruction byte counts and the explicitly configured Codex limit/fallback fields; an unset limit is reported as requiring active-runtime verification. See [official instruction precedence and size behavior](https://learn.chatgpt.com/docs/agent-configuration/agents-md) and the native evidence in [cross-agent parity](cross-agent-parity.md).

Native verification is explicitly opt-in and creates only disposable synthetic repositories/skills. Run it with the normal permissions required for native sandbox startup on Windows:

```powershell
# Native skill catalogs and Codex prompt assembly; no external model turn.
& E:/automation/fleet-config/.venv/Scripts/python.exe skills/_lib/discovery_probe.py --run
# Full instruction proof: Claude compares the known global file locally on
# loopback with a dummy key; Codex uses the existing account and a real model
# to read exactly the two synthetic CLAUDE.md targets. No credential copies.
& E:/automation/fleet-config/.venv/Scripts/python.exe skills/_lib/discovery_probe.py --run --instruction-proof --codex-model-proof gpt-6-astra
```

No arguments returns `not-probed` and exit 2. Catalog mode requires Claude/Codex discovery and reports Pi/Grok capability limits separately. Adding `--instruction-proof` requires the target-reading proof too; without the explicit model option, Codex targets remain unknown and the command returns nonzero. Full mode requires both EOF assembly and a successful actual target read with an exact marker response. It never substitutes a local model, copies credentials, edits live settings, or transmits Claude's captured instructions externally. Only summary metadata survives cleanup. Synthetic scratch directories inherit the temp directory's ordinary Windows ACL so the native sandbox can read them; no existing directory's permissions are changed.

## Other installed surfaces

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
