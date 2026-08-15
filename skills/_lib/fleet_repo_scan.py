"""Shared "enumerate the fleet + resolve a repo's committed default branch"
logic behind /config-map's and /system-map's `build_data.py` (fleet-config#318),
plus the filesystem-crawl half (`is_fleet_repo` / `iter_fleet_repos`) that
/audit-fleet's and /design-sweep's scanners share (fleet-config#561).

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
from typing import Dict, Iterator, Optional

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


def is_linked_worktree(path: str | Path) -> bool:
    """True if `path` is a linked git worktree rather than a real checkout.

    `git worktree add` writes a `.git` **file** containing a `gitdir:` pointer,
    where a real checkout has a `.git` **directory**. That one byte-level fact
    is the only reliable discriminator, and every fleet sweep over
    `E:/automation` needs it: a sibling `<repo>-wt-<N>` tree is a full checkout
    holding a byte-identical copy of its primary's `CLAUDE.md`, `.fleet.toml`
    and skills, so counting it double-counts the repo it belongs to.

    Worktrees are created and torn down constantly by `/issue-start`,
    `/cleanup-fleet` and `/cleanup-fleet-all`, so a scan without this guard
    produces different numbers on every run with no file having changed
    (fleet-config#629 — `/context-audit`'s budget block reported 6.5% phantom
    tokens from two unrelated sessions' worktrees).

    Named and shared rather than re-inlined: `iter_fleet_repos` below asks the
    stricter question ("is this a real repo *of ours*") via `.git.is_dir()`,
    which excludes worktrees as a side effect; callers that only need to
    exclude worktrees — without also imposing repo-ownership — call this.
    """
    return (Path(path) / ".git").is_file()


def is_fleet_repo(remote_url: str | None) -> bool:
    """True if the remote URL belongs to the `ferraroroberto` GitHub org.

    Matches both the https (`https://github.com/ferraroroberto/x.git`) and
    ssh (`git@github.com:ferraroroberto/x.git`) remote URL forms.
    """
    return bool(remote_url) and "ferraroroberto/" in remote_url


def iter_fleet_repos(root: str | Path, only: str | None = None) -> Iterator[Path]:
    """Yield every fleet repo directory directly under `root`, in name order.

    A directory qualifies when it is a real repo (`.git` is a **directory** —
    a linked worktree's `.git` is a *file*, and sweeping a sibling `-wt-<N>`
    tree would double-count the repo it belongs to; see `is_linked_worktree`)
    whose `origin` remote is a `ferraroroberto` one. `only` narrows to a
    single repo by directory name.
    A directory whose `origin` can't be read is skipped silently — it isn't a
    fleet repo as far as this crawl can tell.

    `fleet_audit_scan.scan` and `design_sweep_scan.scan` carried this loop
    copy-pasted verbatim, comment and all (fleet-config#561), which is why the
    worktree guard had to be got right twice.
    """
    for d in sorted(Path(root).iterdir()):
        if not d.is_dir():
            continue
        if not (d / ".git").is_dir():
            continue
        if only and d.name != only:
            continue
        try:
            remote = git_run.run_git_checked(["-C", str(d), "remote", "get-url", "origin"])
        except SystemExit:
            continue
        if not is_fleet_repo(remote):
            continue
        yield d


def default_ref(repo_dir: Path) -> Optional[str]:
    """The ref to read a repo's committed state from (its default branch).

    Resolves ``origin/HEAD`` (e.g. ``origin/main``); falls back through common
    default-branch names. Uses remote-tracking refs so the result is
    independent of which branch the repo is checked out on, and needs no
    network. Routed through the shared `git_run.resolve_default_branch_ref`
    resolver (fleet-config#500); `final_fallback=""` reproduces this helper's
    own pre-existing "give up and return None" contract on top of it.
    """
    ref = git_run.resolve_default_branch_ref(
        repo_dir, candidates=("origin/main", "origin/master", "main", "master"), final_fallback="",
    )
    return ref or None
