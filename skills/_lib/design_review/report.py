"""Stage 4 of /design-review — the evaluate document -> one self-contained HTML report (fleet-config#972).

Pure: reads the JSON `evaluate.py` produced and returns a string. No browser,
no LLM, no git, no network — the page carries its own CSS, requests nothing,
embeds and links no screenshot (the JSON's `screenshot` paths are never
read). Same document in, byte-identical page out, so #974 can diff reports
as well as JSON.

Every sentence in a finding is the rule's `fix_template` with the measured
values substituted, followed by the `standard` it cites — nothing is
authored here. Grades and scores are printed from the document and never
recomputed.

Section order (fixed; a slot whose data is absent is omitted cleanly):

    1. verdict      overall grade + score, target, commit, rubric version
    2. scorecard    one row per category: score, grade, failing, unmeasured
    3. diff         reserved for #974 — rendered only when `doc["diff"]` exists
    4. method       screens measured, devices x themes, errors, caveats
    5. strong       rules that pass, per category
    6. findings     per category: failing rules by severity then id, then the
                    category's unmeasured rules with their reason
    7. judgment     the checklist answers + uncatalogued findings (#973) — rendered
                    only when `doc["judgment"]` exists; outside the grade
    8. mockups      now-vs-proposed partials for failing rules with a template
    9. owners       where the fixes land: spec / scaffold / app

Slots — the steps only add data, the template already reads:

    doc["judgment"] = {                          # written by `judgment.py` (#973)
        "status":       "ok" | "not_confirmed" | "unmeasured",
        "reason":       str | None,              # shown beside the status
        "answers":      [{"id": "J-01", "question": str, "answer": str, "evidence": str,
                          "maps_to": [rule id], "note": str | None}],
        "uncatalogued": [{"title": str, "severity": "P0".."P3", "detail": str, "owner": str,
                          "proposed": {"metric": str, "threshold": str}}],
    }
    doc["diff"] = {                              # written by `ledger.py` (#974)
        "previous_run": str | None,  # the run id compared against; None = first recorded run
        "fixed":        [rule id], "regressed": [rule id],
        "new":          [rule id], "unchanged": [rule id],
        "unmeasured":   [rule id],   # unmeasured on either side — never fixed or regressed
        "rubric_changed": {"from": str, "to": str} | None,
    }

Placeholders (`fix_template`): `{count} {total} {screen} {value} {sample}
{share} {hit_min} {threshold} {component} {ratio} {fg} {bg} {inner_w}
{viewports}` are filled from the worst failing screen's evidence (`facts`,
`items`), the rule's `threshold` / `params` and the document's `params`.
A placeholder with no value is left as its literal `{token}` and the finding
carries a one-line "not in the JSON" note — never a crash, never an invented
number. `{{ }}` are literal braces (TYPE-02's CSS snippet).

Public surface:

    fill_template(template, values)  -> str
    worst_screen(rule)               -> dict     the evidence entry a sentence is written for
    finding_values(rule, doc)        -> dict     the placeholder values for one rule
    finding_sentence(rule, doc)      -> str      the filled fix_template
    render_report(doc)               -> str      the HTML page
    report_summary(doc)              -> dict     grade/score/fail counts/mock-up set (the CLI's lines)
    write_report(doc, out)           -> Path

stdlib only.
"""
from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

from . import mockups
from .evaluate import EVIDENCE_ITEMS

SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
SAMPLE_ITEMS = 3
SAMPLE_TEXT_CHARS = 40
OWNER_HOMES = {
    "spec": "the design system — design.md / design.dark.md (fleet-config)",
    "scaffold": "the shared base — project-scaffolding's stylesheet and vendored components",
    "app": "the target repo itself",
}
_UNFILLED_RE = re.compile(r"\{[a-z_]+(?::[^{}]*)?\}")


# ---- template filling ---------------------------------------------------------


class _Missing:
    """A placeholder with no value renders as its own token, whatever the spec."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __format__(self, spec: str) -> str:
        return "{" + self.name + (":" + spec if spec else "") + "}"

    def __str__(self) -> str:
        return "{" + self.name + "}"


class _Values(dict):
    def __missing__(self, key: str) -> _Missing:
        return _Missing(key)


def fill_template(template: str, values: Mapping[str, object]) -> str:
    """`str.format`-style substitution that keeps unknown placeholders and never raises."""
    try:
        return str(template).format_map(_Values(values))
    except (ValueError, KeyError, IndexError, AttributeError, TypeError):
        return str(template)


def unfilled(sentence: str) -> List[str]:
    """The `{token}` placeholders still present after filling."""
    return sorted({m.group(0).split(":")[0].rstrip("}") + "}" for m in _UNFILLED_RE.finditer(sentence)})


def _compact(value: object) -> object:
    """Integral floats print as ints; other floats keep two decimals."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        return int(value) if value == int(value) else round(value, 2)
    return value


def _esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def worst_screen(rule: dict) -> Optional[dict]:
    """The failing screen the sentence is written for: the extreme value, first on ties."""
    ev = [e for e in rule.get("evidence") or [] if isinstance(e, dict)]
    if not ev:
        return None
    lowest_is_worst = str(rule.get("fail_when")) in ("lt", "lte")
    best_i = 0
    for i, e in enumerate(ev):
        v, b = e.get("value"), ev[best_i].get("value")
        if not isinstance(v, (int, float)) or not isinstance(b, (int, float)):
            continue
        if (v < b) if lowest_is_worst else (v > b):
            best_i = i
    return ev[best_i]


def item_label(item: object) -> str:
    """One short, deterministic label for an evidence item."""
    if not isinstance(item, dict):
        return str(item)
    if "sel" in item:
        label = str(item["sel"])
        text = item.get("text") or item.get("label")
        if text:
            t = str(text)
            clipped = t[:SAMPLE_TEXT_CHARS] + ("…" if len(t) > SAMPLE_TEXT_CHARS else "")
            label += f" “{clipped}”"
        extras = [f"{k}={_compact(item[k])}" for k in ("px", "ratio", "w", "h", "controls", "rows", "options", "why") if k in item]
        if "w" in item and "h" in item:
            extras = [x for x in extras if not x.startswith(("w=", "h="))] + [f"{_compact(item['w'])}×{_compact(item['h'])}px"]
        return label + (f" ({', '.join(extras)})" if extras else "")
    if "component" in item:
        return f"{item['component']} {item.get('fg')} on {item.get('bg')} = {_compact(item.get('ratio'))}:1"
    if "a" in item and "b" in item:
        return f"{item['a']} ∩ {item['b']}"
    if "box" in item:
        return f"{item['box']}px ×{item.get('count', 1)}"
    if "content_w" in item and "inner_w" in item:
        return f"content {_compact(item['content_w'])}px of {_compact(item['inner_w'])}px"
    if "scroll_w" in item and "inner_w" in item:
        return f"scrolls to {_compact(item['scroll_w'])}px in a {_compact(item['inner_w'])}px viewport"
    if "zoom_locked" in item:
        return f"zoom_locked={item['zoom_locked']}, text_size_control={item.get('text_size_control')}"
    if "pane_header_visible" in item:
        return f"header visible={item['pane_header_visible']}, scroll top={item.get('pane_scroll_top')}"
    return ", ".join(f"{k}={_compact(v)}" for k, v in sorted(item.items()))


def _sample(items: List[object]) -> Optional[str]:
    labels = [item_label(i) for i in items[:SAMPLE_ITEMS]]
    if not labels:
        return None
    more = len(items) - len(labels)
    return "; ".join(labels) + (f"; +{more} more" if more > 0 else "")


def finding_values(rule: dict, doc: Optional[dict] = None) -> Dict[str, object]:
    """Every placeholder value the templates and mock-ups can use for one rule.

    Read from the worst failing screen's evidence (`value`, `items`, `facts`),
    the rule's `threshold` and `params`, and the document's resolved `params`.
    Absent values are absent keys — `fill_template` keeps their tokens.
    """
    doc = doc or {}
    out: Dict[str, object] = {}
    thr = (rule.get("threshold") or {}).get("value")
    if thr is not None:
        out["threshold"] = _compact(thr)
    hit_min = (doc.get("params") or {}).get("hit_min")
    if hit_min is not None:
        out["hit_min"] = _compact(hit_min)
    for key in ("viewports", "min_viewport", "tolerance_px"):
        if key in (rule.get("params") or {}):
            out[key] = _compact(rule["params"][key])
    worst = worst_screen(rule)
    if worst is None:
        return out
    items = [i for i in (worst.get("items") or [])]
    facts = worst.get("facts") or {}
    value = _compact(worst.get("value"))
    out.update({"screen": worst.get("screen"), "value": value, "share": value, "items": items,
                "screens_failing": len(rule.get("evidence") or [])})
    out["count"] = _compact(facts["count"]) if "count" in facts else value
    if "total" in facts:
        out["total"] = _compact(facts["total"])
    sample = _sample(items)
    if sample:
        out["sample"] = sample
    first = items[0] if items and isinstance(items[0], dict) else {}
    # An item-level `threshold` (COLOR-01's per-pair AA floor) is the one the
    # sentence means; the rule's own threshold there is the pair *count*.
    for key in ("component", "ratio", "fg", "bg", "inner_w", "content_w", "threshold"):
        if key in first:
            out[key] = _compact(first[key])
    return {k: v for k, v in out.items() if v is not None}


def finding_sentence(rule: dict, doc: Optional[dict] = None) -> str:
    return fill_template(str(rule.get("fix_template", "")), finding_values(rule, doc))


# ---- ordering + summary -------------------------------------------------------


def _sev_key(rule: dict) -> Tuple[int, str]:
    return SEVERITY_ORDER.get(str(rule.get("severity")), 9), str(rule.get("id"))


def failing_rules(doc: dict) -> List[dict]:
    """Failing rules in report order: category order, then severity, then id."""
    cats = list((doc.get("categories") or {}).keys())
    rules = [r for r in doc.get("rules") or [] if r.get("status") == "fail"]
    return sorted(rules, key=lambda r: (cats.index(r["category"]) if r.get("category") in cats else len(cats), *_sev_key(r)))


def report_summary(doc: dict) -> Dict[str, object]:
    """The numbers the CLI prints: grades from the JSON, the mock-up set the page draws."""
    rules = doc.get("rules") or []
    drawn: List[str] = []
    for r in failing_rules(doc):
        mk, _note = mockups.render_mockup(r, finding_values(r, doc))
        if mk is not None and r.get("mockup") not in drawn:
            drawn.append(str(r["mockup"]))
    overall = doc.get("overall") or {}
    return {
        "grade": overall.get("grade"), "score": overall.get("score"), "unmeasured": bool(overall.get("unmeasured")),
        "failed": sum(1 for r in rules if r.get("status") == "fail"),
        "unmeasured_rules": sum(1 for r in rules if r.get("status") == "unmeasured"),
        "total": len(rules), "rubric_version": doc.get("rubric_version"),
        "categories": {c: v.get("grade") for c, v in (doc.get("categories") or {}).items()},
        "mockups": drawn,
    }


# ---- HTML ---------------------------------------------------------------------


def _grade_class(grade: object) -> str:
    return f"grade-{str(grade or 'F').upper()[:1]}"


def _section(sid: str, title: str, body: str, note: str = "") -> str:
    lead = f'<p class="lead">{note}</p>' if note else ""
    return f'<section id="{sid}" class="sec"><h2>{_esc(title)}</h2>{lead}{body}</section>'


def _verdict(doc: dict) -> str:
    o = doc.get("overall") or {}
    flag = ' <span class="badge badge-unm">partly unmeasured</span>' if o.get("unmeasured") else ""
    meta = " &middot; ".join(_esc(x) for x in (
        f"target {doc.get('target')}", f"commit {str(doc.get('commit') or 'none')[:12]}",
        f"rubric v{doc.get('rubric_version')}", f"generated {doc.get('generated_at')}") if x)
    return (f'<div class="verdict"><span class="grade {_grade_class(o.get("grade"))}">{_esc(o.get("grade", "?"))}</span>'
            f'<div><div class="score">{_esc(_compact(o.get("score")))} / 100{flag}</div><div class="muted">{meta}</div>'
            f'<div class="muted small">{_esc(doc.get("base_url") or "")}</div></div></div>')


def _scorecard(doc: dict) -> str:
    rows = []
    for cat, v in (doc.get("categories") or {}).items():
        unm = ", ".join(v.get("unmeasured_rules") or []) or ("yes" if v.get("unmeasured") else "—")
        failed = ", ".join(v.get("failed") or []) or "—"
        rows.append(f'<tr><th scope="row"><a href="#cat-{_esc(cat)}">{_esc(cat)}</a></th><td>{_esc(_compact(v.get("score")))}</td>'
                    f'<td><span class="grade grade-sm {_grade_class(v.get("grade"))}">{_esc(v.get("grade"))}</span></td>'
                    f'<td class="ids">{_esc(failed)}</td><td class="ids">{_esc(unm)}</td></tr>')
    return ('<div class="tbl"><table><thead><tr><th>Category</th><th>Score</th><th>Grade</th><th>Failing rules</th>'
            '<th>Unmeasured</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>")


def _diff(doc: dict) -> str:
    d = doc.get("diff")
    if not isinstance(d, dict):
        return ""
    def bucket(name: str, cls: str) -> str:
        ids = d.get(name) or []
        pills = "".join(f'<span class="pill {cls}">{_esc(i)}</span>' for i in ids) or '<span class="muted">none</span>'
        return f'<div class="bucket"><h4>{_esc(name)} ({len(ids)})</h4>{pills}</div>'
    body = ('<div class="buckets">' + bucket("fixed", "pill-ok") + bucket("regressed", "pill-bad")
            + bucket("new", "pill-bad") + bucket("unchanged", "pill-mute")
            + (bucket("unmeasured", "pill-unm") if "unmeasured" in d else "") + "</div>")
    prev = d.get("previous_run")
    lead = f"compared against run {_esc(prev)}" if prev else "first recorded run for this target — nothing to compare against yet"
    rc = d.get("rubric_changed")
    if isinstance(rc, dict):
        lead += f"; rubric changed from v{_esc(rc.get('from'))} to v{_esc(rc.get('to'))} between the runs, so a moved rule may be the rubric, not the app"
    if "unmeasured" in d:
        lead += "; a rule unmeasured on either side is listed as unmeasured, never as fixed or regressed"
    return _section("diff", "Since the previous run", body, lead)


def _method(doc: dict) -> str:
    screens = [s for s in doc.get("screens") or [] if isinstance(s, dict)]
    ok = [s for s in screens if s.get("status") == "ok"]
    absent = [s for s in screens if s.get("status") == "absent"]
    bad = [s for s in screens if s.get("status") not in ("ok", "absent")]
    devices = sorted({str(s.get("device")) for s in screens if s.get("device")})
    themes = sorted({str(s.get("theme")) for s in screens if s.get("theme")})
    kinds: Dict[str, int] = {}
    for s in ok:
        kinds[str(s.get("kind"))] = kinds.get(str(s.get("kind")), 0) + 1
    items = [
        f"{len(ok)} of {len(screens)} screens measured across {', '.join(devices) or 'no device'} × {', '.join(themes) or 'no theme'}"
        + (f" ({', '.join(f'{n} {k}' for k, n in sorted(kinds.items()))})" if kinds else ""),
        f"metrics captured {doc.get('metrics_generated_at') or 'unknown'}; evaluated {doc.get('generated_at') or 'unknown'}; run directory {doc.get('run_dir') or 'unknown'}",
    ]
    if doc.get("mode") == "synthetic":
        items.append("measured on the target's own synthetic instance (a throwaway copy with synthetic data, "
                     "booted and stopped by this run), not the live app; compared only with earlier synthetic runs")
    if doc.get("unmeasured"):
        u = doc["unmeasured"] if isinstance(doc["unmeasured"], dict) else {"reason": doc["unmeasured"]}
        items.append(f"the whole run is unmeasured: {u.get('reason')} — {u.get('detail') or ''}".rstrip(" —"))
    for s in bad:
        items.append(f"screen {s.get('id')} not measured: {s.get('reason') or 'walk error'} — its rules report unmeasured, never pass")
    for s in absent:
        items.append(f"screen {s.get('id')}: step target absent in this app state (STEP_TARGET_ABSENT) — left out of every rule, "
                     "which is unmeasured only where no other screen measured it")
    if doc.get("metrics_rubric_version") and doc.get("metrics_rubric_version") != doc.get("rubric_version"):
        items.append(f"metrics were captured under rubric v{doc['metrics_rubric_version']} and evaluated under v{doc['rubric_version']}")
    params = doc.get("params") or {}
    if params:
        floors = [f"{k} = {'/'.join(str(_compact(x)) for x in v) if isinstance(v, list) else _compact(v)}" for k, v in params.items()]
        items.append("measurement floors: " + ", ".join(floors))
    items.append("scores and grades are printed from the evaluate document, never recomputed here; a rule is a pass only when every applicable screen was measured and none failed")
    items.append("mock-ups are redrawn with placeholder content; no screenshot or captured page is embedded or linked")
    return "<ul>" + "".join(f"<li>{_esc(i)}</li>" for i in items) + "</ul>"


def _strong(doc: dict) -> str:
    cats = list((doc.get("categories") or {}).keys())
    passing = sorted((r for r in doc.get("rules") or [] if r.get("status") == "pass"),
                     key=lambda r: (cats.index(r["category"]) if r.get("category") in cats else 99, *_sev_key(r)))
    if not passing:
        return '<p class="muted">No rule passes on this run.</p>'
    lis = "".join(f'<li><code class="rid">{_esc(r.get("id"))}</code> {_esc(r.get("title"))} '
                  f'<span class="muted small">— {_esc(r.get("reason"))}</span></li>' for r in passing)
    return f'<ul class="plain">{lis}</ul>'


def _evidence(rule: dict) -> str:
    ev = [e for e in rule.get("evidence") or [] if isinstance(e, dict)]
    if not ev:
        return ""
    rows = "".join(
        f'<tr><td class="ids">{_esc(e.get("screen"))}</td><td>{_esc(_compact(e.get("value")))}</td>'
        f'<td>{_esc(_sample_rows(e))}</td></tr>' for e in ev)
    return (f'<details class="evidence"><summary>Evidence — {len(ev)} screen{"s" if len(ev) != 1 else ""}</summary>'
            f'<div class="tbl"><table><thead><tr><th>Screen</th><th>Value</th><th>Sample (≤{EVIDENCE_ITEMS} items)</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div></details>')


def _sample_rows(e: dict) -> str:
    items = e.get("items") or []
    facts = e.get("facts") or {}
    parts = [f"{k}={_compact(v)}" for k, v in facts.items()]
    parts += [item_label(i) for i in items]
    return "; ".join(parts) if parts else "—"


def _finding(rule: dict, doc: dict) -> str:
    values = finding_values(rule, doc)
    sentence = fill_template(str(rule.get("fix_template", "")), values)
    missing = unfilled(sentence)
    n = int(values.get("screens_failing") or 0)
    scope = f'<span class="muted small">worst of {n} failing screen{"s" if n != 1 else ""}</span>' if n > 1 else ""
    mk, note = mockups.render_mockup(rule, values)
    if mk is not None:
        mock = f'<a href="#mock-{_esc(rule.get("id"))}">{_esc(rule.get("mockup"))}</a>'
    elif note:
        mock = f'<span class="muted">{_esc(note)}</span>'
    else:
        mock = '<span class="muted">none for this rule</span>'
    thr = rule.get("threshold") or {}
    measured = f"{rule.get('metric')} {rule.get('fail_when')} {_compact(thr.get('value'))} ({thr.get('source')})"
    miss = f'<p class="muted small">not in the JSON: {_esc(", ".join(missing))}</p>' if missing else ""
    return (f'<article class="finding sev-{_esc(rule.get("severity"))}" id="{_esc(rule.get("id"))}">'
            f'<header><span class="badge sev sev-{_esc(rule.get("severity"))}">{_esc(rule.get("severity"))}</span><code class="rid">{_esc(rule.get("id"))}</code>'
            f'<h4>{_esc(rule.get("title"))}</h4></header>'
            f'<p class="sentence">{_esc(sentence)} {scope}</p>{miss}'
            f'<dl><dt>Standard</dt><dd>{_esc(rule.get("standard"))}</dd>'
            f'<dt>Owner</dt><dd>{_esc(rule.get("owner"))} — {_esc(OWNER_HOMES.get(str(rule.get("owner")), ""))}</dd>'
            f'<dt>Measured</dt><dd>{_esc(measured)}; {_esc(rule.get("reason"))}</dd>'
            f'<dt>Mock-up</dt><dd>{mock}</dd></dl>{_evidence(rule)}</article>')


def _findings(doc: dict) -> str:
    out = []
    rules = doc.get("rules") or []
    for cat, v in (doc.get("categories") or {}).items():
        mine = [r for r in rules if r.get("category") == cat]
        failing = sorted((r for r in mine if r.get("status") == "fail"), key=_sev_key)
        unm = sorted((r for r in mine if r.get("status") == "unmeasured"), key=_sev_key)
        head = (f'<h3 id="cat-{_esc(cat)}">{_esc(cat)} <span class="grade grade-sm {_grade_class(v.get("grade"))}">{_esc(v.get("grade"))}</span>'
                f' <span class="muted small">{_esc(_compact(v.get("score")))} / 100</span></h3>')
        body = "".join(_finding(r, doc) for r in failing)
        if unm:
            body += ('<ul class="plain unmeasured">' + "".join(
                f'<li><span class="badge badge-unm">unmeasured</span> <code class="rid">{_esc(r.get("id"))}</code> '
                f'{_esc(r.get("title"))} <span class="muted small">— {_esc(r.get("reason"))}</span></li>' for r in unm) + "</ul>")
        if not failing and not unm:
            body = '<p class="muted">No findings.</p>'
        out.append(f'<div class="cat">{head}{body}</div>')
    return "".join(out)


def _judgment(doc: dict) -> str:
    j = doc.get("judgment")
    if not isinstance(j, dict):
        return ""
    rows = "".join(
        f'<tr><td class="ids">{_esc(a.get("id", ""))}</td><td>{_esc(a.get("question", ""))}</td>'
        f'<td class="answer-{_esc(str(a.get("answer", "")).replace(" ", "-"))}">{_esc(a.get("answer", ""))}</td>'
        f'<td class="ids">{_esc(a.get("evidence") or "—")}</td><td class="ids">{_esc(", ".join(a.get("maps_to") or []) or "—")}</td></tr>'
        for a in j.get("answers") or [] if isinstance(a, dict))
    table = (f'<div class="tbl"><table><thead><tr><th>Id</th><th>Question</th><th>Answer</th><th>Evidence</th><th>Maps to</th></tr></thead>'
             f'<tbody>{rows}</tbody></table></div>') if rows else '<p class="muted">No checklist answers.</p>'
    unc = [u for u in j.get("uncatalogued") or [] if isinstance(u, dict)]
    lis = []
    for u in unc:
        prop = u.get("proposed") if isinstance(u.get("proposed"), dict) else {}
        proposed = f' <span class="muted small">proposed rule: {_esc(prop.get("metric"))} fails at {_esc(prop.get("threshold"))}</span>' if prop.get("metric") else ""
        lis.append(f'<li><span class="badge sev sev-{_esc(u.get("severity", ""))}">{_esc(u.get("severity", ""))}</span> <b>{_esc(u.get("title", ""))}</b> '
                   f'{_esc(u.get("detail", ""))} <span class="muted small">owner {_esc(u.get("owner", ""))}</span>{proposed}</li>')
    body = table + "<h3>Uncatalogued</h3>" + (f'<ul class="plain">{"".join(lis)}</ul>' if lis else '<p class="muted">Nothing outside the rubric.</p>')
    lead = f"status: {_esc(j.get('status') or 'unknown')}" + (f" — {_esc(j['reason'])}" if j.get("reason") else "")
    lead += "; checklist answers and uncatalogued findings sit outside the grade"
    return _section("judgment", "Judgment", body, lead)


def _mockups(doc: dict) -> str:
    blocks = []
    for r in failing_rules(doc):
        mk, _note = mockups.render_mockup(r, finding_values(r, doc))
        if mk is None:
            continue
        blocks.append(
            f'<div class="mock" id="mock-{_esc(r.get("id"))}"><h3><code class="rid">{_esc(r.get("id"))}</code> {_esc(r.get("mockup"))}'
            f' <span class="muted small">— {_esc(mk.get("caption", ""))}</span></h3>'
            f'<div class="mk-pair"><div class="mk-variant"><h4>Now</h4>{mk["now"]}</div>'
            f'<div class="mk-variant"><h4>Proposed</h4>{mk["proposed"]}</div></div>'
            f'<p class="muted small"><a href="#{_esc(r.get("id"))}">back to the finding</a></p></div>')
    if not blocks:
        return '<p class="muted">No failing rule has a mock-up template on this run.</p>'
    return "".join(blocks)


def _owners(doc: dict) -> str:
    groups: Dict[str, List[dict]] = {}
    for r in failing_rules(doc):
        groups.setdefault(str(r.get("owner")), []).append(r)
    if not groups:
        return '<p class="muted">Nothing to fix.</p>'
    out = []
    for owner in ("spec", "scaffold", "app"):
        rs = groups.pop(owner, None)
        if not rs:
            continue
        out.append(_owner_block(owner, rs, doc))
    for owner, rs in groups.items():
        out.append(_owner_block(owner, rs, doc))
    return "".join(out)


def _owner_block(owner: str, rules: List[dict], doc: dict) -> str:
    home = OWNER_HOMES.get(owner, "")
    if owner == "app" and doc.get("target"):
        home = f"the {doc['target']} repo"
    lis = "".join(f'<li><span class="badge sev sev-{_esc(r.get("severity"))}">{_esc(r.get("severity"))}</span> <code class="rid">{_esc(r.get("id"))}</code> '
                  f'<a href="#{_esc(r.get("id"))}">{_esc(r.get("title"))}</a></li>' for r in rules)
    return f'<div class="owner"><h3>{_esc(owner)} <span class="muted small">— {_esc(home)}</span></h3><ul class="plain">{lis}</ul></div>'


CSS = """
:root{--canvas:#ffffff;--canvas-subtle:#f6f8fa;--card:#ffffff;--border:#d1d9e0;--border-muted:#d8dee4;--fg:#1f2328;--fg-muted:#656d76;
--accent:#0969da;--accent-soft:color-mix(in srgb,var(--accent) 16%,transparent);--success:#1a7f37;--danger:#cf222e;--attention:#9a6700;
--p0:#cf222e;--p1:#bc4c00;--p2:#9a6700;--p3:#656d76}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--canvas:#0d1117;--canvas-subtle:#010409;--card:#161b22;--border:#30363d;
--border-muted:#21262d;--fg:#e6edf3;--fg-muted:#7d8590;--accent:#2f81f7;--success:#3fb950;--danger:#f85149;--attention:#d29922;
--p0:#f85149;--p1:#db6d28;--p2:#d29922;--p3:#7d8590}}
:root[data-theme="dark"]{--canvas:#0d1117;--canvas-subtle:#010409;--card:#161b22;--border:#30363d;--border-muted:#21262d;--fg:#e6edf3;
--fg-muted:#7d8590;--accent:#2f81f7;--success:#3fb950;--danger:#f85149;--attention:#d29922;--p0:#f85149;--p1:#db6d28;--p2:#d29922;--p3:#7d8590}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--canvas);color:var(--fg);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;padding:16px;max-width:900px;margin-inline:auto;overflow-wrap:anywhere}
h1{font-size:2rem;line-height:1.15;letter-spacing:-.02em;margin:0 0 4px}
h2{font-size:1.5rem;line-height:1.2;margin:32px 0 12px}
h3{font-size:1.15rem;margin:24px 0 8px}
h4{font-size:1rem;margin:0}
a{color:var(--accent)}
code.rid{font:600 .85rem ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--canvas-subtle);border:1px solid var(--border);border-radius:6px;padding:1px 6px;white-space:nowrap}
.muted{color:var(--fg-muted)}
.small{font-size:.85rem}
.lead{color:var(--fg-muted);margin:0 0 12px}
.sec{border-top:1px solid var(--border-muted);padding-top:4px}
.verdict{display:flex;align-items:center;gap:16px;background:var(--card);border:1px solid var(--border);border-radius:16px;padding:16px;margin-top:12px}
.grade{display:inline-flex;align-items:center;justify-content:center;width:72px;height:72px;border-radius:16px;font-size:2rem;font-weight:700;color:#fff;flex:none}
.grade-sm{width:30px;height:30px;font-size:1rem;border-radius:8px;vertical-align:middle}
.grade-A{background:var(--success)}.grade-B{background:#1f883d}.grade-C{background:var(--attention)}.grade-D{background:#bc4c00}.grade-F{background:var(--danger)}
.score{font-size:1.25rem;font-weight:700}
.tbl{overflow-x:auto;border:1px solid var(--border);border-radius:12px;background:var(--card)}
table{border-collapse:collapse;width:100%;font-size:.92rem}
th,td{text-align:left;padding:8px 10px;border-top:1px solid var(--border-muted);vertical-align:top}
thead th{border-top:0;font-size:.78rem;color:var(--fg-muted);letter-spacing:.02em;white-space:nowrap}
th[scope=row]{white-space:nowrap}
td.ids{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.8rem}
.badge{display:inline-block;font-size:.72rem;font-weight:700;border-radius:9999px;padding:2px 8px;color:#fff;vertical-align:middle}
.badge-unm{background:var(--attention)}
.badge.sev{background:var(--p3)}
.badge.sev-P0{background:var(--p0)}.badge.sev-P1{background:var(--p1)}.badge.sev-P2{background:var(--p2)}
td.answer-yes{color:var(--success);font-weight:600}td.answer-no{color:var(--danger);font-weight:600}td.answer-na,td.answer-not-confirmed{color:var(--fg-muted);font-weight:600}
.finding{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:12px 16px;margin:12px 0}
.finding header{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.finding .sentence{font-weight:600;margin:8px 0 4px}
.finding dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 12px;margin:8px 0;font-size:.92rem}
.finding dt{color:var(--fg-muted);font-weight:600;font-size:.78rem;padding-top:2px}
.finding dd{margin:0}
@media (max-width:480px){.finding dl{grid-template-columns:1fr}.finding dt{padding-top:6px}}
details.evidence summary{cursor:pointer;color:var(--accent);font-size:.92rem;margin:4px 0}
details.evidence .tbl{margin-top:8px}
ul.plain{list-style:none;padding:0;margin:8px 0}
ul.plain li{padding:6px 0;border-top:1px solid var(--border-muted)}
ul.plain li:first-child{border-top:0}
.cat{margin-top:8px}
.mock{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:12px 16px;margin:12px 0}
.owner{margin-top:8px}
.buckets{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}
.bucket{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:12px}
.pill{display:inline-block;font:600 .78rem ui-monospace,SFMono-Regular,Menlo,monospace;border-radius:9999px;padding:2px 8px;margin:2px 4px 2px 0;border:1px solid var(--border)}
.pill-ok{color:var(--success)}.pill-bad{color:var(--danger)}.pill-mute{color:var(--fg-muted)}.pill-unm{color:var(--attention)}
footer{margin:40px 0 16px;color:var(--fg-muted);font-size:.78rem;border-top:1px solid var(--border-muted);padding-top:12px}
"""


def render_report(doc: dict) -> str:
    """The whole page for one evaluate document."""
    target = doc.get("target") or "unknown target"
    title = f"Design review — {target}"
    body = [
        f"<h1>{_esc(title)}</h1>",
        f'<p class="muted">rubric v{_esc(doc.get("rubric_version"))} &middot; evaluate schema {_esc(doc.get("schema_version"))}'
        f' &middot; {_esc(len(doc.get("rules") or []))} rules</p>',
        _verdict(doc),
        _section("scorecard", "Scorecard", _scorecard(doc)),
        _diff(doc),
        _section("method", "Method and caveats", _method(doc)),
        _section("strong", "What is already strong", _strong(doc)),
        _section("findings", "Findings by category", _findings(doc),
                 "each finding is the rule's fix template filled with the worst screen's measurements, then the standard it cites"),
        _judgment(doc),
        _section("mockups", "Now vs proposed", _mockups(doc),
                 "redrawn with placeholder content from the measurements; nothing here is a capture of the app"),
        _section("owners", "Where the fixes land", _owners(doc)),
        f'<footer>/design-review &middot; rubric v{_esc(doc.get("rubric_version"))} &middot; target {_esc(target)} @ '
        f'{_esc(str(doc.get("commit") or "none")[:12])} &middot; evaluated {_esc(doc.get("generated_at"))}</footer>',
    ]
    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"<title>{_esc(title)}</title>\n<style>{CSS}{mockups.CSS}</style>\n</head>\n<body>\n"
            + "\n".join(b for b in body if b) + "\n</body>\n</html>\n")


def write_report(doc: dict, out: Path) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_report(doc), encoding="utf-8")
    return out
