"""Check every surviving Python target after a successful shared edit event.

Uses each target's project .venv (then system Python). Syntax failures retain
block() feedback; unavailable targets, unknown outcomes and checker failures
produce explicit unverified feedback instead of silently implying a pass.
"""
from __future__ import annotations

import logging
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402

logger = logging.getLogger(__name__)


def main() -> None:
    payload = _lib.read_stdin_json()
    edit = _lib.edit_event(payload)
    if edit.status == "not_edit" or edit.outcome == "pending":
        _lib.allow()
    if edit.status == "unverified":
        _lib.warn("py_compile: unverified - " + edit.reason)
    if edit.outcome != "success":
        _lib.warn("py_compile: unverified - edit " + edit.outcome + "; final targets not checked")

    # Keep the final state of each path: renames remove their source; deletes
    # remove even an earlier update of the same file. Path equality deduplicates.
    targets: dict[Path, None] = {}
    for change in edit.targets:
        if change.source_path is not None:
            targets.pop(change.source_path, None)
        if change.operation == "delete":
            targets.pop(change.path, None)
        else:
            targets[change.path] = None

    errors: list[str] = []
    unknown: list[str] = []
    usable: dict[str, bool] = {}

    def interpreter_works(path: str) -> bool:
        if path not in usable:
            try:
                result = subprocess.run([path, "--version"], capture_output=True, timeout=5,
                                        creationflags=_lib.NO_WINDOW)
                usable[path] = result.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                usable[path] = False
        return usable[path]

    for target in targets:
        if target.suffix.lower() != ".py":
            continue
        if not target.is_file():
            unknown.append(f"{target}: target missing or not a file")
            continue
        venv = _lib.find_venv_python(target.parent)
        interpreter = str(venv) if venv and interpreter_works(str(venv)) else None
        if interpreter is None:
            fallback = _lib.find_python_executable()
            if fallback and interpreter_works(fallback):
                interpreter = fallback
        if interpreter is None:
            unknown.append(f"{target}: no working Python interpreter")
            continue
        try:
            result = subprocess.run([interpreter, "-m", "py_compile", str(target)],
                                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    timeout=10, creationflags=_lib.NO_WINDOW)
        except subprocess.TimeoutExpired:
            unknown.append(f"{target}: compiler timed out")
            continue
        except OSError as exc:
            unknown.append(f"{target}: compiler could not start ({exc})")
            continue
        logger.info("Python syntax check target=%s exit_code=%s", target, result.returncode)
        if result.returncode:
            errors.append((result.stderr or "").strip() or (result.stdout or "").strip()
                          or f"{target}: py_compile failed")
    syntax_failed = bool(errors)
    if unknown:
        errors.append("unverified - " + "; ".join(unknown))
    if errors:
        feedback = "py_compile: " + "\n".join(errors)
        if syntax_failed:
            _lib.block(feedback)
        _lib.warn(feedback)
    _lib.allow()


if __name__ == "__main__":
    main()
