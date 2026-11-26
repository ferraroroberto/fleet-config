"""Turn the classifier's verdicts on mined signatures into a scored proposal report.

The classifier (a read-only model pass) writes one verdict per signature it
judged; this module joins each verdict to the mined counts and writes the
`proposal_report` input. The evidence line is generated here from counts and
the signature alone, so no transcript text can reach the page or the asks. A
verdict naming a key the mine did not produce is refused, never rendered.
stdlib only.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

#: Fixed across runs, so a proposal's Board ask id stays the same and an answered one is never asked again.
REPORT_ID = "retro-fleet"
#: A signature hit in this many repos belongs in the scaffold, not one repo.
FLEET_WIDE_REPOS = 2
SCAFFOLD_REPO = "project-scaffolding"
VERDICTS = {
    "enforced": "already enforced",
    "unenforced": "mechanical, unenforced",
    "unwired": "check exists, unwired",
    "judgement": "judgement only",
}
#: Repos named in an evidence line, most sessions first; the rest are counted.
EVIDENCE_REPOS = 5
#: Reported, never proposed: the check exists and already fires.
REPORT_ONLY = ("enforced",)
_TEXT = ("title", "summary", "home")
#: Our own words about a check, not quoted output: a long field is a sign transcript text crept in.
MAX_TEXT = 400


def evidence(row: Dict[str, Any]) -> str:
    """Counts and the masked signature; nothing else."""
    shown = list(row["repos"].items())[:EVIDENCE_REPOS]
    rest = len(row["repos"]) - len(shown)
    repos = ", ".join(f"{repo} {n}" for repo, n in shown) + (f" and {rest} more" if rest else "")
    agents = ", ".join(f"{agent} {n}" for agent, n in row["agents"].items())
    return (f"{row['sessions_window']} sessions ({row['count_window']} errors) in "
            f"30 days, {row['sessions']} sessions all time; trend {row['trend']}; "
            f"first seen {row['first_seen']}, last {row['last_seen']}; repos: {repos}; "
            f"agents: {agents}; signature {row['signature']}")


def home_repo(row: Dict[str, Any]) -> str:
    """Two or more real repos make it fleet-wide; otherwise the one repo it happens in."""
    real = [r for r in row["repos"] if r not in ("unknown", "<private>", "<home>")]
    if len(real) >= FLEET_WIDE_REPOS:
        return SCAFFOLD_REPO
    return real[0] if real else "fleet-config"


def build(candidates: Dict[str, Any], verdicts: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
    """The report JSON and the keys reported only; raises ValueError naming every bad verdict."""
    rows = {r["key"]: r for r in [*candidates.get("candidates", []), *candidates.get("enforced", []),
                                   *candidates.get("owner_declined", [])]}
    errors: List[str] = []
    suggestions: List[Dict[str, Any]] = []
    reported: List[str] = []
    for i, verdict in enumerate(verdicts):
        where = f"verdicts[{i}]"
        if not isinstance(verdict, dict):
            errors.append(f"{where}: must be an object")
            continue
        row = rows.get(str(verdict.get("key")))
        kind = verdict.get("verdict")
        if row is None:
            errors.append(f"{where}.key: {verdict.get('key')!r} is not a mined signature")
            continue
        if kind not in VERDICTS:
            errors.append(f"{where}.verdict: one of {sorted(VERDICTS)}, got {kind!r}")
            continue
        if kind in REPORT_ONLY:
            reported.append(row["key"])
            continue
        texts = {k: str(verdict.get(k) or "").strip() for k in _TEXT}
        long = [k for k, v in texts.items() if len(v) > MAX_TEXT]
        if long:
            errors.append(f"{where}: {', '.join(long)} longer than {MAX_TEXT} characters")
        suggestions.append({
            "id": row["key"], "title": texts["title"], "summary": texts["summary"],
            "repo": str(verdict.get("repo") or home_repo(row)), "category": VERDICTS[kind],
            "impact": verdict.get("impact"), "effort": verdict.get("effort"),
            "evidence": evidence(row), "home": texts["home"],
        })
    if errors:
        raise ValueError("; ".join(errors))
    # An enforced signature stays reported only unless a verdict proposed something for it.
    proposed = {item["id"] for item in suggestions}
    reported = list(dict.fromkeys([*reported, *(r["key"] for r in candidates.get("enforced", [])
                                                if r["key"] not in proposed)]))
    return {"id": REPORT_ID, "title": "Repeat agent mistakes: proposed checks",
            "subtitle": subtitle(candidates, len(suggestions), len(reported)),
            "suggestions": suggestions}, reported


def subtitle(candidates: Dict[str, Any], proposed: int, reported: int) -> str:
    logs = ", ".join(f"{agent} {n}" for agent, n in sorted(candidates.get("logs", {}).items()))
    unmeasured = sum(candidates.get("unmeasured", {}).values())
    return (f"{candidates.get('generated')}: logs {logs or 'none'}; {unmeasured} unmeasured, "
            f"{candidates.get('empty', 0)} empty; {candidates.get('flagged_candidates', 0)} recurring "
            f"signatures, {proposed} proposed, {reported} already-enforced reported only")


def load_json(text: str, what: str) -> Any:
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ValueError(f"{what}: not JSON ({exc})") from exc
