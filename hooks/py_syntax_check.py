"""Surface Python syntax errors immediately after an Edit/Write.

Triggers on `PostToolUse` for `Edit` and `Write` when the target file is a
`*.py`. Runs `python -m py_compile <file>` against the project's `.venv`
(falls back to the `py` launcher / system Python). On failure, exits with
the compiler error on stderr so Claude sees it inline and can fix the typo
in the next turn.

Why: silently broken Python sits there until the next manual run. ~50 ms
per edit is a small price for surfacing the problem at the moment of edit.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402


def main() -> None:
    payload = _lib.read_stdin_json()
    if _lib.tool_name(payload) not in {"Edit", "Write", "MultiEdit"}:
        _lib.allow()

    target = _lib.file_path(payload)
    if target is None or target.suffix.lower() != ".py":
        _lib.allow()

    if not target.exists():
        # File may have been written then moved; nothing to compile.
        _lib.allow()

    # Find the right Python interpreter. Prefer the project's .venv, then `py`,
    # then `python` on PATH. Validate each candidate before committing (a broken
    # venv has a python.exe stub that exists on disk but fails to launch when the
    # base installation has been removed).
    interpreter: str | None = None

    def _interpreter_works(path: str) -> bool:
        try:
            r = subprocess.run([path, "--version"], capture_output=True, timeout=5)
            return r.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    venv_py = _lib.find_venv_python(target.parent)
    if venv_py is not None and _interpreter_works(str(venv_py)):
        interpreter = str(venv_py)

    if interpreter is None:
        for name in ("py", "python"):
            resolved = shutil.which(name)
            if resolved and _interpreter_works(resolved):
                interpreter = resolved
                break

    if interpreter is None:
        # Can't check; don't block.
        _lib.allow()

    try:
        result = subprocess.run(
            [interpreter, "-m", "py_compile", str(target)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        _lib.allow()

    if result.returncode == 0:
        _lib.allow()

    # py_compile writes its error to stderr (and sometimes stdout)
    err = (result.stderr or "").strip() or (result.stdout or "").strip() or "py_compile failed"
    _lib.block("py_compile: " + err)


if __name__ == "__main__":
    main()
