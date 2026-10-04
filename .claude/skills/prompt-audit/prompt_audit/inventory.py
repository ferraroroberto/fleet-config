"""Audiences and the scan-surface inventory of every fleet instruction file.

Part of the `prompt_audit` package (fleet-config#931); see `__init__.py`.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .common import HOME_REPO, MASTER_REPO, NEUTRAL, is_linked_worktree, sha12, unfenced_lines


# ---- audiences + inventory ------------------------------------------------------

@dataclass
class Entry:
    key: str          # fleet-root-relative posix path, e.g. fleet-config/global-CLAUDE.md
    path: Path
    repo: str
    kind: str
    audience: str = NEUTRAL
    data: Optional[bytes] = None
    repo_dir: Optional[Path] = None  # set by inventory(); R-34 resolves references against it

    @property
    def sha(self) -> str:
        return sha12(self.data) if self.data is not None else "unmeasured"

    @property
    def text(self) -> str:
        return self.data.decode("utf-8", errors="replace") if self.data is not None else ""


def kind_of(relpath: str) -> Optional[str]:
    name = relpath.rsplit("/", 1)[-1]
    if name == "SKILL.md":
        return "skill"
    if "/.claude/rules/" in f"/{relpath}" and name.endswith(".md"):
        return "rules"
    if name.endswith("CLAUDE.md"):
        return "claude-md"
    if name == "AGENTS.md":
        return "agents-md"
    return None


# ---- skill references (fleet-config#1126) ----------------------------------------

_MD_LINK = re.compile(r"\]\(([^)\s]+)")
_TICKED = re.compile(r"`([^`\s]+)`")
REF_SKIP_DIRS = {"conversations", "evals", "__pycache__"}


def _ref_target(raw: str, bases: Tuple[Path, ...], repo_dir: Path) -> Optional[Path]:
    """The existing in-repo `.md` file a link or backticked path names (under `repo_dir`), else None."""
    target = raw.split("#", 1)[0]
    if (not target.endswith(".md") or "://" in target or target.startswith(("/", "~", "mailto:"))
            or re.match(r"^[A-Za-z]:", target) or any(c in target for c in "<>*$")):
        return None
    root = repo_dir.resolve()
    for base in bases:
        cand = (base / target).resolve()
        if cand.is_file() and cand.is_relative_to(root):
            rel = cand.relative_to(root)
            if REF_SKIP_DIRS.intersection(rel.parts) or kind_of(rel.as_posix()) is not None:
                return None  # a case file, another skill's SKILL.md (delegation), or an always-on file
            return repo_dir / rel
    return None


def references(path: Path, text: str, repo_dir: Path, ticked: bool = True) -> List[Tuple[int, Path]]:
    """(line, file) for each local `.md` a file references, first mention only, fences excluded.

    A reference is a markdown link (resolved against the file's directory) or,
    with `ticked`, a backticked relative path that exists (the file's directory,
    then the repo root). Never another skill's SKILL.md, an always-on instruction
    file, or anything under conversations/, evals/ or __pycache__.
    """
    here = path.resolve()
    seen: Dict[Path, int] = {}
    for n, line in unfenced_lines(text):
        cands = [(t, (path.parent,)) for t in _MD_LINK.findall(line)]
        if ticked:
            cands += [(t, (path.parent, repo_dir)) for t in _TICKED.findall(line)]
        for raw, bases in cands:
            target = _ref_target(raw, bases, repo_dir)
            if target is not None and target.resolve() != here and target not in seen:
                seen[target] = n
    return sorted(((n, t) for t, n in seen.items()), key=lambda x: x[0])


def agents_pointer(repo_dir: Path, target: str) -> bool:
    agents = repo_dir / "AGENTS.md"
    try:
        return agents.is_file() and target in agents.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def audience_of(entry: Entry, repo_dir: Path, audiences: Dict[str, dict]) -> str:
    for name, cfg in audiences.items():
        if any(fnmatch.fnmatch(entry.key, g) for g in cfg.get("path_globs", [])):
            return name
    base = entry.key.rsplit("/", 1)[-1]
    for name, cfg in audiences.items():
        if base in cfg.get("unpointed", []) and not agents_pointer(repo_dir, base):
            return name
    return NEUTRAL


def _read(path: Path) -> Optional[bytes]:
    try:
        return path.read_bytes()
    except OSError:
        return None


def inventory(repos: Dict[str, Path], audiences: Dict[str, dict],
              only: Optional[str] = None) -> List[Entry]:
    """The scan surface in audit order, worktrees excluded, one entry per path.

    (1) the home repo's global file, (2) the scaffolding master, (3) every repo's
    CLAUDE.md / AGENTS.md / .claude/rules/*.md, (4) every SKILL.md in the home
    repo's two skill tiers and each repo's .claude/skills/, (5) the `skill-ref`
    files those SKILL.md files reference (`references()`), shared docs included.
    """
    live = {name: d for name, d in sorted(repos.items())
            if d.is_dir() and not is_linked_worktree(d)}
    wanted: List[Tuple[str, Path]] = []
    home = live.get(HOME_REPO)
    if home:
        wanted.append((HOME_REPO, home / "global-CLAUDE.md"))
    if MASTER_REPO in live:
        wanted.append((MASTER_REPO, live[MASTER_REPO] / "CLAUDE.md"))
    for name, d in live.items():
        wanted += [(name, d / "CLAUDE.md"), (name, d / "AGENTS.md")]
        wanted += [(name, p) for p in sorted((d / ".claude" / "rules").glob("*.md"))]
    if home:
        for tier in ("skills", ".claude/skills"):
            wanted += [(HOME_REPO, p) for p in sorted((home / tier).glob("*/SKILL.md"))]
    for name, d in live.items():
        if name != HOME_REPO:
            wanted += [(name, p) for p in sorted((d / ".claude" / "skills").glob("*/SKILL.md"))]

    seen, out = set(), []

    def add(name: str, path: Path, kind: Optional[str]) -> None:
        rel = path.relative_to(live[name]).as_posix()
        key = f"{name}/{rel}"
        if key in seen or kind is None:
            return
        seen.add(key)
        entry = Entry(key=key, path=path, repo=name, kind=kind, data=_read(path), repo_dir=live[name])
        entry.audience = audience_of(entry, live[name], audiences)
        out.append(entry)

    for name, path in wanted:
        if only and name != only:
            continue
        if path.is_file():
            add(name, path, kind_of(path.relative_to(live[name]).as_posix()))
    for skill in [e for e in out if e.kind == "skill"]:
        for _, ref in references(skill.path, skill.text, live[skill.repo]):
            add(skill.repo, ref, "skill-ref")
    return out


_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def sections(text: str, file_audience: str, audiences: Dict[str, dict]) -> List[dict]:
    """Heading sections whose audience differs from the file's (marker-scoped)."""
    lines = text.splitlines()
    heads = []
    for i, line in unfenced_lines(text):
        m = _HEADING.match(line)
        if m:
            heads.append((i, len(m.group(1)), m.group(2).strip()))
    out = []
    for idx, (start, level, heading) in enumerate(heads):
        aud = next((n for n, c in audiences.items()
                    if any(mk in heading for mk in c.get("markers", []))), None)
        if not aud or aud == file_audience:
            continue
        end = len(lines)
        for nxt, nlevel, _ in heads[idx + 1:]:
            if nlevel <= level:
                end = nxt - 1
                break
        out.append({"start": start, "end": end, "audience": aud, "heading": heading})
    return out


def audience_at(line_no: int, file_audience: str, secs: List[dict]) -> str:
    inner = [s for s in secs if s["start"] <= line_no <= s["end"]]
    return max(inner, key=lambda s: s["start"])["audience"] if inner else file_audience
