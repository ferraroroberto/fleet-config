# How the fleet guarantees code quality

A change reaches the main branch only after a set of deterministic checks has run against it, and no rule depends on an agent or a person remembering it. This page explains the philosophy, how the checks fit together, what each one catches, and how to take the whole system to another codebase. It is written to be read cold: every term is explained where it first appears, and nothing here requires knowing the fleet.

Contents: [Principles](#principles) · [The life of a check](#the-life-of-a-check) · [The layers](#the-layers) · [The ratchets](#the-ratchets) · [Gates beyond the ratchets](#gates-beyond-the-ratchets) · [Adding a new check](#adding-a-new-check) · [Porting this elsewhere](#porting-this-elsewhere) · [What is on by default](#what-is-on-by-default)

The ratchet scripts and their per-ratchet pages live in the public [project-scaffolding](https://github.com/ferraroroberto/project-scaffolding) repository, the one source every other repo copies from. [`ratchets.md`](ratchets.md) is this repository's own index of them.

## Principles

1. **A rule a machine can check is enforced by a check, never by memory.** Prose rules in an instruction file, a review checklist item or "remember to" all drift: they are skipped under time pressure, forgotten after a context reset, and never fail anything. A check is deterministic, runs every time and fails the change by name. A rule that truly cannot be checked mechanically stays prose, and says why.
2. **Existing debt is recorded, never hidden, and may only shrink.** A new check on an old codebase would fail on day one, and the usual answer is to exclude the offenders, which hides them forever. A *ratchet* instead records today's findings in a committed baseline file. A change fails only when a measure rises above its baseline entry, or a new offender appears. The baseline is a ledger to burn down to zero, not a list of permanent exceptions.
3. **"Could not check" is a failure, never a pass.** A check that did not run (a missing tool, an unreadable file, a failed `git` call, a path it could not resolve) has established nothing. Every check here reports that as its own state, `NOT CONFIRMED` (exit code 3 for the ratchets), and the gate fails on it. The same discipline holds for evidence: a gate pass is bound to a hash of the exact tree it ran against, and an unreadable state prints `UNKNOWN`, never `FRESH`.
4. **One source, copied byte for byte.** Each check is one script in one repository. Adopters copy it verbatim and record which commit it came from; they never edit the copy. A fix lands once, and a drift check finds any copy that differs from its source.
5. **Fix the finding; do not raise the baseline.** A ratchet failure is answered by rewording, deleting, splitting or routing through the right helper. Raising a baseline is for a brand-new rule's pre-existing debt only, needs an explicit flag, and shows as changed lines in review.
6. **Behaviour changes ship with the mechanism that enforces them.** A change that alters how agents must behave is built as a hook, a lint rule, a test or code. Adding instruction text is a last resort, its growth is reported as a number in the pull request, and a size cap bounds the instruction files.
7. **Keep the exceptions visible.** A single legitimate exception carries a marker comment of the form `<rule>-exempt: <reason>` next to the code, with a real reason. There is no silent allow-list.

## The life of a check

Every ratchet moves through the same stages, which is what makes them cheap to add and cheap to trust.

1. **Rule.** A convention is stated once, with its why, in an instruction file or a decision record.
2. **Scaffold script.** One standard-library-only script implements it in the source repository, with tests, and a page explaining what it counts and how to fix a hit. The source repository's decision record says a convention that can be checked mechanically becomes such a script by default; a one-off lint, hook or checklist item needs a stated reason it cannot be one.
3. **Vendored copy.** Each adopter copies the script byte for byte and declares it in a `[vendored]` table in its `.fleet.toml`, with the source commit. A re-vendoring command opens one pull request per adopter, verifies the hashes, runs the gate and merges; a drift check reports any copy that no longer matches or is behind.
4. **Opt-in configuration.** The adopter enables it in a `.fleet.toml` table named after the script (`enabled`, `baseline`, scope options). An absent table or `enabled = false` prints "not enabled" and exits 0, so re-vendoring never turns an adopter red on its own.
5. **Gate stage.** The check runs as its own stage of the repo's gate command, whole-tree and never routed by which paths changed (a count needs the whole tree). Exit 0 passes, 1 means a measure rose, 2 means bad configuration, 3 means not confirmed; anything but 0 fails.
6. **Baseline.** `--init` writes the first committed baseline, recording today's debt. After that `--update-baseline` only lowers or removes entries (and refuses to write if anything rose), while `--update-baseline --allow-additions` is the one flag that raises them.
7. **Burn-down.** Fixing entries and running `--update-baseline` shrinks the file. The baseline reaching zero is the goal; a rule whose baseline is zero guards against regression for free.

## The layers

The same rule can be caught at several points. Earlier is cheaper; later is more complete. No layer replaces another.

| Layer | When it runs | What it does | Where it lives |
| --- | --- | --- | --- |
| Edit-time hooks | Before or after each tool call an agent makes | Refuse a dangerous command, a secret in a command or file, an attribution trailer, an edit on the default branch; nudge on style slips; syntax-check each edited Python file | `hooks/`, catalogue in [`hooks.md`](hooks.md) |
| Commit and pull-request checks | When shipping a branch | Debug-tag scan, red-first evidence for bug fixes, PR-body checker, instruction-growth report, UX proof before merging a UI change | `skills/_lib/`, wired in the finish skill |
| The gate | Every change, before the pull request | The acceptance run: hook matrix, tree-boundary and scope guards, every ratchet, the runtime-write bracket | `tests/run_acceptance.py` and `tests/acceptance/` |
| Independent review | Before an unattended change ships | A fresh agent with no memory of the build re-reads the diff and re-runs the gate | [`independent-review-gate.md`](independent-review-gate.md) |
| After merge | Right after the merge | Restart of the live process, a build-identity poll, deploy-coverage and skip-delta reports so "merged" is not reported as "shipped" | [`docs/skills/deploy-coverage-gate.md`](skills/deploy-coverage-gate.md), [`docs/skills/skip-delta.md`](skills/skip-delta.md) |
| Scheduled audits | Weekly, unattended | Judgment-based sweeps that no script can decide, filing deduplicated issues for the cleanup flow | see [Scheduled audits](#scheduled-audits) |

### Edit-time hooks

A hook is a small program the agent harness runs on a tool-call event. A `PreToolUse` hook can refuse the call; a `PostToolUse` hook can only report. The refusal message names the rule and the fix, so the agent corrects itself in the same turn. Hooks refuse through one shared helper that owns each harness's wire format, and they read their payload through one normalizing function, so a hook never learns a second harness's vocabulary.

The hooks that protect quality, in plain terms:

- **No attribution trailer** (`hooks/pre_commit_no_ai_trailer.py`): refuses an AI attribution trailer or session link in a commit message, in a `-F` message file, or in a `gh` issue or pull-request body.
- **Secret scan** (`hooks/secret_scan_guard.py`): refuses a live credential in a commit diff, in a command, in a `gh` body, and a command or tool call that would print a secret-bearing file.
- **Default-branch guard, edit half and commit half** (`hooks/branch_before_edit_guard.py`, `hooks/main_commit_guard.py`): a dispatched agent may not edit tracked files while its checkout sits on the default branch, and `git commit` on the default branch is refused unless a short-lived, path-scoped marker covers every staged file. Both halves are live in Claude Code; see [what is on by default](#what-is-on-by-default).
- **Dangerous operations** (`hooks/safe_kill_guard.py`): refuses a force-push to the default branch, `--no-verify`, a blanket kill of every Python process, and commands that would power off the host.
- **Environment discipline** (`hooks/venv_discipline.py`, `hooks/bash_windows_path_guard.py`, `hooks/bash_cmdexe_syntax_guard.py`): wrong-interpreter and wrong-shell mistakes that otherwise fail silently.
- **Docs hygiene** (`hooks/docs_dated_filename_guard.py`): refuses a new date-prefixed file under `docs/`, because history belongs in git and issues.
- **Syntax check** (`hooks/py_syntax_check.py`): compiles every edited Python file and reports all errors together; an unverifiable target is reported as unverified, not as fine.
- **UI proof before merge** (`hooks/ui_proof_merge_guard.py`): refuses a merge of a pull request that touches a UX surface without the recorded proof page.
- **Nudges** (`hooks/gh_body_file_guard.py`, `hooks/hub_bypass_warn.py`, `hooks/browser_stealth_lint.py`): non-blocking, for patterns that are usually a mistake but occasionally intended.

Hooks fail open for themselves: a crashing or missing hook does not block the call, because a broken guard must not wedge every session. That is why the same rule is usually backed by a later layer.

### Commit and pull-request checks

The finish flow runs these in order, each deterministic:

- **Gate evidence** ([`docs/skills/gate-evidence.md`](skills/gate-evidence.md)): runs the gate itself, hashes the whole working tree before and after, and records the exit code only if the tree did not change. Later questions ("is the gate still green?") are answered by comparing the current tree's hash, never from memory: `FRESH`, `STALE`, `MISSING` or `UNKNOWN`.
- **Red-first evidence** for bug fixes: the new regression test must be seen failing on the pre-fix tree, recorded the same way. A bug with no automatable test says so in the pull-request body with a reason.
- **Debug tags** ([`docs/skills/debug-tags.md`](skills/debug-tags.md)): temporary diagnostic output carries a `[DEBUG-<id>]` tag, and a branch that still adds a tagged line cannot ship.
- **Instruction delta** ([`docs/skills/instruction-delta.md`](skills/instruction-delta.md)): reports how many lines of instruction text the branch adds, so prose growth is a number in the pull request.
- **PR-body checker** ([`docs/skills/pr-body-check.md`](skills/pr-body-check.md)): one script for every rule a pull-request body must keep: closing references match the branch's issue exactly, no images, no attribution, a merge-danger line ([`docs/skills/merge-danger.md`](skills/merge-danger.md)), red-first evidence on a bug, and no ticked checklist box without a matching verified acceptance verdict.
- **UX conformance and end-to-end routing** ([`docs/skills/ux-conformance-gate.md`](skills/ux-conformance-gate.md), [`docs/skills/e2e.md`](skills/e2e.md)): a diff-keyed decision on whether the design check and the browser suite apply, which only ever escalates when uncertain.

## The ratchets

Nine ratchets gate this repository. Each section below says what it catches, why, and what a hit and its fix look like. The mechanics (flags, exit codes, baseline format, scope options) are the same for all of them and are described in [The life of a check](#the-life-of-a-check); the linked scaffold page for each ratchet is the full reference.

### Static analysis

Catches duplicated blocks (via a pinned `jscpd`), unused imports and variables, a curated set of bug-prone and security-sensitive patterns (a mutable default argument, `return` inside `finally`, `eval` of data, an HTTP call with no timeout, `shell=True`) and import order. The curated list is limited to codes whose hits a reviewer would fix rather than argue with. Why: an audit re-finds the same duplication every week while the fix lives in someone's head; a gate stage encodes the lesson in structure. It needs `ruff` and Node in the gate's environment, and a missing tool is exit 3. A repository that cannot install one switches that half off with `duplicates = false` (no Node) or `ruff = false` (no ruff) in its table; the run then prints `OFF ... not checked, not clean` on every pass instead of passing silently, and turning both off is a configuration error.

```python
# hit: the default list is created once and shared by every call
def collect(item, seen=[]):
    seen.append(item)
    return seen

# fix
def collect(item, seen=None):
    seen = [] if seen is None else seen
    seen.append(item)
    return seen
```

Reference: [static ratchet](https://github.com/ferraroroberto/project-scaffolding/blob/main/docs/static-ratchet.md).

### Docs

Catches a `README.md` past its size budget, and history markers in `README.md` and `docs/`: an issue-number reference (a hash sign followed by digits), a heading holding a date, a "Changelog" or "Release notes" heading. Why: documentation states what is true now and why, in the present tense; the issue tracker, pull requests and `git log` hold history, and narrative in docs goes stale and buries the reference. A hit is fixed by rewriting the sentence as a present-tense fact, or by moving a long section into a topic file under `docs/` and leaving a one-line pointer.

Reference: [docs ratchet](https://github.com/ferraroroberto/project-scaffolding/blob/main/docs/docs-ratchet.md).

### Structure

Catches a file past 1,000 lines, a function past 100 lines, a directory holding more than 40 modules, an import from application code into library code, and top-level folders that do not match the repo's declared shape. A near-limit band keeps files at 900-1,000 lines and functions at 90-100 lines from creeping up. Why: a module that only grows becomes the 20,000-line file every agent then imitates. Each failure names the rule, the limit and where the code goes instead:

```
structure ratchet: 1 finding(s) above the baseline:
  [flat directory] src/ has 107 modules (limit 40); baseline 106
      put the new module in a domain subpackage, src/<domain>/.
```

The fix is to split by subject, never to raise the limit.

Reference: [structure ratchet](https://github.com/ferraroroberto/project-scaffolding/blob/main/docs/structure-ratchet.md).

### Comment

Counts issue-number references (a hash sign followed by digits) inside code comments, per file, in Python, JavaScript, CSS, PowerShell and YAML, using a real scanner for each language so strings and URLs do not count. Two further rules are off until enabled: references inside Python docstrings, and a short curated list of history phrases ("used to be", "formerly"). Why: a comment states why the code is the way it is now; how it got there lives in the issue and `git log`. A hit is fixed by rewriting the comment to state the present reason.

```python
# hit
# workaround for issue 4821, remove once the upstream client is fixed

# fix
# the upstream client drops the trailing slash, so it is added back here
```

Reference: [comment ratchet](https://github.com/ferraroroberto/project-scaffolding/blob/main/docs/comment-ratchet.md).

### Instruction files

Runs the deterministic rules of the weekly prompt audit over `CLAUDE.md`, `AGENTS.md`, rules files and every `SKILL.md`: pressure wording in capitals, forced re-check lines, reasoning scripts written for older models, size caps (200 lines for a `CLAUDE.md`, 500 for a skill body), skill-description limits, contradictions inside one file, vendor names in agent-neutral text, and backslash paths in skills. Optional file-shape rules cover a CI-expectations section, an `AGENTS.md` that is a pointer rather than a copy, a root `CLAUDE.md` line budget, and text a `CLAUDE.md` repeats from `docs/`. Why: an instruction file is loaded into every session, so growth and drift cost everywhere, and a finding the weekly audit would file is cheaper refused at ship time. The fix is to delete the line, shorten it, or replace repeated text with a pointer to the doc that owns it.

Reference: [instruction ratchet](https://github.com/ferraroroberto/project-scaffolding/blob/main/docs/instruction-ratchet.md).

### Doc references

Reads `README.md`, `CLAUDE.md` and `docs/**/*.md`, extracts every repository path, `python -m` module, relative link and loopback port, and resolves each against the tracked tree and the repo's declared port. It also flags every use of a *retired name* the repo lists: a flag, skill, environment variable or config key that was removed. Why: a doc naming a script that moved sends the next reader down a dead end, and nothing else fails when code moves. The fix is to point at what exists now; a retired name is added to the list in the same pull request that removes it, so it cannot return in prose.

Reference: [doc references ratchet](https://github.com/ferraroroberto/project-scaffolding/blob/main/docs/doc-refs-ratchet.md).

### Dead code

Parses every Python file with the standard library `ast` and reports a function, class or method whose name nothing in the tracked tree reads. Matching is by name, not by binding, so it misses dead code rather than inventing it; entry points (dunder methods, `__all__`, routed handlers, test functions, plugin registrations) are never reported. Why: a helper nobody calls, and a green test around it, is false confidence. The fix is to delete the definition or call it; a real entry point the rules cannot see goes in the allow list with a reason, not in the baseline.

Reference: [dead code ratchet](https://github.com/ferraroroberto/project-scaffolding/blob/main/docs/dead-code-ratchet.md).

### Conventions

A set of small `ast` and line rules, each opted into by name, each counted per file against the baseline. The available rules:

| Rule | A hit is |
| --- | --- |
| `no-window` | a subprocess spawn on Windows with no `creationflags=` (opens a console window) |
| `logging-not-print` | a `print` call in library code instead of the logging facility |
| `no-drive-path` | a string literal starting with a drive letter and separator |
| `git-wrapper` | a raw `git` spawn outside the repo's one wrapper that pins safe environment settings |
| `subprocess-timeout` | a blocking `subprocess` call with no `timeout=` |
| `must-use` | a call to a primitive (an atomic write, a pooled HTTP session, one database connection dependency) made outside its declared single-home helper; declared as data in the config, so a new helper needs an entry and no new code |
| `secret-compare`, `password-input-vendored`, `login-lockout` | an authentication-code convention, for repos that serve a sign-in page |
| `no-pwsh-stub` | a command that runs a placeholder executable that does nothing |

Why: each convention used to be guarded by a one-off test in whichever repo last hit the bug; this stage is the single vendored copy. A single legitimate call carries the exempt marker with its reason.

```python
# hit
subprocess.run(["git", "fetch"])

# fix
subprocess.run(["git", "fetch"], timeout=60, creationflags=NO_WINDOW)
```

Reference: [conventions ratchet](https://github.com/ferraroroberto/project-scaffolding/blob/main/docs/conventions-ratchet.md).

### Runtime writes

Finds every write target in the Python source (an `open` for writing, `write_text`, `mkdir`, a log file handler, `sqlite3.connect`, a copy destination) and resolves where it points. A new target under a code folder fails; a target it cannot resolve is exit 3, because unknown is never clean. A bracket around the whole gate run takes a snapshot before the first check and a probe after the last, and fails on any file the run left under a code folder. Why: logs, caches and databases next to the code pollute the working tree and the checkout. The fix is to resolve the path under a state or temporary directory.

```python
# hit
LOG = Path(__file__).parent / "run.log"

# fix
LOG = state_dir() / "run.log"
```

Reference: [runtime write ratchet](https://github.com/ferraroroberto/project-scaffolding/blob/main/docs/runtime-write-ratchet.md).

### Other ratchets in the family

The source repository carries ratchets this repository does not adopt because it has no web interface. The [usage track ratchet](https://github.com/ferraroroberto/project-scaffolding/blob/main/docs/usage-track-ratchet.md) fails a new interactive control, dialog or sheet that carries no usage-tracking id, and a web app that does not mount its usage log.

## Gates beyond the ratchets

The acceptance run also holds checks that are not baselines because they have nothing to burn down: they either pass or fail.

- **Hook matrix** (`tests/acceptance/hook_matrix.py`): every hook is driven with sample payloads, per harness, and the expected exit code and output are asserted. A hook is not claimed to work until it has been driven through this.
- **Spawn scanner** (`tests/acceptance/spawn_scanner.py`): a static scan that every subprocess spawn passes the no-window flag with the right value and that every `git` call goes through the wrapper.
- **Tree boundary** (`tests/acceptance/tree_boundary.py`): the hooks tree and the skills tree are linked into the agent homes independently, so a hook must import nothing from the skills tree.
- **Fleet-scope guard** (`tests/acceptance/fleet_scope_guard.py`): every route that enumerates repos fleet-wide honours the switch that excludes dormant ones, or is recorded as deliberately unfiltered with a reason.
- **Index drift** (`tests/acceptance/docs_index/`): [`ratchets.md`](ratchets.md) and the skills index must agree with the configuration, the gates and the baselines; adding a ratchet, option, rule or baseline entry without updating the index fails by name.
- **Foreign tool-id leak scanner**: a harness's tool names must not leak into another harness's instructions.
- **Vendored drift** (`skills/_lib/vendored_drift.py`): reports a copied component that differs from the bytes it claims, one that is behind its source, and a repo that carries a component without declaring it (which a re-vendoring wave would otherwise skip silently).
- **Decision-register sync** (in the source repository): every open architecture-decision issue must be listed in the register exactly once, offline, from a committed snapshot.

## Scheduled audits

Some quality questions need judgment (is this duplication, is this code stale, does this prompt follow the vendor guidance). Those run weekly and unattended, and file deduplicated issues rather than failing a change. Each is read-only except for filing issues:

- **Codebase audit** ([`docs/skills/codebase-audit.md`](skills/codebase-audit.md)) across every repo ([`docs/skills/audit-fleet.md`](skills/audit-fleet.md)): duplication, stale and dead code, drift from the project's own instructions, maintainability, bloat, bugs and documentation, bundled into at most seven issues per repo. A security gap is fixed in place with a redacted issue and a regression test, never filed as a public checklist.
- **Prompt audit** ([`docs/skills/prompt-audit.md`](skills/prompt-audit.md)): every instruction file against the vendors' current prompting guidance, after first checking whether those guides changed.
- **Context audit**, **design sweep**, **performance sweep**, **end-to-end suite audit** ([`docs/skills/context-audit.md`](skills/context-audit.md), [`docs/skills/design-sweep.md`](skills/design-sweep.md), [`docs/skills/perf-sweep.md`](skills/perf-sweep.md), [`docs/skills/e2e-audit.md`](skills/e2e-audit.md)): always-on context size, design-system drift, phone load speed against budgets, and test-suite bloat.

The pattern is a loop. An audit finds a class of problem by judgment; when the class is mechanical, it is promoted to a ratchet, which then stops the next occurrence at ship time, and the audit stops finding it.

## Adding a new check

1. **Decide the shape.** One pattern over Python syntax or text lines joins the conventions ratchet as an opt-in rule. A single-home helper is a `must-use` data entry. A name a repo removes is a retired-name entry. Something with its own perimeter, baseline semantics or size measure is a new ratchet. A rule with no mechanical definition stays prose and says why.
2. **Measure before building.** Run the candidate detector over every repository, read a sample of its hits by hand, and keep only what a reviewer would fix rather than argue with. Record the hit counts in the pull request.
3. **Build it in the source repository.** One standard-library script, tests for each rule and for the exit codes (including not confirmed), a page explaining what is counted and how to fix a hit, and the opt-in table.
4. **Land it dark.** It is off by default, so re-vendoring changes nothing for an adopter that has not asked.
5. **Propagate.** Re-vendor to adopters; each enables it and commits an `--init` baseline in the same pull request, so it goes in green.
6. **Wire the stage.** Add it to the gate as its own stage (not path-routed, exit 3 failing) and to the index page so the drift check passes.
7. **Burn down.** File the per-repository cleanups from the baselines and work them to zero.

## Porting this elsewhere

The system is mostly independent of this repository's agent tooling. A project can adopt it in layers, smallest first.

**The fastest route is the public adoption kit.** The source repository ships [`docs/adopting-ratchets.md`](https://github.com/ferraroroberto/project-scaffolding/blob/main/docs/adopting-ratchets.md) as the single entry point for a codebase outside this fleet. It lists the prerequisites (Python 3.11 or newer, `git`, and `ruff` plus Node only for the static ratchet), then two scripts do the work:

```sh
python <scaffold>/scripts/adopt_ratchets.py <target> [--ratchets comment,structure,runtime_write]
python scripts/run_ratchets.py --tests python -m pytest -q     # run in the target; wire into its gate or CI
```

`adopt_ratchets.py` copies each chosen ratchet and its sibling modules into `<target>/scripts/`, records every copy in `.fleet.toml`, appends a minimal `enabled = true` table, and runs `--init` to write the first baseline. `--ratchets` takes a list, `recommended` (the default: the Python-only ratchets that need no choices) or `all`. It is idempotent, and a copy that differs from the source is refused, never overwritten. `run_ratchets.py` is a cross-platform runner: it runs every enabled ratchet in gate order, then takes the runtime-write snapshot, runs the test command and runs the probe, and reports exit 3 as not confirmed rather than skipping it. The kit also ships a CI workflow template and a ready-to-paste prompt for handing the adoption to a coding agent. Nothing in it needs a private repository or tool.

The manual steps below are what those scripts automate.

**The minimum, which gives most of the value:**

1. Copy one or more ratchet scripts from the source repository, unchanged. The standard-library-only ones (structure, docs, comment, instruction, doc references, dead code, conventions, runtime writes) need Python 3.11 or newer and nothing else; the static ratchet also needs `ruff` and Node.
2. Add a `.fleet.toml` at the repository root with one table per ratchet, and run each script once with `--init` to commit its baseline:

   ```toml
   [structure_ratchet]
   enabled  = true
   baseline = ".structure-ratchet-baseline.json"

   [vendored]
   structure_ratchet = { src = "scripts/structure_ratchet.py", sha = "<source commit>", dest = "scripts/structure_ratchet.py" }
   ```

3. Run the scripts from one gate command that stops on any non-zero exit. The contract is only the exit code, so any task runner or CI job works:

   ```sh
   python scripts/structure_ratchet.py          # check; this is what the gate runs
   python scripts/structure_ratchet.py --init   # once, to record today's debt
   python scripts/structure_ratchet.py --update-baseline    # after fixing entries: shrink only
   ```

4. Make the rule that exit 3 fails the gate explicit in the gate's code, and keep each ratchet a dedicated stage (or use `run_ratchets.py`, which already fails on 3) rather than a generic "run these scripts, skip on 3" loop, where a shared skip code would read a not-confirmed result as a skip. Never route a ratchet by which paths changed: each compares the whole tree with its baseline.

**Worth adding next:**

- A hook or CI check that refuses secrets and attribution trailers in commits and pull-request bodies.
- The PR-body checker and the gate-evidence helper, which together stop "tests pass" from being a claim.
- A drift check over the `[vendored]` table, once more than one repository copies the scripts.
- A weekly audit job for the questions only judgment can answer, filing issues rather than failing builds.

**Leave behind:**

- Rules tied to one platform or toolchain you do not use. `no-window` and `no-pwsh-stub` exist for Windows; the authentication rules apply only to a repo that serves a sign-in page.
- The agent-harness plumbing: the hook wire-format normalization, the dispatcher, the dispatch and session tooling. A plain CI job covers the same ground for a project with no agents.
- Fleet-scope switches, the chief orchestrator, per-machine project tables and notification transports.
- This repository's thresholds where yours differ. Every limit (file length, function length, directory size, the instruction-file caps) is a default in the script and is overridden in the `.fleet.toml` table.

## What is on by default

Not everything described here is switched on everywhere. State as of the current main branch:

- **On in this repository's gate:** all nine ratchets; the hook matrix, spawn scanner, tree boundary, fleet-scope guard, index drift and tool-id leak checks; the debug-tag scan, instruction delta, gate evidence, red-first evidence and PR-body checker in the finish flow.
- **Off until a repository opts in:** the comment ratchet's docstring and history-phrase rules; the conventions ratchet's `secret-compare`, `password-input-vendored`, `login-lockout` and `no-pwsh-stub` rules (this repository leaves them off, see [`ratchets.md`](ratchets.md) for each reason); the instruction ratchet's file-shape and repeated-text rules, which a repository enables per table; every ratchet in a repository that has not declared its table.
- **Awaiting adoption in other repositories:** the newest rules, such as the docstring and history-phrase counts, the import-order rule and the instruction-file shape rules, have landed in the source repository as opt-in. Each repository records its existing debt with `--init` or `--update-baseline --allow-additions` in its own pull request, and the per-repository burn-down follows from those baselines.
- **Live but harness-limited:** the default-branch commit guard is wired in the settings template and in the live agent settings, so it blocks `git commit` on the default branch in Claude Code. The other harnesses do not support the event (see [`cross-agent-parity.md`](cross-agent-parity.md)). A new machine takes effect once its live settings are refreshed from the template, and the acceptance run's settings-sync check fails by name until they are.
- **Available to any repository:** the public adoption kit described under [Porting this elsewhere](#porting-this-elsewhere).
- **Advisory by design:** the nudge hooks, the structure ratchet's warning band, and CI, which is advisory because the local gate is the contract.
