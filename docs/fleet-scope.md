# Fleet scope

Which repos fleet-wide runs reach. Every repo declared in `hooks/projects.toml` is **active** unless it is listed in that file's `[global] architecture_ignore` array, which makes it **dormant**: out of every fleet-wide run and off the architecture and config maps. Waking a repo is removing one line.

## Where the switch lives

`hooks/projects.toml` is machine-local and gitignored. The tracked `hooks/projects.toml.example` is its template, and `install.ps1` copies it into place on a fresh clone. Flipping a repo therefore changes this machine only: no commit, no dirty tree.

The two files must declare the same repo tables. A new repo, or a new key on one, goes into both in the same PR; `tests/run_acceptance.py`'s `projects_toml` check fails when their tables differ, and when a switch entry names a repo that is not declared. Only `architecture_ignore` is expected to differ between them.

## Flipping a repo

```powershell
& E:/automation/fleet-config/.venv/Scripts/python.exe skills/_lib/fleet_status.py list
& E:/automation/fleet-config/.venv/Scripts/python.exe skills/_lib/fleet_status.py set <repo> dormant
& E:/automation/fleet-config/.venv/Scripts/python.exe skills/_lib/fleet_status.py set <repo> active
```

`fleet_status.py` is the only writer, and App Launcher's Settings toggle calls the same module (`list_status()`, `set_status()`). It edits only the array's own lines, so comments and every other byte survive, and a dormant-then-active round trip restores the file byte for byte. It re-parses the result and refuses to write unless `architecture_ignore` is the only change. It refuses an unknown repo, an invalid value, a file that is missing or does not parse, and an array not written one repo per line, each with its own message and exit 2. Setting a repo to its current status reports `unchanged`.

## Who obeys it

| Route | Used by | Treatment |
|---|---|---|
| `fleet_repo_scan.fleet_repos()` | `/system-map`, `/config-map`, `/context-audit`'s skill scan, `/prompt-audit`, scoped discovery, `/propagate-vendored` | drops dormant repos |
| `fleet_repo_scan.iter_fleet_repos()` | `/audit-fleet`, `/design-sweep`, `/fleet-10x` | drops dormant repos; a repo named through `only` (`--only`) still runs |
| `fleet_repos(include_ignored=True)` | `chief_ops.py` repo-name resolution | keeps them: a repo the user names must resolve |
| `iter_fleet_repos(include_ignored=True)` | the `fleet_membership` acceptance check | keeps them: a dormant repo is still declared |
| `fleet_repo_tables()` | `/design-review <repo>` target resolution | unfiltered: it answers what a repo declares, not whether a run reaches it |
| `_lib.load_registry()` | every hook | unfiltered: hooks serve any session in any repo |
| `gh_issue_fetch.py fetch` (owner-wide `gh repo list`) | `/cleanup-fleet`, `/cleanup-fleet-all`, `/issue-triage`, `/audit-fleet`'s drift counts, `/chief` backlog search | drops dormant repos and still fetches a repo the list does not declare; `--include-dormant` fetches all; `SKIPPED_DORMANT=<n>: <names>` on stderr names what was dropped |
| `design_review.fleet.fleet_targets()` | `/design-review fleet` | skips dormant web apps and records them in the digest as skipped (`skipped_dormant`, `SKIPPED_DORMANT=`), never as absent |
| `audit.py`'s CLAUDE.md budget scan | `/context-audit` | drops dormant repos and lists them as `NOT MEASURED (dormant)`; their context cost is unknown, never zero, and an undeclared directory is still measured |
| `gate.py`'s `surface_files()` | `/context-purge --fleet` | drops dormant repos and names them on `SKIPPED_DORMANT=`: not assessed, not clean |

## Deliberately unfiltered

These routes keep dormant repos, each for its own reason. A new fleet-wide route obeys the switch or joins this list with its reason.

| Route | Used by | Why dormant repos stay |
|---|---|---|
| `learning-log/gather.py` `gh repo list` | `/learning-log` | it reads merged history, not a run over repos |
| `service_probe` via `fleet_repo_tables()` | restart and deploy-coverage checks | a dormant repo's app may still be running |
| `hooks/backup/select.py` `iter_repos` | the daily private backup | a backup never skips a repo for being idle |
| `chief_sweep.py` | worktree housekeeping | residue in a dormant repo is still residue |

## When the local file is missing

Each reader fails in the way that is safe for it:

- **Fleet runs** (`fleet_repos`, `iter_fleet_repos`, `fleet_repo_tables`, `fleet_status.py`) raise `ProjectsTomlMissing`, naming the example to copy. A run never guesses "no repos" or "every repo".
- **Hooks** read the example and print a warning on stderr. An empty registry would quietly switch off guards such as `never_kill_ports`; the example keeps them enforcing until the local file is restored.
- **The acceptance gate** reads the example, so it runs on a fresh clone and in a worktree, neither of which has the local file. `tests/run_acceptance.py` points `FLEET_PROJECTS_TOML` at the example for the whole run, child suites included, when the local file is absent; its own checks name the example through `gate_projects_toml()` in `tests/acceptance/shared.py`.

`CLAUDE_HOOKS_PROJECTS_TOML` overrides the path for hooks and tests; `FLEET_PROJECTS_TOML` does the same for fleet runs (`fleet_repo_scan`). Each is an explicit choice, never a fallback: set to a path that does not exist, a fleet run still raises `ProjectsTomlMissing`.
