"""The lint engine (R-01..R-17, R-30, R-34..R-37): exact hit counts per instruction file.

Part of the `prompt_audit` package (fleet-config#931); see `__init__.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from .common import (NEUTRAL, SIZE_CAPS, clean, frontmatter_description, frontmatter_error, frontmatter_field,
                     prose_words, strip_quoted, unfenced_lines)
from .inventory import Entry, _HEADING, _TICKED, _read, audience_at, references, sections
from .rules import rule_applies_to_kind, verdict_cap


# ---- lint -----------------------------------------------------------------------

_INLINE_CODE = re.compile(r"`[^`\n]*`")
_MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"

PATTERNS: Dict[str, List[re.Pattern]] = {
    "R-01": [re.compile(r"\b(?:MUST|NEVER|ALWAYS|CRITICAL|IMPORTANT|REQUIRED|MANDATORY|NOT|DON'T)\b")],
    "R-02": [re.compile(r"\b(?:if|when) in doubt\b|\bdefault to (?:using|calling|running|invoking)\b", re.I)],
    "R-03": [re.compile(r"\bdouble[- ]check|\bre-?verify\b|\bverify your (?:answer|work|output|response|changes)\b"
                        r"|\bfinal verification step\b|\bsubagent to verify\b|\bre-?check your\b", re.I)],
    "R-04": [re.compile(r"\bevery\s+(?:\d+|few|couple of)\s+tool[- ]calls?\b"
                        r"|\bafter every\s+\d+\s+(?:tool[- ]calls?|steps)\b", re.I)],
    "R-05": [re.compile(r"\bhold (?:all |every |any )?(?:findings|results|updates|output)\s+"
                        r"(?:for|until)\s+(?:the\s+)?(?:final|end)\b", re.I)],
    "R-07": [re.compile(r"\bthink step[- ]by[- ]step\b|\bstep[- ]by[- ]step (?:reasoning|thinking)\b"
                        r"|\breason step[- ]by[- ]step\b", re.I),
             re.compile(r"^\s*\d+[.)]\s+(?:think|reason|reflect)\b", re.I)],
    "R-08": [re.compile(r"\b(?:do not|don't|never)\s+(?:think|reason)\b(?!\s+(?:about|of|that)\b)", re.I)],
    "R-09": [re.compile(r"\bonly report (?:high|critical)[- ]severity\b|\bbe conservative\b"
                        r"|\b(?:do not|don't) nitpick\b", re.I)],
    "R-10": [re.compile(r"\bprefill(?:ed|s|ing)?\b|\bbudget_tokens\b", re.I)],
    "R-11": [re.compile(rf"\b(?:before|after|until|since|as of|by)\s+(?:{_MONTHS})\s+\d{{4}}\b", re.I)],
    "R-12": [re.compile(r"\b(?:outline|present|share|state|write)\s+(?:a|an|your)\s+(?:upfront\s+)?plan\s+before\b"
                        r"|\bupfront plan\b|\bbegin (?:each|every) (?:response|turn) with a plan\b", re.I)],
    "R-30": [re.compile(r"\b(?:explain|show|write out) (?:your|its) reasoning in (?:the|your) (?:response|answer|reply|output)\b"
                        r"|\bwrite out (?:your|its) reasoning\b|\bshow your work\b"
                        r"|\bthink out loud in (?:the|your) (?:response|answer|reply|output)\b", re.I)],
}
# Line-level detectors: one hit per matching line, not per occurrence.
LINE_PATTERNS: Dict[str, re.Pattern] = {
    "R-06": re.compile(r"\b(?:do not|don't|never|avoid|no)\s+(?:use\s+|using\s+)?"
                       r"(?:markdown|bullet(?:s| points)?|headers|headings|bold|lists|formatting)\b", re.I),
    "R-13": re.compile(r"\b(?:never|do not|don't|avoid|must not|mustn't)\b", re.I),
}
_MODAL = re.compile(r"\b(always|must not|must|never|do not|don't)\s+([a-z][a-z-]*(?:\s+[a-z][a-z-]*){0,2})", re.I)
# "I" only as a capitalised standalone word (never the I in "I/O"); the rest any case.
_PERSON = re.compile(r"\bI\b(?!/)|\b(?i:me|my|we|our|you|your)\b")
# R-15 frontmatter (#1126): the name field's charset and length; no XML tags in the description.
_SKILL_NAME = re.compile(r"[a-z0-9-]{1,64}")
_XML_TAG = re.compile(r"</?[A-Za-z][\w.-]*(?:\s[^<>]*)?>")
# R-35: a contents heading near the top of a long reference file.
_CONTENTS = re.compile(r"^#{1,6}\s+(?:table of\s+)?contents\b", re.I)
REF_TOC_LINES, REF_TOC_WINDOW = 100, 40
# R-36: a drive path or a Windows venv path (host-local, caps at consider) and any other relative
# backslash path (caps at violation). A segment after a backslash never opens with `_`, so a markdown
# escape (`snake\_case`) is not a path.
_DRIVE_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:\\[^\s`'\"<>|*?()]*")
_REL_PATH = re.compile(r"(?<![\w.:\\/%-])(?:[\w.-]+\\)+[A-Za-z0-9.][\w.-]*")
_VENV_PATH = re.compile(r"(?:^|\\)\.venv\\", re.I)
# R-37: qualified tool names; the tool is what follows the last `__`.
_QUALIFIED = re.compile(r"mcp__[A-Za-z0-9_-]+")


def audience_terms(audience_cfg: dict) -> re.Pattern:
    """One audience's product terms as a case-sensitive whole-word pattern (never matches when empty)."""
    terms = audience_cfg.get("terms", [])
    return re.compile("|".join(rf"\b{re.escape(t)}\b" for t in terms) if terms else r"(?!)")


LINT_RULES = sorted(set(PATTERNS) | set(LINE_PATTERNS)
                    | {"R-14", "R-15", "R-16", "R-17", "R-34", "R-35", "R-36", "R-37"})


def mcp_vocabulary(texts: Iterable[str]) -> Dict[str, Set[str]]:
    """Bare tool name -> its qualified spellings, from every qualified name in the given skills (R-37).

    A prefix form ending in `_` (a wildcard over a server's tools) names no tool.
    """
    vocab: Dict[str, Set[str]] = {}
    for text in texts:
        for q in _QUALIFIED.findall(text):
            tool = q.rsplit("__", 1)[1] if q.count("__") >= 2 else ""
            if tool and not q.endswith("_"):
                vocab.setdefault(tool, set()).add(q)
    return vocab


@dataclass
class Hit:
    rule: str
    line: int
    count: int
    text: str
    cap: str = "violation"


@dataclass
class LintResult:
    entry: Entry
    lines: int
    hits: List[Hit] = field(default_factory=list)
    neg: Tuple[int, int] = (0, 0)
    desc_words: str = "n/a"
    desc_chars: str = "n/a"
    size: str = ""

    def counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for h in self.hits:
            out[h.rule] = out.get(h.rule, 0) + h.count
        return dict(sorted(out.items()))


def body_start(text: str) -> int:
    """1-based line number where a frontmatter-bearing file's body begins."""
    if not text.startswith("---"):
        return 1
    lines = text.splitlines()
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            return i + 2
    return 1


def instruction_lines(text: str) -> List[Tuple[int, str]]:
    """(line number, text) for lintable prose: no frontmatter, fences or inline code."""
    start = body_start(text)
    return [(i, _INLINE_CODE.sub(" ", raw)) for i, raw in unfenced_lines(text) if i >= start]


def paragraphs(lines: List[Tuple[int, str]]) -> List[List[Tuple[int, str]]]:
    paras, cur = [], []
    for n, line in lines:
        if line.strip():
            cur.append((n, line))
        elif cur:
            paras.append(cur)
            cur = []
    if cur:
        paras.append(cur)
    return paras


def _excerpt(line: str) -> str:
    return clean(line)[:120]


def lint_entry(entry: Entry, rules: Dict[str, dict], audiences: Dict[str, dict],
               mcp_vocab: Optional[Dict[str, Set[str]]] = None) -> LintResult:
    """Lint one file. `mcp_vocab` (from `mcp_vocabulary()` over the fleet's skills) feeds R-37."""
    text = entry.text
    raw_lines = text.splitlines()
    res = LintResult(entry=entry, lines=len(raw_lines))
    secs = sections(text, entry.audience, audiences)
    lines = instruction_lines(text)

    def add(rule_id: str, line_no: int, count: int, excerpt: str, cap_as: Optional[str] = None) -> None:
        rule = rules.get(rule_id)
        if rule is None or not rule_applies_to_kind(rule, entry.kind):
            return
        cap = verdict_cap(rule["vendor"], audience_at(line_no, entry.audience, secs), audiences)
        if cap is not None:
            # A rule whose Detect line states a per-hit cap (R-36) sets it wherever the rule applies.
            res.hits.append(Hit(rule_id, line_no, count, _excerpt(excerpt), cap_as or cap))

    instr = negative = 0
    for n, line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Occurrence patterns read headings too ("## CRITICAL"); line patterns and the
        # negative ratio count instruction lines only, so hits and ratio agree.
        for rule_id, pats in PATTERNS.items():
            count = sum(len(p.findall(line)) for p in pats)
            if count:
                add(rule_id, n, count, raw_lines[n - 1])
        if _HEADING.match(stripped):
            continue
        instr += 1
        negative += bool(LINE_PATTERNS["R-13"].search(line))
        for rule_id, pat in LINE_PATTERNS.items():
            if pat.search(line):
                add(rule_id, n, 1, raw_lines[n - 1])
    # The ratio is a measurement of the file, reported whether or not R-13 applies to its reader.
    res.neg = (negative, instr)

    # R-14 size cap (a skill-ref file has none)
    if entry.kind in SIZE_CAPS:
        unit, limit = SIZE_CAPS[entry.kind]
        if unit == "lines":
            size = len(raw_lines)
        elif unit == "body-lines":
            size = len(raw_lines) - body_start(text) + 1
        else:
            size = len(entry.data or b"")
        res.size = f"{size}{'b' if unit == 'bytes' else 'l'}/{limit}"
        if size > limit:
            add("R-14", 1, 1, f"{unit} {size} over cap {limit}")
    else:
        res.size = "n/a"

    # R-15 skill description
    if entry.kind == "skill":
        err = frontmatter_error(text)
        desc = "" if err else frontmatter_description(text)
        if desc:
            prose = strip_quoted(desc)
            res.desc_words = str(prose_words(desc))
            res.desc_chars = str(len(desc))
            if len(desc) > 1024:
                add("R-15", 1, 1, f"description {len(desc)} chars over 1024")
            person = len(_PERSON.findall(prose))
            if person:
                add("R-15", 1, person, f"description: {prose}")
            tags = _XML_TAG.findall(desc)
            if tags:
                add("R-15", 1, len(tags), f"description XML tag: {' '.join(tags)}")
        else:
            res.desc_words = res.desc_chars = "unmeasured"
        name = None if err else (frontmatter_field(text, "name") or "").strip().strip("\"'")
        if name is not None and not _SKILL_NAME.fullmatch(name):
            add("R-15", 1, 1, f"name {name!r} is not 1-64 chars of [a-z0-9-]")
        lint_skill_structure(entry, text, mcp_vocab or {}, add)

    # R-35 long reference file without a contents list
    if entry.kind == "skill-ref" and len(raw_lines) > REF_TOC_LINES:
        top = [l for n, l in unfenced_lines(text) if n <= REF_TOC_WINDOW]
        if not any(_CONTENTS.match(l) for l in top):
            add("R-35", 1, 1, f"{len(raw_lines)} lines, no contents heading in the first {REF_TOC_WINDOW}")

    # R-16 mechanically visible contradictions
    polarity: Dict[str, Dict[str, int]] = {}
    for n, line in lines:
        for modal, phrase in _MODAL.findall(line):
            sign = "+" if modal.lower() in ("always", "must") else "-"
            polarity.setdefault(phrase.lower(), {}).setdefault(sign, n)
    for phrase, signs in sorted(polarity.items()):
        if len(signs) == 2:
            first = min(signs.values())
            add("R-16", first, 1, raw_lines[first - 1])

    # R-17 vendor paragraph without a marker (only where the reader is neutral)
    all_markers = [mk for c in audiences.values() for mk in c.get("markers", [])]
    for para in paragraphs(lines):
        first = para[0][0]
        if audience_at(first, entry.audience, secs) != NEUTRAL:
            continue
        joined = " ".join(l for _, l in para)
        if any(mk in joined for mk in all_markers):
            continue
        named = [a for a, c in audiences.items() if audience_terms(c).search(joined)]
        if len(named) == 1:
            terms = audience_terms(audiences[named[0]])
            anchor = next((n for n, l in para if terms.search(l)), first)
            add("R-17", anchor, 1, raw_lines[anchor - 1])
    res.hits.sort(key=lambda h: (h.line, h.rule))
    return res


def lint_skill_structure(entry: Entry, text: str, mcp_vocab: Dict[str, Set[str]],
                         add: Callable[..., None]) -> None:
    """R-34, R-36 and R-37 for one SKILL.md; `add` is `lint_entry`'s hit recorder."""
    body = body_start(text)

    # R-34 nested references: a reference that links the reader on to a file SKILL.md never names.
    # Onward references count markdown links only: a backticked path in a reference doc is a mention.
    if entry.repo_dir is not None:
        direct = references(entry.path, text, entry.repo_dir)
        named = {p.resolve() for _, p in direct}
        root = entry.repo_dir

        def rel(p: Path) -> str:
            return p.relative_to(root).as_posix()

        for n, ref in direct:
            data = _read(ref)
            if data is None:
                continue
            leaves = [leaf for _, leaf in references(ref, data.decode("utf-8", "replace"), entry.repo_dir,
                                                      ticked=False)
                      if leaf.resolve() not in named]
            if leaves:
                add("R-34", n, len(leaves),
                    f"{rel(ref)} references {', '.join(rel(l) for l in leaves)} - not referenced from SKILL.md")

    for n, line in unfenced_lines(text):
        if n < body:
            continue
        # R-36 backslash paths (inline code included: it is what gets copied)
        rel_paths = _REL_PATH.findall(_DRIVE_PATH.sub(" ", line))
        host = _DRIVE_PATH.findall(line) + [p for p in rel_paths if _VENV_PATH.search(p)]
        skill = [p for p in rel_paths if not _VENV_PATH.search(p)]
        if host:
            add("R-36", n, len(host), line, cap_as="consider")
        if skill:
            add("R-36", n, len(skill), line, cap_as="violation")
        # R-37 a bare tool name the fleet elsewhere qualifies, on a line without the qualified form
        bare = {t for t in _TICKED.findall(line) if t in mcp_vocab and not any(q in line for q in mcp_vocab[t])}
        if bare:
            add("R-37", n, len(bare), line)


def hits_line(res: LintResult) -> str:
    counts = res.counts()
    hits = ",".join(f"{k}:{v}" for k, v in counts.items()) or "none"
    return (f"HITS={res.entry.key}|audience={res.entry.audience}|kind={res.entry.kind}"
            f"|lines={res.lines}|size={res.size}|hits={hits}|neg={res.neg[0]}/{res.neg[1]}"
            f"|desc_words={res.desc_words}|desc_chars={res.desc_chars}")


def hit_detail(res: LintResult) -> List[str]:
    return [f"HIT={res.entry.key}:{h.line}|rule={h.rule}|cap={h.cap}|count={h.count}|text={h.text}"
            for h in res.hits]
