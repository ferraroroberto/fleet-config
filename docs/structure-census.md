# Structure census

`skills/_lib/structure_census/` measures the code structure of every fleet repo and compares two measurements. It is read-only. It reads each repo through `git archive` at a named commit, never the working tree, and it fetches, builds and writes nothing except its output file. The structure ratchet and its baselines say whether each repo is holding its limits. The census answers a different question: what the whole fleet looks like by one fixed set of definitions, so that two dates can be compared like for like.

## Run it

From the fleet-config primary checkout:

```powershell
# Now: every repo at origin/main as last fetched
& E:/automation/fleet-config/.venv/Scripts/python.exe skills/_lib/structure_census/census.py run --out after.json

# Then: every repo at its last first-parent commit at or before a moment
& E:/automation/fleet-config/.venv/Scripts/python.exe skills/_lib/structure_census/census.py run --as-of 2026-10-22T05:08:00Z --out before.json

# Compare: Markdown tables on stdout, the same figures as data with --json
& E:/automation/fleet-config/.venv/Scripts/python.exe skills/_lib/structure_census/census.py compare before.json after.json --json compare.json
```

`--projects-toml <path>` reads another copy of the fleet list. A linked worktree has no `hooks/projects.toml` of its own, so a run from one passes the primary's. A full run takes about three minutes. Write the output files outside the repo, because they name private repos' paths.

`2026-10-22T05:08:00Z` is the moment of the first census. Re-run with that cutoff, the census reproduces its published totals exactly. One repo's history was rewritten after that census, and for that repo the cutoff resolves to the rewritten commit with the same timestamp.

## Which repos

Every table in `hooks/projects.toml` that carries a `cwd_prefix`. A repo switched off in `architecture_ignore` (see `docs/fleet-scope.md`) is still measured and marked `dormant`, so a comparison can report all repos and active repos side by side. Each repo is read at `origin/main`, else `origin/master`, else `HEAD`. A repo with no checkout, no such ref, or a git failure is `unknown` with its reason, never measured as zero.

## What each number means

| Metric | Definition |
|---|---|
| `loc` | Lines in code files. A code file has one of the extensions in `measure.CODE_EXT` (Python, JS/TS, CSS/SCSS, HTML, PowerShell, batch, shell, SQL and a few others). Files under a vendor, `node_modules`, `fixtures`, `dist`, `build` or `_vendored*` folder are excluded, as are minified, bundled, lock and generated files. A file's lines are its newline count, plus one for a last line with no newline. |
| `loc_vendored` | The part of `loc` that sits under a `dest` in the repo's own `.fleet.toml` `[vendored]` table, such as the copied ratchet scripts. It is still counted in `loc`, so the first census's definition holds. Subtract it for the repo's own code. |
| `code_files` | The number of code files. |
| `files_over_1000`, `files_over_2000` | Code files above that many lines, tests included. |
| `functions_over_100`, `functions_over_200`, `functions_300_plus` | Functions above 100 or 200 lines, or at least 300. Python spans come from `ast`, from `def` to the last line. Nested functions and methods count on their own as well as inside the function that holds them. A JS/TS span runs from the opening line to the first later line at the same or a lower indent, which must open with `}`. A function whose closer cannot be found is skipped, and spans under 20 lines are not recorded. |
| `files_900_to_1000`, `functions_90_to_100` | The repo's own files and functions just under the ratchet's limits, vendored copies left out. A rising count means splits are stopping as soon as the gate passes. The weekly `/audit-fleet` digest reports the same band per repo against that repo's own limits (`structure_census/near_limit.py`), built on the same `measure.near_band`. |
| `flat_dirs_over_40` | Folders holding more than 40 modules directly (`.py`, `.js`, `.mjs`, `.css`, not counting `__init__.py`). This is the structure ratchet's own rule, applied to the census's code files. |
| `src_app_imports` | Imports of `app` or `app.*` from a Python file under `src/`, the layer rule the structure ratchet enforces. |
| `structure_ratchet`, `ratchets_enabled` | Repos whose `.fleet.toml` enables the structure ratchet, and the number of enabled ratchet tables across the repos. |
| `no_gate_found` | Repos with no gate file (`verify-before-ship.ps1`, `run_acceptance.py`, a `Makefile`, a GitHub workflow) and no enabled ratchet table. |

Each repo record also lists its files over 1,000 lines, its 300-line functions, its flat folders, its gate files and the checks named in them, and the baselined hit count of every enabled ratchet. For the structure ratchet it also gives the baselined files, functions and folders. A baseline the shared reader cannot count, such as a repo's own ratchet in another format, is `None` and listed under `unknown`, never counted as zero.

## Why the census and the ratchet can disagree

The census applies one set of definitions to every repo. The ratchet applies each repo's own `[structure_ratchet]` config: its `exclude` globs, its vendored files and its limits. A repo can therefore pass its ratchet with an empty baseline and still show census offenders. Examples are generated reports under an excluded docs folder, workshop exercises excluded by design, and a test folder over 40 modules only once vendored test helpers are counted. The comparison reports the census figures. The ratchet baselines show what each repo has agreed to hold.

## Shapes

A repo's shape is the top-level `shape` in its `.fleet.toml`. A repo that declares none gets a guess from its code: FastAPI means `webapp-tray`, Streamlit means `streamlit`, Node or plain HTML means `node` or `static-site`, and anything else is `pipeline-cli`. `compare` groups both runs by the later run's shape, so a repo stays in one group on both sides.
