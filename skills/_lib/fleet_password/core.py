"""Find the apps that carry the vendored password module and set the password in each.

An app is a target when its `.fleet.toml` `[vendored]` table declares the
`password` component and its checkout holds `src/password.py`. Each app is set
through its own copy of that module (`python -m src.password set --stdin`, run
with the app's own `.venv`), so this file never hashes or stores anything
itself. The password travels only on the child's stdin: never in argv, output
or a log line. After the set, the app's module is asked to check the password
back; a failed check restores the previous hash file.

The hash is read on every sign-in, so no app needs a restart afterwards.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from fleet_core import fleet_toml  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402

COMPONENT = "password"
MODULE_REL = Path("src") / "password.py"
HASH_FILE_REL = Path("config") / "app_password.json"
HASH_PREFIX = "pbkdf2_sha256$"
# The setter hashes with 200k PBKDF2 rounds (~0.3 s) behind an app's own import of `src`;
# a minute leaves room for a slow cold start without hanging the run on a stuck child.
CHILD_TIMEOUT_S = 60
# The child would otherwise honor an inherited override and write every app's hash to one file.
OVERRIDE_ENV = "APP_PASSWORD_FILE"
_CHECK_SNIPPET = (
    "import sys\n"
    "from src import password\n"
    "sys.exit(0 if password.check_password(sys.stdin.readline().rstrip('\\r\\n')) else 1)\n"
)

PythonFor = Callable[[Path], Optional[Path]]

# One report line per app; these are verdicts of a single pass, not lifecycle states.
OK, SKIPPED, FAILED, WOULD_SET = "OK", "SKIPPED", "FAILED", "WOULD-SET"


@dataclass
class Result:
    """One app's outcome: `OK`, `SKIPPED`, `FAILED` or (dry run) `WOULD-SET`."""

    repo: str
    outcome: str
    detail: str = ""

    def line(self) -> str:
        """The one output line for this app; never carries a secret."""
        return f"{self.outcome:<9} {self.repo}" + (f" ({self.detail})" if self.detail else "")


def declares_password(repo_dir: Path) -> bool:
    """True when the repo's `.fleet.toml` `[vendored]` table names the password component."""
    vendored = fleet_toml.table(fleet_toml.load(repo_dir), "vendored")
    return isinstance((vendored or {}).get(COMPONENT), dict)


def find_targets(repos: Dict[str, Path]) -> Dict[str, Path]:
    """The repos, in name order, that declare the component; linked worktrees are dropped."""
    return {
        name: path
        for name, path in sorted(repos.items())
        if not (path / ".git").is_file() and declares_password(path)
    }


def venv_python(repo_dir: Path) -> Optional[Path]:
    """The app's own `.venv` interpreter, or None when it has none."""
    for rel in (Path(".venv") / "Scripts" / "python.exe", Path(".venv") / "bin" / "python"):
        if (repo_dir / rel).is_file():
            return repo_dir / rel
    return None


def hash_stored(repo_dir: Path) -> bool:
    """Whether the app already has a stored hash. Reads the file; prints nothing from it."""
    try:
        value = json.loads((repo_dir / HASH_FILE_REL).read_text(encoding="utf-8")).get("password_hash", "")
    except (OSError, ValueError, AttributeError):
        return False
    return isinstance(value, str) and value.startswith(HASH_PREFIX)


def _child_env() -> Dict[str, str]:
    env = dict(os.environ)
    env.pop(OVERRIDE_ENV, None)
    env["PYTHONUTF8"] = "1"
    return env


def _run(python: Path, args: List[str], repo_dir: Path, stdin_text: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(python), *args],
        cwd=str(repo_dir),
        input=stdin_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_child_env(),
        timeout=CHILD_TIMEOUT_S,
        creationflags=NO_WINDOW,
    )


def _reason(proc: subprocess.CompletedProcess) -> str:
    """The child's last stderr line, cut short: the setter never writes the password there."""
    lines = [ln.strip() for ln in (proc.stderr or "").splitlines() if ln.strip()]
    tail = lines[-1] if lines else "no error output"
    return f"exit {proc.returncode}: {tail[:160]}"


def _restore(repo_dir: Path, backup: Optional[Path]) -> None:
    target = repo_dir / HASH_FILE_REL
    if backup is not None:
        # runtime-write-exempt: puts back the app's own git-ignored hash file the run just replaced
        shutil.copy2(backup, target)
    else:
        target.unlink(missing_ok=True)


def plan_one(name: str, repo_dir: Path, python_for: PythonFor = venv_python) -> Result:
    """The dry-run line for one app: what a real run would do, touching nothing."""
    if not (repo_dir / MODULE_REL).is_file():
        return Result(name, SKIPPED, f"declares the component but has no {MODULE_REL.as_posix()}")
    if python_for(repo_dir) is None:
        return Result(name, FAILED, "no .venv interpreter")
    stored = "replaces the stored hash" if hash_stored(repo_dir) else "first hash; the old password stops working"
    return Result(name, WOULD_SET, stored)


def set_one(
    name: str, repo_dir: Path, password: str, backup_dir: Path, python_for: PythonFor = venv_python
) -> Result:
    """Back up the app's hash file, set the password with its own setter, then check it back."""
    planned = plan_one(name, repo_dir, python_for)
    if planned.outcome != WOULD_SET:
        return planned
    python = python_for(repo_dir)
    current = repo_dir / HASH_FILE_REL
    backup: Optional[Path] = None
    if current.is_file():
        backup = backup_dir / name / HASH_FILE_REL.name
        # runtime-write-exempt: the backup goes under the hooks state dir the caller passes, outside every repo
        backup.parent.mkdir(parents=True, exist_ok=True)
        # runtime-write-exempt: the backup goes under the hooks state dir the caller passes, outside every repo
        shutil.copy2(current, backup)
    try:
        proc = _run(python, ["-m", "src.password", "set", "--stdin"], repo_dir, password + "\n")
        if proc.returncode != 0:
            return Result(name, FAILED, f"setter {_reason(proc)}")
        check = _run(python, ["-c", _CHECK_SNIPPET], repo_dir, password + "\n")
    except (OSError, subprocess.TimeoutExpired) as exc:
        _restore(repo_dir, backup)
        return Result(name, FAILED, f"{type(exc).__name__}; previous hash restored")
    if check.returncode != 0:
        _restore(repo_dir, backup)
        return Result(name, FAILED, f"app did not accept it back ({_reason(check)}); previous hash restored")
    return Result(name, OK, "backed up" if backup else "no earlier hash")
