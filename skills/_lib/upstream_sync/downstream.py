"""The `/downstream-sync` half: the local repo's own work the source lacks, the spec lint
that keeps a hand-back free of code and private detail, and the match that tells a forward
sync when the source has answered an offered feature.

A local commit that moved the `[upstream]` table came in through a sync lane (a port or
its bookkeeping), because only those lanes write that table. That is how ported work is
told apart from local features without any provenance line in public text.

stdlib only; every git call goes through `git_run.run_git`.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from git_run import run_git  # noqa: E402

from upstream_sync import ledger, source  # noqa: E402

# Every heading a cold implementer needs; `Behavior` is accepted for `Behaviour`.
REQUIRED_SECTIONS: Tuple[Tuple[str, str], ...] = (
    ("Problem", r"problem"),
    ("Behaviour", r"behaviou?r"),
    ("Interface", r"interface"),
    ("Acceptance criteria", r"acceptance criteria"),
    ("Tests", r"tests?"),
)

_CODE = re.compile(r"^\s*(```|~~~)|<(pre|code)\b", re.IGNORECASE | re.MULTILINE)
# (reason, pattern): detail that names this machine, its network, a person or a secret.
_PRIVATE: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("a Windows path", re.compile(r"(?<![\w])[A-Za-z]:[\\/]")),
    ("a network share path", re.compile(r"\\\\[\w.-]+\\")),
    ("a home directory path", re.compile(r"/(home|Users)/[\w.-]+", re.IGNORECASE)),
    ("a private host", re.compile(r"\.ts\.net\b|\blocalhost\b|\.(local|internal|lan)\b(?!\.)", re.IGNORECASE)),
    ("a private or loopback address", re.compile(
        r"\b(127\.\d+|10\.\d+|192\.168|172\.(1[6-9]|2\d|3[01]))\.\d+\.\d+\b")),
    ("an email address", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("a secret-shaped token", re.compile(
        r"\b(ghp_|gho_|github_pat_|sk-|xox[abp]-|AKIA)[A-Za-z0-9_-]{10,}")),
)
_GITHUB = re.compile(r"github\.com[:/]+([\w.-]+)/([\w.-]+?)(\.git)?/?$", re.IGNORECASE)


@dataclass(frozen=True)
class LocalItem:
    sha: str
    subject: str
    pr: Optional[int]
    files: Tuple[str, ...]
    synced: bool
    present: bool
    offered: bool

    @property
    def candidate(self) -> bool:
        return not (self.synced or self.present or self.offered)


def _git(repo: Path, *args: str) -> str:
    done = run_git(["-C", str(repo), *args])
    if done.returncode != 0:
        raise source.SourceError(f"git {' '.join(args)}: {(done.stderr or done.stdout).strip()}")
    return done.stdout


def _table_at(repo: Path, rev: str) -> Tuple[Optional[ledger.UpstreamState], str]:
    done = run_git(["-C", str(repo), "show", f"{rev}:{ledger.FILENAME}"])
    return ledger.parse_text(done.stdout if done.returncode == 0 else None)


def _moved_table(repo: Path, sha: str, parents: Sequence[str]) -> bool:
    before = _table_at(repo, parents[0]) if parents else (None, "absent")
    return _table_at(repo, sha) != before


def local_shas(repo: Path, branch: str, against: str, since: Optional[str] = None) -> List[str]:
    """The local first-parent commits on `against` the source branch does not reach,
    oldest first, after `since` when given."""
    args = ["rev-list", "--reverse", "--first-parent", against, f"^{source.ref(branch)}"]
    if since:
        args.append(f"^{since}")
    return _git(repo, *args).split()


def _present(repo: Path, branch: str, against: str) -> Set[str]:
    # `git cherry` marks with "-" a local commit whose patch the source already has.
    out = _git(repo, "cherry", source.ref(branch), against)
    return {line[2:].strip() for line in out.splitlines() if line.startswith("- ")}


def compare(repo: Path, state: ledger.UpstreamState, against: str = "HEAD",
            since: Optional[str] = None) -> List[LocalItem]:
    """Every local-only item, each flagged synced / present / offered; the rest are the
    candidates to hand back."""
    present = _present(repo, state.branch, against)
    offered = state.offered_shas()
    items: List[LocalItem] = []
    for sha in local_shas(repo, state.branch, against, since):
        parents, _, subject = _git(repo, "show", "-s", "--format=%P%x00%s", sha).strip().partition("\x00")
        parent_list = parents.split()
        base = parent_list[0] if parent_list else None
        diff = ["diff", "--name-only", base, sha] if base else ["show", "--name-only", "--format=", sha]
        files = tuple(f for f in _git(repo, *diff).splitlines() if f)
        items.append(LocalItem(sha=sha, subject=subject, pr=source.pr_number(subject), files=files,
                               synced=_moved_table(repo, sha, parent_list), present=sha in present,
                               offered=sha in offered))
    return items


def supersedes(repo: Path, item_shas: Iterable[str], offered: Sequence[ledger.Offer]) -> Dict[str, str]:
    """`{source sha: local sha}` for each source item whose message references the
    source issue an offered feature was filed as. A hint for the survey: a source
    version that names no issue is matched by behaviour, by whoever reads the survey."""
    by_issue = {o.source_issue: o.local_sha for o in offered if o.source_issue is not None}
    found: Dict[str, str] = {}
    if not by_issue:
        return found
    for sha in item_shas:
        message = _git(repo, "show", "-s", "--format=%B", sha)
        for issue, local in by_issue.items():
            if re.search(rf"(?<![\w/&])#{issue}\b", message):
                found[sha] = local
                break
    return found


def _own_names(repo: Path, source_url: str) -> Set[str]:
    """The local repo's own name and owner, which a hand-back must never carry, less any
    word the source's own url already uses (a copy often keeps the source's name)."""
    names: Set[str] = set()
    common = run_git(["-C", str(repo), "rev-parse", "--path-format=absolute", "--git-common-dir"])
    if common.returncode == 0 and common.stdout.strip():
        names.add(Path(common.stdout.strip()).parent.name)
    origin = run_git(["-C", str(repo), "remote", "get-url", "origin"])
    match = _GITHUB.search(origin.stdout.strip()) if origin.returncode == 0 else None
    if match:
        names.update({match.group(1), match.group(2)})
    shared = {w.lower() for w in re.split(r"[^\w-]+", source_url) if w}
    return {n for n in names if n and n.lower() not in shared}


def lint_spec(text: str, allow_code: bool, forbidden: Iterable[str]) -> List[str]:
    """The reasons a hand-back spec may not leave this machine; empty means it may."""
    problems: List[str] = []
    for label, pattern in REQUIRED_SECTIONS:
        if not re.search(rf"^#{{1,6}}\s+{pattern}\s*$", text, re.IGNORECASE | re.MULTILINE):
            problems.append(f"missing section: {label}")
    if not allow_code and _CODE.search(text):
        problems.append("contains a code block (the source may not see this repo's code)")
    for reason, pattern in _PRIVATE:
        if pattern.search(text):
            problems.append(f"contains {reason}")
    for word in sorted({w for w in forbidden if w and w.strip()}):
        if re.search(rf"(?<![\w-]){re.escape(word.strip())}(?![\w-])", text, re.IGNORECASE):
            problems.append(f"names {word.strip()!r}, which identifies this repo or its owner")
    return problems


def lint_spec_file(repo: Path, path: Path, state: Optional[ledger.UpstreamState], allow_code: bool,
                   extra_forbidden: Iterable[str]) -> List[str]:
    forbidden = set(extra_forbidden) | _own_names(repo, state.url if state else "")
    return lint_spec(path.read_text(encoding="utf-8"), allow_code, forbidden)
