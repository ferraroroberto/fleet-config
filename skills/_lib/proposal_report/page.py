"""Render a proposal report to one self-contained HTML page.

The chart and the list are both rendered here, so the page reads without
scripts; the inline script only filters and sorts what is already there.
Every chart dot and list row carries the same `data-repo`, `data-category`
and `data-quadrant`, so one filter narrows both.
"""

from __future__ import annotations

from collections import defaultdict
from html import escape
from math import cos, pi, sin
from typing import Dict, Iterable, List, Tuple

from proposal_report.model import (
    HIGH_EFFORT_FROM,
    HIGH_IMPACT_FROM,
    QUADRANT_LABELS,
    SCORE_MAX,
    Quadrant,
    Report,
    Suggestion,
)
from proposal_report.page_assets import SCRIPT, STYLE, theme_css

# Chart geometry in SVG user units: a square plot with room for the axis labels.
LEFT, TOP, SIZE = 44, 12, 280
CELL = SIZE / SCORE_MAX
DOT_RADIUS = 11
# Dots sharing one score pair fan out on a ring this far from the cell centre.
SPREAD = 13

_SUN = ('<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/>'
        '<path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>')


def point(impact: int, effort: int) -> Tuple[float, float]:
    """The centre of a score cell: effort runs left to right, impact bottom to top."""
    return LEFT + (effort - 0.5) * CELL, TOP + (SCORE_MAX - impact + 0.5) * CELL


def _placed(items: List[Suggestion]) -> List[Tuple[int, Suggestion, float, float]]:
    """Each suggestion with its 1-based number and position; a shared cell fans its dots out."""
    cells: Dict[Tuple[int, int], List[int]] = defaultdict(list)
    for index, item in enumerate(items):
        cells[(item.impact, item.effort)].append(index)
    placed = []
    for (impact, effort), members in cells.items():
        cx, cy = point(impact, effort)
        for slot, index in enumerate(members):
            dx = dy = 0.0
            if len(members) > 1:
                angle = 2 * pi * slot / len(members) - pi / 2
                dx, dy = SPREAD * cos(angle), SPREAD * sin(angle)
            placed.append((index + 1, items[index], round(cx + dx, 1), round(cy + dy, 1)))
    return sorted(placed, key=lambda row: row[0])


def _data_attrs(item: Suggestion) -> str:
    return (f'data-repo="{escape(item.repo)}" data-category="{escape(item.category)}" '
            f'data-quadrant="{item.quadrant.value}"')


def chart_svg(items: List[Suggestion]) -> str:
    mid_x = LEFT + (HIGH_EFFORT_FROM - 1) * CELL
    mid_y = TOP + (SCORE_MAX - HIGH_IMPACT_FROM + 1) * CELL
    parts = [f'<svg viewBox="0 0 {LEFT + SIZE + 8} {TOP + SIZE + 40}" role="img" '
             f'aria-label="Impact against effort for {len(items)} suggestions">',
             f'<rect class="qw" x="{LEFT}" y="{TOP}" width="{mid_x - LEFT}" height="{mid_y - TOP}" rx="8"/>']
    for step in range(SCORE_MAX + 1):
        x, y = LEFT + step * CELL, TOP + step * CELL
        parts.append(f'<line class="grid" x1="{x}" y1="{TOP}" x2="{x}" y2="{TOP + SIZE}"/>'
                     f'<line class="grid" x1="{LEFT}" y1="{y}" x2="{LEFT + SIZE}" y2="{y}"/>')
    parts.append(f'<line class="mid" x1="{mid_x}" y1="{TOP}" x2="{mid_x}" y2="{TOP + SIZE}"/>'
                 f'<line class="mid" x1="{LEFT}" y1="{mid_y}" x2="{LEFT + SIZE}" y2="{mid_y}"/>')
    for score in range(1, SCORE_MAX + 1):
        x, _ = point(1, score)
        _, y = point(score, 1)
        parts.append(f'<text class="axis" x="{x}" y="{TOP + SIZE + 14}" text-anchor="middle">{score}</text>'
                     f'<text class="axis" x="{LEFT - 8}" y="{y + 4}" text-anchor="end">{score}</text>')
    parts.append(f'<text class="axis" x="{LEFT + SIZE / 2}" y="{TOP + SIZE + 32}" text-anchor="middle">'
                 'Effort →</text>'
                 f'<text class="axis" x="12" y="{TOP + SIZE / 2}" text-anchor="middle" '
                 f'transform="rotate(-90 12 {TOP + SIZE / 2})">Impact →</text>')
    corners = ((Quadrant.QUICK_WIN, LEFT + 6, "start", TOP + 14, " win"),
               (Quadrant.BIG_BET, LEFT + SIZE - 6, "end", TOP + 14, ""),
               (Quadrant.FILL_IN, LEFT + 6, "start", TOP + SIZE - 6, ""),
               (Quadrant.MONEY_PIT, LEFT + SIZE - 6, "end", TOP + SIZE - 6, ""))
    for quad, x, anchor, y, extra in corners:
        parts.append(f'<text class="qlabel{extra}" x="{x}" y="{y}" text-anchor="{anchor}">'
                     f'{QUADRANT_LABELS[quad]}</text>')
    for number, item, x, y in _placed(items):
        parts.append(f'<a class="dot" href="#{item.anchor}" data-id="{escape(item.id)}" {_data_attrs(item)} '
                     f'data-impact="{item.impact}" data-effort="{item.effort}" data-cx="{x}" data-cy="{y}">'
                     f'<title>{number}. {escape(item.title)} (impact {item.impact}, effort {item.effort})</title>'
                     f'<circle cx="{x}" cy="{y}" r="{DOT_RADIUS}"/>'
                     f'<text x="{x}" y="{y + 3.5}" text-anchor="middle">{number}</text></a>')
    parts.append("</svg>")
    return "".join(parts)


def _chip_group(group: str, label: str, values: Iterable[Tuple[str, str]]) -> str:
    chips = "".join(f'<button type="button" class="fchip" data-group="{group}" data-value="{escape(value)}" '
                    f'aria-pressed="false">{escape(text)}</button>' for value, text in values)
    return f'<div class="fgroup" role="group" aria-label="{label}"><span class="flabel">{label}</span>{chips}</div>'


def filters_html(items: List[Suggestion]) -> str:
    repos = sorted({item.repo for item in items})
    categories = sorted({item.category for item in items})
    quads = [q for q in Quadrant if any(item.quadrant == q for item in items)]
    return ('<div class="filters">'
            + _chip_group("repo", "Repo", ((r, r) for r in repos))
            + _chip_group("category", "Category", ((c, c) for c in categories))
            + _chip_group("quadrant", "Quadrant", ((q.value, QUADRANT_LABELS[q]) for q in quads))
            + '</div><div class="toolbar"><span class="count" id="count" aria-live="polite">'
            f'Showing {len(items)} of {len(items)}</span>'
            '<span><label class="count" for="sort">Sort </label><select id="sort">'
            '<option value="order">Report order</option><option value="impact">Impact, high first</option>'
            '<option value="effort">Effort, low first</option><option value="ratio">Impact per effort</option>'
            '</select> <button type="button" class="reset" id="reset">Clear filters</button></span></div>')


def item_html(number: int, item: Suggestion) -> str:
    quad = item.quadrant
    lines = [f'<li class="item" id="{item.anchor}" {_data_attrs(item)} data-impact="{item.impact}" '
             f'data-effort="{item.effort}" data-order="{number}">',
             f'<h3><span class="num">{number}</span><span>{escape(item.title)}</span></h3>',
             f'<div class="chips"><span class="chip {quad.value}">{QUADRANT_LABELS[quad]}</span>'
             f'<span class="chip">Impact {item.impact}/{SCORE_MAX}</span>'
             f'<span class="chip">Effort {item.effort}/{SCORE_MAX}</span>'
             f'<span class="chip">{escape(item.repo)}</span><span class="chip">{escape(item.category)}</span></div>',
             f'<p>{escape(item.summary)}</p>']
    if item.evidence:
        lines.append(f'<p class="muted">Evidence: {escape(item.evidence)}</p>')
    if item.home:
        lines.append(f'<p class="muted">Proposed home: {escape(item.home)}</p>')
    lines.append("</li>")
    return "".join(lines)


def render(report: Report) -> str:
    items = report.suggestions
    wins = sum(1 for item in items if item.quadrant == Quadrant.QUICK_WIN)
    context = report.subtitle or f"{len(items)} suggestions, {wins} quick wins"
    body = "".join(item_html(number, item) for number, item in enumerate(items, 1))
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{escape(report.title)}</title><style>{theme_css()}{STYLE}</style></head><body><main>"
            f'<header class="head"><div><h1>{escape(report.title)}</h1><p class="ctx">{escape(context)}</p></div>'
            f'<button type="button" class="icon-button" id="theme" aria-label="Switch theme">{_SUN}</button></header>'
            f'<section class="card chart" aria-label="Impact and effort chart"><h2>Impact vs effort</h2>'
            f'{chart_svg(items)}</section>'
            f'<section class="card" aria-label="Filters">{filters_html(items)}</section>'
            f'<section class="card" aria-label="Suggestions"><ol class="items">{body}</ol>'
            '<p class="empty" id="empty" hidden>No suggestion matches these filters.</p></section>'
            f"</main><script>{SCRIPT}</script></body></html>")
