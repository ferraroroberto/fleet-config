"""Shared "enumerate the fleet + resolve a repo's committed default branch"
logic behind /config-map's and /system-map's `build_data.py` (fleet-config#318).

Both skills derive their per-repo data from the same `hooks/projects.toml`
membership list, and both need each repo's committed default branch (to read
state from `origin/HEAD`, independent of whatever branch the repo happens to
be checked out on) — before this module existed, `config-map/build_data.py`
and `system-map/build_data.py` each carried a copy-pasted `fleet_repos`/
`_default_ref` pair. One shared pair now backs both; each `build_data.py`
keeps a thin same-named wrapper (its own `PROJECTS_TOML` default) so its call
sites and public API are unchanged.

stdlib + the `git` CLI only (via `skills/_lib/git_run`).
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import git_run  # noqa: E402

# skills/_lib/fleet_repo_scan.py -> repo root is two levels up.
_DEFAULT_PROJECTS_TOML = Path(__file__).resolve().parent.parent.parent / "hooks" / "projects.toml"


def fleet_repos(projects_toml: Path = _DEFAULT_PROJECTS_TOML) -> Dict[str, Path]:
    """Return ``{repo_name: repo_dir}`` for the architecture fleet.

    Fleet = every ``[<name>]`` table carrying a ``cwd_prefix`` − the
    ``[global] architecture_ignore`` list (vendored / legacy / out-of-scope).
    """
    toml = tomllib.loads(projects_toml.read_text(encoding="utf-8"))
    ignore = set(toml.get("global", {}).get("architecture_ignore", []))
    return {
        name: Path(tbl["cwd_prefix"])
        for name, tbl in toml.items()
        if name != "global" and isinstance(tbl, dict) and "cwd_prefix" in tbl
        and name not in ignore
    }


def default_ref(repo_dir: Path) -> Optional[str]:
    """The ref to read a repo's committed state from (its default branch).

    Resolves ``origin/HEAD`` (e.g. ``origin/main``); falls back through common
    default-branch names. Uses remote-tracking refs so the result is
    independent of which branch the repo is checked out on, and needs no
    network.
    """
    head = git_run.run_git(["-C", str(repo_dir), "rev-parse", "--abbrev-ref", "origin/HEAD"])
    if head.returncode == 0 and head.stdout.strip():
        return head.stdout.strip()
    for cand in ("origin/main", "origin/master", "main", "master"):
        if git_run.run_git(["-C", str(repo_dir), "rev-parse", "--verify", "--quiet", cand]).returncode == 0:
            return cand
    return None
