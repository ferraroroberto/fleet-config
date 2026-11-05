"""Refuse a branch that still adds tagged temporary debug output.

A bug diagnosis may add temporary logs, each carrying one unique tag such as
`[DEBUG-` + `a4f2]` (an id of letters and digits in square brackets), so cleanup
is a single search. This check makes that cleanup provable before a PR opens: it
fails when the branch adds a line carrying such a tag.

  check.py [repo-path] [--base REF]

Prints exactly one of, with the exit code:
  DEBUG_TAGS=clean                                    exit 0
  DEBUG_TAGS=FOUND count=<n>, then FOUND=<path>:<line> per hit   exit 1
  DEBUG_TAGS=NOT_CONFIRMED reason=<text>               exit 2

`NOT_CONFIRMED` (no base ref, git failed, an unreadable file) is its own state and
never reads as clean. Scans the working tree against the merge-base with the
default branch, so committed, staged, unstaged and new untracked files all count.
stdlib only.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import git_run  # noqa: E402

#: A real tag has a concrete id, so the placeholder `[DEBUG-<id>]` in docs never matches.
TAG_RE = re.compile(r"\[DEBUG-[0-9A-Za-z]+\]")
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

Hit = Tuple[str, int]


def parse_added(diff: str) -> List[Hit]:
    """`(path, new line number)` for every added line carrying a tag in a `--unified=0` diff."""
    hits: List[Hit] = []
    path, line = "", 0
    for row in diff.splitlines():
        if row.startswith("+++ "):
            path = row[6:] if row.startswith("+++ b/") else row[4:]
        elif (m := HUNK_RE.match(row)) is not None:
            line = int(m.group(1))
        elif row.startswith("+"):
            if TAG_RE.search(row):
                hits.append((path, line))
            line += 1
    return hits


def scan(repo: Path, base: Optional[str] = None) -> "List[Hit] | str":
    """Tagged added lines against the merge-base with `base`, or a reason string when it cannot be established."""
    ref = base or git_run.resolve_default_branch_ref(repo)
    mb = git_run.run_git(["-C", str(repo), "merge-base", ref, "HEAD"])
    if mb.returncode != 0 or not mb.stdout.strip():
        return f"no merge-base with {ref}"
    diff = git_run.run_git(["-C", str(repo), "diff", "--no-color", "--no-ext-diff", "--unified=0", mb.stdout.strip(), "--"])
    if diff.returncode != 0:
        return f"git diff failed ({diff.returncode})"
    untracked = git_run.run_git(["-C", str(repo), "ls-files", "--others", "--exclude-standard"])
    if untracked.returncode != 0:
        return f"git ls-files failed ({untracked.returncode})"
    hits = parse_added(diff.stdout)
    for path in untracked.stdout.splitlines():
        try:
            text = (repo / path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"cannot read {path}: {exc}"
        hits += [(path, n) for n, row in enumerate(text.splitlines(), 1) if TAG_RE.search(row)]
    return hits


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("repo", nargs="?", default=".", type=Path)
    parser.add_argument("--base", default=None, help="ref to diff against (default: the default branch)")
    args = parser.parse_args(argv)
    result = scan(args.repo.resolve(), args.base)
    if isinstance(result, str):
        print(f"DEBUG_TAGS=NOT_CONFIRMED reason={result}")
        return 2
    if not result:
        print("DEBUG_TAGS=clean")
        return 0
    print(f"DEBUG_TAGS=FOUND count={len(result)} remove each tagged debug line before the PR")
    for path, line in result:
        print(f"FOUND={path}:{line}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
