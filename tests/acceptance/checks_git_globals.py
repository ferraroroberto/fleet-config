"""`_lib.git_globals`, the one walk over git's global options (fleet-config#1556).

Five guard sites used to skip the options between `git` and the subcommand each
in their own loop, so a form taught to one (`--work-tree=`) stayed invisible to
the others. These checks pin the helper and drive every site through the same
forms.
"""
from __future__ import annotations

import sys
from typing import Tuple

from acceptance.shared import HOOKS, _Checker


def _git_globals_unit_checks() -> Tuple[int, int]:
    sys.path.insert(0, str(HOOKS))
    import _lib  # noqa: E402
    import context_filter as cf  # noqa: E402
    import safe_kill_guard as skg  # noqa: E402
    import venv_discipline as vd  # noqa: E402

    check = _Checker()
    walk = _lib.git_globals

    plain = walk(["commit", "-m", "x"], 0)
    check("git_globals: no options -> the subcommand is the first token", plain.subcommand == 0 and plain.workdir == "")
    check("git_globals: bare flags and value options are skipped",
          walk(["--no-pager", "-c", "a=b", "--git-dir", "g", "push"], 0).subcommand == 5)
    check("git_globals: every -c value is collected", walk(["-c", "a=1", "-c", "b=2", "log"], 0).config == ["a=1", "b=2"])
    check("git_globals: several -C compose, as in git", walk(["-C", "a", "-C", "b", "push"], 0).workdir == "a/b")
    check("git_globals: an absolute -C replaces the base", walk(["-C", "a", "-C", "/r", "push"], 0).workdir == "/r")
    check("git_globals: --work-tree=<dir> and --work-tree <dir> both count",
          walk(["--work-tree=w", "push"], 0).workdir == "w" and walk(["--work-tree", "w", "push"], 0).workdir == "w")
    check("git_globals: starts where told", walk(["git", "-C", "r", "push"], 1).subcommand == 3)
    check("git_globals: options with no subcommand end at len(tokens)", walk(["-C"], 0).subcommand == 1)

    wt = "git --work-tree=/r push --force"
    check("push guard: --work-tree=<dir> before push is read like -C", skg._parse_forced_push(wt) == ([], "/r"))
    check("push guard: the --work-tree directory is carried to the branch lookup",
          skg._push_args("git --work-tree=/r push -f")[1] == "/r")
    check("bypass guard: a -c bypass behind --work-tree= is still found",
          skg.git_bypass_flag("git --work-tree=/r -c " + "commit.gpg" + "sign=false commit -m x") is not None)
    check("commit guard: --work-tree=<dir> does not hide the commit", _lib.runs_git_commit("git --work-tree=/r commit -m x"))
    check("clean guard: --work-tree=<dir> still scopes the clean operand",
          vd.git_clean_operands(["git", "--work-tree=/r", "clean", "-xfd"]) == (vd.GIT_CLEAN_VERB, ["/r"]))
    check("context filter: --work-tree <dir> does not hide a diff", cf.is_content_command("git --work-tree w diff"))

    return check.failures, check.total
