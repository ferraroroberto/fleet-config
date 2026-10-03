"""Stage 3c of /design-review — the bounded judgment checklist (fleet-config#973).

Some of the audit's most valuable findings were not metrics (lists built like
spreadsheets, destructive actions as the loudest controls, every tab with a
different anatomy). They need judgment, and judgment is where runs diverge.
This module bounds it as a **file contract, not prose**:

  judge_prompt(doc, run_dir, rubric)      -> str    the one deterministic prompt a
                                                     fresh-context judge receives
  parse_payload(text)                     -> (obj | None, error)
  validate_answers(payload, rubric, doc)  -> (judgment_doc, errors)
  merge_judges([judgment_doc, ...])       -> judgment_doc
  answer_counts(judgment_doc)             -> (yes, no, na)

The prompt carries only the `[[judgment]]` checklist, the run's screen list
with the **local** screenshot paths (`shots/<id>.png`, `shots/<id>-full.png`),
the path to `metrics.json`, and the required output schema — never
`evaluate.json`, `report.html`, a previous run, or an expected answer, or
agreement between judges would mean nothing. It contains no timestamp, so
the same run dir + rubric produce the same bytes.

Judge output schema (one JSON object, nothing else):

    {"answers":      [{"id": "J-01", "answer": "yes" | "no" | "na",
                       "evidence": "<screen id>" (may be null only for na),
                       "maps_to": ["<rule id>", ...] | null,   # subset of the question's maps_to
                       "note": "<one sentence>" | null}],
     "uncatalogued": [{"question": "J-04", "title": str, "severity": "P0".."P3",
                       "detail": str, "owner": "spec" | "scaffold" | "app",
                       "proposed": {"metric": str, "threshold": str | number}}]}

`validate_answers` keeps what is valid and drops what is not (fleet-config#1185,
reversing the all-or-nothing contract of #973 on Roberto's decision of
2026-10-03). Per answer: every seed id answered exactly once,
answers in the enum, `evidence` a real screen id inside the question's
scope, every `no` either mapped to one of its question's rule ids or backed
by an `uncatalogued` entry naming that question, `uncatalogued` only on a
`no`. One alias is accepted: `<screen id>-full` (the filename stem of the
screen's full-page PNG, which the prompt lists) reads as that screen's id,
and every such reading is recorded in `normalized` (fleet-config#1158: five of
seven recorded `unmeasured` judgments were only this). A violation drops only the answer or uncatalogued entry it sits on — an
answer left without its backing `no` entry goes too — and every drop is
listed in `dropped` as `{id, reason}` (`status: "partial"`), never silent. A
bad envelope (not one JSON object, an unknown top-level key, `answers` not a
list) or a payload from which no answer survives is
`{status: "unmeasured", reason, errors: [...]}` with empty answers. The skill
still never edits a reply and never re-prompts a judge.

`merge_judges` (for `--judges 2`) keeps the answers every judge agrees on,
lists disagreements as `not confirmed` (status `not_confirmed`; a question one
judge dropped is a disagreement too), carries every judge's `dropped`, and keeps
an uncatalogued finding only when every judge raised it — the same title
after normalisation (case, punctuation and whitespace folded) or the same
question, so an agreed unmapped `no` never loses its finding to wording.

The judgment document written into `evaluate.json["judgment"]`:

    {"status": "ok" | "partial" | "not_confirmed" | "unmeasured", "reason": str | null,
     "rubric_version": str, "judges": int,
     "answers": [{"id", "question", "answer", "evidence", "maps_to": [...], "note"}],
     "uncatalogued": [{"question", "title", "severity", "detail", "owner", "proposed": {...}}],
     "errors": [str], "disagreements": [{"id", "answers": [...]}],
     "dropped": [{"id", "reason"}],   # + "judge": n after a merge
     "normalized": [{"id", "from", "to"}]}

Nothing here touches `categories` / `overall`: grades are the rubric's
arithmetic over the metrics and the report prints them from the JSON, so an
uncatalogued finding can never move a grade (asserted in
`tests/test_design_judgment.py`). This module spawns no agent and reads no
screenshot — the skill spawns the judge; this is the contract it must meet.

stdlib only.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .rubric import OWNERS, SEVERITIES, Judgment, Rubric

ANSWERS = ("yes", "no", "na")
STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_NOT_CONFIRMED = "not_confirmed"
STATUS_UNMEASURED = "unmeasured"
NOT_CONFIRMED = "not confirmed"
SHOTS_DIR = "shots"
FULL_SUFFIX = "-full"
SCOPE_KINDS: Dict[str, Optional[Tuple[str, ...]]] = {"all": None, "tabs": ("tab",), "dialogs": ("dialog",)}

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n(.*?)\n\s*```\s*$", re.DOTALL)
_NORM_RE = re.compile(r"[^a-z0-9]+")


# ---- screens ------------------------------------------------------------------


def screen_rows(doc: dict) -> List[dict]:
    """The screens a judge may cite: measured, with a viewport screenshot on disk."""
    out = []
    for s in doc.get("screens") or []:
        if not isinstance(s, dict) or not s.get("id") or not s.get("screenshot"):
            continue
        out.append({"id": str(s["id"]), "kind": str(s.get("kind") or ""), "device": str(s.get("device") or ""),
                    "theme": str(s.get("theme") or ""), "view": str(s.get("view") or ""),
                    "screenshot": str(s["screenshot"]), "screenshot_full": str(s["screenshot_full"]) if s.get("screenshot_full") else None})
    return out


def _in_scope(screen: dict, scope: str) -> bool:
    kinds = SCOPE_KINDS.get(scope)
    return kinds is None or screen.get("kind") in kinds


# ---- the prompt ---------------------------------------------------------------


def judge_prompt(doc: dict, run_dir: Path, rubric: Rubric) -> str:
    """The deterministic judge prompt for one run directory.

    Only the checklist, the screen list with local screenshot paths, the
    metrics path and the output schema. No timestamp, no previous result.
    """
    run_dir = Path(run_dir)
    shots = run_dir / SHOTS_DIR
    screens = screen_rows(doc)
    rules_by_id = {r.id: r for r in rubric.rules}
    referenced = sorted({m for j in rubric.judgment for m in j.maps_to})
    lines: List[str] = []
    lines.append(f"# /design-review judgment — {doc.get('target') or 'unknown target'} @ {str(doc.get('commit') or 'none')[:12]} (rubric v{rubric.version})")
    lines.append("")
    lines.append("You are the judge of a bounded design checklist. Answer every question below from the "
                 "screenshots and the metrics file listed here and from nothing else. Do not read any other "
                 "file in this run directory or any other run directory, do not look for a previous report, "
                 "and do not open the app.")
    lines.append("")
    lines.append("## Files you may read")
    lines.append("")
    lines.append(f"- metrics: `{run_dir / 'metrics.json'}` — per-screen rendered measurements (sizes, contrast, hit rectangles, names)")
    lines.append(f"- screenshots: `{shots}` — one viewport PNG per screen id, plus a `-full` page PNG where listed")
    lines.append("")
    lines.append("## Screens")
    lines.append("")
    lines.append("| id | kind | device | theme | view | screenshot | full page |")
    lines.append("|---|---|---|---|---|---|---|")
    for s in screens:
        full = f"`{shots / s['screenshot_full']}`" if s["screenshot_full"] else "—"
        lines.append(f"| {s['id']} | {s['kind']} | {s['device']} | {s['theme']} | {s['view']} | `{shots / s['screenshot']}` | {full} |")
    lines.append("")
    lines.append("Scopes: `all` = every screen above; `tabs` = screens of kind `tab`; `dialogs` = screens of kind `dialog`.")
    lines.append("")
    lines.append("Minimum coverage: before answering, open every `iphone-light` screenshot — the viewport PNG and, for "
                 "tabs, the `-full` page PNG (it shows collapsed sections expanded, so controls hidden in the viewport "
                 "appear there) — and every `desktop-light` tab `-full` PNG; open other devices and themes only to "
                 "settle a doubt, light and dark render the same layout in different colours. Judge what the "
                 "screenshots show; the metrics file supports a reading and never overrides what is visible. Cite as "
                 "evidence the screen you actually looked at, by its id from the first column of the table, never a file "
                 "name: the `-full` PNG of a screen is cited by that screen's id, without `-full`.")
    lines.append("")
    lines.append("## Checklist")
    lines.append("")
    lines.append("Answer each question `yes`, `no` or `na` exactly as its own text defines them, and name the one "
                 "screen id (inside the question's scope) your answer rests on as `evidence`. A `no` must either "
                 "map to one of the rule ids listed for that question, or be raised as an uncatalogued finding "
                 "with a proposed metric and threshold.")
    lines.append("")
    for j in rubric.judgment:
        lands = ", ".join(j.maps_to) if j.maps_to else "none — a `no` must be uncatalogued"
        lines.append(f"### {j.id} (screens: {j.screens}; a `no` may map to: {lands})")
        lines.append("")
        lines.append(j.question)
        lines.append("")
    if referenced:
        lines.append("## Rule ids a `no` may map to")
        lines.append("")
        for rid in referenced:
            r = rules_by_id.get(rid)
            if r is not None:
                lines.append(f"- `{rid}` — {r.title}")
        lines.append("")
    lines.append("## Output")
    lines.append("")
    lines.append("Reply with exactly one JSON object and nothing else — no prose before or after it, no markdown "
                 "fence. Free-form commentary is rejected: the payload is schema-validated, an answer that breaks the "
                 "schema is dropped and reported (your other answers are kept), and a reply that is not one JSON "
                 "object of exactly the two keys below makes the whole judgment `unmeasured`.")
    lines.append("")
    lines.append("```")
    lines.append(json.dumps({
        "answers": [{"id": "J-01", "answer": "yes | no | na", "evidence": "<screen id>",
                     "maps_to": ["<rule id from the question's list>"], "note": "<one sentence, or null>"}],
        "uncatalogued": [{"question": "<J-id answered no>", "title": "<short title>", "severity": "P0 | P1 | P2 | P3",
                          "detail": "<what is wrong and where>", "owner": "spec | scaffold | app",
                          "proposed": {"metric": "<what to measure>", "threshold": "<when it fails>"}}],
    }, indent=2))
    lines.append("```")
    lines.append("")
    lines.append("Rules:")
    lines.append("")
    lines.append(f"- `answers` holds every id {', '.join(j.id for j in rubric.judgment)} exactly once and no other id.")
    lines.append("- `answer` is one of `yes`, `no`, `na`.")
    lines.append("- `evidence` is a screen id from the table's first column (never a file name, and without any `-full`), inside the question's scope; it may be null only when the answer is `na`.")
    lines.append("- `maps_to` lists only rule ids from that question's own list; use null or [] when the answer is not `no`.")
    lines.append("- every `no` either has a non-empty `maps_to` or an `uncatalogued` entry whose `question` is that id, with `proposed.metric` and `proposed.threshold` filled.")
    lines.append("- an `uncatalogued` entry may only name a question you answered `no`.")
    lines.append("- `note` is one short sentence or null; no other keys anywhere.")
    lines.append("")
    return "\n".join(lines)


# ---- the payload --------------------------------------------------------------


def parse_payload(text: str) -> Tuple[Optional[object], Optional[str]]:
    """One JSON object from a judge's reply; a ```json fence around it is tolerated, anything else is not."""
    raw = str(text or "").strip()
    m = _FENCE_RE.match(raw)
    if m:
        raw = m.group(1).strip()
    if not raw:
        return None, "empty reply"
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as exc:
        return None, f"not a single JSON object: {exc.msg} at char {exc.pos}"


def unmeasured_doc(rubric: Rubric, errors: List[str], judges: int = 1) -> dict:
    """The refusal: status unmeasured, every error listed, no answer kept."""
    return {"status": STATUS_UNMEASURED, "reason": f"{len(errors)} schema violation{'s' if len(errors) != 1 else ''}: {errors[0]}" if errors else "no errors listed",
            "rubric_version": rubric.version, "judges": judges, "answers": [], "uncatalogued": [], "errors": list(errors), "disagreements": [], "dropped": []}


def validate_answers(payload: object, rubric: Rubric, doc: dict) -> Tuple[dict, List[str]]:
    """Schema-validate one judge's payload against the rubric checklist and the run's screens.

    Returns `(judgment_doc, errors)`. A payload whose envelope is wrong, or
    from which no answer survives, is `status: unmeasured` with every error
    listed and `errors` non-empty. Otherwise each malformed answer (or
    uncatalogued entry) is dropped on its own and recorded in `dropped`
    (`status: partial`), the valid answers are kept, and `errors` is empty.
    """
    questions: Dict[str, Judgment] = {j.id: j for j in rubric.judgment}
    if not questions:
        errors = ["rubric has no [[judgment]] entries"]
        return unmeasured_doc(rubric, errors), errors
    screens = {s["id"]: s for s in screen_rows(doc)}
    if not isinstance(payload, dict):
        errors = [f"payload is {type(payload).__name__}, not a JSON object"]
        return unmeasured_doc(rubric, errors), errors
    envelope: List[str] = []
    extra = sorted(set(payload) - {"answers", "uncatalogued"})
    if extra:
        envelope.append(f"unknown top-level keys {extra}")
    raw_answers = payload.get("answers")
    if not isinstance(raw_answers, list):
        envelope.append("answers must be a list")
        raw_answers = []
    raw_unc = payload.get("uncatalogued", [])
    if raw_unc is None:
        raw_unc = []
    if not isinstance(raw_unc, list):
        envelope.append("uncatalogued must be a list")
        raw_unc = []

    dropped: List[Tuple[str, str]] = []  # (what was dropped, why)
    seen: Dict[str, dict] = {}
    duplicated: set = set()
    normalized: List[dict] = []
    for i, a in enumerate(raw_answers):
        if not isinstance(a, dict):
            dropped.append((f"answers[{i}]", f"answers[{i}] is not an object"))
            continue
        jid = str(a.get("id", "")).strip()
        if jid not in questions:
            dropped.append((f"answers[{i}]", f"answers[{i}]: unknown id {jid!r}"))
            continue
        if jid in seen or jid in duplicated:
            # Two answers to one question: neither is the judge's final word, so neither stands.
            seen.pop(jid, None)
            duplicated.add(jid)
            dropped.append((jid, f"{jid}: answered more than once"))
            continue
        q = questions[jid]
        errs: List[str] = []
        bad_keys = sorted(set(a) - {"id", "answer", "evidence", "maps_to", "note"})
        if bad_keys:
            errs.append(f"{jid}: unknown keys {bad_keys}")
        answer = a.get("answer")
        if answer not in ANSWERS:
            errs.append(f"{jid}: answer {answer!r} not in {ANSWERS}")
        evidence = a.get("evidence")
        alias = None
        if isinstance(evidence, str) and evidence not in screens and evidence.endswith(FULL_SUFFIX) and evidence[:-len(FULL_SUFFIX)] in screens:
            alias = {"id": jid, "from": evidence, "to": evidence[:-len(FULL_SUFFIX)]}
            evidence = evidence[:-len(FULL_SUFFIX)]
        if evidence is None or evidence == "":
            if answer != "na":
                errs.append(f"{jid}: evidence is required for a {answer!r} answer")
            evidence = None
        elif not isinstance(evidence, str) or evidence not in screens:
            errs.append(f"{jid}: evidence {evidence!r} is not a screen id of this run")
        elif not _in_scope(screens[evidence], q.screens):
            errs.append(f"{jid}: evidence {evidence!r} is outside the question's scope ({q.screens})")
        maps_to = a.get("maps_to")
        if maps_to is None:
            maps_to = []
        if not isinstance(maps_to, list) or any(not isinstance(m, str) for m in maps_to):
            errs.append(f"{jid}: maps_to must be a list of rule ids or null")
            maps_to = []
        outside = [m for m in maps_to if m not in q.maps_to]
        if outside:
            errs.append(f"{jid}: maps_to {outside} not in the question's list {q.maps_to or '[]'}")
        if maps_to and answer != "no":
            errs.append(f"{jid}: maps_to given on a {answer!r} answer")
        note = a.get("note")
        if note is not None and not isinstance(note, str):
            errs.append(f"{jid}: note must be a string or null")
        if errs:
            dropped.extend((jid, e) for e in errs)
            continue
        if alias:
            normalized.append(alias)
        seen[jid] = {"id": jid, "question": q.question, "answer": answer, "evidence": evidence,
                     "maps_to": list(maps_to), "note": (note or None)}

    unc_out: List[dict] = []
    unc_by_q: Dict[str, int] = {}
    for i, u in enumerate(raw_unc):
        label = f"uncatalogued[{i}]"
        if not isinstance(u, dict):
            dropped.append((label, f"{label} is not an object"))
            continue
        qid = str(u.get("question", "")).strip()
        errs = []
        if qid not in questions:
            errs.append(f"{label}: question {qid!r} is not a checklist id")
        elif qid not in seen:
            errs.append(f"{label}: names {qid}, which has no kept answer")
        elif seen[qid]["answer"] != "no":
            errs.append(f"{label}: names {qid} which was answered {seen[qid]['answer']!r}, not 'no'")
        title = u.get("title")
        if not isinstance(title, str) or not title.strip():
            errs.append(f"{label}: title is required")
        if u.get("severity") not in SEVERITIES:
            errs.append(f"{label}: severity {u.get('severity')!r} not in {SEVERITIES}")
        if u.get("owner") not in OWNERS:
            errs.append(f"{label}: owner {u.get('owner')!r} not in {OWNERS}")
        detail = u.get("detail")
        if not isinstance(detail, str) or not detail.strip():
            errs.append(f"{label}: detail is required")
        proposed = u.get("proposed")
        if not isinstance(proposed, dict) or not str(proposed.get("metric") or "").strip() or proposed.get("threshold") in (None, ""):
            errs.append(f"{label}: proposed.metric and proposed.threshold are required")
        if errs:
            dropped.extend((label, e) for e in errs)
            continue
        unc_by_q[qid] = unc_by_q.get(qid, 0) + 1
        unc_out.append({"question": qid, "title": title.strip(), "severity": u.get("severity"),
                        "detail": detail.strip(), "owner": u.get("owner"),
                        "proposed": {"metric": proposed.get("metric"), "threshold": proposed.get("threshold")}})
    # A `no` that nothing backs (no mapped rule, no surviving uncatalogued entry) is dropped, never kept bare.
    for jid in [j for j, a in seen.items() if a["answer"] == "no" and not a["maps_to"] and not unc_by_q.get(j)]:
        del seen[jid]
        dropped.append((jid, f"{jid}: answered 'no' with no maps_to and no uncatalogued entry"))
    named = {d[0] for d in dropped}
    unanswered = [j.id for j in rubric.judgment if j.id not in seen and j.id not in named]
    if unanswered:
        dropped.append((", ".join(unanswered), f"unanswered: {', '.join(unanswered)}"))

    answers = [seen[j.id] for j in rubric.judgment if j.id in seen]
    if envelope or not answers:
        errors = envelope + [r for _what, r in dropped]
        return unmeasured_doc(rubric, errors), errors
    drops = [{"id": what, "reason": why} for what, why in dropped]
    return {"status": STATUS_PARTIAL if drops else STATUS_OK,
            "reason": f"{len(drops)} malformed {'entry' if len(drops) == 1 else 'entries'} dropped, the rest kept: {drops[0]['reason']}" if drops else None,
            "rubric_version": rubric.version, "judges": 1,
            "answers": answers, "uncatalogued": unc_out, "errors": [], "disagreements": [], "dropped": drops, "normalized": normalized}, []


# ---- merging ------------------------------------------------------------------


def normalise_title(title: object) -> str:
    return _NORM_RE.sub(" ", str(title or "").lower()).strip()


def merge_judges(docs: List[dict]) -> dict:
    """Two or more judgment documents -> one: agreed answers kept, disagreements `not confirmed`.

    Any `unmeasured` input makes the merge `unmeasured` (a judgment that
    failed validation confirms nothing). One input is returned as-is.
    """
    if not docs:
        return {"status": STATUS_UNMEASURED, "reason": "no judge document", "rubric_version": None, "judges": 0,
                "answers": [], "uncatalogued": [], "errors": ["no judge document"], "disagreements": [], "dropped": []}
    bad = [(i + 1, d) for i, d in enumerate(docs) if d.get("status") == STATUS_UNMEASURED]
    if bad:
        errors = [f"judge {i}: {e}" for i, d in bad for e in (d.get("errors") or [d.get("reason") or "unmeasured"])]
        out = _unmeasured_like(docs[0], errors, len(docs))
        out["reason"] = f"judge {bad[0][0]} unmeasured: {bad[0][1].get('reason')}"
        return out
    if len(docs) == 1:
        one = json.loads(json.dumps(docs[0]))
        one["judges"] = 1
        return one
    first = docs[0]
    answers: List[dict] = []
    disagreements: List[dict] = []
    by_judge = [{x.get("id"): x for x in (d.get("answers") or [])} for d in docs]
    for jid in sorted({k for m in by_judge for k in m}):
        got = [m.get(jid) for m in by_judge]
        votes = [g.get("answer") if g else None for g in got]
        template = next(g for g in got if g)
        if all(got) and len(set(votes)) == 1:
            maps: List[str] = []
            for src in got:
                for m in src.get("maps_to") or []:
                    if m not in maps:
                        maps.append(m)
            answers.append({**template, "maps_to": maps})
        else:
            # A question one judge dropped has no second opinion: not confirmed, like a split vote.
            note = f"judges answered {votes}" if not all(got) else "judges answered " + " / ".join(str(v) for v in votes)
            answers.append({**template, "answer": NOT_CONFIRMED, "evidence": None, "maps_to": [], "note": note})
            disagreements.append({"id": jid, "answers": votes})
    # An uncatalogued finding survives when every judge raised it: same title
    # after normalisation, or the same question — two judges who both answered
    # a question `no` with nothing to map it to have raised the same finding
    # whatever they called it, and dropping it would leave an agreed `no`
    # unbacked. The first judge's wording is kept.
    unc: List[dict] = []
    for u in first.get("uncatalogued") or []:
        key = normalise_title(u.get("title"))
        qid = u.get("question")
        if all(any((key and normalise_title(x.get("title")) == key) or (qid and x.get("question") == qid)
                   for x in (d.get("uncatalogued") or [])) for d in docs[1:]):
            unc.append(json.loads(json.dumps(u)))
    dropped = [{**x, "judge": i + 1} for i, d in enumerate(docs) for x in d.get("dropped") or []]
    status = STATUS_NOT_CONFIRMED if disagreements else STATUS_PARTIAL if dropped else STATUS_OK
    reason = (f"{len(disagreements)} question(s) not confirmed across {len(docs)} judges" if disagreements
              else f"{len(dropped)} malformed {'entry' if len(dropped) == 1 else 'entries'} dropped across {len(docs)} judges, the rest kept" if dropped else None)
    return {"status": status, "reason": reason,
            "rubric_version": first.get("rubric_version"), "judges": len(docs),
            "answers": answers, "uncatalogued": unc, "errors": [], "disagreements": disagreements, "dropped": dropped,
            "normalized": [n for d in docs for n in d.get("normalized") or []]}


def _unmeasured_like(template: dict, errors: List[str], judges: int) -> dict:
    return {"status": STATUS_UNMEASURED, "reason": errors[0] if errors else "unmeasured", "rubric_version": template.get("rubric_version"),
            "judges": judges, "answers": [], "uncatalogued": [], "errors": list(errors), "disagreements": [], "dropped": []}


def answer_counts(jdoc: dict) -> Tuple[int, int, int]:
    """`(yes, no, na)` over the kept answers; `not confirmed` answers count in none."""
    votes = [a.get("answer") for a in jdoc.get("answers") or []]
    return votes.count("yes"), votes.count("no"), votes.count("na")
