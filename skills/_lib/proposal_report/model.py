"""Suggestion records for a proposal report: loading, checking and quadrant placement."""

from __future__ import annotations

import enum
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

# Scores run 1-5. A 3 sits on the high side of both axes so the midline falls
# between 2 and 3: an idea has to be cheap (1-2) to count as a quick win.
SCORE_MIN, SCORE_MAX = 1, 5
HIGH_IMPACT_FROM = 3
HIGH_EFFORT_FROM = 3

# The id becomes the page anchor and part of the Board ask id, so it stays URL- and id-safe.
_ITEM_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,47}")
_REPORT_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
REQUIRED_TEXT = ("id", "title", "summary", "repo", "category")
OPTIONAL_TEXT = ("evidence", "home")


class Quadrant(enum.StrEnum):
    QUICK_WIN = "quick-win"
    BIG_BET = "big-bet"
    FILL_IN = "fill-in"
    MONEY_PIT = "money-pit"


QUADRANT_LABELS: Dict[Quadrant, str] = {
    Quadrant.QUICK_WIN: "Quick win",
    Quadrant.BIG_BET: "Big bet",
    Quadrant.FILL_IN: "Fill-in",
    Quadrant.MONEY_PIT: "Money pit",
}


def quadrant(impact: int, effort: int) -> Quadrant:
    """Where a suggestion lands: impact decides the row, effort the column."""
    high = impact >= HIGH_IMPACT_FROM
    cheap = effort < HIGH_EFFORT_FROM
    if high:
        return Quadrant.QUICK_WIN if cheap else Quadrant.BIG_BET
    return Quadrant.FILL_IN if cheap else Quadrant.MONEY_PIT


@dataclass(frozen=True)
class Suggestion:
    id: str
    title: str
    summary: str
    repo: str
    category: str
    impact: int
    effort: int
    evidence: str = ""
    home: str = ""

    @property
    def quadrant(self) -> Quadrant:
        return quadrant(self.impact, self.effort)

    @property
    def anchor(self) -> str:
        return f"s-{self.id}"


@dataclass(frozen=True)
class Report:
    id: str
    title: str
    subtitle: str
    suggestions: List[Suggestion]


def _score(raw: Dict[str, Any], key: str, where: str, errors: List[str]) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not SCORE_MIN <= value <= SCORE_MAX:
        errors.append(f"{where}.{key}: must be a whole number {SCORE_MIN}-{SCORE_MAX}, got {value!r}")
        return SCORE_MIN
    return value


def _text(raw: Dict[str, Any], key: str, where: str, errors: List[str], required: bool) -> str:
    value = raw.get(key, "")
    if not isinstance(value, str) or (required and not value.strip()):
        errors.append(f"{where}.{key}: must be {'non-empty ' if required else ''}text")
        return ""
    return value.strip()


def _suggestion(raw: Any, where: str, errors: List[str]) -> Suggestion:
    if not isinstance(raw, dict):
        errors.append(f"{where}: must be an object")
        raw = {}
    fields = {key: _text(raw, key, where, errors, True) for key in REQUIRED_TEXT}
    fields.update({key: _text(raw, key, where, errors, False) for key in OPTIONAL_TEXT})
    if fields["id"] and not _ITEM_ID.fullmatch(fields["id"]):
        errors.append(f"{where}.id: lowercase letters, digits and hyphens, at most 48, got {fields['id']!r}")
    return Suggestion(impact=_score(raw, "impact", where, errors),
                      effort=_score(raw, "effort", where, errors), **fields)


def parse(doc: Any) -> Report:
    """A checked report from decoded JSON; raises ValueError naming every problem."""
    errors: List[str] = []
    if not isinstance(doc, dict):
        raise ValueError("report: must be an object with id, title and suggestions")
    report_id = _text(doc, "id", "report", errors, True)
    if report_id and not _REPORT_ID.fullmatch(report_id):
        errors.append(f"report.id: lowercase letters, digits and hyphens, got {report_id!r}")
    title = _text(doc, "title", "report", errors, True)
    subtitle = _text(doc, "subtitle", "report", errors, False)
    rows = doc.get("suggestions")
    if not isinstance(rows, list) or not rows:
        errors.append("report.suggestions: must be a non-empty list")
        rows = []
    suggestions = [_suggestion(row, f"suggestions[{i}]", errors) for i, row in enumerate(rows)]
    seen: set = set()
    for item in suggestions:
        if item.id in seen:
            errors.append(f"suggestions: id {item.id!r} is used twice")
        seen.add(item.id)
    if errors:
        raise ValueError("; ".join(errors))
    return Report(report_id, title, subtitle, suggestions)


def load(path: Path) -> Report:
    """Read and check a report file; raises ValueError for unreadable or invalid input."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path}: cannot read report JSON ({exc})") from exc
    return parse(doc)
