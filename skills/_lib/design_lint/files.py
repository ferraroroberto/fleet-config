"""Which files a lens reads, and how their paths are spelled in findings.

The one place that knows what counts as a repo file (tracked, non-skipped) and
where an app keeps its `_vendored/` components. Every lens starts here.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

# `git_run` is a *sibling top-level module* in skills/_lib, not part of this
# package — reached the same way the pre-split single-file module reached it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import git_run  # noqa: E402


SKIP_DIR_PARTS = {".git", ".venv", "node_modules", "__pycache__", "spike", "spikes"}


def repo_files(root: Path, suffixes: Tuple[str, ...]) -> List[Path]:
    """Tracked files by suffix — `git ls-files` when available, rglob fallback.

    The `git` shell-out is `git_run.run_git` (fleet-config#561); only the rglob
    fallback is local. The wrapper it used to hand-roll had already been
    factored out into that shared helper, which owns the explicit UTF-8 decode
    and `NO_WINDOW`.
    """
    try:
        out = git_run.run_git(["-C", str(root), "ls-files"], timeout=15)
        if out.returncode == 0:
            names = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
            paths = [root / n for n in names]
        else:
            raise OSError(out.stderr.strip() or "git ls-files failed")
    except (OSError, subprocess.SubprocessError):
        paths = list(root.rglob("*"))
    keep: List[Path] = []
    for p in paths:
        if p.suffix.lower() not in suffixes or not p.is_file():
            continue
        if any(part in SKIP_DIR_PARTS for part in p.parts):
            continue
        keep.append(p)
    return sorted(keep)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def find_vendored_root(root: Path) -> Optional[Path]:
    """Locate the app-side `static/_vendored` dir regardless of layout.

    project-scaffolding's own `app/webapp/static/` is one layout among several
    the fleet actually uses (`app/static/` — grocery; `app_web/static/` —
    local-llm-hub), so this searches rather than hardcoding the scaffold's
    path (fleet-config#291, #292). Bounded to two path segments ahead of
    `static/_vendored` — deeper nesting isn't a layout seen in the fleet.
    """
    candidates = sorted(p for p in root.glob("*/static/_vendored") if p.is_dir())
    candidates += sorted(p for p in root.glob("*/*/static/_vendored") if p.is_dir())
    for c in candidates:
        if not any(part in SKIP_DIR_PARTS for part in c.parts):
            return c
    return None
