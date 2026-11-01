"""The deterministic owner-check rule: does this work need Roberto, and why.

Pure: no `gh`, no git, no clock. The inputs are the issue's type labels, its title,
its body (only for the explicit `Owner validation:` line), the PR's changed files
and what the lane says about in-session verification. The rule only ever adds
reasons; an explicit `none` in the issue cannot suppress one, so a drafting slip
never hides real owner work.

Self-verification comes first. Whatever the lane proved in its own session needs
nobody; what it could not prove (no stated verification, or a live-only path a
session cannot drive) and a new surface the owner has to try as a user need
Roberto. Words are matched in the title only: issue bodies quote viewports
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
VERIFIED_UNSTATED = "unstated"
# Before the work exists (`/issue-add`) and for history (`backfill`) verification
# is not a fact anyone can state, so the clause does not apply.
VERIFIED_NA = "n/a"
VERIFICATION_STATES = (VERIFIED_YES, VERIFIED_NO, VERIFIED_UNSTATED, VERIFIED_NA)

_EXPLICIT = re.compile(r"^\W*owner validation\W*\s*(required|none)\b", re.IGNORECASE | re.MULTILINE)

# Input a session cannot drive: a real device, a live microphone or camera.
_LIVE_ONLY_TITLE = re.compile(
    r"\b(microphones?|mic|webcams?|cameras?|wasapi|dictation|voice notes?|iphone|ipad|android|"
    r"on-device|real device|haptics?|on-screen keyboard|home-screen|bluetooth)\b",
    re.IGNORECASE,
)
_LIVE_ONLY_PATH_TOKENS = frozenset({"wasapi", "mic", "microphone", "camera", "webcam"})
# An account only the owner holds (enhancements only).
_ACCOUNT_TITLE = re.compile(
    r"\b(sign[- ]?ups?|sign[- ]?ins?|log[- ]?ins?|oauth|accounts?|subscriptions?)\b",
    re.IGNORECASE,
)
# A new page, view or template is a surface the owner has to try (enhancements only).
_SURFACE_PATH = re.compile(r"(^|/)(static|templates)/|\.html?$", re.IGNORECASE)
_INLINE_CODE = re.compile(r"`[^`\n]*`")


@dataclass(frozen=True)
class Decision:
    verdict: str
    reasons: Tuple[str, ...]


def explicit_line(body: str) -> Optional[str]:
    """The issue's own `Owner validation: required|none` line, or None."""
    m = _EXPLICIT.search(body or "")
    return m.group(1).lower() if m else None


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
        if status == "added" and _SURFACE_PATH.search(path.replace("\\", "/")):
            return path
    return None


def decide(
    labels: Iterable[str],
    title: str,
    body: str,
    files: Optional[Sequence[Tuple[str, str]]] = None,
    verification: str = VERIFIED_NA,
) -> Decision:
    """Required or none, with every reason that fired.

    `files` are `(status, path)` pairs with GitHub's PR-file statuses
    (`added`, `modified`, `removed`, `renamed`); None means no diff is part of
    this decision (an issue draft, a back-fill).
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
            reasons.append(f"involves an account only the owner holds ({word})")
        added = _added_surface_path(files)
        if added:
            reasons.append(f"adds a new user-facing surface ({added})")
    if verification == VERIFIED_UNSTATED:
        reasons.append("the lane did not state that it verified the work in its own session")
    elif verification == VERIFIED_NO:
        reasons.append("the lane could not verify the work in its own session")

    return Decision(REQUIRED if reasons else NONE, tuple(reasons))
