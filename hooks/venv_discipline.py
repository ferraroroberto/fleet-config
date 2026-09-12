"""Enforce the project's `.venv` discipline.

Every project in the fleet uses `.venv` (never `venv`), never activates,
always invokes via `& .\\.venv\\Scripts\\python.exe ...`. This hook catches
the four common drifts:

  1. `python -m venv venv`   — wrong directory name
  2. `.\\.venv\\Scripts\\activate` / `source .venv/bin/activate`
                              — activation is banned
  3. Bare `python <file>` / `pip install ...` when a project `.venv` exists
                              — would hit the system Python instead
  4. A recursive delete, or a venv (re)creation, whose target resolves through
     a **`.venv` junction** — fleet-config#847

Rule 4 is the destructive one. A worktree's `.venv` is a *junction* to the
primary checkout's real venv (`worktree_claim.py`: worktrees don't share
untracked files, and a 24-repo fleet can't recreate heavy venvs per worktree),
so `git worktree remove`, `rm -rf`, `Remove-Item -Recurse` and `rmdir /s` all
follow the reparse point and delete the primary's real site-packages — which
every other lane in that repo, and any running app, is importing from. Only
`worktree_claim.py remove-worktree` is safe: it strips the junction with a
bare `rmdir` (reparse-safe, never `/s`) *before* calling git.

Allow-listed:
  * `python -m venv .venv`                 — correct directory name
  * `python -m venv --clear .venv`         — flags are flags, not a directory name
  * `& .\\.venv\\Scripts\\python.exe ...`  — correct invocation form
  * `& ./.venv/bin/python ...`             — POSIX equivalent
  * Bare `python` when no `.venv` exists at or above the project root.
  * `rmdir <path>` with no `/s` — the reparse-safe form, which by definition
    cannot recurse into a junction's target.
  * `pip install` / `pip uninstall` through a worktree's junctioned venv —
    the venv is *deliberately* shared, so mutating packages through it is the
    intended behaviour, not the footgun. Rule 4 is about delete and recreate.

Heredoc bodies that the consuming command can only *write* — `cat`/`tee` into
a file, or `--body-file -` — are stripped before any matching: that text is
documentation, not a command. This guard refused the very issue that asked for
rule 4 to exist, because the issue body quoted the hazard it describes
(fleet-config#847). Bodies that are executed (`python - <<'PY'`, `cat <<'EOF'
| sh`) are left intact, redirect or no redirect.
"""

from __future__ import annotations

import os
import re
import stat
import sys
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402


# 1) `python -m venv <name>` where name is NOT `.venv`.
#
# `venv`'s own flags (`--clear`, `--upgrade`, `--system-site-packages`, …) sit
# between the module and the directory, and are NOT a directory name: the
# original pattern read the first token after `venv` unconditionally and
# refused `python -m venv --clear .venv` as if `--clear` were a bad name
# (fleet-config#847). Flags are consumed, and the captured name may not itself
# start with `-`, so a flags-only fragment matches nothing at all.
#
# The `.exe` in the invocation prefix matters for rule 4: the fleet's own
# documented form is `& .\.venv\Scripts\python.exe -m venv …`, and without it
# only a bare `python` reached either pattern.
_VENV_INVOKE = r"\bpython\w*(?:\.exe)?\s+(?:-\d(?:\.\d+)?\s+)?-m\s+venv\s+"
_VENV_FLAG = r"(?:--?[\w-]+(?:=[^\s;|&]+)?\s+)*"
_VENV_NAME = r"([^-\s;|&][^\s;|&]*)"

VENV_CREATE_RE = re.compile(
    _VENV_INVOKE + _VENV_FLAG + r"(?!\.venv\b)" + _VENV_NAME,
    re.IGNORECASE,
)

# Same shape, but capturing the target of *any* venv creation — including the
# correctly-named `.venv` — so rule 4 can ask whether that target is a junction.
VENV_TARGET_RE = re.compile(
    _VENV_INVOKE + _VENV_FLAG + _VENV_NAME,
    re.IGNORECASE,
)

# 2) Activation
ACTIVATE_PATTERNS = (
    r"\.[\\/]\.venv[\\/]Scripts[\\/]Activate(?:\.ps1|\.bat)?\b",
    r"\bsource\s+[^\s;|&]*\.venv/bin/activate\b",
    r"\bvenv[\\/]Scripts[\\/]Activate(?:\.ps1|\.bat)?\b",
    r"\bsource\s+[^\s;|&]*venv/bin/activate\b",
)

# 3) Bare `python` / `pip` invocation. We only block when:
#      (a) the token appears at a command boundary (start of line, or after
#          `;`/`&&`/`||`/`|`/`& `), NOT deep inside a quoted string;
#      (b) a project `.venv` exists at or above cwd.
#
# Matches `python ...`, `python3 ...`, `pip install ...`. Does NOT match
# `& .\.venv\Scripts\python.exe ...` (path-scoped) or `py -m ...` (the
# launcher is fine for one-off tooling like py_compile).
_BOUNDARY = r"(?:^|[\r\n;]|&&|\|\||\||&\s)\s*"
BARE_PYTHON_RE = re.compile(
    _BOUNDARY + r"python\d*(?:\.exe)?(?=\s|$)",
    re.IGNORECASE,
)
BARE_PIP_RE = re.compile(
    _BOUNDARY + r"pip\d*(?:\.exe)?(?=\s|$)",
    re.IGNORECASE,
)

# These prefixes mean the command is path-scoped — allow.
PATH_SCOPED_HINTS = (
    r"\.venv[\\/]Scripts[\\/]python",
    r"\.venv/bin/python",
    r"\.venv[\\/]Scripts[\\/]pip",
    r"\.venv/bin/pip",
)


def _is_path_scoped(cmd: str) -> bool:
    return any(re.search(p, cmd, re.IGNORECASE) for p in PATH_SCOPED_HINTS)


# Same clause separators safe_kill_guard.py's forced_push_refspecs already
# splits on. Path-scoping must be judged per clause, not over the whole
# command string — otherwise a compound command that pairs a correctly
# venv-scoped invocation with a later bare `python`/`pip` clause passes
# whole, because `_is_path_scoped` is satisfied by the first clause
# (fleet-config#709).
_SEGMENT_SPLIT_RE = re.compile(r"[\n;|&]+")


# --------------------------------------------------------- heredoc stripping
#
# `cat > notes.md <<'EOF' … EOF` writes a file; its body is text, never a
# command. Matching guard patterns inside it produces a refusal for *writing
# about* the hazard — which is how this hook blocked fleet-config#847 from
# being filed, on the phrase the issue was quoting.
#
# The discriminator is the *consuming command*, not the redirect alone: a body
# is stripped only where that command cannot execute it. `cat`/`tee` into a
# file, and `--body-file -` (how the fleet files issues and PRs), are text
# sinks. `python - <<'PY' > out.txt` is NOT — it has a file redirect but runs
# its body — and `cat <<'EOF' | sh` is not either, which is why the redirect is
# still required alongside `cat`.

_HEREDOC_START_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
# A `>` / `>>` that redirects to a path — not `2>&1`, not the `<<` itself.
_FILE_REDIRECT_RE = re.compile(r"(?<![0-9<>])>>?\s*(?!&)")
# Commands that copy stdin to a file or to stdout without ever running it.
_TEXT_SINK_COMMANDS = {"cat", "tee"}
# `gh issue create --body-file -`, `gh pr create -F -`: the body becomes an API
# field, never a command.
_BODY_FILE_STDIN_RE = re.compile(r"(?:--body-file|--body|-F)[=\s]+-(?=\s|$)")


def _is_text_sink_line(line: str) -> bool:
    """True when this heredoc's body is written somewhere, never executed."""
    if _BODY_FILE_STDIN_RE.search(line):
        return True
    tokens = _tokens(line)
    if not tokens:
        return False
    first = tokens[0].lower().rsplit("/", 1)[-1]
    return first in _TEXT_SINK_COMMANDS and bool(_FILE_REDIRECT_RE.search(line))


def strip_nonexecuted_heredoc_bodies(cmd: str) -> str:
    """`cmd` with the body of every text-sink heredoc removed.

    Delimiter lines are kept so line-oriented patterns can't be knitted
    together across the hole the body leaves behind.
    """
    lines = cmd.split("\n")
    out: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        match = _HEREDOC_START_RE.search(line)
        if match and _is_text_sink_line(line):
            delimiter = match.group(2)
            i += 1
            while i < len(lines) and lines[i].strip() != delimiter:
                i += 1
            if i < len(lines):
                out.append(lines[i])  # the closing delimiter
        i += 1
    return "\n".join(out)


# ------------------------------------------------------ .venv junction guard

_TOKEN_RE = re.compile(r"'([^']*)'|\"([^\"]*)\"|(\S+)")


def _tokens(segment: str) -> List[str]:
    """Shell-ish tokens, quotes stripped, backslashes left alone.

    `shlex` is not usable here: POSIX mode eats the backslashes out of every
    Windows path, and non-POSIX mode keeps the quotes.
    """
    out: List[str] = []
    for match in _TOKEN_RE.finditer(segment):
        for group in match.groups():
            if group is not None:
                out.append(group)
                break
    return out


# A cmd.exe switch (`/s`, `/q`) vs. a Git Bash absolute path (`/e/automation/…`)
# — both start with `/`, and treating the second as a flag would drop the
# operand from the exact command that caused fleet-config#847.
_CMD_SWITCH_RE = re.compile(r"^/[A-Za-z]{1,3}$")

# Git Bash hands us MSYS paths; `Path("/e/automation/x")` has a root but no
# drive on Windows, so it is not `is_absolute()` and would be joined onto the
# payload cwd. Translate it back to the drive form first.
_MSYS_DRIVE_RE = re.compile(r"^/([A-Za-z])/(.*)$")


# PowerShell resolves any unambiguous prefix of a parameter name, so `-r`,
# `-rec` and `-Recurse` are all the same switch.
_PS_RECURSE_RE = re.compile(r"^-r(?:e(?:c(?:u(?:r(?:s(?:e)?)?)?)?)?)?$", re.IGNORECASE)


def _is_flag(token: str) -> bool:
    return token.startswith("-") or bool(_CMD_SWITCH_RE.match(token))


def _operand_path(raw: str, base: Path) -> Path:
    """`raw` as a filesystem path to test, resolved against the payload cwd.

    A glob is reduced to the directory it expands *within*: the shell expands
    `rm -rf *` long after the hook has seen the command string, so the only
    honest question left is whether that directory holds the junction.
    """
    text = raw.strip().strip("'\"")
    msys = _MSYS_DRIVE_RE.match(text)
    if msys:
        text = f"{msys.group(1).upper()}:/{msys.group(2)}"
    if any(ch in text for ch in "*?["):
        text = text.replace("\\", "/").rsplit("/", 1)[0] if "/" in text.replace("\\", "/") else "."
    path = Path(text)
    if not path.is_absolute():
        path = base / path
    return path


def is_reparse_point(path: Path) -> bool:
    """True if `path` is a junction, symlink, or other reparse point.

    Windows junctions set `FILE_ATTRIBUTE_REPARSE_POINT`; the `S_ISLNK`
    fallback covers POSIX symlinks, which is also what makes this testable off
    Windows.
    """
    try:
        st = os.lstat(path)
    except (OSError, ValueError):
        return False
    attributes = getattr(st, "st_file_attributes", 0)
    if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
        return True
    return stat.S_ISLNK(st.st_mode)


def junctioned_venv_under(target: Path) -> Optional[Path]:
    """The `.venv` reparse point a delete of `target` would recurse *through*.

    Two shapes reach the primary's real venv: the junction named directly
    (`rm -rf <wt>/.venv`), and the worktree that contains it (`git worktree
    remove <wt>`). Returns None when there is no junction — a real `.venv`
    directory is the caller's own to delete.
    """
    if target.name == ".venv" and is_reparse_point(target):
        return target
    nested = target / ".venv"
    if is_reparse_point(nested):
        return nested
    return None


def destructive_operands(segment: str) -> Tuple[Optional[str], List[str]]:
    """`(verb, paths)` for a segment that deletes recursively or builds a venv.

    `(None, [])` when the segment does neither. `rmdir` without `/s` is
    deliberately absent: it removes a reparse point without following it, and
    is the safe form `worktree_claim.py` itself uses.
    """
    tokens = _tokens(segment)
    if not tokens:
        return None, []
    lowered = [t.lower() for t in tokens]

    # `git [-C <repo>] worktree remove [--force] <path>`
    if "git" in lowered and "worktree" in lowered:
        index = lowered.index("worktree")
        if index + 1 < len(lowered) and lowered[index + 1] == "remove":
            return "git worktree remove", [
                t for t in tokens[index + 2:] if not _is_flag(t)
            ]

    # `rm -rf <path>` (a recursive flag anywhere in the flag run)
    if lowered[0] in {"rm", "rm.exe"}:
        recursive = any(
            t == "--recursive" or (t.startswith("-") and not t.startswith("--") and ("r" in t or "R" in t))
            for t in tokens[1:]
        )
        if recursive:
            return "rm -r", [t for t in tokens[1:] if not _is_flag(t)]

    # `Remove-Item -Recurse <path>` (PowerShell). PowerShell accepts any
    # unambiguous prefix of a parameter name, so `-r` and `-rec` are as legal
    # as `-Recurse` and must match too.
    if lowered[0] in {"remove-item", "ri", "rm", "del", "erase"}:
        if any(_PS_RECURSE_RE.match(t) for t in tokens[1:]):
            return "Remove-Item -Recurse", [t for t in tokens[1:] if not _is_flag(t)]

    # `rmdir /s <path>` / `del /s <path>` (cmd). Without `/s` both are safe:
    # `rmdir` removes a reparse point without following it, and `del` needs
    # `/s` before it will descend at all.
    if lowered[0] in {"rmdir", "rd", "del", "erase"}:
        if any(t.lower().lstrip("/").find("s") >= 0 and t.startswith("/") for t in tokens[1:]):
            return lowered[0] + " /s", [t for t in tokens[1:] if not _is_flag(t)]

    # `python -m venv [flags] <dir>` — creating or `--clear`-ing over a junction
    # writes through it just as surely as a delete does.
    match = VENV_TARGET_RE.search(segment)
    if match:
        return "python -m venv", [match.group(1)]

    return None, []


def junction_hazard(cmd: str, base: Path) -> Optional[Tuple[str, str, Path]]:
    """`(verb, operand, junction)` for the first hazardous clause, else None."""
    for segment in _SEGMENT_SPLIT_RE.split(cmd):
        verb, operands = destructive_operands(segment)
        if not verb:
            continue
        for operand in operands:
            junction = junctioned_venv_under(_operand_path(operand, base))
            if junction is not None:
                return verb, operand, junction
    return None


def main() -> None:
    payload = _lib.read_stdin_json()
    if _lib.tool_name(payload) not in {"Bash", "PowerShell"}:
        _lib.allow()

    cmd = _lib.command_string(payload)
    if not cmd:
        _lib.allow()

    # Documentation about a hazard is not the hazard (fleet-config#847).
    cmd = strip_nonexecuted_heredoc_bodies(cmd)

    # 1) Wrong-name venv creation
    m = VENV_CREATE_RE.search(cmd)
    if m:
        bad_name = m.group(1).strip().strip("'\"")
        _lib.block(
            "Blocked: `python -m venv " + bad_name + "` — the canonical "
            "directory name in this fleet is `.venv` (with a leading dot). "
            "Use `python -m venv .venv` instead."
        )

    # 2) Activation
    for pattern in ACTIVATE_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            _lib.block(
                "Blocked: never activate the venv (matched: " + pattern + "). "
                "Invoke directly: `& .\\.venv\\Scripts\\python.exe ...` on Windows, "
                "`./.venv/bin/python ...` on POSIX."
            )

    # 3) Bare python/pip when a project .venv is present. Evaluated per
    # clause (see _SEGMENT_SPLIT_RE) so a compound command can't launder a
    # bare invocation behind an earlier, correctly-scoped one.
    hit_verb = None
    for segment in _SEGMENT_SPLIT_RE.split(cmd):
        has_bare_python = bool(BARE_PYTHON_RE.search(segment))
        has_bare_pip    = bool(BARE_PIP_RE.search(segment))
        if (has_bare_python or has_bare_pip) and not _is_path_scoped(segment):
            hit_verb = "python" if has_bare_python else "pip"
            break

    if hit_verb is not None:
        venv_python = _lib.find_venv_python(_lib.cwd(payload))
        if venv_python is not None:
            _lib.block(
                "Blocked: bare `" + hit_verb + "` invocation with a project .venv present at "
                + str(venv_python.parent.parent) + ". "
                "Use `& .\\.venv\\Scripts\\python.exe ...` (or `& .\\.venv\\Scripts\\pip.exe ...`) "
                "so you hit the venv, not the system Python."
            )

    # 4) Anything that would delete or rebuild *through* a `.venv` junction.
    # Checked last: it is the only rule that reads the filesystem, so the three
    # pure-text rules above answer first and this one only runs on a command
    # that is otherwise fine.
    hazard = junction_hazard(cmd, _lib.cwd(payload))
    if hazard is not None:
        verb, operand, junction = hazard
        try:
            real = os.path.realpath(junction)
        except OSError:
            real = "(unresolvable)"
        _lib.block(
            "Blocked: `" + verb + " " + operand + "` would run through the .venv "
            "junction at " + str(junction) + " -> " + str(real) + ". "
            "That junction is the primary checkout's REAL venv, shared by every "
            "worktree in this repo and by any app running from it — a recursive "
            "delete or a venv rebuild follows it and guts the primary "
            "(fleet-config#847: this destroyed two repos' venvs in one night, "
            "one of them under a live app). "
            "Use the reparse-safe teardown, which strips the junction first: "
            "`worktree_claim.py remove-worktree <worktree-path>`. "
            "To remove just the junction and keep its target, `rmdir <path>` "
            "with no `/s`."
        )

    _lib.allow()


if __name__ == "__main__":
    main()
