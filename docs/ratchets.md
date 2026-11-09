# Ratchets

Nine baseline-and-ratchet stages gate `tests/run_acceptance.py`. [`code-quality.md`](code-quality.md) explains the whole system and how to port it; this page is the index of fleet-config's instances: which ratchets run, what each fails on, its gate, its `.fleet.toml` options, its baseline and how many entries that baseline still holds. The concept is the scaffold's: why ratchets are the default way to enforce a convention, and how every one is built, is the project-scaffolding [architecture decision](https://github.com/ferraroroberto/project-scaffolding/blob/main/docs/architecture-decisions.md#ratchets-are-the-default-way-to-enforce-a-convention), and each detector's own page is linked in the table.

`tests/acceptance/docs_index/` holds this page to the tree: a ratchet table, gate, option, baseline count or rule added or removed without updating this page fails the acceptance run by name.

## The contract here

- Each gate runs a script vendored byte-for-byte from project-scaffolding and declared in `.fleet.toml` `[vendored]`. Re-vendor with `/propagate-vendored <ratchet>`, never edit the copy.
- Configuration is the `.fleet.toml` table named after the script (`[static_ratchet]`, `[docs_ratchet]`, …).
- Existing debt sits in the committed `.<name>-ratchet-baseline.json`, never hidden in an `exclude`. A baseline is a ledger burned down to zero, not a list of permanent exceptions: `--update-baseline` only shrinks it, and only `--allow-additions` adds to it, which shows in review.
- Exit 3 means the detector could not run, count, measure, scan or resolve. It is NOT CONFIRMED, so the gate fails; it is never a skip. That is why each ratchet is a dedicated check in `run_acceptance.py`'s ratchet list and never a `standalone_dispatch` row, where the shared skip exit (also 3) would read as skipped.
- A legitimate single exception carries a `# <rule>-exempt: <reason>` comment on the line or in the comment block right above it.

## Index

| Ratchet | Fails on | Gate | Options | Baseline | Entries | Scaffold doc |
| --- | --- | --- | --- | --- | --- | --- |
| `static_ratchet` | a duplicate block (jscpd), an unused import or variable (ruff `F401`, `F841`), a ruff bug (`B`) or security (`S`) finding, an unsorted import block (ruff `I001`); `S113` is strict | `tests/acceptance/static_ratchet_gate.py` | `exclude` | `.static-ratchet-baseline.json` | 0 | [static-ratchet.md](https://github.com/ferraroroberto/project-scaffolding/blob/main/docs/static-ratchet.md) |
| `docs_ratchet` | `README.md` past 40 KiB or its baselined size; an issue reference, dated heading or changelog heading in `README.md` or `docs/**/*.md` | `tests/acceptance/docs_ratchet_gate.py` | — | `.docs-ratchet-baseline.json` | 0 | [docs-ratchet.md](https://github.com/ferraroroberto/project-scaffolding/blob/main/docs/docs-ratchet.md) |
| `structure_ratchet` | a file past 1,000 lines, a function past 100 lines, a flat directory past 40 modules, new or above its entry; a folder layout that breaks `shape` plus `extra_roots` (layout rules below) | `tests/acceptance/structure_ratchet_gate.py` | — | `.structure-ratchet-baseline.json` | 0 | [structure-ratchet.md](https://github.com/ferraroroberto/project-scaffolding/blob/main/docs/structure-ratchet.md) |
| `comment_ratchet` | a file's count of `#NNN` issue references inside comments rising above its entry, or any in a file with no entry | `tests/acceptance/comment_ratchet_gate.py` | — | `.comment-ratchet-baseline.json` | 0 | [comment-ratchet.md](https://github.com/ferraroroberto/project-scaffolding/blob/main/docs/comment-ratchet.md) |
| `instruction_ratchet` | a prompt-audit lint rule's hit count in an instruction file rising above its entry, or a file past its size cap | `tests/acceptance/instruction_ratchet_gate.py` | `skill_dirs`, `extra_files` | `.instruction-ratchet-baseline.json` | 0 | [instruction-ratchet.md](https://github.com/ferraroroberto/project-scaffolding/blob/main/docs/instruction-ratchet.md) |
| `doc_refs_ratchet` | `README.md`, `CLAUDE.md` or `docs/**/*.md` naming a repo path, `python -m` module or relative link that does not exist | `tests/acceptance/doc_refs_ratchet_gate.py` | `allow_refs` | `.doc-refs-ratchet-baseline.json` | 0 | [doc-refs-ratchet.md](https://github.com/ferraroroberto/project-scaffolding/blob/main/docs/doc-refs-ratchet.md) |
| `dead_code_ratchet` | a function, class or method nothing in the tracked tree refers to | `tests/acceptance/dead_code_ratchet_gate.py` | — | `.dead-code-ratchet-baseline.json` | 0 | [dead-code-ratchet.md](https://github.com/ferraroroberto/project-scaffolding/blob/main/docs/dead-code-ratchet.md) |
| `conventions_ratchet` | a file's hit count for an enabled `ast` rule rising above its entry (rules below) | `tests/acceptance/conventions_ratchet_gate.py` | `rules`, `exclude`, `print_paths`, `git_wrappers`, `must_use` | `.conventions-ratchet-baseline.json` | 0 | [conventions-ratchet.md](https://github.com/ferraroroberto/project-scaffolding/blob/main/docs/conventions-ratchet.md) |
| `runtime_write_ratchet` | a tracked Python file gaining a write target that resolves under a code folder, or one it cannot resolve; any file the run leaves under a code folder | `tests/acceptance/runtime_write/runtime_write_ratchet_gate.py` | `code_folders`, `safe_calls` | `.runtime-write-ratchet-baseline.json` | 0 | [runtime-write-ratchet.md](https://github.com/ferraroroberto/project-scaffolding/blob/main/docs/runtime-write-ratchet.md) |

## Layout rules

The `structure_ratchet` row above also enforces the repo's folder layout, through the vendored `scripts/structure_layout.py`. The declared `shape` fixes the allowed root folders, the root files, the file types each folder takes and, where the shape names homes, where files may sit one level down. This repo declares `shape = "skills-governance"` and `extra_roots = ["agy", "codex", "commands", "copilot-hooks", "pi", "shell", "stream-deck", "tray"]`, both top-level `.fleet.toml` keys. The preset tables and the reasoning are in project-scaffolding's [structure-ratchet.md](https://github.com/ferraroroberto/project-scaffolding/blob/main/docs/structure-ratchet.md) and [repo-layout.md](https://github.com/ferraroroberto/project-scaffolding/blob/main/docs/repo-layout.md).

| Rule | Fails on | Baselined |
| --- | --- | --- |
| `root-layout` | a required root folder with no tracked file, or a tracked root folder that neither the shape nor `extra_roots` lists | never |
| `root` | an `extra_roots` folder the shape's preset does not already list | yes, so the list only shrinks |
| `rootfile` | a tracked root file that neither the common set nor the shape lists; a kept one is named in `extra_root_files` with its reason | yes, by name |
| `type` | a file whose extension its folder's type does not take | yes, per folder |
| `place` | where the shape declares homes, a file under one of its roots that sits outside every home | yes, per folder |
| `redundant` | an `extra_roots` or `extra_root_files` entry the shape's preset already allows | yes |

A new root folder is declared in `extra_roots`, never allowed by default; a stale or redundant entry is deleted rather than kept.

## Conventions rules

Every rule the vendored script knows, and whether this repo turns it on.

| Rule | State | Why |
| --- | --- | --- |
| `no-window` | on | every spawn runs on Windows, where a spawn without `creationflags=` opens a console window |
| `logging-not-print` | on | `skills/_lib/` is library code and logs; hooks print the wire format by design, so they sit outside `print_paths` |
| `no-drive-path` | on | nothing hardcodes a drive letter; a machine default that a flag or config overrides carries an exempt marker |
| `git-wrapper` | on | `run_git` exists in both tiers (`hooks/_lib.py`, `skills/_lib/git_run.py`) and owns `GIT_OPTIONAL_LOCKS=0` |
| `subprocess-timeout` | on | a blocking `subprocess.run`/`call`/`check_*` with no `timeout=` hangs an unattended run; a foreground runner whose length is the caller's carries `# subprocess-timeout-exempt: <why>` |
| `secret-compare` | off | the repo has no constant-time compare helper to route compares through |
| `password-input-vendored` | off | the repo ships no sign-in page |
| `login-lockout` | off | the repo serves no password route |
| `no-pwsh-stub` | off | not enabled yet; it reads text files across the whole tree, so turning it on is its own change |
| `must-use` | on | one `os.replace` entry: atomic writes go through `atomic_write_text` (`hooks/_lib.py`, its mirror `skills/_lib/fleet_core/hooks_state.py`) or `config_edit.atomic_write`; a rename that writes nothing carries `# must-use-exempt: <why>`. No `sqlite3.connect` entry: its 4 calls take 3 shapes and share no helper |

## Instruction rules

The rule titles, vendor tags and fix shapes live in [`.claude/skills/prompt-audit/rules.md`](../.claude/skills/prompt-audit/rules.md).

- **Enforced at ship time:** R-01, R-02, R-03, R-04, R-05, R-06, R-07, R-08, R-09, R-10, R-11, R-12, R-14, R-16, R-17, R-30, R-34, R-35, R-36, R-45.
- **Advisory, never counted:** R-15 (its hits sit on line 1 of an agent-neutral `SKILL.md`, where no heading can scope them, so they stay at "consider").
- **Left to the weekly `/prompt-audit`:** R-13 (a ratio of negative wording the audit judges, so a count would refuse every legitimate new prohibition) and R-37 (its vocabulary spans every repo's skills, so it is not decidable from one tree).

## Notes per ratchet

- **Static:** needs ruff (`requirements.txt`) in the gate's venv and Node for `npx jscpd`; a missing detector exits 3 and fails. An import that must run before the next one (a helper that puts a folder on `sys.path`) is kept first with a `# isort: split` line between the two, because ruff would otherwise sort the dependent import above it. `hooks/runtime_files/**` is in `exclude`: it is the hooks-tier mirror of `skills/_lib/fleet_core/runtime_files.py`, repeated because a hook imports with only its own directory on `sys.path`, and `tests/repo/test_runtime_files.py` holds the shared functions to the same code; the skills copy stays scanned.
- **Docs:** no `exclude` is set because `docs/` holds no generated or dated report folders. Docs state the present-tense why; history belongs to git, PRs and issues.
- **Structure:** the baseline is empty and stays recorded at real size, never excluded, so nothing may grow past a limit. A new module goes in a subject subfolder (`tests/<subject>/`, a `skills/_lib/<domain>/` package), not a flat directory, and a suite past 1,000 lines splits into a `_suite.py` plus ordered parts. The root folders are `shape = "skills-governance"` plus `extra_roots`, both top-level `.fleet.toml` keys; a new root folder is declared in `extra_roots`, never allowed by default.
- **Comment:** scans Python, JS, CSS and PowerShell comments; docstrings and strings are not scanned. A comment states the present-tense why; the history lives in the issue and `git log`.
- **Instruction:** scans `CLAUDE.md`, `global-CLAUDE.md` (`extra_files`), every `SKILL.md` under `skills/` and `.claude/skills/` (`skill_dirs`) and the docs a skill links to. A finding the weekly `/prompt-audit` would file fails at ship time instead.
- **Doc references:** point the reference at what exists now; a pointer into another repo's tree goes in `allow_refs`, not the baseline.
- **Dead code:** delete the definition or call it. A real entry point the rules miss (a framework hook called by name) goes in `entry_decorators` or `allow`, not the baseline. Every vendored file is declared in `[vendored]` first, or its unused API is reported as this repo's.
- **Conventions:** `tests/**` is in `exclude`: tests are exempt from `no-window`, and every other rule already skips test modules. The local `spawn_scanner` checks stay beside it, because they also require the flag's value to be `NO_WINDOW`, scan `gh` spawns and walk the repo-root modules. The exempt markers in use are the KEY=value protocol prints in `skills/_lib/worktree/worktree_config.py` (`logging-not-print`), the machine-default fleet paths (`no-drive-path`), and the `os.replace` renames that move, claim or rotate a file without writing it (`must-use`).
- **Runtime write:** a write target is an open for writing, `Path.write_*`, `mkdir`, a log `FileHandler`, `sqlite3.connect` or a copy destination; the code folders are `code_folders`. Re-vendor both scripts together (`/propagate-vendored runtime_write_ratchet,runtime_write_resolve`). The stage brackets the whole acceptance run, `--snapshot` before the first check and `--probe` after the last, and fails on any untracked or ignored file the run left under `hooks/`, `skills/`, `scripts/` or `.claude/`. Runtime output belongs in the state dir or a temp dir, never beside the code. A helper whose every definition returns a home-, state- or temp-rooted path is listed in `safe_calls` in `.fleet.toml` (check each `return` expression before adding one), so writes through it need no marker. A write whose path comes from a parameter or an argument cannot be resolved statically, so it goes through the guarded writers instead: `skills/_lib/fleet_core/runtime_files.py` for the skill tier and `.claude/skills`, and its subset in `hooks/runtime_files/` for the hooks (the two import worlds cannot share a module; `tests/repo/test_runtime_files.py` holds the shared functions to the same code). Each writer (`make_dirs`, `touch_file`, `save_text`, `save_bytes`, `open_for_write`, `copy_file`, `copy_with_metadata`, `copy_tree`, `move`, `file_handler`, `connect_sqlite`, `connect_read_only`) resolves the path, links and junctions followed, and raises `ValueError` when it lands under a code folder. The one in-tree exception is `hooks/state`, the gitignored live hooks-state folder that `~/.claude/hooks` resolves to; `CODE_FOLDERS` in both copies must equal `code_folders` above (the test checks). A write that must land in a code folder on purpose (restoring a file into the checkout, installing the classifier into a target repo's `scripts/`) is not routed through a writer and carries `# runtime-write-exempt: <specific reason>` on the line above.

## Fixing a hit and burning a baseline down

1. Run the gate. A ratchet failure names the ratchet, the file and the rule or measure that rose.
2. Fix the finding: reword, delete, split, route through the helper. Raise a baseline only for a new rule's existing debt, with `--update-baseline --allow-additions`, and say so in the PR.
3. For a single legitimate exception, add the `# <rule>-exempt: <reason>` marker with a real reason.
4. To burn a baseline down, fix the entries, run the script with `--update-baseline` (it only drops stale entries), and update the Entries column above in the same change; the index check fails until the two agree.

```powershell
& E:/automation/fleet-config/.venv/Scripts/python.exe scripts/runtime_write_ratchet.py --update-baseline
```
