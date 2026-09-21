"""The R-01..R-17 lint engine: exact hit counts per instruction file.

Part of the `prompt_audit` package (fleet-config#931); see `__init__.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from .common import NEUTRAL, SIZE_CAPS, _FENCE, clean, frontmatter_description, frontmatter_error, prose_words, strip_quoted
from .inventory import Entry, _HEADING, audience_at, sections
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


def audience_terms(audience_cfg: dict) -> re.Pattern:
    """One audience's product terms as a case-sensitive whole-word pattern (never matches when empty)."""
    terms = audience_cfg.get("terms", [])
    return re.compile("|".join(rf"\b{re.escape(t)}\b" for t in terms) if terms else r"(?!)")


LINT_RULES = sorted(set(PATTERNS) | set(LINE_PATTERNS) | {"R-14", "R-15", "R-16", "R-17"})


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
    out = []
    in_fence = False
    start = body_start(text)
    for i, raw in enumerate(text.splitlines(), start=1):
        if i < start:
            continue
        if _FENCE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        out.append((i, _INLINE_CODE.sub(" ", raw)))
    return out


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


def lint_entry(entry: Entry, rules: Dict[str, dict], audiences: Dict[str, dict]) -> LintResult:
    text = entry.text
    raw_lines = text.splitlines()
    res = LintResult(entry=entry, lines=len(raw_lines))
    secs = sections(text, entry.audience, audiences)
    lines = instruction_lines(text)

    def add(rule_id: str, line_no: int, count: int, excerpt: str) -> None:
        rule = rules.get(rule_id)
        if rule is None or not rule_applies_to_kind(rule, entry.kind):
            return
        cap = verdict_cap(rule["vendor"], audience_at(line_no, entry.audience, secs), audiences)
        if cap is not None:
            res.hits.append(Hit(rule_id, line_no, count, _excerpt(excerpt), cap))

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

    # R-14 size cap
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
        else:
            res.desc_words = res.desc_chars = "unmeasured"

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


def hits_line(res: LintResult) -> str:
    counts = res.counts()
    hits = ",".join(f"{k}:{v}" for k, v in counts.items()) or "none"
    return (f"HITS={res.entry.key}|audience={res.entry.audience}|kind={res.entry.kind}"
            f"|lines={res.lines}|size={res.size}|hits={hits}|neg={res.neg[0]}/{res.neg[1]}"
            f"|desc_words={res.desc_words}|desc_chars={res.desc_chars}")


def hit_detail(res: LintResult) -> List[str]:
    return [f"HIT={res.entry.key}:{h.line}|rule={h.rule}|cap={h.cap}|count={h.count}|text={h.text}"
            for h in res.hits]
