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
| `fleet_repo_tables()` | `service_probe`, `/design-review <repo>` target resolution | unfiltered: it answers what a repo declares, not whether a run reaches it |
| `_lib.load_registry()` | every hook | unfiltered: hooks serve any session in any repo |
| owner-wide `gh repo list` and other direct walks | `/cleanup-fleet`, `/cleanup-fleet-all`, `/issue-triage`, `/learning-log`, `/design-review fleet`, `/context-purge`, `/context-audit`'s CLAUDE.md budget scan | do not read the switch yet |

## When the local file is missing

Each reader fails in the way that is safe for it:

- **Fleet runs** (`fleet_repos`, `iter_fleet_repos`, `fleet_repo_tables`, `fleet_status.py`) raise `ProjectsTomlMissing`, naming the example to copy. A run never guesses "no repos" or "every repo".
- **Hooks** read the example and print a warning on stderr. An empty registry would quietly switch off guards such as `never_kill_ports`; the example keeps them enforcing until the local file is restored.
- **The acceptance gate** reads the example, so it runs on a fresh clone and in a worktree, neither of which has the local file.

`CLAUDE_HOOKS_PROJECTS_TOML` overrides the path for hooks and tests.
