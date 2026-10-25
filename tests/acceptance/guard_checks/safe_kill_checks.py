"""Acceptance checks for `safe_kill_guard.py`'s git refusals.

The force-push guard decides on the ref being pushed, and the bypass-flag
check reads real `git` arguments rather than words on the line. Both are
driven through the hook module's own predicates.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Tuple

from acceptance.shared import HOOKS, REPO, _Checker


def _safe_kill_force_push_unit_checks() -> Tuple[int, int]:
    """The force-push guard decides on the *ref being pushed* (fleet-config#562).

    The predicate used to be a word-boundary search for `main`/`master` across
    the whole command line, so `git push --force origin
    chore/rename-main-config-loader` was refused — a legitimate feature-branch
    force-push the module's own docstring promises to allow. A fleet-wide guard
    blocking valid work is the expensive kind of wrong (#464/#472 reverted a
    hook within the hour for it), so the branch-name-contains-main case is
    pinned here alongside the protections it must not weaken.
    """
    sys.path.insert(0, str(HOOKS))
    import safe_kill_guard as skg  # noqa: E402

    check = _Checker()
    push = "git " + "push"  # split so this file's own text isn't a force-push line
    here = REPO

    def blocked(cmd: str) -> bool:
        return skg.forced_push_hits_protected(cmd, here)

    check("force-push: --force origin main -> blocked", blocked(f"{push} --force origin main"))
    check("force-push: -f origin master -> blocked", blocked(f"{push} -f origin master"))
    check("force-push: --force-with-lease origin HEAD:main -> blocked",
          blocked(f"{push} --force-with-lease origin HEAD:main"))
    check("force-push: +refs/heads/master refspec -> blocked",
          blocked(f"{push} --force origin +refs/heads/master"))
    check("force-push: short-flag cluster -fu origin main -> blocked",
          blocked(f"{push} -fu origin main"))
    check("force-push: chained after another command -> blocked",
          blocked(f"git status && {push} --force origin main"))

    # A leading `+` forces that one refspec with no flag at all (fleet-config#960).
    check("force-push: flagless +HEAD:main -> blocked (#960)", blocked(f"{push} origin +HEAD:main"))
    check("force-push: flagless +main -> blocked (#960)", blocked(f"{push} origin +main"))
    check("force-push: flagless +refs/heads/master among plain refspecs -> blocked (#960)",
          blocked(f"{push} origin feature/x +refs/heads/master"))
    check("force-push: flagless +feature/x -> allowed (own branch) (#960)",
          not blocked(f"{push} origin +feature/x"))
    check("force-push: flagless + only forces its own refspec: plain main beside +feature -> allowed (#960)",
          not blocked(f"{push} origin main +feature/x"))
    check("force-push: flagless push reports only the +refspecs (#960)",
          skg._parse_forced_push(f"{push} origin main +HEAD:feature/x") == (["+HEAD:feature/x"], None))

    # Git's global options sit between `git` and `push` (fleet-config#1275).
    gpush = "git -C /some/repo " + "push"
    check("force-push: -C <dir> --force origin main -> blocked (#1275)",
          blocked(f"{gpush} --force origin main"))
    check("force-push: -c k=v -f origin master -> blocked (#1275)",
          blocked("git -c push.default=current " + "push -f origin master"))
    check("force-push: --git-dir=<d> and --work-tree <d> before push -> blocked (#1275)",
          blocked("git --git-dir=/r/.git --work-tree /r " + "push --force origin main"))
    check("force-push: quoted -C path with a space -> blocked (#1275)",
          blocked('git -C "/some repo" ' + "push --force origin main"))
    check("force-push: -C <dir> to a branch NAMED *-main-* -> allowed (#562 holds)",
          not blocked(f"{gpush} --force origin feature/main-thing"))
    check("force-push: -C <dir> with no force flag -> not a forced push",
          skg._parse_forced_push(f"{gpush} origin main") is None)
    check("force-push: a force flag on another git subcommand is not a push",
          skg._parse_forced_push("git -C /r checkout -f main") is None)

    check("force-push: feature branch -> allowed", not blocked(f"{push} --force origin feature/foo"))
    check("force-push: branch whose NAME contains 'main' -> allowed (the #562 false positive)",
          not blocked(f"{push} --force origin chore/rename-main-config-loader"))
    check("force-push: branch whose name contains 'master' -> allowed",
          not blocked(f"{push} --force origin fix/12-master-list-parser"))
    check("force-push: a src-side 'main' pushed onto a feature ref -> allowed",
          not blocked(f"{push} --force origin main:feature/staging-main"))
    check("force-push: no force flag -> not a forced push at all",
          skg._parse_forced_push(f"{push} origin main") is None)
    check("force-push: --foo is not the short -f (no false positive)",
          skg._parse_forced_push(f"{push} --foo origin main") is None)

    check("force-push: refspec-less push reports an empty list, not a guess",
          skg._parse_forced_push(f"{push} --force origin") == ([], None))
    check("destination_branch: strips + and refs/heads/, keeps the dst side",
          skg.destination_branch("+refs/heads/main") == "main"
          and skg.destination_branch("HEAD:refs/heads/master") == "master"
          and skg.destination_branch("feature/x") == "feature/x")

    _safe_kill_refspecless_push_cases(check, skg, push)

    return check.failures, check.total


def _safe_kill_refspecless_push_cases(check: _Checker, skg: Any, push: str) -> None:
    """A force push with no refspec is judged by the branch checked out where it runs."""
    # Refspec-less force push falls back to the checked-out branch of `cwd`.
    tmp = Path(tempfile.mkdtemp(prefix="fc-push-"))
    try:
        subprocess.run(["git", "-C", str(tmp), "init", "-b", "main"], capture_output=True)
        check("force-push: refspec-less push on main -> blocked via the checked-out branch",
              skg.forced_push_hits_protected(f"{push} --force origin", tmp))
        subprocess.run(["git", "-C", str(tmp), "checkout", "-b", "feat/x"], capture_output=True)
        check("force-push: refspec-less push on a feature branch -> allowed",
              not skg.forced_push_hits_protected(f"{push} --force origin", tmp))
        # `-C <dir>` names the repo the push runs in — the branch comes from it,
        # not from the payload cwd (fleet-config#1275).
        other = Path(tempfile.mkdtemp(prefix="fc-push-cwd-"))
        try:
            subprocess.run(["git", "-C", str(tmp), "symbolic-ref", "HEAD", "refs/heads/main"], capture_output=True)
            check("force-push: refspec-less -C <repo on main> from another cwd -> blocked (#1275)",
                  skg.forced_push_hits_protected(f"git -C {tmp} " + "push --force", other))
            subprocess.run(["git", "-C", str(other), "init", "-b", "main"], capture_output=True)
            subprocess.run(["git", "-C", str(tmp), "symbolic-ref", "HEAD", "refs/heads/feat/y"], capture_output=True)
            check("force-push: refspec-less -C <repo on a feature branch> while cwd is on main -> allowed (#1275)",
                  not skg.forced_push_hits_protected(f"git -C {tmp} " + "push --force", other))
            subprocess.run(["git", "-C", str(tmp), "symbolic-ref", "HEAD", "refs/heads/main"], capture_output=True)
            check("force-push: relative -C resolves against the payload cwd (#1275)",
                  skg.forced_push_hits_protected(f"git -C {tmp.name} " + "push --force", tmp.parent))
        finally:
            shutil.rmtree(other, ignore_errors=True)
        check("_current_branch: unresolvable cwd reports '' (fails open, never guesses)",
              skg._current_branch(tmp / "not-a-repo-here") == "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _safe_kill_git_bypass_unit_checks() -> Tuple[int, int]:
    """The bypass-flag check reads real `git` arguments, not words on the line (fleet-config#1303).

    The old line regex refused any command mentioning the flag after the word
    `git` - including a read-only `rg` for that phrase over `docs/`. Command-
    position parsing must keep refusing every real invocation, so the
    protections are pinned beside the false positive.
    """
    sys.path.insert(0, str(HOOKS))
    import safe_kill_guard as skg  # noqa: E402

    check = _Checker()
    nv = "--no-" + "verify"
    gs = "--no-gpg-" + "sign"
    gpg = "commit.gpg" + "sign=false"

    def flagged(cmd: str) -> bool:
        return skg.git_bypass_flag(cmd) is not None

    check("bypass: plain commit with the flag -> blocked", flagged(f"git commit {nv} -m hi"))
    check("bypass: flag after the message -> blocked", flagged(f'git commit -m "hi" {nv}'))
    check("bypass: push with the flag -> blocked", flagged(f"git push {nv}"))
    check("bypass: --no-gpg-sign -> blocked", flagged(f"git commit {gs} -m hi"))
    check("bypass: -c gpgsign=false before the subcommand -> blocked", flagged(f"git -c {gpg} commit -m hi"))
    check("bypass: global -C <dir> before the subcommand does not hide it", flagged(f"git -C /r commit {nv}"))
    check("bypass: chained after another command -> blocked", flagged(f"git add -A && git commit {nv}"))
    check("bypass: env assignment before git -> blocked", flagged(f"GIT_X=1 git commit {nv}"))
    check("bypass: git.exe by path -> blocked", flagged(f'"C:/Program Files/Git/bin/git.exe" commit {nv}'))
    check("bypass: PowerShell call operator -> blocked", flagged(f"& git commit {nv}"))
    check("bypass: nested shell string -> blocked", flagged(f'bash -c "git commit {nv}"'))
    check("bypass: nested shell with a quoted separator -> blocked", flagged(f'bash -c "git add .; git commit {nv}"'))
    check("bypass: xargs prefix -> blocked", flagged(f"echo x | xargs git commit {nv}"))
    check("bypass: flag in a nested shell string via env prefix -> blocked", flagged(f"env FOO=1 git commit {nv}"))

    check("bypass: rg for the phrase over docs/ -> allowed (the #1303 false positive)",
          not flagged(f'rg "git commit {nv}" docs/'))
    check("bypass: grep for the flag after a pipe -> allowed",
          not flagged(f"git log --oneline | grep -- {nv}"))
    check("bypass: echo of the phrase -> allowed", not flagged(f"echo git commit {nv}"))
    check("bypass: flag inside a commit message -> allowed", not flagged(f'git commit -m "document {nv}"'))
    check("bypass: ordinary commit and push -> allowed",
          not flagged("git commit -m hi") and not flagged("git push origin feat/x"))
    check("bypass: a longer unrelated flag is not the bypass flag", not flagged(f"git commit {nv}-hooks"))
    check("bypass: quoted string handed to a non-shell command -> allowed",
          not flagged(f'python -c "print(1)" "git commit {nv}"'))

    return check.failures, check.total
