"""Net growth of the instruction files a branch carries, as one reported line.

An agent-behaviour change that lands as new prose grows every session's context
and a model can skip it; the instruction ratchet caps hits per rule and size per
file, but says nothing about how many instruction lines a PR adds. This reports
that number from `git diff --numstat` so the gate output and the PR body carry it
as a fact rather than as a model's recollection.

  delta.py [repo-path] [--base REF]

Prints exactly one of:
  INSTRUCTION_DELTA=+<added> -<removed> net=<n> files=<count>
  INSTRUCTION_DELTA=UNKNOWN reason=<text>

`UNKNOWN` (no base ref, git failed) is its own state and never reads as `+0 -0`.
Counts the working tree against the merge-base with the default branch, so
committed, staged, unstaged and new untracked instruction files all count. Report
only: always exits 0. stdlib only.
"""

from __future__ import annotations

import argparse
import fnmatch
import sys
from pathlib import Path
from typing import Iterable, List, NamedTuple, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import git_run  # noqa: E402

#: Instruction files by path glob (`*` crosses `/`), the set the instruction ratchet reads.
INSTRUCTION_GLOBS = (
    "CLAUDE.md", "AGENTS.md", "global-CLAUDE.md",
    "*/CLAUDE.md", "*/AGENTS.md",
    "skills/*/SKILL.md", ".claude/skills/*/SKILL.md", ".claude/rules/*.md",
)


class Delta(NamedTuple):
    added: int
    removed: int
    files: int

    @property
    def net(self) -> int:
        return self.added - self.removed


def is_instruction_file(path: str) -> bool:
    return any(fnmatch.fnmatchcase(path, g) for g in INSTRUCTION_GLOBS)


def parse_numstat(text: str) -> Delta:
    """Sum `git diff --numstat` rows over instruction files; a binary row (`-`) counts zero."""
    added = removed = files = 0
    for row in text.splitlines():
        parts = row.split("\t", 2)
        if len(parts) != 3 or not is_instruction_file(parts[2].strip()):
            continue
        a, r, _ = parts
        added += int(a) if a.isdigit() else 0
        removed += int(r) if r.isdigit() else 0
        files += 1
    return Delta(added, removed, files)


def add_new_files(delta: Delta, line_counts: Iterable[int]) -> Delta:
    counts: List[int] = list(line_counts)
    return Delta(delta.added + sum(counts), delta.removed, delta.files + len(counts))


def render(delta: Delta) -> str:
    return f"INSTRUCTION_DELTA=+{delta.added} -{delta.removed} net={delta.net:+d} files={delta.files}"


def measure(repo: Path, base: Optional[str] = None) -> "Delta | str":
    """The delta against the merge-base with `base`, or a reason string when it cannot be established."""
    ref = base or git_run.resolve_default_branch_ref(repo)
    mb = git_run.run_git(["-C", str(repo), "merge-base", ref, "HEAD"])
    if mb.returncode != 0 or not mb.stdout.strip():
        return f"no merge-base with {ref}"
    base_sha = mb.stdout.strip()
    diff = git_run.run_git(["-C", str(repo), "diff", "--numstat", base_sha, "--"])
    if diff.returncode != 0:
        return f"git diff failed ({diff.returncode})"
    untracked = git_run.run_git(["-C", str(repo), "ls-files", "--others", "--exclude-standard"])
    if untracked.returncode != 0:
        return f"git ls-files failed ({untracked.returncode})"
    counts: List[int] = []
    for path in untracked.stdout.splitlines():
        if is_instruction_file(path):
            try:
                counts.append(len((repo / path).read_text(encoding="utf-8", errors="replace").splitlines()))
            except OSError as exc:
                return f"cannot read {path}: {exc}"
    return add_new_files(parse_numstat(diff.stdout), counts)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("repo", nargs="?", default=".", type=Path)
    parser.add_argument("--base", default=None, help="ref to diff against (default: the default branch)")
    args = parser.parse_args(argv)
    result = measure(args.repo.resolve(), args.base)
    print(render(result) if isinstance(result, Delta) else f"INSTRUCTION_DELTA=UNKNOWN reason={result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
