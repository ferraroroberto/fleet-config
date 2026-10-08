"""Table-driven tests for the hooks' shared shell tokenisers (fleet-config#1304).

Standalone: the guards split a command three different ways, and each split is
load-bearing for the guard that uses it. These tables pin every split to the
behaviour the per-hook copies had before they moved into `hooks/_lib.py`, so the
move is proven to change nothing a guard refuses. `hooks/` is junctioned live
into every `~/.claude/hooks`, so a silent split change would land fleet-wide on
merge.

Run: E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_shell_parse.py
Exit 0 = all pass.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "hooks"))
sys.path.insert(0, str(REPO / "tests" / "_lib"))

import _lib  # noqa: E402
from check_harness import CheckHarness  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


_h = CheckHarness()
check = _h.check

# The splits under test, by the name each table below is keyed on.
SPLITS = {
    "raw": _lib.shell_tokens,  # safe_kill_guard
    "unquoted": lambda text: _lib.shell_tokens(text, unquote=True),  # venv_discipline
    "words": _lib.shell_words,  # secret_scan_guard
    "commit": _lib._COMMIT_TOKEN_RE.findall,  # _lib.runs_git_commit
}

# (command, {split: expected tokens}). Expected values are the pre-#1304
# per-hook outputs, captured verbatim; edge cases (unterminated quotes, a quote
# glued to a word, a backslash before a closing quote) are where the splits
# disagree, and each guard depends on its own answer there.
CASES = [
    ("", {"raw": [], "unquoted": [], "words": [], "commit": []}),
    ("   ", {"raw": [], "unquoted": [], "words": [], "commit": []}),
    ("git push origin main", {
        "raw": ["git", "push", "origin", "main"],
        "unquoted": ["git", "push", "origin", "main"],
        "words": ["git", "push", "origin", "main"],
        "commit": ["git", "push", "origin", "main"]}),
    ('git -C "E:/my repo" push --force', {
        "raw": ["git", "-C", '"E:/my repo"', "push", "--force"],
        "unquoted": ["git", "-C", "E:/my repo", "push", "--force"],
        "words": ["git", "-C", "E:/my repo", "push", "--force"],
        "commit": ["git", "-C", '"E:/my repo"', "push", "--force"]}),
    ("git -C 'E:/my repo' status", {
        "raw": ["git", "-C", "'E:/my repo'", "status"],
        "unquoted": ["git", "-C", "E:/my repo", "status"],
        "words": ["git", "-C", "E:/my repo", "status"],
        "commit": ["git", "-C", "'E:/my repo'", "status"]}),
    ('echo --x="a b" c', {
        "raw": ["echo", '--x="a', 'b"', "c"],
        "unquoted": ["echo", '--x="a', 'b"', "c"],
        "words": ["echo", "--x=a b", "c"],
        "commit": ["echo", '--x="a b"', "c"]}),
    ('"a"b', {"raw": ['"a"', "b"], "unquoted": ["a", "b"], "words": ["ab"], "commit": ['"a"b']}),
    ('a"b c"', {"raw": ['a"b', 'c"'], "unquoted": ['a"b', 'c"'], "words": ["ab c"], "commit": ['a"b c"']}),
    ("'", {"raw": ["'"], "unquoted": ["'"], "words": [], "commit": []}),
    ('"', {"raw": ['"'], "unquoted": ['"'], "words": [], "commit": []}),
    ("''", {"raw": ["''"], "unquoted": [""], "words": [""], "commit": ["''"]}),
    ('""', {"raw": ['""'], "unquoted": [""], "words": [""], "commit": ['""']}),
    ("'unterminated arg", {
        "raw": ["'unterminated", "arg"], "unquoted": ["'unterminated", "arg"],
        "words": ["unterminated", "arg"], "commit": ["unterminated", "arg"]}),
    ('"unterminated arg', {
        "raw": ['"unterminated', "arg"], "unquoted": ['"unterminated', "arg"],
        "words": ["unterminated", "arg"], "commit": ["unterminated", "arg"]}),
    (r"rm -rf C:\Users\me\.venv", {
        "raw": ["rm", "-rf", r"C:\Users\me\.venv"],
        "unquoted": ["rm", "-rf", r"C:\Users\me\.venv"],
        "words": ["rm", "-rf", r"C:\Users\me\.venv"],
        "commit": ["rm", "-rf", r"C:\Users\me\.venv"]}),
    ('Remove-Item -Recurse "C:\\path with space\\.venv\\"', {
        "raw": ["Remove-Item", "-Recurse", '"C:\\path with space\\.venv\\"'],
        "unquoted": ["Remove-Item", "-Recurse", "C:\\path with space\\.venv\\"],
        "words": ["Remove-Item", "-Recurse", "C:\\path with space\\.venv\\"],
        "commit": ["Remove-Item", "-Recurse", "C:\\path", "with", "space\\.venv\\"]}),
    ('cd /e/automation && gh issue create --body-file="C:/a b/pr.md"', {
        "raw": ["cd", "/e/automation", "&&", "gh", "issue", "create", '--body-file="C:/a', 'b/pr.md"'],
        "unquoted": ["cd", "/e/automation", "&&", "gh", "issue", "create", '--body-file="C:/a', 'b/pr.md"'],
        "words": ["cd", "/e/automation", "&&", "gh", "issue", "create", "--body-file=C:/a b/pr.md"],
        "commit": ["cd", "/e/automation", "&&", "gh", "issue", "create", '--body-file="C:/a b/pr.md"']}),
    ("git commit -m \"it's fine\"", {
        "raw": ["git", "commit", "-m", "\"it's fine\""],
        "unquoted": ["git", "commit", "-m", "it's fine"],
        "words": ["git", "commit", "-m", "it's fine"],
        "commit": ["git", "commit", "-m", "\"it's fine\""]}),
    ("bash -c \"git commit -m 'x'\"", {
        "raw": ["bash", "-c", "\"git commit -m 'x'\""],
        "unquoted": ["bash", "-c", "git commit -m 'x'"],
        "words": ["bash", "-c", "git commit -m 'x'"],
        "commit": ["bash", "-c", "\"git commit -m 'x'\""]}),
    ("a;b|c&&d||e (f) {g}", {
        "raw": ["a;b|c&&d||e", "(f)", "{g}"],
        "unquoted": ["a;b|c&&d||e", "(f)", "{g}"],
        "words": ["a;b|c&&d||e", "(f)", "{g}"],
        "commit": ["a", ";", "b", "|", "c", "&&", "d", "||", "e", "(", "f", ")", "{", "g", "}"]}),
    ('echo "esc \\" quote" next', {
        "raw": ["echo", '"esc \\"', 'quote"', "next"],
        "unquoted": ["echo", "esc \\", 'quote"', "next"],
        "words": ["echo", "esc \\", "quote", "next"],
        "commit": ["echo", '"esc \\" quote"', "next"]}),
    ("VAR=1 git -c core.hooksPath=/dev/null commit", {
        "raw": ["VAR=1", "git", "-c", "core.hooksPath=/dev/null", "commit"],
        "unquoted": ["VAR=1", "git", "-c", "core.hooksPath=/dev/null", "commit"],
        "words": ["VAR=1", "git", "-c", "core.hooksPath=/dev/null", "commit"],
        "commit": ["VAR=1", "git", "-c", "core.hooksPath=/dev/null", "commit"]}),
    ("cat <<'EOF' > out.txt\nbody line\nEOF", {
        "raw": ["cat", "<<'EOF'", ">", "out.txt", "body", "line", "EOF"],
        "unquoted": ["cat", "<<'EOF'", ">", "out.txt", "body", "line", "EOF"],
        "words": ["cat", "<<EOF", ">", "out.txt", "body", "line", "EOF"],
        "commit": ["cat", "<<'EOF'", ">", "out.txt", "\n", "body", "line", "\n", "EOF"]}),
    ("x\ty\r\nz", {
        "raw": ["x", "y", "z"], "unquoted": ["x", "y", "z"],
        "words": ["x", "y", "z"], "commit": ["x", "y", "\n", "z"]}),
]

for command, expected in CASES:
    for split, tokens in expected.items():
        got = SPLITS[split](command)
        check(got == tokens, f"{split} split of {command!r}: {tokens!r} (got {got!r})")

_h.report_and_exit("test_shell_parse")
