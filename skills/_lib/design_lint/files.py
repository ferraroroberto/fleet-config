"""Which files a lens reads, and how their paths are spelled in findings.

The one place that knows what counts as a repo file (tracked, non-skipped),
where an app keeps its `_vendored/` components, and which trees are
third-party rather than app-authored. Every lens starts here.
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

# A genuinely third-party library vendored for offline use (Leaflet, xterm.js)
# sits under a `vendor/` segment. Deliberately *not* the fleet's own
# `_vendored/` component family — those are project-scaffolding components we
# author and byte-compare, so they stay fully in scope. Hence an exact
# path-segment comparison, never a substring: `vendor` must not read as a
# prefix of `_vendored` (fleet-config#416, #843, #940).
THIRD_PARTY_DIR_PARTS = {"vendor", "vendors"}


def is_third_party(path: Path) -> bool:
    """True for a bundled third-party library under `vendor/`.

    **The rule every lens inherits** (fleet-config#940): a check that judges
    what the *app authored* — its button tiers, its touch targets, its token
    adoption — must not read third-party bytes. We did not write them, we will
    never restyle them, and a finding against them has no available fix, so it
    can only sit unresolved in the repo's audit issue forever. A check that
    instead asks "does this signal exist anywhere in what ships" (a focus ring,
    a nav shell) legitimately reads everything. In the contracts lens that is
    the `*_own` / `*_all` blob split on `_ContractsCtx`; a new contract picks
    one deliberately rather than re-deciding the exclusion.

    Matched by path segment rather than declared per repo (a `.fleet.toml`
    key): the pattern needs no per-repo adoption step, and a repo that simply
    forgot to declare its tree would regress to the exact false findings this
    exists to stop — silently, which is the worse failure mode.
    """
    return any(part.lower() in THIRD_PARTY_DIR_PARTS for part in path.parts)


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
