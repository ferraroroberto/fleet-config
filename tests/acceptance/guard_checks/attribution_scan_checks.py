"""Acceptance checks for `pre_commit_no_ai_trailer`'s file and `gh` body sinks.

The hook reads the commit message a `git commit -F <file>` names and the body a
`gh issue|pr create|edit|comment` publishes, inline or through `--body-file`.
A real trailer line or session link is refused and the refusal quotes the line
to remove; ordinary clean bodies, prose that merely names a trailer, and a file
the hook cannot read pass. Every verdict comes from the real hook subprocess
against real temp files, because the sink is a filesystem read.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Callable, Tuple

from acceptance.shared import _Checker, run

HOOK = "pre_commit_no_ai_trailer"
CO_AUTHOR = "Co-Authored-By: Claude <noreply@anthropic.com>"
GENERATED = "🤖 Generated with [Claude Code](https://claude.com/claude-code)"
SESSION_URL = "https://claude.ai/code/session_x1"
SESSION_TRAILER = "Claude-Session: " + SESSION_URL

Verdict = Callable[..., Tuple[int, str, str]]


def _commit_file_cases(check: _Checker, verdict: Verdict, tmp: Path) -> None:
    """`git commit -F <file>`: the message in the file is scanned like `-m`."""
    msg = tmp / "msg.txt"
    msg.write_text(f"feat: x\n\n- why\n\n{CO_AUTHOR}\n", encoding="utf-8")
    clean = tmp / "clean.txt"
    clean.write_text("feat: x\n\n- why it matters\n", encoding="utf-8")

    for label, command in (
        ("-F <abs>", f"git commit -F {msg.as_posix()}"),
        ("-F <rel> against the payload cwd", "git commit -F msg.txt"),
        ("--file <rel>", "git commit --file msg.txt"),
        ("--file=<rel>", "git commit --file=msg.txt"),
        ("-F<rel> attached", "git commit -Fmsg.txt"),
        ("-a -F <rel>", "git commit -a -F msg.txt"),
        ("-sF <rel> short-flag cluster", "git commit -sF msg.txt"),
        ("quoted path", f'git commit -F "{msg.as_posix()}"'),
        ("after && in a chain", f"git add -A && git commit -F {msg.as_posix()}"),
    ):
        code, _out, err = verdict(command)
        check(f"trailer file: git commit {label} with a trailer -> block", code == 2, err)

    sub = tmp / "sub"
    sub.mkdir()
    (sub / "sub-msg.txt").write_text(f"fix: y\n\n{CO_AUTHOR}\n", encoding="utf-8")
    elsewhere = tmp / "elsewhere"
    check("trailer file: git -C <dir> commit -F <rel> resolves under the -C dir",
          verdict(f"git -C {sub.as_posix()} commit -F sub-msg.txt", cwd=elsewhere)[0] == 2)
    check("trailer file: cd <dir> && git commit -F <rel> resolves under the cd dir",
          verdict(f"cd {sub.as_posix()} && git commit -F sub-msg.txt", cwd=elsewhere)[0] == 2)
    check("trailer file: Set-Location <dir>; git commit -F <rel> (PowerShell)",
          verdict(f'Set-Location "{sub}"; git commit -F sub-msg.txt', cwd=elsewhere,
                  tool="PowerShell")[0] == 2)

    for name, text in (("generated", f"feat: x\n\n{GENERATED}\n"),
                       ("session-trailer", f"feat: x\n\n{SESSION_TRAILER}\n"),
                       ("bare-url", f"feat: x\n\nSee {SESSION_URL}\n")):
        (tmp / f"{name}.txt").write_text(text, encoding="utf-8")
        check(f"trailer file: -F file carrying {name} -> block",
              verdict(f"git commit -F {name}.txt")[0] == 2)
    utf16 = tmp / "utf16.txt"
    utf16.write_text(f"feat: x\n\n{CO_AUTHOR}\n", encoding="utf-16")
    check("trailer file: a UTF-16 (PowerShell Out-File) message is decoded and scanned",
          verdict(f"git commit -F {utf16.as_posix()}")[0] == 2)

    code, _out, err = verdict("git commit -F msg.txt")
    check("trailer file: the refusal names the file and quotes the line to remove",
          code == 2 and "msg.txt" in err and CO_AUTHOR in err and "Remove" in err, err)

    check("trailer file: clean -F file -> allow", verdict("git commit -F clean.txt")[0] == 0)
    check("trailer file: clean -F <abs> -> allow",
          verdict(f"git commit -F {clean.as_posix()}")[0] == 0)
    code, out, err = verdict("git commit -F missing.txt")
    check("trailer file: missing -F file -> allow, nothing on stdout", code == 0 and not out, err)
    check("trailer file: -F - (stdin) -> allow", verdict("git commit -F -")[0] == 0)
    check("trailer file: $VAR operand -> allow", verdict("git commit -F $MSGFILE")[0] == 0)
    check("trailer file: a -F file on a non-commit git command is not read",
          verdict("git log --grep x -F msg.txt")[0] == 0)
    check("trailer file: grep -F elsewhere in the chain is not a message file",
          verdict("grep -F x msg.txt && git commit -F clean.txt")[0] == 0)


def _gh_body_cases(check: _Checker, verdict: Verdict, tmp: Path) -> None:
    """`gh issue|pr create|edit|comment`: the posted body is scanned, inline or by file."""
    bodies = {
        "co-author": f"## Summary\n\n- fix\n\n{CO_AUTHOR}\n",
        "generated": f"## Summary\n\n- fix\n\n{GENERATED}\n",
        "bullet-generated": "## Summary\n\n- Generated with Claude Code\n",
        "session-url-line": f"## Summary\n\n- fix\n\n{SESSION_URL}\n",
        "session-url-inline": f"Done, see {SESSION_URL} for the run.\n",
        "session-trailer": f"## Summary\n\n{SESSION_TRAILER}\n",
    }
    verbs = ("gh pr create --title t", "gh pr edit 5", "gh pr comment 5",
             "gh issue create --title t", "gh issue edit 5", "gh issue comment 5")
    for name, text in bodies.items():
        path = tmp / f"body-{name}.md"
        path.write_text(text, encoding="utf-8")
        codes = {v: verdict(f"{v} --body-file {path.as_posix()}")[0] for v in verbs}
        check(f"trailer gh: {name} in --body-file blocks every publish verb",
              all(c == 2 for c in codes.values()), repr(codes))

    path = tmp / "body-co-author.md"
    check("trailer gh: --body-file=<file> blocks",
          verdict(f"gh pr create --title t --body-file={path.as_posix()}")[0] == 2)
    check("trailer gh: -F <rel> resolves against the payload cwd",
          verdict("gh pr create --title t -F body-co-author.md")[0] == 2)
    check("trailer gh: after a cd, a relative --body-file resolves in the new dir",
          verdict(f"cd {tmp.as_posix()} && gh pr create --title t --body-file body-generated.md",
                  cwd=tmp / "elsewhere")[0] == 2)
    code, _out, err = verdict(f"gh pr create --title t --body-file {path.as_posix()}")
    check("trailer gh: the refusal quotes the line to remove and names the body file",
          code == 2 and CO_AUTHOR in err and "body-co-author.md" in err and "Remove" in err, err)

    for label, command, tool in (
        ("--body, trailer on its own line",
         f'gh pr create --title t --body "## Summary\n\n- fix\n\n{CO_AUTHOR}"', "Bash"),
        ("--body, the whole body is a session link",
         f'gh pr create --title t --body "{SESSION_URL}"', "Bash"),
        ("--body, the whole body is a trailer",
         f'gh issue comment 5 --body "{CO_AUTHOR}"', "Bash"),
        ("--body=, trailer on its own line",
         f'gh issue edit 5 --body="x\n{GENERATED}"', "Bash"),
        ("-b, single quotes",
         f"gh pr comment 5 -b 'done\n\n{CO_AUTHOR}'", "Bash"),
        ("--body with a $(cat <<heredoc)",
         f"gh pr create --title t --body \"$(cat <<'EOF'\n## Summary\n\n{GENERATED}\nEOF\n)\"", "Bash"),
        ("a heredoc writing the body file in the same command",
         f"cat > b.md <<'EOF'\n## Summary\n\n{CO_AUTHOR}\nEOF\ngh pr create --title t --body-file b.md", "Bash"),
        ("PowerShell here-string",
         f"gh pr create --title t --body @'\n## Summary\n\n{GENERATED}\n'@", "PowerShell"),
        ("literal \\n in a double-quoted body",
         f'gh pr create --title t --body "fix\\n\\n{CO_AUTHOR}"', "Bash"),
        ("PowerShell backtick-n in a double-quoted body",
         f'gh pr create --title t --body "fix`n`n{CO_AUTHOR}"', "PowerShell"),
    ):
        code, _out, err = verdict(command, tool=tool)
        check(f"trailer gh: inline {label} -> block", code == 2, err)
    code, _out, err = verdict(f'gh pr create --title t --body "x\n\n{CO_AUTHOR}"')
    check("trailer gh: the inline refusal quotes the line to remove",
          code == 2 and CO_AUTHOR in err and "Remove" in err, err)


def _allow_cases(check: _Checker, verdict: Verdict, tmp: Path) -> None:
    """A clean body, prose naming a trailer and an unreadable body all pass."""
    clean = tmp / "clean-body.md"
    clean.write_text("## Summary\n\n- the fix\n\n## Test plan\n\n- [x] gate\n\nCloses #5\n",
                     encoding="utf-8")
    prose = tmp / "prose-body.md"
    prose.write_text(
        "The hook should refuse a `Co-Authored-By: Claude` trailer and the\n"
        "`Generated with Claude Code` footer, plus a Claude-Session: link.\n"
        "Text generated with the Claude API is fine.\n"
        "18 PRs say \"Generated with Claude Code\" and 200 carry a session URL\n"
        "shaped like https://claude.ai/code/session_<id>.\n", encoding="utf-8")
    for verb in ("gh pr create --title t", "gh issue create --title t", "gh pr edit 5",
                 "gh issue comment 5"):
        code, out, err = verdict(f"{verb} --body-file {clean.as_posix()}")
        check(f"trailer gh: clean --body-file passes `{verb}`", code == 0 and not out, err)
        code, out, err = verdict(f"{verb} --body-file {prose.as_posix()}")
        check(f"trailer gh: prose naming a trailer passes `{verb}`", code == 0 and not out, err)

    check("trailer gh: clean inline --body passes",
          verdict('gh pr create --title t --body "## Summary\n\n- fix\n\nCloses #5"')[0] == 0)
    check("trailer gh: inline body quoting a trailer mid-line passes",
          verdict('gh issue create --title t --body "the hook misses `Co-Authored-By: Claude` and '
                  'git commit -m \'Generated with Claude Code\'"')[0] == 0)
    check("trailer gh: a trailer-like --title is not the body",
          verdict(f'gh issue create --title "{CO_AUTHOR}" --body-file {clean.as_posix()}')[0] == 0)
    check("trailer gh: a heredoc body quoting a trailer inside a command passes",
          verdict("cat > b.md <<'EOF'\nthe guard misses git commit -m \"Co-Authored-By: Claude\"\nEOF\n"
                  "gh issue create --title t --body-file b.md")[0] == 0)
    code, out, err = verdict("gh issue create --title t --body-file missing.md")
    check("trailer gh: missing body file -> allow", code == 0 and not out, err)
    check("trailer gh: -F - (stdin) -> allow", verdict("gh issue create --title t -F -")[0] == 0)
    check("trailer gh: $VAR operand -> allow",
          verdict("gh issue create --title t --body-file $BODY")[0] == 0)
    for read in ("gh issue view 5", "gh pr list --state open", "gh pr view 5 --comments"):
        code, out, err = verdict(read)
        check(f"trailer gh: read `{read}` -> allow silently", code == 0 and not out and not err)
    check("trailer gh: only the gh segment's -F is read, not a sibling grep -F",
          verdict(f"grep -F x {tmp.as_posix()}/body-co-author.md && gh issue create --title t "
                  f"--body-file {clean.as_posix()}")[0] == 0)


def _attribution_scan_unit_checks() -> Tuple[int, int]:
    """`pre_commit_no_ai_trailer`: `git commit -F` files and `gh` bodies (fleet-config#1914).

    Every blocking case exits 0 on the pre-#1914 hook, which read only the
    command string of a `git commit` and ignored `gh` entirely.
    """
    check = _Checker()
    tmp = Path(tempfile.mkdtemp(prefix="attribution_scan_"))
    try:
        (tmp / "elsewhere").mkdir()

        def verdict(command: str, cwd: Path = tmp, tool: str = "Bash") -> Tuple[int, str, str]:
            return run(HOOK, {"tool_name": tool, "cwd": str(cwd),
                              "tool_input": {"command": command}})

        _commit_file_cases(check, verdict, tmp)
        _gh_body_cases(check, verdict, tmp)
        _allow_cases(check, verdict, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return check.failures, check.total
