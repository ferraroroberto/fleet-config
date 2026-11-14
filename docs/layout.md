# Layout

Where code goes in this repo. `scripts/structure_ratchet.py` enforces it (shape `skills-governance`, declared in `.fleet.toml`); the rules are in project-scaffolding's repo-layout and structure-ratchet references. The README's layout tree describes every file at the root and the hooks; this page is the map of the folders and where a new file belongs.

## Top-level folders

- `skills/` — one folder per fleet skill (`skills/<name>/SKILL.md`, plus the helpers private to that one skill). `skills/_lib/` holds the shared library: CLIs that a `SKILL.md` invokes by path (`skills/_lib/<entry>.py`, which keep their path) and packages shared by two or more skills (`skills/_lib/<pkg>/`).
- `hooks/` — one module per hook, the shared core (`_lib.py`, `hook_wire/`) and the helpers a hook family owns (`secret_scan/`, `lane_compact/`). `hooks/agents/<agent>/` holds one agent's integration of the hook layer (`agents/agy/` is the Antigravity plugin and `agents/copilot/` the Copilot CLI hook definition, both installed by copy; `agents/codex/` holds the Codex agent definitions and the model catalog the policy writer merges). Linked into `~/.claude/hooks` and `~/.codex/hooks` by `install.ps1`.
- `scripts/` — the vendored ratchet scripts and their siblings at its top level, copied verbatim from project-scaffolding and never edited here; this repo's own shell scripts sit in subfolders (`scripts/shell/` holds the PowerShell profile snippet `install.ps1` wires into `$PROFILE`).
- `tests/` — pytest suite mirroring what it tests: one folder per hook family, skill or `skills/_lib` package (for example `tests/hooks/`, `tests/chief_ops/`, `tests/design_lint/`), `tests/acceptance/` and the data they read under `tests/fixtures/`.
- `docs/` — durable reference, one file per topic; `docs/skills/` holds the operating notes for individual skills.
- `architecture/` — the generated fleet and config maps and the contract docs beside them (allowed by the shape).
- `.claude/` — this repo's own project skills and workflows (hidden, so not a layout folder).

## Declared extra roots

Each is a surface this repo ships beyond skills and hooks, and each holds files an installer links into an agent's home, so the folder name is part of that contract.

- `pi/` — Pi extensions (`pi/extensions/`).
- `stream-deck/` — the Stream Deck plugin: its source, templates, registry and tests.
- `tray/` — `tray_lifecycle.ps1`, the shared tray restart helper every fleet repo's `tray.bat` runs from `~/.claude/tray/`.

## Exceptions recorded in the baseline

These shrink with each move and are not allowed to grow.

- **Root files.** The design specs and rubrics, `global-CLAUDE.md`, the statusline and config-writer scripts, `settings.template.json`, `codex-hooks.json` and `uninstall.ps1` sit at the root because `install.ps1` and the agent homes link them by that path. Moving one means changing the installer, the links on every machine and the docs that cite it, so each move is its own change.
- **Folder type.** `tests/acceptance/` holds JSON expectations beside its code.

A new exception needs its reason in this file and in `.fleet.toml`.

## Where new code goes

- A helper used by one skill stays in that skill's folder; at its second caller it moves to `skills/_lib/`.
- A new hook is `hooks/<name>.py`; code two hooks share goes in `hooks/_lib.py` or a package beside it.
- A new test lands in the `tests/` folder that mirrors the code it covers.
- A new document is `docs/<topic>.md`, named for its topic and never dated.
