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
| `/issue-batch` bare-number lookup (`gh search issues --owner`) | `/issue-batch` | an issue the user names must resolve in any repo |
| `chief_ops.py` repo-name resolution, `/design-review <repo>` target resolution | `/chief`, `/design-review` | a repo the user names must resolve |

## The guard

`tests/acceptance/fleet_scope_guard.py` fails the gate on a fleet enumeration route that neither handles the status nor is listed above. It scans `hooks/`, `skills/` and `.claude/skills/` (`.py` and `SKILL.md`; `tests/` is exempt) for:

- `fleet_repo_tables(`, which is unfiltered by design;
- `fleet_repos(` and `iter_fleet_repos(` called with `include_ignored=True`, the explicit opt-out (their defaults already drop dormant repos);
- `gh repo list` and `gh search issues --owner`, as a command line or an argv list;
- `.iterdir()` within ten lines of a `".git"` test, an ad hoc crawl of a directory of repos.

A hit passes when its file names `ignored_repos`, `split_dormant`, `list_status`, `get_status`, `include_dormant` or `SKIPPED_DORMANT` (an `include_ignored=True` call needs an entry regardless), or when `tests/acceptance/fleet_scope_allowlist.json` lists the file and pattern with a reason. The allow-list carries the routes of the two tables above that the scan matches, plus prose that names a route only to rule it out; an entry with no reason, an unknown pattern or no remaining match fails as drift. The failure names the file, line and pattern. The scan is textual and errs noisy: a new route obeys the switch, or joins the allow-list and the table above with its reason.

## An active repo needs a gate

An active repo has a recognised verification gate, or it is dormant. `skills/_lib/fleet_core/fleet_gate.py` answers it per repo (`gate_verdict()`) and fleet-wide (`survey()`); the CLI prints the same report, and a digest reads `survey()` directly:

```powershell
& E:/automation/fleet-config/.venv/Scripts/python.exe skills/_lib/fleet_core/fleet_gate.py
```

A repo has a gate when its committed default branch carries any of these forms (`GATE_SCRIPTS`, `RATCHET_NAMES` and friends in the module hold the list):

| Form | Evidence |
|---|---|
| `gate_cmd` | set in the repo's own `projects.toml` table |
| `scripts/verify-before-ship.ps1` | the file is tracked |
| `scripts/verify.js` | the file is tracked |
| `package.json` | `scripts.verify` is a non-empty command; a `package.json` without one is not a gate |
| a ratchet in a test | a tracked `tests/**/*.py` names `static_ratchet`, `docs_ratchet` or `structure_ratchet` |

Evidence is read with `git show` and `git grep` against the default branch, so a dirty or off-branch checkout does not change the answer.

Each active repo lands in one bucket:

- **gated**: a form above matched.
- **ungated**: the checkout was read and no form matched. These are the names to fix by adding a gate or flipping the repo to dormant.
- **unknown**: the answer could not be established (no checkout, no default-branch ref, an unparsable `package.json` with no other form). It is reported on its own line and never counted as gated.
- **skipped (dormant)**: listed by name, not inspected.

The `fleet_gate` acceptance check prints this report and is advisory, as the report's header says: its inputs are sibling checkouts, so an ungated or unknown repo is counted as skipped and never fails the run.

## When the local file is missing

Each reader fails in the way that is safe for it:

- **Fleet runs** (`fleet_repos`, `iter_fleet_repos`, `fleet_repo_tables`, `fleet_status.py`) raise `ProjectsTomlMissing`, naming the example to copy. A run never guesses "no repos" or "every repo".
- **Hooks** read the example and print a warning on stderr. An empty registry would quietly switch off guards such as `never_kill_ports`; the example keeps them enforcing until the local file is restored.
- **The acceptance gate** reads the example, so it runs on a fresh clone and in a worktree, neither of which has the local file. `tests/run_acceptance.py` points `FLEET_PROJECTS_TOML` at the example for the whole run, child suites included, when the local file is absent; its own checks name the example through `gate_projects_toml()` in `tests/acceptance/shared.py`.
  The `system_map` check is the one place the example cannot stand in: which repos are switched off is this machine's answer, so with no local list a repo missing from the map is reported as skipped (not confirmed), never failed. Keep the example's `architecture_ignore` equal to the local list so that case stays rare.

`CLAUDE_HOOKS_PROJECTS_TOML` overrides the path for hooks and tests; `FLEET_PROJECTS_TOML` does the same for fleet runs (`fleet_repo_scan`). Each is an explicit choice, never a fallback: set to a path that does not exist, a fleet run still raises `ProjectsTomlMissing`.
