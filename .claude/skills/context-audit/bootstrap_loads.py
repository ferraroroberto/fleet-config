"""Measure what each skill's bootstrap auto-loads on invocation (fleet-config#1014).

`audit.py` measures the always-on surface: CLAUDE.md files and skill
descriptions. A skill that bootstraps by reading files pays a second cost on
every invocation, and the fleet had no measurement of it. Life OS skills load
the shared identity files, their own `context/` files and their
`conversations/index.md` each time. Two of those indexes had grown past the
point where one Read returns only a partial view, so the tail silently didn't
load. A truncated load that reports success is "unknown folded into pass".

A repo opts in by declaring its bootstrap in `hooks/projects.toml`:

    bootstrap_shared   = ["identity/who-i-am.md"]        # repo-relative, every skill
    bootstrap_skill    = ["{skill}/description.md"]      # relative to skills_dir
    bootstrap_sections = ["Step 1"]                      # SKILL.md headings whose
                                                         # `<x-root>/path` refs load

Each file is `ok`, `over-cap` (its estimate exceeds the read cap, so a single
Read returns a partial view), `missing` (declared but absent: bootstraps treat
most of these as optional, e.g. no conversation index yet) or `unmeasured`
(present but unreadable). Only an `ok` file counts as fully loaded.

Auto-loaded files are also scanned for credential-shaped assignments
(`password: …`, `token = …`). Only the file and a count are reported, never a
value: a credential must live in a file read at the point of need, not in one
every session of the skill loads.

Nothing here prints file contents; private repos are measured by size and
pattern counts only. stdlib only.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Dict, List, Optional

# Claude Code's Read returns a partial view past a token limit that is not a
# documented constant (code.claude.com/docs/en/tools.md: "exceeds the token
# limit"). 25k is the threshold observed when #1014 was filed; override with
# `audit.py --read-cap`.
READ_CAP_TOKENS = 25_000

# UTF-8 bytes per token for the cap question. `audit.py`'s ~4 chars/token is a
# trend estimate; here the question is "will this load truncated?", and an
# underestimate reports a truncated load as ok -- the exact failure this module
# exists to catch. Calibrated on the one measured fact: Read refused a 69k-byte
# life-os conversation index at ~29k tokens (#1014), about 2.4 bytes/token for
# dense generated markdown. Plain English prose tokenizes looser, so this errs
# toward flagging early, never toward passing a truncated file.
CAP_BYTES_PER_TOKEN = 2.4

_REF_RE = re.compile(r"<[a-z][a-z0-9-]*-root>/([^\s`'\")\]]+)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
CREDENTIAL_RE = re.compile(
    r"(?i)\b(?:password|passphrase|passwd|pwd|secret|api[_ -]?key|access[_ -]?token|token)\b"
    r"[^\S\n]*[:=][^\S\n]*[`'\"]?[^\s`'\"]{4,}"
)


def _est_tokens(text: str) -> int:
    """Conservative token estimate for the read-cap question (see `CAP_BYTES_PER_TOKEN`)."""
    return round(len(text.encode("utf-8")) / CAP_BYTES_PER_TOKEN)


def section_refs(skill_md: str, sections: List[str]) -> List[str]:
    """Repo-relative paths a SKILL.md names as `<x-root>/path` inside the given headings.

    A section runs from a heading whose text starts with one of `sections` to
    the next heading of the same or a higher level.
    """
    refs: List[str] = []
    level: Optional[int] = None
    for line in skill_md.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            depth, text = len(m.group(1)), m.group(2).strip()
            if level is not None and depth <= level:
                level = None
            if level is None and any(text.startswith(s) for s in sections):
                level = depth
            continue
        if level is None:
            continue
        for ref in _REF_RE.findall(line):
            ref = ref.rstrip(".,;:")
            if ref.endswith("/") or "." not in ref.rsplit("/", 1)[-1]:
                continue  # a directory to list, not a file that loads
            if ref not in refs:
                refs.append(ref)
    return refs


def measure_file(path: Path, read_cap: int) -> Dict[str, object]:
    """Size and state of one auto-loaded file; never returns its text."""
    if not path.exists():
        return {"state": "missing", "est_tokens": None, "credential_hits": None}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {"state": "unmeasured", "reason": type(exc).__name__,
                "est_tokens": None, "credential_hits": None}
    tokens = _est_tokens(text)
    return {"state": "over-cap" if tokens > read_cap else "ok", "est_tokens": tokens,
            "lines": text.count("\n") + (0 if text.endswith("\n") or not text else 1),
            "credential_hits": len(CREDENTIAL_RE.findall(text))}


def skill_loads(repo_dir: Path, skills_dir: str, skill: str, decl: dict) -> List[str]:
    """Every repo-relative file `skill`'s bootstrap loads, declared order, no duplicates."""
    loads: List[str] = list(decl.get("bootstrap_shared", []))
    loads += [f"{skills_dir}/{p.format(skill=skill)}" for p in decl.get("bootstrap_skill", [])]
    skill_md = repo_dir / skills_dir / skill / "SKILL.md"
    try:
        text = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        text = ""
    loads += section_refs(text, list(decl.get("bootstrap_sections", [])))
    seen: List[str] = []
    for rel in loads:
        if rel not in seen:
            seen.append(rel)
    return seen


def scan_repo(name: str, repo_dir: Path, decl: dict, read_cap: int = READ_CAP_TOKENS) -> Dict[str, object]:
    """Per-skill bootstrap loads for one declaring repo."""
    skills_dir = str(decl.get("skills_dir", ".claude/skills"))
    root = repo_dir / skills_dir
    skills = sorted(p.name for p in root.iterdir()
                    if p.is_dir() and not p.name.startswith(("_", ".")) and (p / "SKILL.md").is_file()) \
        if root.is_dir() else []
    cache: Dict[str, Dict[str, object]] = {}
    rows = []
    for skill in skills:
        files = []
        for rel in skill_loads(repo_dir, skills_dir, skill, decl):
            if rel not in cache:
                cache[rel] = measure_file(repo_dir / rel, read_cap)
            files.append({"path": rel, **cache[rel]})
        measured = [f for f in files if isinstance(f["est_tokens"], int)]
        rows.append({
            "skill": skill,
            "files": files,
            "est_tokens": sum(int(f["est_tokens"]) for f in measured),  # type: ignore[arg-type]
            "over_cap": [f["path"] for f in files if f["state"] == "over-cap"],
            "unmeasured": [f["path"] for f in files if f["state"] == "unmeasured"],
        })
    credential_files = sorted(
        ({"path": rel, "hits": m["credential_hits"]} for rel, m in cache.items() if m.get("credential_hits")),
        key=lambda r: r["path"])
    return {"repo": name, "read_cap": read_cap, "skills": rows, "credential_files": credential_files,
            "status": "ok" if root.is_dir() else f"unmeasured: no {skills_dir} in the checkout"}


def scan_fleet(projects_toml: Path, read_cap: int = READ_CAP_TOKENS) -> List[Dict[str, object]]:
    """Every repo in `projects.toml` that declares a bootstrap; others are not measured here."""
    try:
        toml = tomllib.loads(projects_toml.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return [{"repo": "?", "status": f"unmeasured: projects.toml unreadable ({type(exc).__name__})",
                 "skills": [], "credential_files": []}]
    out = []
    for name, tbl in toml.items():
        if not isinstance(tbl, dict) or "cwd_prefix" not in tbl:
            continue
        if not any(k in tbl for k in ("bootstrap_shared", "bootstrap_skill", "bootstrap_sections")):
            continue
        repo_dir = Path(tbl["cwd_prefix"])
        if not repo_dir.is_dir():
            out.append({"repo": name, "status": "unmeasured: checkout missing", "skills": [],
                        "credential_files": []})
            continue
        out.append(scan_repo(name, repo_dir, tbl, read_cap))
    return out
