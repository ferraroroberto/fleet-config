"""The source repo as a fetch-only git remote, its work after the watermark, and its licence.

The source is someone else's repo, so nothing here can write to it: the remote's push url
is set to a value git cannot push to, and every call is a read or a fetch.

stdlib only; every git call goes through `git_run.run_git`.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from git_run import run_git  # noqa: E402

REMOTE = "upstream-sync"
# Any push to this remote fails: git cannot resolve the url.
NO_PUSH = "no-push://upstream-sync-is-fetch-only"

_PR_SQUASH = re.compile(r"\(#(\d+)\)\s*$")
_PR_MERGE = re.compile(r"^Merge pull request #(\d+)\b")
_LICENCE_NAME = re.compile(r"^(LICEN[CS]E|COPYING)(\.(md|txt|rst))?$", re.IGNORECASE)

# (needle, name, reuse). Copyleft is its own state: reuse is allowed only under terms
# the owner has to accept, so the skill never treats it as a plain yes.
_LICENCES: Tuple[Tuple[str, str, str], ...] = (
    ("GNU AFFERO GENERAL PUBLIC LICENSE", "AGPL", "copyleft"),
    ("GNU LESSER GENERAL PUBLIC LICENSE", "LGPL", "copyleft"),
    ("GNU GENERAL PUBLIC LICENSE", "GPL", "copyleft"),
    ("Mozilla Public License", "MPL-2.0", "copyleft"),
    ("Apache License", "Apache-2.0", "yes"),
    ("Permission is hereby granted, free of charge", "MIT", "yes"),
    ("Redistribution and use in source and binary forms", "BSD", "yes"),
    ("Permission to use, copy, modify, and/or distribute", "ISC", "yes"),
    ("This is free and unencumbered software released into the public domain", "Unlicense", "yes"),
)


class SourceError(RuntimeError):
    """A git step against the source failed; the message carries git's own error."""


@dataclass(frozen=True)
class Item:
    sha: str
    subject: str
    pr: Optional[int]
    merge: bool
    files: Tuple[str, ...]
    present: bool


def _git(repo: Path, *args: str) -> str:
    done = run_git(["-C", str(repo), *args])
    if done.returncode != 0:
        raise SourceError(f"git {' '.join(args)}: {(done.stderr or done.stdout).strip()}")
    return done.stdout


def ref(branch: str) -> str:
    return f"{REMOTE}/{branch}"


def ensure_remote(repo: Path, url: str) -> None:
    """Point the `upstream-sync` remote at `url` and make it fetch-only."""
    remotes = _git(repo, "remote").split()
    if REMOTE in remotes:
        _git(repo, "remote", "set-url", REMOTE, url)
    else:
        _git(repo, "remote", "add", "--no-tags", REMOTE, url)
    _git(repo, "remote", "set-url", "--push", REMOTE, NO_PUSH)


def fetch(repo: Path, branch: str) -> str:
    """Fetch the source branch; returns its head sha."""
    _git(repo, "fetch", "--quiet", "--no-tags", REMOTE,
         f"+refs/heads/{branch}:refs/remotes/{REMOTE}/{branch}")
    return _git(repo, "rev-parse", ref(branch)).strip()


def is_ancestor(repo: Path, sha: str, branch: str) -> bool:
    return run_git(["-C", str(repo), "merge-base", "--is-ancestor", sha, ref(branch)]).returncode == 0


def pending(repo: Path, watermark: str, branch: str) -> List[str]:
    """The source's first-parent commits after `watermark`, oldest first -- one per
    merged PR whether the source squashes or merges."""
    out = _git(repo, "rev-list", "--reverse", "--first-parent", f"{watermark}..{ref(branch)}")
    return out.split()


def _present(repo: Path, watermark: str, branch: str, against: str) -> set:
    # `git cherry` marks with "-" a source commit whose patch `against` already has.
    out = _git(repo, "cherry", against, ref(branch), watermark)
    return {line[2:].strip() for line in out.splitlines() if line.startswith("- ")}


def pr_number(subject: str) -> Optional[int]:
    match = _PR_MERGE.match(subject) or _PR_SQUASH.search(subject)
    return int(match.group(1)) if match else None


def survey(repo: Path, watermark: str, branch: str, against: str = "HEAD") -> List[Item]:
    """The items after `watermark`, each marked `present` when the local ref `against`
    already carries the same patch."""
    present = _present(repo, watermark, branch, against)
    items: List[Item] = []
    for sha in pending(repo, watermark, branch):
        parents, _, subject = _git(repo, "show", "-s", "--format=%P%x00%s", sha).strip().partition("\x00")
        files = tuple(f for f in _git(repo, "diff", "--name-only", f"{sha}^1", sha).splitlines() if f)
        items.append(Item(sha=sha, subject=subject, pr=pr_number(subject),
                          merge=len(parents.split()) > 1, files=files, present=sha in present))
    return items


def licence_text(repo: Path, branch: str) -> str:
    """The text of the source's root licence file at its head, or "" when it has none."""
    names = _git(repo, "ls-tree", "--name-only", ref(branch)).split("\n")
    for name in names:
        if _LICENCE_NAME.match(name.strip()):
            return _git(repo, "show", f"{ref(branch)}:{name.strip()}")
    return ""


def classify_licence(text: str) -> Tuple[str, str]:
    """`(name, reuse)` with reuse `yes`, `copyleft`, `no` (no licence: all rights
    reserved) or `unknown` (a file this table does not recognise)."""
    if not text.strip():
        return "none", "no"
    for needle, name, reuse in _LICENCES:
        if needle.lower() in text.lower():
            return name, reuse
    return "unrecognised", "unknown"
