"""Enforce the project's `.venv` discipline.

Every project in the fleet uses `.venv` (never `venv`), never activates,
always invokes via `& .\\.venv\\Scripts\\python.exe ...`. This hook catches
the three common drifts:

  1. `python -m venv venv`   — wrong directory name
  2. `.\\.venv\\Scripts\\activate` / `source .venv/bin/activate`
                              — activation is banned
  3. Bare `python <file>` / `pip install ...` when a project `.venv` exists
                              — would hit the system Python instead

Allow-listed:
  * `python -m venv .venv`                 — correct directory name
  * `& .\\.venv\\Scripts\\python.exe ...`  — correct invocation form
  * `& ./.venv/bin/python ...`             — POSIX equivalent
  * Bare `python` when no `.venv` exists at or above the project root.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402


# 1) `python -m venv <name>` where name is NOT `.venv`
VENV_CREATE_RE = re.compile(
    r"\bpython\w*\s+(?:-\d(?:\.\d+)?\s+)?-m\s+venv\s+(?!\.venv\b)([^\s;|&]+)",
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


def main() -> None:
    payload = _lib.read_stdin_json()
    if _lib.tool_name(payload) not in {"Bash", "PowerShell"}:
        _lib.allow()

    cmd = _lib.command_string(payload)
    if not cmd:
        _lib.allow()

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

    # 3) Bare python/pip when a project .venv is present
    has_bare_python = bool(BARE_PYTHON_RE.search(cmd))
    has_bare_pip    = bool(BARE_PIP_RE.search(cmd))

    if (has_bare_python or has_bare_pip) and not _is_path_scoped(cmd):
        venv_python = _lib.find_venv_python(_lib.cwd(payload))
        if venv_python is not None:
            verb = "python" if has_bare_python else "pip"
            _lib.block(
                "Blocked: bare `" + verb + "` invocation with a project .venv present at "
                + str(venv_python.parent.parent) + ". "
                "Use `& .\\.venv\\Scripts\\python.exe ...` (or `& .\\.venv\\Scripts\\pip.exe ...`) "
                "so you hit the venv, not the system Python."
            )

    _lib.allow()


if __name__ == "__main__":
    main()
