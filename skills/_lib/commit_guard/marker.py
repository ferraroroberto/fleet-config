"""Grant a scheduled map refresh the right to commit on the default branch.

`hooks/main_commit_guard.py` refuses `git commit` on a repo's default branch.
A scheduled `/system-map` or `/config-map` run has nobody attending and commits
its generated `architecture/` output straight on main (global-CLAUDE.md "Hard
rules"), so the skill grants itself a short-lived marker around that commit:

    marker.py grant  <repo> --reason system-map --path architecture/ [--path docs/fleet-guide.md]
    marker.py revoke <repo>
    marker.py status <repo>        # GRANT=none | GRANT=<reason> paths=… expires_in=<s>

A grant covers one repo and the path prefixes named by `--path`; the guard
checks the commit's staged files against them. It expires after `--minutes`
(default 15, at most 60), so a run that dies before `revoke` leaves nothing open.
`/quick` needs no grant: it commits on its own `quick/<slug>` branch.

The marker is one JSON file per repo under `<hooks state>/main-commit-allow/`.
The guard is in `hooks/` and cannot import this tier, so it spells the same
directory, key and fields; `tests/hooks/test_main_commit_guard.py` grants through
this module and checks the hook honours it.

Exit 0 on success, 2 on a refused grant (bad argument, not a git repo).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import git_run  # noqa: E402
from fleet_core.hooks_state import atomic_write_text, state_dir  # noqa: E402

MARKER_DIR = "main-commit-allow"
DEFAULT_MINUTES = 15
MAX_MINUTES = 60


def repo_key(top: str) -> str:
    """Stable id of a working tree, the marker file's name (same spelling as the hook's)."""
    return hashlib.sha1(os.path.normcase(os.path.realpath(top)).encode("utf-8")).hexdigest()[:16]


def _toplevel(repo: str) -> Optional[str]:
    res = git_run.run_git(["-C", repo, "rev-parse", "--show-toplevel"])
    top = res.stdout.strip()
    return top if res.returncode == 0 and top else None


def _marker_path(top: str) -> Path:
    return state_dir() / MARKER_DIR / f"{repo_key(top)}.json"


def grant(top: str, reason: str, paths: List[str], minutes: int) -> Path:
    path = _marker_path(top)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "repo_key": repo_key(top),
        "repo": top,
        "reason": reason,
        "paths": [p.replace("\\", "/") for p in paths],
        "granted_at": time.time(),
        "expires_at": time.time() + minutes * 60,
    }
    atomic_write_text(path, json.dumps(row, indent=2) + "\n")
    return path


def status(top: str) -> str:
    try:
        row = json.loads(_marker_path(top).read_text(encoding="utf-8"))
        left = int(float(row["expires_at"]) - time.time())
    except (OSError, ValueError, KeyError, TypeError):
        return "GRANT=none"
    if left <= 0:
        return "GRANT=none"
    return f"GRANT={row.get('reason', '')} paths={','.join(row.get('paths', []))} expires_in={left}"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("verb", choices=("grant", "revoke", "status"))
    parser.add_argument("repo")
    parser.add_argument("--reason", default="")
    parser.add_argument("--path", action="append", default=[], dest="paths")
    parser.add_argument("--minutes", type=int, default=DEFAULT_MINUTES)
    args = parser.parse_args(argv)

    top = _toplevel(args.repo)
    if top is None:
        print(f"marker: {args.repo} is not a git working tree", file=sys.stderr)
        return 2
    if args.verb == "status":
        print(status(top))
        return 0
    if args.verb == "revoke":
        try:
            _marker_path(top).unlink()
        except FileNotFoundError:
            pass
        print("GRANT=none")
        return 0
    if not args.reason.strip() or not args.paths:
        print("marker: grant needs --reason and at least one --path", file=sys.stderr)
        return 2
    if not 1 <= args.minutes <= MAX_MINUTES:
        print(f"marker: --minutes must be 1..{MAX_MINUTES}", file=sys.stderr)
        return 2
    path = grant(top, args.reason.strip(), args.paths, args.minutes)
    print(f"GRANT={args.reason.strip()} file={path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
