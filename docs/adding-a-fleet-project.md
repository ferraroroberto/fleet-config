# Adding a new fleet project

The onboarding procedure for a new repo under `E:/automation/`. `CLAUDE.md` keeps the one-line rule and points here.

## 1. Declare it in `hooks/projects.toml` and its example

New repo under `E:/automation/` → **always** add a minimal entry before the `[global]` block of both `hooks/projects.toml.example` (tracked, carried by the PR) and this machine's gitignored `hooks/projects.toml` (the `projects_toml` acceptance check fails while their repo tables differ):

```toml
[my-new-project]
cwd_prefix = "E:/automation/my-new-project"
```

Required for `notify_on_idle` to name the right project in Telegram pings (else it falls back to `[claude]`). `tests/run_acceptance.py`'s `fleet_membership` check enforces this: any repo on disk under `E:/automation/` that is neither declared here nor listed in `[global] architecture_ignore` fails the gate by name. Add port/gate/tray fields only if the project has a tray app or verification gate. A new repo is active: every fleet-wide run reaches it until it is switched off (see [fleet-scope.md](fleet-scope.md)).

## 2. Regenerate the maps in the same PR

**That block is also the fleet-membership list** — `fleet_repos()` reads it (minus `[global] architecture_ignore`), so a new entry expands `/system-map`, `/config-map`, and `/context-audit`'s skill-description cap gate too. Same PR, or `tests/run_acceptance.py` fails:

```powershell
& E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/system-map/build_data.py     # fleet.data.js
& E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/system-map/render_mermaid.py # system-map.mmd
& E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/config-map/build_data.py     # config.data.js
```

## 3. Place it on the architecture map

Then add the repo's row to `architecture/ARCHITECTURE.md`. A repo shipping its own root `.fleet.toml` also belongs in the residual's `_adopted` registry (so a deleted declaration fails loud); one that doesn't needs a fallback card in `architecture/fleet.residual.json`, or it is in the fleet but absent from the map. A repo that genuinely should stay off the map goes in `[global] architecture_ignore` — a recorded decision, not silence. That switch also takes it out of every fleet-wide run; flip it with `skills/_lib/fleet_status.py` ([fleet-scope.md](fleet-scope.md)).
