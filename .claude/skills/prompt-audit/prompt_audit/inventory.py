"""Audiences and the scan-surface inventory of every fleet instruction file.

Part of the `prompt_audit` package (fleet-config#931); see `__init__.py`.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .common import HOME_REPO, MASTER_REPO, NEUTRAL, _FENCE, is_linked_worktree, sha12


# ---- audiences + inventory ------------------------------------------------------

@dataclass
class Entry:
    key: str          # fleet-root-relative posix path, e.g. fleet-config/global-CLAUDE.md
    path: Path
    repo: str
    kind: str
    audience: str = NEUTRAL
    data: Optional[bytes] = None

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
    repo's two skill tiers and each repo's .claude/skills/.
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
    for name, path in wanted:
        if only and name != only:
            continue
        if not path.is_file():
            continue
        rel = path.relative_to(live[name]).as_posix()
        key = f"{name}/{rel}"
        kind = kind_of(rel)
        if key in seen or kind is None:
            continue
        seen.add(key)
        entry = Entry(key=key, path=path, repo=name, kind=kind, data=_read(path))
        entry.audience = audience_of(entry, live[name], audiences)
        out.append(entry)
    return out


_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def sections(text: str, file_audience: str, audiences: Dict[str, dict]) -> List[dict]:
    """Heading sections whose audience differs from the file's (marker-scoped)."""
    lines = text.splitlines()
    heads = []
    in_fence = False
    for i, line in enumerate(lines, start=1):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        m = None if in_fence else _HEADING.match(line)
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
