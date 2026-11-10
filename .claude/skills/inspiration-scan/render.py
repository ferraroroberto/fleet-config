"""Self-contained HTML digest for /inspiration-scan.

Inline CSS with light and dark tokens, no scripts and no external requests, so
it renders in the owner's chat view through `send_file.py`. Every value from a
feed is escaped.

stdlib only.
"""

from __future__ import annotations

import html
from collections import OrderedDict
from typing import Dict, List

from feeds import STATUS_LOGIN_NEEDED, STATUS_OK, SourceResult
from score import Scored
from sources import KIND_NEWSLETTER, KIND_X, KIND_YOUTUBE

KIND_LABELS = OrderedDict([(KIND_NEWSLETTER, "Newsletters"), (KIND_YOUTUBE, "YouTube"), (KIND_X, "X")])

_CSS = """
:root{--bg:#f7f7f5;--card:#fff;--text:#1d1d1f;--muted:#6e6e73;--line:#e3e3e0;--accent:#2f6fde;
--ok:#1f7a3a;--warn:#a15c00;--mono-bg:#f0f0ee;color-scheme:light dark}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--card:#1e1e21;--text:#f2f2f4;--muted:#9a9aa1;
--line:#2e2e33;--accent:#6fa0ff;--ok:#5cc47a;--warn:#e0a24a;--mono-bg:#26262a}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.5 -apple-system,BlinkMacSystemFont,
"Segoe UI",Roboto,sans-serif}
main{max-width:720px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--muted);margin:0 0 16px;font-size:14px}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 20px;padding:0;list-style:none}
.chip{border:1px solid var(--line);border-radius:999px;padding:4px 12px;font-size:13px;background:var(--card)}
.chip.ok{color:var(--ok)}.chip.warn{color:var(--warn)}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin:0 0 12px}
.meta{color:var(--muted);font-size:13px;display:flex;justify-content:space-between;gap:8px}
.score{font-weight:600;color:var(--accent)}
.card h2{font-size:17px;margin:6px 0 8px}
.card h2 a{color:var(--text);text-decoration:none}.card h2 a:hover{text-decoration:underline}
.card p{margin:4px 0}
.label{color:var(--muted);font-size:13px}
.issue{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:13px;background:var(--mono-bg);
border-radius:8px;padding:8px 10px;margin-top:8px;overflow-wrap:anywhere}
.note{color:var(--muted);font-size:14px}
ul.plain{padding-left:18px}
footer{color:var(--muted);font-size:13px;margin-top:24px}
"""


def _e(text: object) -> str:
    return html.escape(str(text), quote=True)


def health_chips(results: List[SourceResult]) -> List[Dict[str, str]]:
    """One chip per source type: how many sources were read, and why the rest were not."""
    chips = []
    for kind, label in KIND_LABELS.items():
        mine = [r for r in results if r.source.kind == kind]
        if not mine:
            continue
        read = sum(1 for r in mine if r.status == STATUS_OK)
        if any(r.status == STATUS_LOGIN_NEEDED for r in mine):
            skipped = sum(1 for r in mine if r.status == STATUS_LOGIN_NEEDED)
            text = f"{label} skipped: not logged in ({skipped} of {len(mine)})"
            others = sorted({f"{r.status}: {r.reason}" if r.reason else r.status
                             for r in mine if r.status not in (STATUS_OK, STATUS_LOGIN_NEEDED)})
            chips.append({"text": text + (" · " + "; ".join(others) if others else ""), "cls": "warn"})
            continue
        reasons = sorted({f"{r.status}: {r.reason}" if r.reason else r.status
                          for r in mine if r.status != STATUS_OK})
        text = f"{label} {read}/{len(mine)} read"
        if reasons:
            text += " · " + "; ".join(reasons)
        chips.append({"text": text, "cls": "ok" if read == len(mine) else "warn"})
    return chips


def _card(entry: Scored) -> str:
    item = entry.item
    label = KIND_LABELS.get(item.kind, item.kind)
    score = f'<span class="score">{entry.score}/10</span>' if entry.score is not None else "unscored"
    parts = [f'<article class="card"><div class="meta"><span>{_e(label)} · {_e(item.source)}</span>{score}</div>',
             f'<h2><a href="{_e(item.link)}">{_e(item.title or item.link)}</a></h2>']
    if entry.why_fits:
        parts.append(f'<p><span class="label">Why it fits:</span> {_e(entry.why_fits)}</p>')
    if entry.experiment:
        parts.append(f'<p><span class="label">Try:</span> {_e(entry.experiment)}</p>')
    if entry.issue_idea:
        parts.append(f'<div class="issue">/issue-add {_e(entry.issue_idea)}</div>')
    parts.append("</article>")
    return "".join(parts)


def _page(body: List[str]) -> str:
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>Inspiration digest</title><style>{_CSS}</style></head><body><main>"
            + "".join(body) + "</main></body></html>")


def render_unfilled(date: str, sources_file: str) -> str:
    """The digest for a run with no usable source list: what to fill in, and where."""
    return _page([f"<h1>Inspiration digest · {_e(date)}</h1>",
                  '<p class="sub">Source list not filled in yet</p>',
                  '<article class="card"><p>No sources were read today. Open this file and replace the '
                  'example newsletters, YouTube channels and X handles with your own:</p>'
                  f'<div class="issue">{_e(sources_file)}</div>'
                  '<p class="note">The next run reads them. X also needs a one-time login '
                  '(<code>scan.py login-x</code>).</p></article>'])


def render(date: str, results: List[SourceResult], top: List[Scored], unscored: List[Scored],
           dropped_seen: int, scoring_errors: List[str]) -> str:
    chips = "".join(f'<li class="chip {c["cls"]}">{_e(c["text"])}</li>' for c in health_chips(results))
    body = [f"<h1>Inspiration digest · {_e(date)}</h1>",
            f'<p class="sub">{len(top)} ideas from {sum(len(r.items) for r in results)} items read</p>',
            f'<ul class="chips">{chips}</ul>']
    if any(r.status == STATUS_LOGIN_NEEDED for r in results):
        body.append('<p class="note">X was skipped because its Chrome profile is not logged in. '
                    'This is not a quiet day on X. Log in once with <code>scan.py login-x</code>.</p>')
    if scoring_errors:
        body.append(f'<p class="note">Scoring not confirmed for {len(unscored)} item(s): '
                    f'{_e("; ".join(scoring_errors))}. They are listed below and come back tomorrow.</p>')
    body.extend(_card(entry) for entry in top)
    if not top and not unscored:
        body.append('<p class="note">Nothing new cleared the bar today.</p>')
    if unscored:
        body.append("<h2>Unscored</h2>")
        body.extend(_card(entry) for entry in unscored)
    body.append(f"<footer>{dropped_seen} item(s) already seen on earlier days were left out.</footer>")
    return _page(body)
