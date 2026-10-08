"""Table-driven tests for the hooks' shared shell parsing (fleet-config#1304).

Standalone: the guards split a command three different ways, and each split is
load-bearing for the guard that uses it. These tables pin every split, the
directory-change verbs and MSYS path translation two guards follow, and the
heredoc openers four guards recognise, to the
behaviour the per-hook copies had before they moved into `hooks/_lib.py`, so
each move is proven to change nothing a guard refuses. `hooks/` is junctioned live
into every `~/.claude/hooks`, so a silent split change would land fleet-wide on
merge.

Run: E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_shell_parse.py
Exit 0 = all pass.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "hooks"))
sys.path.insert(0, str(REPO / "tests" / "_lib"))

import _lib  # noqa: E402
from check_harness import CheckHarness  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")




def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "hooks" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_secret_scan = _load("secret_scan_guard")
_venv = _load("venv_discipline")

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

def _rel(path, base: Path):
    """`path` as posix text with `base` shown as `<base>`, so tables stay portable."""
    return None if path is None else path.as_posix().replace(base.as_posix(), "<base>")


# Directory-change verbs (fleet-config#1304): every Bash and PowerShell spelling,
# case-insensitive, as each tracker followed it before the verb sets moved to
# `_lib`. secret_scan_guard reads a push like a cd and forgets the base on a pop;
# venv_discipline keeps a push stack and only follows a directory that exists.
_BASE = Path("C:/base")
SECRET_SCAN_CD = [
    ("cd sub", "<base>/sub"), ("chdir sub", "<base>/sub"), ("Set-Location sub", "<base>/sub"),
    ("SL sub", "<base>/sub"), ("pushd sub", "<base>/sub"), ("Push-Location sub", "<base>/sub"),
    ("CD sub", "<base>/sub"), ("popd", None), ("Pop-Location", None), ("cd -", None),
    ("cd /e/automation", "E:/automation"), ("dir sub", "<base>"),
]
for verb, expected in SECRET_SCAN_CD:
    got = [(op, _rel(b, _BASE)) for op, b in _secret_scan.body_file_operands(
        f"{verb} && gh issue create --body-file b.md", _BASE)]
    check(got == [("b.md", expected)], f"secret_scan cd tracking after {verb!r}: {expected!r} (got {got!r})")

with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    (base / "sub").mkdir()
    VENV_CD = [
        ("cd sub", [], ("cd", "<base>/sub")), ("chdir sub", [], ("cd", "<base>/sub")),
        ("Set-Location sub", [], ("cd", "<base>/sub")), ("SL sub", [], ("cd", "<base>/sub")),
        ("CD sub", [], ("cd", "<base>/sub")), ("pushd sub", [], ("push", "<base>/sub")),
        ("Push-Location sub", [], ("push", "<base>/sub")),
        ("popd", [base / "sub"], ("pop", "<base>/sub")),
        ("Pop-Location", [base / "sub"], ("pop", "<base>/sub")),
        ("popd", [], None), ("dir sub", [], None), ("cd missing", [], None),
    ]
    for segment, stack, expected in VENV_CD:
        result = _venv.directory_change(segment, base, stack)
        got = None if result is None else (result[0], _rel(result[1], base))
        check(got == expected, f"venv directory_change({segment!r}, stack={len(stack)}): {expected!r} (got {got!r})")

# MSYS drive paths (`/e/x` -> `E:/x`), as each guard resolved them.
for raw, expected in [("/e/x/b.md", "E:/x/b.md"), ("/E/x", "E:/x"), ("/ee/x", None),
                      ("e/x", None), ("C:/x", "C:/x")]:
    got = _rel(_secret_scan._resolve(raw, None), _BASE)
    check(got == expected, f"secret_scan resolves {raw!r} to {expected!r} (got {got!r})")
for raw, expected in [("/e/x/.venv", "E:/x/.venv"), ("/e/x/*", "E:/x"), ("/ee/x", "C:/ee/x"),
                      ("'/c/a b/.venv'", "C:/a b/.venv")]:
    got = _rel(_venv._operand_path(raw, _BASE), _BASE)
    check(got == expected, f"venv operand {raw!r} is {expected!r} (got {got!r})")


# Heredoc openers (fleet-config#1304), as each of the four callers read them.
# Every case is `cat <opener> > f`, then an unquoted drive-path body line, then
# the delimiter, so one command shows all four answers: whether `_lib` and
# venv_discipline keep the body as shell text, which drive paths
# bash_windows_path_guard refuses, and whether gh_body_file_guard nudges on the
# same opener inside an inline `--body`.
_bwp = _load("bash_windows_path_guard")
_BODY = "E:\\x"


def _gh_nudges(command: str) -> bool:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    result = subprocess.run([sys.executable, str(REPO / "hooks" / "gh_body_file_guard.py")],
                            input=payload, capture_output=True, text=True, timeout=30)
    return bool(result.stdout.strip())


# (opener, delimiter, lib keeps body, venv keeps body, bwp hits, gh nudges)
HEREDOCS = [
    ("<<EOF", "EOF", False, False, [], True),
    ("<<'EOF'", "EOF", False, False, [], True),
    ('<<"EOF"', "EOF", False, False, [], True),
    ("<<-EOF", "EOF", False, False, [], True),
    ("<< EOF", "EOF", False, False, [], True),
    ("<<EOF1", "EOF1", False, False, [], True),
    ("<<_x", "_x", False, False, [], True),
    # The agreed standard (#1304) changed only these rows; the pre-#1304 answer
    # is named per caller. Each change scans more text or nudges less, never
    # skips text a guard used to read.
    # A here-string has no body. Was: venv False, bwp [], gh True.
    ("<<<EOF", "EOF", True, True, [_BODY], False),
    # A digit delimiter. Was: bwp [], gh True.
    ("<<1", "1", True, True, [_BODY], False),
    ("<<'1'", "1", True, True, [_BODY], False),
    # An unterminated quote; bwp's single-quote scan already covered the rest.
    # Was: gh True.
    ("<<'EOF", "EOF", True, True, [], False),
    ("<<\\EOF", "EOF", True, True, [_BODY], False),
]
for opener, delim, lib_keeps, venv_keeps, bwp_hits, gh_nudge in HEREDOCS:
    command = f"cat {opener} > f\n{_BODY}\n{delim}\necho done"
    got = _BODY in _lib.strip_heredoc_bodies(command)
    check(got == lib_keeps, f"_lib keeps the body after {opener!r}: {lib_keeps} (got {got})")
    got = _BODY in _venv.strip_nonexecuted_heredoc_bodies(command)
    check(got == venv_keeps, f"venv keeps the body after {opener!r}: {venv_keeps} (got {got})")
    got = [m.group(0) for m in _bwp.find_unsafe_drive_paths(command)]
    check(got == bwp_hits, f"bash_windows_path_guard hits after {opener!r}: {bwp_hits!r} (got {got!r})")
    got = _gh_nudges(f'gh issue comment 5 --body "$(cat {opener}\nhi\n{delim}\n)"')
    check(got == gh_nudge, f"gh_body_file_guard nudges on {opener!r}: {gh_nudge} (got {got})")

# `<<-` lets the closing delimiter be tab-indented; bash_windows_path_guard
# reads the dash to know the body ends there, not at the end of the command.
command = f"cat <<-EOF > f\n\t{_BODY}\n\tEOF\nls {_BODY}"
got = [m.group(0) for m in _bwp.find_unsafe_drive_paths(command)]
check(got == [_BODY], f"bash_windows_path_guard ends a <<- body at a tab-indented delimiter (got {got!r})")

_h.report_and_exit("test_shell_parse")
