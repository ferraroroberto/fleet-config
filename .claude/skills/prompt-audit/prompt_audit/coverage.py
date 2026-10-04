"""Coverage check: which sections of each tracked guide no `rules.md` `Source:` line cites (fleet-config#1128).

Part of the `prompt_audit` package (fleet-config#931); see `__init__.py`.

The freshness gate only fires when a page's bytes move, so guidance that was on a
page at baseline but never encoded would never surface again. This diffs every
tracked page's leaf sections against the `(page, section)` pairs `rules.md`
cites — rules, appendix, "Recorded as rejected" and "Covered elsewhere" alike.
Deterministic and token-free; it reports, it never edits `rules.md`.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .common import clean, fence_flags
from .state import state_path

# Pages with sections worth encoding; an index (llms.txt) or a changelog has none.
COVERAGE_ROLES = ("hub", "per-model", "instruction-files")
_HEADING = re.compile(r"^(#{2,4})\s+(.+?)\s*#*\s*$")
_ANCHOR = re.compile(r"\s*\{#[^}]*\}$")
_CHECKLIST = re.compile(r"^\s*[-*]\s+\[[ xX]\]\s+(.+)$")
_SOURCE_PART = re.compile(r"(https?://[^\s()·]+)(?:\s*\(([^)]*)\))?")
ITEM_MARKER = "prompt-audit-coverage"


def pages_dir() -> Path:
    """`~/.claude/prompt-audit/pages/` (beside `state.json`; `PROMPT_AUDIT_STATE_DIR` moves both)."""
    return state_path().parent / "pages"


def cache_page(sid: str, data: bytes) -> Path:
    """Write a fetched page's verbatim bytes to the cache; the next run reuses them while the page is fresh."""
    d = pages_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sid}.md"
    tmp = p.with_name(f"{p.name}.tmp")
    tmp.write_bytes(data)
    tmp.replace(p)
    return p


def page_text(sid: str, scratch: Optional[Path] = None) -> Optional[str]:
    """This run's fetched copy (`<scratch>/<sid>.md`), else the cached copy, else None (not checked)."""
    for p in ([scratch / f"{sid}.md"] if scratch else []) + [pages_dir() / f"{sid}.md"]:
        try:
            data = p.read_bytes()
        except OSError:
            continue
        if data:
            return data.decode("utf-8", errors="replace")
    return None


def page_sections(text: str) -> Tuple[List[str], List[str]]:
    """(leaf `##`/`###`/`####` section titles, checklist lines), code fences excluded.

    A leaf is a heading with no deeper heading before the next heading at its own
    level or above. Fences are tracked by length (`fence_flags`), so a heading-shaped
    line inside a four-backtick example is never a section.
    """
    lines = text.splitlines()
    heads: List[Tuple[int, str]] = []
    checklist: List[str] = []
    for line, fenced in zip(lines, fence_flags(lines)):
        if fenced:
            continue
        m = _HEADING.match(line)
        if m:
            heads.append((len(m.group(1)), _ANCHOR.sub("", m.group(2)).strip()))
            continue
        c = _CHECKLIST.match(line)
        if c:
            checklist.append(c.group(1).strip())
    leaves = [title for i, (level, title) in enumerate(heads)
              if not (i + 1 < len(heads) and heads[i + 1][0] > level)]
    return leaves, checklist


def page_key(url: str) -> str:
    """A page URL without scheme, fragment, `.md` suffix or trailing slash: how `Source:` lines and `sources.toml` meet."""
    u = re.sub(r"^https?://", "", url.split("#", 1)[0]).rstrip("/")
    return u[:-3] if u.endswith(".md") else u


def cited_sections(rules_text: str) -> Tuple[Dict[str, Set[str]], Dict[str, List[str]]]:
    """({page: cited sections}, {page: Source: lines citing it with no section}).

    `A → B` (a nested section) cites both A and B.
    """
    cited: Dict[str, Set[str]] = {}
    bare: Dict[str, List[str]] = {}
    for line in rules_text.splitlines():
        if not line.startswith("Source:"):
            continue
        for url, secs in _SOURCE_PART.findall(line):
            key = page_key(url)
            if not secs.strip():
                bare.setdefault(key, []).append(clean(line)[:160])
                continue
            for sec in secs.split(";"):
                cited.setdefault(key, set()).update(s.strip() for s in sec.split("→") if s.strip())
    return cited, bare


def item_id(sid: str, section: str) -> str:
    return hashlib.sha256(f"{sid}#{section}".encode("utf-8")).hexdigest()[:12]


def known_items(text: str) -> Set[str]:
    """Item ids already posted (in the update issue's body and comments)."""
    return set(re.findall(rf"<!-- {ITEM_MARKER}: id=([0-9a-f]{{12}})", text))


def coverage(sid: str, src: dict, text: Optional[str], rules_text: str, known: Iterable[str] = ()) -> dict:
    """One tracked page's coverage. `text` None -> `not-checked`, never 0 uncovered."""
    out = {"id": sid, "uncovered": [], "new": [], "unsectioned_sources": [], "sections": 0, "checklist": 0}
    if src.get("role") not in COVERAGE_ROLES:
        return dict(out, status="not-applicable")
    if not text:
        return dict(out, status="not-checked")
    leaves, checklist = page_sections(text)
    if not leaves:
        return dict(out, status="not-checked")  # a page we cannot section is not a covered page
    cited, bare = cited_sections(rules_text)
    keys = {page_key(u) for u in (src.get("url"), src.get("baseline_final_url")) if u}
    have = set().union(*(cited.get(k, set()) for k in keys))
    uncovered = [s for s in dict.fromkeys(leaves) if s not in have]
    seen = set(known)
    return dict(out, status="checked", uncovered=uncovered, sections=len(set(leaves)), checklist=len(checklist),
                new=[s for s in uncovered if item_id(sid, s) not in seen],
                unsectioned_sources=sorted({l for k in keys for l in bare.get(k, [])}))


def digest_line(result: dict) -> str:
    """`coverage: <id> uncovered N (new M)`, or the state that kept it from being counted."""
    if result.get("status") != "checked":
        return f"coverage: {result.get('id')} {result.get('status', 'not-checked')}"
    extra = f", {len(result['unsectioned_sources'])} section-less Source line(s)" if result["unsectioned_sources"] else ""
    return f"coverage: {result['id']} uncovered {len(result['uncovered'])} (new {len(result['new'])}){extra}"


def coverage_comment(results: List[dict], drafts: str = "") -> Optional[str]:
    """The update-issue comment for this run's new uncovered sections, or None when nothing is new."""
    new = [(r["id"], s) for r in results if r.get("status") == "checked" for s in r["new"]]
    if not new:
        return None
    out = ["**Coverage check: tracked-guide sections no `rules.md` `Source:` line cites.** "
           "Each needs a rule, an appendix entry, a rejected entry or a Covered-elsewhere entry. "
           "This doesn't stop the scan; the rule-set is incomplete, not stale.", ""]
    out += [f"- [ ] `{sid}`: {sec} <!-- {ITEM_MARKER}: id={item_id(sid, sec)} -->" for sid, sec in new]
    if drafts.strip():
        out += ["", "<details><summary>Drafted entries (review before a rule-set PR)</summary>", "", drafts.strip(),
                "", "</details>"]
    return "\n".join(out) + "\n"


def section_text(text: str, title: str) -> str:
    """One section's lines, from its heading to the next heading at its level or above (fences kept)."""
    lines = text.splitlines()
    flags = fence_flags(lines)
    start, level = None, 0
    for i, (line, fenced) in enumerate(zip(lines, flags)):
        m = None if fenced else _HEADING.match(line)
        if not m:
            continue
        if start is not None and len(m.group(1)) <= level:
            return "\n".join(lines[start:i]).strip() + "\n"
        if start is None and _ANCHOR.sub("", m.group(2)).strip() == title:
            start, level = i, len(m.group(1))
    return "\n".join(lines[start:]).strip() + "\n" if start is not None else ""
