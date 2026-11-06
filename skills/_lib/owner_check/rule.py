"""The deterministic owner-check rule: does this work need Roberto, and why.

Pure: no `gh`, no git, no clock. The inputs are the issue's type labels, its title,
its body (only for the explicit `Owner validation:` line), the PR's changed files
and what the lane says about in-session verification. The rule only ever adds
reasons; an explicit `none` in the issue cannot suppress one, so a drafting slip
never hides real owner work.

Self-verification comes first. Whatever the lane proved in its own session needs
nobody. What it could not prove needs Roberto only through a named criterion and
one owner-only class (`OWNER_CLASSES`); a generic "could not verify" is no reason.
A live-only title word asks him only while no in-session proof exists, and a
live-input path in the diff or a new page or view no UX proof covered always does.
An account clause fires only for a third party's account (sign-up, OAuth,
subscription): the app's own password sign-in is drivable with test credentials. Words are matched in the title only: issue bodies quote viewports
("iPhone 390px"), network loopback and "report" so often that body matching
flagged more issues than were enhancements at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

REQUIRED = "required"
NONE = "none"
UNKNOWN = "unknown"

# What the lane says about re-running the original repro / acceptance in-session.
VERIFIED_YES = "yes"
VERIFIED_NO = "no"
# Everything verified in-session except a criterion only a later scheduled run can
# show; reading that run is the chief's job, so it adds no reason.
VERIFIED_SCHEDULED = "scheduled"
# Before the work exists (`/issue-add`) and for history (`backfill`) verification
# is not a fact anyone can state, so the clause does not apply.
VERIFIED_NA = "n/a"
VERIFICATION_STATES = (VERIFIED_YES, VERIFIED_SCHEDULED, VERIFIED_NO, VERIFIED_NA)
_PROVEN = (VERIFIED_YES, VERIFIED_SCHEDULED)

# The only reasons a session cannot prove a criterion itself. Anything else is
# provable with the gate, the repro, a screenshot, rendered geometry or a probe.
OWNER_CLASSES = {
    "device": "a real device or live input (mic, camera, phone hardware)",
    "owner-account": "an account or sign-in only the owner holds",
    "physical": "a physical action",
    "third-party": "a third-party system the session cannot reach",
    "taste": "a taste or judgement call",
}
# Recorded lane reasons open with this, so a re-check keeps them as recorded.
GAP_PREFIX = "the lane could not prove"

_EXPLICIT = re.compile(r"^\W*owner validation\W*\s*(required|none)\b", re.IGNORECASE | re.MULTILINE)

# Input a session cannot drive: a real device, a live microphone or camera.
_LIVE_ONLY_TITLE = re.compile(
    r"\b(microphones?|mic|webcams?|cameras?|wasapi|dictation|voice notes?|iphone|ipad|android|"
    r"on-device|real device|haptics?|on-screen keyboard|home-screen|bluetooth)\b",
    re.IGNORECASE,
)
_LIVE_ONLY_PATH_TOKENS = frozenset({"wasapi", "mic", "microphone", "camera", "webcam"})
# A third party's account only the owner holds (enhancements only). The app's own
# sign-in is not here: a session drives it with test credentials.
_THIRD_PARTY = r"google|microsoft|apple|github|telegram|twitter|linkedin|notion|slack|stripe|cloudflare"
_ACCOUNT_TITLE = re.compile(
    rf"\b(sign[- ]?ups?|oauth|subscriptions?|(?:sign|log)[- ]?in with|"
    rf"(?:{_THIRD_PARTY}) (?:account|sign[- ]?in|log[- ]?in))\b",
    re.IGNORECASE,
)
# A new page or view entry is a surface the owner has to try (enhancements only);
# a vendored component, a helper script or a test fixture is not.
_PAGE_PATH = re.compile(r"\.html?$", re.IGNORECASE)
_VIEW_PATH = re.compile(r"(^|/)views?/[^/]+\.m?js$|[-_.]view\.m?js$", re.IGNORECASE)
_NOT_A_SURFACE = re.compile(r"(^|/)(_vendored|tests?|docs?|fixtures?)/", re.IGNORECASE)
_INLINE_CODE = re.compile(r"`[^`\n]*`")


@dataclass(frozen=True)
class Decision:
    verdict: str
    reasons: Tuple[str, ...]


def parse_gap(text: str) -> Tuple[str, str]:
    """`<class>:<criterion>` -> (class, criterion); ValueError names what is wrong."""
    cls, sep, criterion = (text or "").partition(":")
    cls, criterion = cls.strip().lower(), " ".join(criterion.split())
    if not sep or cls not in OWNER_CLASSES or not criterion:
        raise ValueError(f"expected <class>:<criterion> with class one of {', '.join(OWNER_CLASSES)}, got {text!r}")
    return cls, criterion


def gap_reason(cls: str, criterion: str) -> str:
    return f"{GAP_PREFIX} {criterion}, because it needs {OWNER_CLASSES[cls]}"


def explicit_line(body: str) -> Optional[str]:
    """The issue's own `Owner validation: required|none` line, or None."""
    m = _EXPLICIT.search(body or "")
    return m.group(1).lower() if m else None


def ux_proven(pr_body: str) -> bool:
    """True when the PR description carries `/issue-finish`'s fresh visual-proof line."""
    return "VISUAL_PROOF=fresh" in (pr_body or "")


def _first(pattern: re.Pattern, text: str) -> Optional[str]:
    m = pattern.search(text)
    return m.group(1).lower() if m else None


def _live_only_path(files: Sequence[Tuple[str, str]]) -> Optional[str]:
    for _status, path in files:
        if set(re.split(r"[^a-z0-9]+", path.lower())) & _LIVE_ONLY_PATH_TOKENS:
            return path
    return None


def _added_surface_path(files: Sequence[Tuple[str, str]]) -> Optional[str]:
    for status, path in files:
        norm = path.replace("\\", "/")
        if status == "added" and not _NOT_A_SURFACE.search(norm) and (
            _PAGE_PATH.search(norm) or _VIEW_PATH.search(norm)
        ):
            return path
    return None


def decide(
    labels: Iterable[str],
    title: str,
    body: str,
    files: Optional[Sequence[Tuple[str, str]]] = None,
    verification: str = VERIFIED_NA,
    ux_proven: bool = False,
    gaps: Sequence[Tuple[str, str]] = (),
) -> Decision:
    """Required or none, with every reason that fired.

    `files` are `(status, path)` pairs with GitHub's PR-file statuses
    (`added`, `modified`, `removed`, `renamed`); None means no diff is part of
    this decision (an issue draft, a back-fill). `ux_proven` is true when the PR
    carries a fresh visual proof, which already showed the new page or view working.
    `gaps` are `(class, criterion)` pairs from `parse_gap`; `VERIFIED_NO` needs at
    least one, since "could not verify" alone names nothing the owner can do.
    """
    if verification not in VERIFICATION_STATES:
        raise ValueError(f"verification must be one of {VERIFICATION_STATES}, got {verification!r}")
    if (verification == VERIFIED_NO) != bool(gaps):
        raise ValueError("verification 'no' needs at least one owner-only gap, and only 'no' takes gaps")
    enhancement = "enhancement" in {str(label).lower() for label in labels}
    files = list(files or [])
    title = _INLINE_CODE.sub(" ", title or "")
    reasons: List[str] = []

    if explicit_line(body) == REQUIRED:
        reasons.append("the issue says Owner validation: required")
    word = None if verification in _PROVEN else _first(_LIVE_ONLY_TITLE, title)
    if word:
        reasons.append(f"needs a real device or live input ({word}), which a session cannot drive")
    path = _live_only_path(files)
    if path:
        reasons.append(f"changes a live-input path ({path})")
    if enhancement:
        word = _first(_ACCOUNT_TITLE, title)
        if word:
            reasons.append(f"involves a third-party account only the owner holds ({word})")
        added = None if ux_proven else _added_surface_path(files)
        if added:
            reasons.append(f"adds a new page or view no UX proof covered ({added})")
    reasons.extend(gap_reason(cls, criterion) for cls, criterion in gaps)

    return Decision(REQUIRED if reasons else NONE, tuple(reasons))
