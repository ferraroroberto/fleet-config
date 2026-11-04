"""The deterministic owner-check rule: does this work need Roberto, and why.

Pure: no `gh`, no git, no clock. The inputs are the issue's type labels, its title,
its body (only for the explicit `Owner validation:` line), the PR's changed files
and what the lane says about in-session verification. The rule only ever adds
reasons; an explicit `none` in the issue cannot suppress one, so a drafting slip
never hides real owner work.

Self-verification comes first. Whatever the lane proved in its own session needs
nobody; what it could not prove (no stated verification, or a live-only path a
session cannot drive) and a new page or view no UX proof covered need Roberto.
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
VERIFIED_UNSTATED = "unstated"
# Before the work exists (`/issue-add`) and for history (`backfill`) verification
# is not a fact anyone can state, so the clause does not apply.
VERIFIED_NA = "n/a"
VERIFICATION_STATES = (VERIFIED_YES, VERIFIED_SCHEDULED, VERIFIED_NO, VERIFIED_UNSTATED, VERIFIED_NA)

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
) -> Decision:
    """Required or none, with every reason that fired.

    `files` are `(status, path)` pairs with GitHub's PR-file statuses
    (`added`, `modified`, `removed`, `renamed`); None means no diff is part of
    this decision (an issue draft, a back-fill). `ux_proven` is true when the PR
    carries a fresh visual proof, which already showed the new page or view working.
    """
    if verification not in VERIFICATION_STATES:
        raise ValueError(f"verification must be one of {VERIFICATION_STATES}, got {verification!r}")
    enhancement = "enhancement" in {str(label).lower() for label in labels}
    files = list(files or [])
    title = _INLINE_CODE.sub(" ", title or "")
    reasons: List[str] = []

    if explicit_line(body) == REQUIRED:
        reasons.append("the issue says Owner validation: required")
    word = _first(_LIVE_ONLY_TITLE, title)
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
    if verification == VERIFIED_UNSTATED:
        reasons.append("the lane did not state that it verified the work in its own session")
    elif verification == VERIFIED_NO:
        reasons.append("the lane could not verify the work in its own session")

    return Decision(REQUIRED if reasons else NONE, tuple(reasons))
