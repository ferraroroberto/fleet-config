"""Acceptance checks for `venv_discipline.py`'s delete guards.

Rule 4 (a delete that would walk through a worktree's `.venv` junction),
rule 5 (a delete aimed at a primary checkout's real `.venv`, `git clean -x`
included), both across a leading directory change, and the
destructive-action audit trail. Every check drives the real hook subprocess
against real temp directories and junctions: the hazard is a filesystem fact,
so a mocked reparse point would prove nothing.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from acceptance.shared import HOOKS, _Checker, run


def _venv_junction_guard_unit_checks() -> Tuple[int, int]:
    """`venv_discipline.py` rule 4 and its two false-positive fixes
    (fleet-config#847).

    Drives the real hook subprocess against a real primary `.venv` and a real
    worktree whose `.venv` is a real junction to it -- the hazard is a
    filesystem fact, so a mocked reparse point would prove nothing.

    The incident these cover: a lane ran `git worktree remove --force
    <worktree>` on a worktree whose `.venv` junction was still in place, and
    git's recursive delete walked it into `task-os`'s primary venv while a live
    app was importing from it. The allow-cases at the bottom are the guard's
    own false positives -- it refused the issue that asked for this rule,
    because the body quoted the hazard it describes.
    """
    check = _Checker()

    tmp = Path(tempfile.mkdtemp(prefix="venv_junction_"))
    try:
        primary = tmp / "repo"
        real_venv = primary / ".venv"
        real_venv.mkdir(parents=True)
        (real_venv / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")

        worktree = tmp / "repo-wt-847"
        worktree.mkdir()
        link = worktree / ".venv"
        if sys.platform == "win32":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(real_venv)],
                           capture_output=True, encoding="oem", errors="replace")
        else:
            os.symlink(real_venv, link, target_is_directory=True)

        sys.path.insert(0, str(HOOKS))
        import venv_discipline  # noqa: E402

        if not venv_discipline.is_reparse_point(link):
            check.advisory("venv_junction: could not create a real junction -- cases skipped",
                           False, f"mklink/symlink produced no reparse point at {link}")
            return check.failures, check.total

        def verdict(command: str, cwd: Path = worktree, tool: str = "Bash") -> int:
            code, _out, _err = run("venv_discipline",
                                   {"tool_name": tool, "cwd": str(cwd),
                                    "tool_input": {"command": command}})
            return code

        _venv_junction_block_cases(check, verdict, tmp, primary, worktree, link)

        _venv_junction_allow_cases(check, verdict, tmp, primary, worktree, link)
    finally:
        if sys.platform == "win32":
            subprocess.run(["cmd", "/c", "rmdir", str(tmp / "repo-wt-847" / ".venv")],
                           capture_output=True, encoding="oem", errors="replace")
        shutil.rmtree(tmp, ignore_errors=True)

    return check.failures, check.total


def _venv_junction_block_cases(check: _Checker, verdict: Callable[..., int], tmp: Path,
                               primary: Path, worktree: Path, link: Path) -> None:
    """Rule 4: every delete that would follow the junction into the primary venv is refused."""
    # ---- rule 4: deletes that would follow the junction -> block ----
    check("venv_junction: `git worktree remove --force <wt>` -> block",
          verdict(f"git worktree remove --force {worktree}", cwd=primary) == 2)
    check("venv_junction: `git -C <repo> worktree remove <wt>` -> block",
          verdict(f"git -C {primary} worktree remove {worktree}", cwd=primary) == 2)
    check("venv_junction: `rm -rf <wt>` -> block",
          verdict(f"rm -rf {worktree}", cwd=primary) == 2)
    check("venv_junction: `rm -rf <wt>/.venv` (the junction itself) -> block",
          verdict(f"rm -rf {link}", cwd=primary) == 2)
    check("venv_junction: `Remove-Item -Recurse <wt>` -> block",
          verdict(f"Remove-Item -Recurse -Force {worktree}", cwd=primary,
                  tool="PowerShell") == 2)
    check("venv_junction: `rmdir /s <wt>` -> block",
          verdict(f"rmdir /s /q {worktree}", cwd=primary) == 2)
    # PowerShell resolves any unambiguous parameter prefix, so the guard
    # must match the abbreviations too, not just the spelled-out switch.
    check("venv_junction: `Remove-Item -r` (PowerShell abbreviation) -> block",
          verdict(f"Remove-Item -r -Force {worktree}", cwd=primary,
                  tool="PowerShell") == 2)
    check("venv_junction: `Remove-Item -Rec` -> block",
          verdict(f"Remove-Item -Rec {worktree}", cwd=primary, tool="PowerShell") == 2)
    check("venv_junction: `del /s` through the junction -> block",
          verdict(f"del /s /q {worktree}", cwd=primary) == 2)
    # The shell expands a glob long after the hook sees the command, so an
    # operand carrying one is reduced to the directory it expands within.
    check("venv_junction: `rm -rf *` from the worktree root -> block",
          verdict("rm -rf *", cwd=worktree) == 2)
    check("venv_junction: `rm -rf <wt>/*` -> block",
          verdict(f"rm -rf {worktree}/*", cwd=primary) == 2)
    check("venv_junction: `rm -rf build/*` (no junction there) -> allow",
          verdict("rm -rf build/*", cwd=worktree) == 0)
    check("venv_junction: relative operand resolved against payload cwd -> block",
          verdict("rm -rf repo-wt-847", cwd=tmp) == 2)
    # The incident command was typed in Git Bash, so its operand was an
    # MSYS path: `/e/automation/task-os-wt-225`. That has a root but no
    # drive, so it is not `is_absolute()` on Windows -- and its leading `/`
    # looks exactly like a cmd switch. Both traps are on the one path shape
    # that actually caused fleet-config#847, so it gets its own case.
    if sys.platform == "win32" and str(tmp)[1:3] == ":\\":
        msys = "/" + str(worktree)[0].lower() + str(worktree)[2:].replace("\\", "/")
        check("venv_junction: MSYS operand (`/e/automation/...`) -> block (#847)",
              verdict(f"git worktree remove --force {msys}", cwd=primary) == 2)
        check("venv_junction: MSYS operand under `rm -rf` -> block (#847)",
              verdict(f"rm -rf {msys}", cwd=primary) == 2)
    check("venv_junction: venv creation over the junction -> block",
          verdict(r"& .\.venv\Scripts\python.exe -m venv .venv",
                  cwd=worktree, tool="PowerShell") == 2)
    check("venv_junction: venv reset over the junction -> block",
          verdict(r"& .\.venv\Scripts\python.exe -m venv --clear .venv",
                  cwd=worktree, tool="PowerShell") == 2)
    check("venv_junction: hazard in a later clause is not laundered by the first -> block",
          verdict(f"echo hi; rm -rf {worktree}", cwd=primary) == 2)


def _venv_junction_allow_cases(check: _Checker, verdict: Callable[..., int], tmp: Path,
                               primary: Path, worktree: Path, link: Path) -> None:
    """Safe forms and the guard's two former false positives (flags, heredoc text) stay allowed."""
    # ---- the safe forms stay allowed ----
    check("venv_junction: `rmdir <junction>` with no /s (reparse-safe) -> allow",
          verdict(f"rmdir {link}", cwd=primary) == 0)
    check("venv_junction: `worktree_claim.py remove-worktree <wt>` -> allow",
          verdict(f"E:/automation/fleet-config/.venv/Scripts/python.exe "
                  f"skills/_lib/worktree_claim.py remove-worktree {worktree}",
                  cwd=primary) == 0)
    check("venv_junction: `rm -rf` on a real .venv (not a junction) -> allow",
          verdict(f"rm -rf {primary}", cwd=tmp) == 0)
    check("venv_junction: `rm -rf` on a path with no .venv at all -> allow",
          verdict(f"rm -rf {tmp / 'nothing-here'}", cwd=tmp) == 0)
    check("venv_junction: `git worktree remove` on a junction-free worktree -> allow",
          verdict(f"git worktree remove {tmp / 'plain-wt'}", cwd=primary) == 0)

    # ---- false positive 1: flags are not directory names ----
    # Pre-fix this blocked, reporting `--clear` as the bad directory name.
    check("venv_flags: venv reset with the canonical name -> allow (#847)",
          verdict("python -m venv --clear .venv", cwd=tmp) == 0)
    check("venv_flags: venv creation with a flag then the canonical name -> allow (#847)",
          verdict("python -m venv --system-site-packages .venv", cwd=tmp) == 0)
    check("venv_flags: a flag before a WRONG name still blocks the name",
          verdict("python -m venv --clear venv", cwd=tmp) == 2)
    check("venv_flags: wrong name with no flags still blocks (unchanged)",
          verdict("python -m venv venv", cwd=tmp) == 2)

    # ---- false positive 2: a heredoc body written to a file is text ----
    # This exact shape refused fleet-config#847 from being filed.
    doc = tmp / "issue.md"
    hazard_text = "The shape is consistent with a venv reset or creation\n" \
                  "run against a junctioned path -- `python -m venv venv`.\n"
    heredoc = f"cat > {doc} <<'DOCEOF'\n{hazard_text}DOCEOF\n"
    check("venv_heredoc: file-redirected heredoc quoting the hazard -> allow (#847)",
          verdict(heredoc, cwd=tmp) == 0)
    check("venv_heredoc: `>>` append heredoc quoting the hazard -> allow (#847)",
          verdict(heredoc.replace(f"> {doc}", f">> {doc}"), cwd=tmp) == 0)
    check("venv_heredoc: `gh issue create --body-file -` heredoc -> allow (#847)",
          verdict(f"gh issue create --body-file - <<'MD'\n{hazard_text}MD\n", cwd=tmp) == 0)
    # ... but a body the consuming command EXECUTES still counts, whether or
    # not the line carries a file redirect. `python - <<'PY' > out.txt` is
    # the case a redirect-only discriminator would have wrongly stripped.
    check("venv_heredoc: non-redirected heredoc piping a real command -> block",
          verdict(f"cat <<'SHEOF' | sh\n{hazard_text}SHEOF\n", cwd=tmp) == 2)
    check("venv_heredoc: executed heredoc that ALSO redirects to a file -> block",
          verdict(f"sh - <<'SHEOF' > {tmp / 'out.txt'}\n{hazard_text}SHEOF\n", cwd=tmp) == 2)
    check("venv_heredoc: a real command AFTER a stripped heredoc still blocks",
          verdict(heredoc + "python -m venv venv\n", cwd=tmp) == 2)


def _venv_primary_guard_unit_checks() -> Tuple[int, int]:
    """`venv_discipline.py` rule 5 + the destructive-action audit trail
    (fleet-config#828).

    The incident: an unattended `/cleanup-fleet-all` lane emptied
    `email-archiver`'s primary `.venv` -- directory surviving, contents gone --
    and the command was never identified, because the run retained only
    per-sub-agent progress markers. Rule 4 (#847) answers only a delete that
    walks *through* a worktree's junction; the evidence could not rule out one
    aimed at the primary's own `.venv`, and that route was undefended.

    The first block below is the **reproduction**: it runs the candidate
    commands for real against a real primary-plus-junctioned-worktree topology
    and asserts which one actually produces the incident's signature. Of the
    plausible candidates only `git worktree remove --force` over a live
    junction does -- `rm -rf`, `Remove-Item -Recurse` and `shutil.rmtree` all
    delete the reparse point without following it. That is what makes the
    verdict cases below a regression test rather than an assertion about a
    hazard nobody has demonstrated.

    Every rule-5 case exits 0 against pre-fix code, where a recursive delete of
    a primary checkout's real venv was explicitly out of scope
    (`junctioned_venv_under`: "a real `.venv` directory is the caller's own to
    delete").
    """
    check = _Checker()

    tmp = Path(tempfile.mkdtemp(prefix="venv_primary_"))
    try:
        _venv_primary_repro(check, tmp)

        # ---- rule 5 verdicts, against a real primary checkout
        state = tmp / "state"
        repo = tmp / "repo"
        (repo / ".git").mkdir(parents=True)      # primary checkout: `.git` is a DIR
        (repo / ".venv" / "Scripts").mkdir(parents=True)
        (repo / "build").mkdir()
        # A linked worktree carrying its OWN real venv: `.git` is a gitFILE, so
        # this venv is the worktree's to dispose of and must stay allowed.
        sibling = tmp / "repo-wt-2"
        (sibling / ".venv").mkdir(parents=True)
        (sibling / ".git").write_text("gitdir: ../repo/.git/worktrees/repo-wt-2",
                                      encoding="utf-8")

        def verdict(command: str, cwd: Path, tool: str = "Bash") -> int:
            code, _out, _err = run("venv_discipline",
                                   {"tool_name": tool, "cwd": str(cwd),
                                    "session_id": "sess-828",
                                    "tool_input": {"command": command}},
                                   {"CLAUDE_HOOKS_STATE_DIR": str(state)})
            return code

        check("venv_primary: `rm -rf <repo>/.venv` -> block",
              verdict(f"rm -rf {repo}/.venv", cwd=repo) == 2)
        check("venv_primary: bare `rm -rf .venv` from the checkout -> block",
              verdict("rm -rf .venv", cwd=repo) == 2)
        check("venv_primary: `Remove-Item -Recurse -Force .venv` -> block",
              verdict("Remove-Item -Recurse -Force .venv", cwd=repo,
                      tool="PowerShell") == 2)
        check("venv_primary: `rmdir /s /q .venv` -> block",
              verdict("rmdir /s /q .venv", cwd=repo) == 2)
        check("venv_primary: `rm -rf <repo>` (the checkout holding it) -> block",
              verdict(f"rm -rf {repo}", cwd=tmp) == 2)

        check("venv_primary: `rm -rf <repo>/build` (no venv there) -> allow",
              verdict(f"rm -rf {repo}/build", cwd=repo) == 0)
        check("venv_primary: a worktree's OWN real venv is its own to delete -> allow",
              verdict(f"rm -rf {sibling}/.venv", cwd=tmp) == 0)
        check("venv_primary: `git worktree remove --force` on a junction-free "
              "worktree -> allow",
              verdict(f"git worktree remove --force {sibling}", cwd=repo) == 0)
        # The escape hatch rule 5's own refusal points at. A guard that blocks
        # its own remedy is the expensive kind of wrong (#464/#472, #847).
        check("venv_primary: `python -m venv --clear .venv` in place -> allow",
              verdict(r"& .\.venv\Scripts\python.exe -m venv --clear .venv",
                      cwd=repo, tool="PowerShell") == 0)

        _venv_primary_audit_trail_cases(check, state, repo)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return check.failures, check.total


def _venv_primary_repro(check: _Checker, tmp: Path) -> None:
    """Which candidate delete actually empties a primary venv through a worktree junction."""
    # ---- reproduction: does the hazard actually exist, and in which form?
    if sys.platform == "win32" and shutil.which("git"):
        repro = tmp / "repro"
        primary = repro / "alpha"
        (primary / "src").mkdir(parents=True)
        (primary / ".gitignore").write_text(".venv/\n", encoding="utf-8")
        for args in (("init", "-q", "-b", "main"), ("config", "user.email", "t@t"),
                     ("config", "user.name", "t"), ("add", "-A"),
                     ("commit", "-qm", "init")):
            subprocess.run(["git", *args], cwd=str(primary), capture_output=True)
        real = primary / ".venv"
        real.mkdir()
        (real / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
        wt = repro / "alpha-wt-99"
        subprocess.run(["git", "worktree", "add", "-q", "-b", "fix/99-x", str(wt)],
                       cwd=str(primary), capture_output=True)
        subprocess.run(["cmd", "/c", "mklink", "/J", str(wt / ".venv"), str(real)],
                       capture_output=True, encoding="oem", errors="replace")
        if (wt / ".venv").exists():
            subprocess.run(["git", "worktree", "remove", "--force", str(wt)],
                           cwd=str(primary), capture_output=True)
            check("venv_primary: repro -- `git worktree remove --force` over a live "
                  "junction empties the primary venv, directory surviving (#828 signature)",
                  real.is_dir() and not any(real.iterdir()),
                  f"{real}: exists={real.is_dir()} "
                  f"children={sorted(p.name for p in real.iterdir()) if real.is_dir() else '-'}")
        else:
            check.advisory("venv_primary: repro -- junction could not be created, "
                           "reproduction skipped", False, str(wt / ".venv"))
    else:
        check.advisory("venv_primary: repro needs Windows junctions + git -- skipped",
                       False, f"platform={sys.platform} git={bool(shutil.which('git'))}")


def _venv_primary_audit_trail_cases(check: _Checker, state: Path, repo: Path) -> None:
    """Every destructive clause, refused or allowed, is attributable after the fact."""
    # ---- the audit trail (#828 acceptance 3: attributable after the fact)
    trail = state / "destructive-actions.jsonl"
    check("venv_primary: audit trail written under the state dir", trail.is_file(),
          f"expected {trail}")
    rows = [json.loads(line) for line in
            trail.read_text(encoding="utf-8").splitlines()] if trail.is_file() else []
    blocked = [r for r in rows if r["verdict"] == "BLOCK"]
    allowed = [r for r in rows if r["verdict"] == "ALLOW"]
    check("venv_primary: every refused clause is recorded", len(blocked) == 5,
          f"got {len(blocked)}: {[r['verb'] for r in blocked]}")
    # The whole point: a refusal the agent alone sees answers "was it
    # stopped", never "what ran". An ALLOWed destructive clause is the one
    # #828 needed a record of and did not have.
    check("venv_primary: allowed destructive clauses are recorded too",
          len(allowed) == 4, f"got {len(allowed)}: {[r['verb'] for r in allowed]}")
    check("venv_primary: each record carries the fields a post-mortem needs",
          bool(rows) and all(
              set(r) >= {"ts", "verdict", "rule", "verb", "operand", "venv",
                         "tool", "cwd", "launcher_session", "session", "command"}
              for r in rows),
          f"first record keys: {sorted(rows[0]) if rows else '(none)'}")
    check("venv_primary: the record names the session and the venv at risk",
          bool(blocked) and all(r["session"] == "sess-828" for r in blocked)
          and all(r["venv"] for r in blocked),
          f"{[(r['session'], r['venv']) for r in blocked]}")
    check("venv_primary: both hazard routes are distinguishable in the trail",
          {r["rule"] for r in blocked} == {"primary"},
          f"rules seen: {sorted({r['rule'] for r in blocked})}")
    # An unwritable state dir must never take the fleet's Bash tool down
    # with it -- the audit is best-effort by construction.
    check("venv_primary: an unwritable audit path still allows a clean command",
          run("venv_discipline",
              {"tool_name": "Bash", "cwd": str(repo),
               "tool_input": {"command": f"rm -rf {repo}/build"}},
              {"CLAUDE_HOOKS_STATE_DIR": str(repo / ".venv" / "Scripts" / "python.exe")}
              )[0] == 0)


def _venv_git_clean_guard_unit_checks() -> Tuple[int, int]:
    """`venv_discipline.py` rules 5 + the audit trail over `git clean`
    (fleet-config#867).

    `-x` drops the ignore rules, and every fleet repo ignores `.venv`, so
    `git clean -xfd` in a primary checkout deletes its real venv outright --
    the route #866's reviewer found neither refused nor recorded. The first
    block is the **reproduction** with real git: it establishes the hazard,
    the `-e .venv` escape hatch the refusal points at, and that a worktree's
    junctioned `.venv` is only unlinked (which is why rule 4 must not fire on
    `git clean`). Every block case below exits 0 against pre-fix code, where
    `destructive_operands` had no `git clean` branch at all.
    """
    check = _Checker()

    tmp = Path(tempfile.mkdtemp(prefix="venv_git_clean_"))
    junctions = []
    try:
        _venv_git_clean_repro(check, tmp, junctions)

        # ---- verdicts, against a synthetic primary checkout
        state = tmp / "state"
        repo = tmp / "repo"
        (repo / ".git").mkdir(parents=True)      # primary checkout: `.git` is a DIR
        (repo / ".venv" / "Scripts").mkdir(parents=True)
        (repo / "src").mkdir()
        bare = tmp / "novenv"                    # a primary with nothing to lose
        (bare / ".git").mkdir(parents=True)

        def verdict(command: str, cwd: Path, tool: str = "Bash") -> Tuple[int, str]:
            code, _out, err = run("venv_discipline",
                                  {"tool_name": tool, "cwd": str(cwd),
                                   "session_id": "sess-867",
                                   "tool_input": {"command": command}},
                                  {"CLAUDE_HOOKS_STATE_DIR": str(state)})
            return code, err

        msys_repo = "/" + str(repo)[0].lower() + str(repo)[2:].replace("\\", "/")
        blocks = [
            ("`git clean -xfd` in the primary", "git clean -xfd", repo, "Bash"),
            ("`git -C <repo> clean -xdf`", f"git -C {repo} clean -xdf", tmp, "Bash"),
            ("`git -C <msys path> clean -xdf`", f"git -C {msys_repo} clean -xdf", tmp, "Bash"),
            ("PowerShell `git -C <repo> clean -dfx`", f"git -C '{repo}' clean -dfx", tmp,
             "PowerShell"),
            ("`git clean -Xfd` (ignored-only still takes .venv)", "git clean -Xfd", repo, "Bash"),
            ("`git clean -x -f -d` (split flags)", "git clean -x -f -d", repo, "Bash"),
            ("`git clean -xf .` (a pathspec makes -d irrelevant)", "git clean -xf .", repo,
             "Bash"),
            ("`git clean -xf -- .venv`", "git clean -xf -- .venv", repo, "Bash"),
            ("global options before the verb", "git --no-pager -c core.quotepath=off clean -dxf",
             repo, "Bash"),
            ("`--attr-source <tree>` global option (the set that drifted from _lib, #1304)",
             "git --attr-source HEAD clean -xfd", repo, "Bash"),
            ("an unrelated `-e` does not protect .venv", "git clean -xfd -e build", repo, "Bash"),
            ("`-X -e .venv` still deletes it", "git clean -Xfd -e .venv", repo, "Bash"),
            ("a trailing redirect is not a pathspec", "git clean -xfd 2>&1", repo, "Bash"),
            ("a compound command's later clause", "git status && git clean -xfd", repo, "Bash"),
        ]
        stderr_seen = ""
        for label, command, cwd, tool in blocks:
            code, err = verdict(command, cwd, tool)
            stderr_seen = stderr_seen or err
            check(f"venv_git_clean: {label} -> block", code == 2, f"exit={code} cmd={command}")
        check("venv_git_clean: the refusal names the `-e .venv` escape hatch",
              "-e .venv" in stderr_seen, stderr_seen[:300])

        recorded = _venv_git_clean_allow_cases(check, verdict, tmp, repo, bare, junctions)

        trail = state / "destructive-actions.jsonl"
        rows = [json.loads(line) for line in
                trail.read_text(encoding="utf-8").splitlines()] if trail.is_file() else []
        blocked = [r for r in rows if r["verdict"] == "BLOCK"]
        allowed = [r for r in rows if r["verdict"] == "ALLOW"]
        check("venv_git_clean: every refused `git clean` is recorded as rule 5",
              len(blocked) == len(blocks)
              and all(r["rule"] == "primary" and r["venv"] for r in blocked),
              f"got {[(r['verb'], r['rule']) for r in blocked]}")
        check("venv_git_clean: allowed destructive `git clean`s are recorded, dry runs "
              "and -x-less cleans are not",
              len(allowed) == len(recorded)
              and all(r["verb"].startswith("git clean") for r in allowed),
              f"got {[(r['verb'], r['command']) for r in allowed]}")
    finally:
        for link in junctions:
            try:
                os.rmdir(link)                   # reparse-safe: never follows it
            except OSError:
                pass
        shutil.rmtree(tmp, ignore_errors=True)

    return check.failures, check.total


def _venv_git_clean_repro(check: _Checker, tmp: Path, junctions: List[Path]) -> None:
    """What real `git clean` does to a real `.venv`, in a primary and through a worktree junction."""
    # ---- reproduction: what real `git clean` does to a real `.venv`
    if shutil.which("git"):
        def real_repo(name: str) -> Path:
            root = tmp / "repro" / name
            (root / "src").mkdir(parents=True)
            (root / ".gitignore").write_text(".venv/\n", encoding="utf-8")
            (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
            for args in (("init", "-q", "-b", "main"), ("config", "user.email", "t@t"),
                         ("config", "user.name", "t"), ("add", "-A"),
                         ("commit", "-qm", "init")):
                subprocess.run(["git", *args], cwd=str(root), capture_output=True)
            (root / ".venv").mkdir()
            (root / ".venv" / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
            return root

        gone = real_repo("gone")
        subprocess.run(["git", "clean", "-xfd"], cwd=str(gone), capture_output=True)
        check("venv_git_clean: repro -- `git clean -xfd` deletes a primary's "
              "gitignored .venv outright", not (gone / ".venv").exists())
        kept = real_repo("kept")
        subprocess.run(["git", "clean", "-xfd", "-e", ".venv"], cwd=str(kept),
                       capture_output=True)
        check("venv_git_clean: repro -- `-e .venv` keeps it (the escape hatch is real)",
              (kept / ".venv" / "pyvenv.cfg").is_file())

        if sys.platform == "win32":
            prim = real_repo("prim")
            wt = tmp / "repro" / "prim-wt-1"
            subprocess.run(["git", "worktree", "add", "-q", "-b", "fix/1-x", str(wt)],
                           cwd=str(prim), capture_output=True)
            subprocess.run(["cmd", "/c", "mklink", "/J", str(wt / ".venv"),
                            str(prim / ".venv")],
                           capture_output=True, encoding="oem", errors="replace")
            junctions.append(wt / ".venv")
            if (wt / ".venv").exists():
                subprocess.run(["git", "clean", "-xfd"], cwd=str(wt), capture_output=True)
                check("venv_git_clean: repro -- in a worktree, `git clean -xfd` unlinks "
                      "the .venv junction without following it",
                      not (wt / ".venv").exists()
                      and (prim / ".venv" / "pyvenv.cfg").is_file())
            else:
                check.advisory("venv_git_clean: repro -- junction could not be created, "
                               "skipped", False, str(wt / ".venv"))
    else:
        check.advisory("venv_git_clean: repro needs git -- skipped", False, "no git")


def _venv_git_clean_allow_cases(check: _Checker, verdict: Callable[..., Tuple[int, str]], tmp: Path,
                                repo: Path, bare: Path,
                                junctions: List[Path]) -> List[Tuple[str, str, Path]]:
    """The `git clean` shapes that stay allowed; returns the ones the audit trail records."""
    # Allowed and NOT recorded: these delete nothing a venv could lose.
    unrecorded = [
        ("`git clean -nxd` (dry run)", "git clean -nxd", repo),
        ("`git clean -xfd --dry-run`", "git clean -xfd --dry-run", repo),
        ("`git clean -xfd --dry` (git's long-option prefix)", "git clean -xfd --dry", repo),
        ("`git clean -fd` (no -x: ignore rules keep .venv)", "git clean -fd", repo),
        ("`git clean -xf` (no -d, no pathspec: never recurses)", "git clean -xf", repo),
        ("`git commit -m \"git clean -xfd\"` (a message, not a verb)",
         'git commit -m "git clean -xfd"', repo),
        ("`echo git clean -xfd`", "echo git clean -xfd", repo),
    ]
    # Allowed but recorded: they do delete ignored files, just not a venv.
    recorded = [
        ("`git clean -xfd -e .venv`", "git clean -xfd -e .venv", repo),
        ("`git clean -xfd --exclude=.venv/`", "git clean -xfd --exclude=.venv/", repo),
        ("`git clean -xfd` from a subdirectory", "git clean -xfd", repo / "src"),
        ("`git clean -xfd` where no .venv exists", "git clean -xfd", bare),
    ]
    if sys.platform == "win32":
        linked = tmp / "repo-wt-3"
        linked.mkdir()
        (linked / ".git").write_text("gitdir: ../repo/.git/worktrees/repo-wt-3",
                                     encoding="utf-8")
        subprocess.run(["cmd", "/c", "mklink", "/J", str(linked / ".venv"),
                        str(repo / ".venv")],
                       capture_output=True, encoding="oem", errors="replace")
        junctions.append(linked / ".venv")
        if (linked / ".venv").exists():
            recorded.append(("`git clean -xfd` in a worktree over a junction (git "
                             "unlinks it, never follows it)", "git clean -xfd", linked))
    for label, command, cwd in unrecorded + recorded:
        code, _err = verdict(command, cwd)
        check(f"venv_git_clean: {label} -> allow", code == 0, f"exit={code}")
    return recorded


def _venv_cd_prefix_guard_unit_checks() -> Tuple[int, int]:
    """`venv_discipline.py` rules 4 + 5 across a directory change
    (fleet-config#871).

    Agents here prefix almost every command with `cd /e/automation/<repo> &&`,
    because the Bash tool resets cwd between calls. Rules 4 and 5 resolved
    every operand against the payload cwd, so `cd <primary> && git clean -xfd`
    issued from anywhere else was allowed, and its audit record named no venv.
    Every case in `fixed_blocks` exits 0 against pre-fix code.

    `hooks/` is junctioned live into `~/.claude/hooks`, so a false block here
    stalls every running session the moment it merges: the allow table is the
    real non-destructive shapes agents issue all day, and it is held to the
    same bar as the blocks. Everything runs against temp directories only.
    """
    check = _Checker()

    tmp = Path(tempfile.mkdtemp(prefix="venv_cd_prefix_"))
    junctions = []
    try:
        state = tmp / "state"
        repo = tmp / "repo"
        (repo / ".git").mkdir(parents=True)      # primary checkout: `.git` is a DIR
        (repo / ".venv" / "Scripts").mkdir(parents=True)
        (repo / "src").mkdir()
        (repo / "build").mkdir()
        elsewhere = tmp / "elsewhere"            # a session cwd with nothing to lose
        elsewhere.mkdir()

        def verdict(command: str, cwd: Path, tool: str = "Bash") -> int:
            code, _out, _err = run("venv_discipline",
                                   {"tool_name": tool, "cwd": str(cwd),
                                    "session_id": "sess-871",
                                    "tool_input": {"command": command}},
                                   {"CLAUDE_HOOKS_STATE_DIR": str(state)})
            return code

        msys_repo = "/" + str(repo)[0].lower() + str(repo)[2:].replace("\\", "/")
        venv = os.path.normcase(str(repo / ".venv"))
        fixed_blocks = _venv_cd_prefix_fixed_blocks(repo, msys_repo, elsewhere)
        for label, command, cwd, tool in fixed_blocks:
            code = verdict(command, cwd, tool)
            check(f"venv_cd_prefix: {label} -> block", code == 2, f"exit={code} cmd={command!r}")

        # A `cd` that cannot succeed leaves the next `;` clause where it was.
        code = verdict(f"cd {tmp}/missing; git clean -xfd", repo)
        check("venv_cd_prefix: `cd <missing>; git clean -xfd` in the primary -> block",
              code == 2, f"exit={code}")
        kept_blocks = 1

        if sys.platform == "win32":
            linked = tmp / "repo-wt-5"
            linked.mkdir()
            (linked / ".git").write_text("gitdir: ../repo/.git/worktrees/repo-wt-5",
                                         encoding="utf-8")
            subprocess.run(["cmd", "/c", "mklink", "/J", str(linked / ".venv"),
                            str(repo / ".venv")],
                           capture_output=True, encoding="oem", errors="replace")
            junctions.append(linked / ".venv")
        else:
            linked = None
        junction_ok = linked is not None and (linked / ".venv").exists()
        if junction_ok:
            code = verdict(f"cd {linked} && rm -rf .venv", elsewhere)
            check("venv_cd_prefix: `cd <worktree> && rm -rf .venv` -> rule 4 block",
                  code == 2, f"exit={code}")
        else:
            check.advisory("venv_cd_prefix: junction could not be created, rule 4 case "
                           "skipped", False, f"platform={sys.platform}")

        _venv_cd_prefix_trail_cases(check, state, venv, len(fixed_blocks) + kept_blocks, junction_ok)

        _venv_cd_prefix_allow_cases(check, verdict, repo, elsewhere, msys_repo, linked, junction_ok)
    finally:
        for link in junctions:
            try:
                os.rmdir(link)                   # reparse-safe: never follows it
            except OSError:
                pass
        shutil.rmtree(tmp, ignore_errors=True)

    return check.failures, check.total


def _venv_cd_prefix_fixed_blocks(repo: Path, msys_repo: str,
                                 elsewhere: Path) -> List[Tuple[str, str, Path, str]]:
    """The cd-prefixed deletes pre-fix code allowed, as (label, command, cwd, tool)."""
    # (label, command, cwd, tool) -- each one allowed before #871.
    fixed_blocks = [
        ("`cd <primary> && git clean -xfd`", f"cd {repo} && git clean -xfd",
         elsewhere, "Bash"),
        ("`cd <primary> && rm -rf .venv`", f"cd {repo} && rm -rf .venv", elsewhere, "Bash"),
        ("`cd <msys primary> && git clean -xfd`", f"cd {msys_repo} && git clean -xfd",
         elsewhere, "Bash"),
        ("`cd <primary>` then `rm -rf .venv` on the next line",
         f"cd {repo}\nrm -rf .venv", elsewhere, "Bash"),
        ("the new base survives an unrelated clause",
         f"cd {repo} && git status && git clean -xfd", elsewhere, "Bash"),
        ("relative `cd` chains resolve", f"cd {repo}/src && cd .. && git clean -xfd",
         elsewhere, "Bash"),
        ("a redirected `cd` still moves", f"cd {repo} >/dev/null 2>&1 && git clean -xfd",
         elsewhere, "Bash"),
        ("`pushd <primary>`", f"pushd {repo} && rm -rf .venv && popd", elsewhere, "Bash"),
        ("PowerShell `cd '<primary>'; Remove-Item -Recurse`",
         f"cd '{repo}'; Remove-Item -Recurse -Force .venv", elsewhere, "PowerShell"),
        ("PowerShell `Set-Location -Path`",
         f"Set-Location -Path '{repo}'; Remove-Item -Recurse .venv", elsewhere,
         "PowerShell"),
    ]
    return fixed_blocks


def _venv_cd_prefix_trail_cases(check: _Checker, state: Path, venv: str, expected_primary: int,
                                junction_ok: bool) -> None:
    """Every refusal lands in the audit trail under the rule that made it."""
    trail = state / "destructive-actions.jsonl"
    rows = [json.loads(line) for line in
            trail.read_text(encoding="utf-8").splitlines()] if trail.is_file() else []
    blocked = [r for r in rows if r["verdict"] == "BLOCK"]
    primary_rows = [r for r in blocked if r["rule"] == "primary"]
    check("venv_cd_prefix: every rule-5 refusal is recorded with the venv at risk",
          len(primary_rows) == expected_primary
          and all(os.path.normcase(r["venv"]) == venv for r in primary_rows),
          f"got {[(r['rule'], r['venv'], r['command']) for r in blocked]}")
    if junction_ok:
        check("venv_cd_prefix: the worktree refusal is recorded as rule 4",
              [r["rule"] for r in blocked].count("junction") == 1,
              f"got {[(r['rule'], r['command']) for r in blocked]}")


def _venv_cd_prefix_allow_cases(check: _Checker, verdict: Callable[..., int], repo: Path,
                                elsewhere: Path, msys_repo: str, linked: Optional[Path],
                                junction_ok: bool) -> None:
    """The non-destructive shapes that must stay allowed across a directory change."""
    # ---- false-block bar: the shapes agents issue dozens of times an hour
    allows = [
        ("`cd <primary>/src && git clean -xfd`", f"cd {repo}/src && git clean -xfd",
         elsewhere, "Bash"),
        ("`cd <primary> && git status`", f"cd {repo} && git status", elsewhere, "Bash"),
        ("`cd <msys primary> && git log`", f"cd {msys_repo} && git log --oneline -5",
         elsewhere, "Bash"),
        ("`cd <primary> && git fetch && git pull`",
         f"cd {repo} && git fetch origin && git pull --ff-only", elsewhere, "Bash"),
        ("`cd <primary> && git diff`", f"cd {repo} && git diff main...HEAD", elsewhere,
         "Bash"),
        ("`cd <primary> && .venv/Scripts/python.exe -m pytest`",
         f"cd {repo} && .venv/Scripts/python.exe -m pytest -q", elsewhere, "Bash"),
        ("PowerShell `cd <primary>; & .\\.venv\\Scripts\\python.exe`",
         f"cd '{repo}'; & .\\.venv\\Scripts\\python.exe -m compileall -q hooks",
         elsewhere, "PowerShell"),
        ("`cd <primary> && gh issue view`", f"cd {repo} && gh issue view 871 --json state",
         elsewhere, "Bash"),
        ("`cd <primary> && rm -rf build`", f"cd {repo} && rm -rf build", elsewhere, "Bash"),
        ("`cd <primary> && git clean -xfd -e .venv`",
         f"cd {repo} && git clean -xfd -e .venv", elsewhere, "Bash"),
        ("`cd <primary> || git clean -xfd` (runs only where cd failed)",
         f"cd {repo} || git clean -xfd", elsewhere, "Bash"),
        ("`pushd <primary> && popd && git clean -xfd` (popd restores)",
         f"pushd {repo} && popd && git clean -xfd", elsewhere, "Bash"),
        ("`cd <elsewhere> && git clean -xfd` issued from the primary",
         f"cd {elsewhere} && git clean -xfd", repo, "Bash"),
        ("`cd ~ && git status`", "cd ~ && git status", repo, "Bash"),
        ("a quoted `cd … && rm -rf .venv` in `echo`",
         f'echo "cd {repo} && rm -rf .venv"', elsewhere, "Bash"),
        ("a quoted `cd … && git clean -xfd` in a commit message",
         f'git commit -m "docs: cd {repo} && git clean -xfd is refused"', elsewhere,
         "Bash"),
        ("a quoted `cd …; rm -rf .venv` in a gh comment",
         f"gh issue comment 1 --body 'run: cd {repo}; rm -rf .venv'", elsewhere, "Bash"),
        ("a text-sink heredoc body quoting the hazard",
         f"cat > notes.md <<'EOF'\ncd {repo} && rm -rf .venv\nEOF", elsewhere, "Bash"),
        ("an executed heredoc whose text splits at a stray quote",
         f"cd {repo} && python - <<'PY'\nnote = \"a && rm -rf .venv, then \"\nPY",
         elsewhere, "Bash"),
        ("`rm -rf \"\"` names no path", f"cd {repo} && rm -rf \"\"", elsewhere, "Bash"),
        ("a `--body-file -` heredoc body quoting the hazard",
         f"gh issue create --title x --body-file - <<'EOF'\ncd {repo}\ngit clean -xfd\nEOF",
         elsewhere, "Bash"),
    ]
    if junction_ok:
        allows.append(("`cd <worktree> && git worktree list`",
                       f"cd {linked} && git worktree list", elsewhere, "Bash"))
    for label, command, cwd, tool in allows:
        code = verdict(command, cwd, tool)
        check(f"venv_cd_prefix: {label} -> allow", code == 0, f"exit={code} cmd={command!r}")
