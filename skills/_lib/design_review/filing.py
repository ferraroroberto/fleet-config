"""Stage 6 of /design-review — findings -> one managed GitHub issue per repo (fleet-config#974).

Opt-in (`file`), never the default: the report is the product, the issue is
the hand-off. One `design-review` issue per repo through `audit_issue.py`
(the same marker identity and collapse-strays mechanics `/design-sync`
uses for `design-drift`), title `design-review: rendered findings`, label
`design-review`.

What goes in — and what never does. A finding line carries the rubric id,
severity, owner, title, the screen count and the worst screen id, and the
`standard` the rule cites:

    - [ ] **TOUCH-01** (P0, app) — <title> — 12 screens, worst iphone-light-board · <standard>

An uncatalogued judgment finding (status `ok` only) carries its question
id, severity, owner and title, keyed by `question` + normalised title.
Never a screenshot path, captured page text, an `evidence.items` label, or
a judge's `note` / `detail`.

Routing (per evaluate document):

  * **app-owned** failing rules -> the app repo's issue;
  * **spec- / scaffold-owned** failing rules are never filed on the app: they
    go to the combined fleet digest (`fleet.py`), deduped by rule id and
    listing which apps, and from there to fleet-config's / project-scaffolding's
    own `design-review` issue in fleet mode;
  * a `[[design.accepted]]` entry with a `rule` id and a `reason` in the
    target's `.fleet.toml` suppresses that rule for that repo (listed under
    `## Accepted`, never under Findings); a declaration that names no
    failing rule this run is reported, not silently kept;
  * in fleet mode an app-owned rule failing in two or more apps is promoted
    to the scaffold list and left off every app issue (`fleet.py`).

Merge rules (the `design-drift` ones, by rubric id instead of file + role):
an existing line keeps its checkbox state (`- [x]` verbatim) and is
updated in place; a listed rule that now **passes** keeps its line and its
checkbox and gains `_(fixed — passes since <date> @ <sha7>)_` (never
auto-ticked — the fleet's audit issues leave ticking to a human); a listed
rule `unmeasured` this run is `_(carried — unmeasured this run)_`; a rule no
longer in the rubric is `_(carried — not in rubric v<x>)_`. A dated bullet
is appended to `## Review run log` on every filing.

Public surface:

    load_accepted_rules(root)                    -> ({rule id: {reason, record}}, [problem])
    route(doc, accepted)                         -> {"app", "spec", "scaffold", "suppressed", "unmatched"}
    finding_line(summary, ticked=False)          -> str
    parse_body(body)                             -> {"intro", "sections": {name: [line]}, "order": [name]}
    merge_body(existing, findings, unc, accepted, log_line, header) -> str
    changed_sections(a, b)                       -> bool     (Findings / Uncatalogued / Accepted differ)
    repo_slug(root)                              -> "owner/name" | None
    file_run(run_dir, repo, dry_run, ...)        -> result dict (the CLI prints it)

stdlib + `audit_issue` (which owns the `gh` calls).
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import audit_issue  # noqa: E402
import git_run  # noqa: E402

from .judgment import STATUS_OK, normalise_title  # noqa: E402
from .report import SEVERITY_ORDER, worst_screen  # noqa: E402

KIND = "design-review"
LABEL = "design-review"
TITLE = audit_issue.DESIGN_REVIEW_TITLE
SEC_FINDINGS = "Findings"
SEC_UNCATALOGUED = "Uncatalogued (judgment)"
SEC_ACCEPTED = "Accepted"
SEC_LOG = "Review run log"
ISSUE_BODY_NAME = "issue-body.md"

_LINE_RE = re.compile(r"^- \[( |x|X)\] \*\*([A-Za-z0-9-]+)\*\*\s*(.*)$")
_TAG_RE = re.compile(r"\s*_\((fixed|carried)[^)]*\)_\s*$")
_FIXED_RE = re.compile(r"_\(fixed — passes since [^)]*\)_")
_HEADER_RE = re.compile(r"^## (.+?)\s*$")
_REMOTE_RE = re.compile(r"(?:github\.com[:/])([^/]+)/([^/\s]+?)(?:\.git)?/?$")


def intro(rubric_version: object, target: object) -> str:
    return (f"Surfaced by `/design-review` for `{target}`, kept up to date across runs. Rubric `design.rubric.toml` "
            f"v{rubric_version}; measured in real browsers by `skills/_lib/design_review/` (deterministic). Rule ids, "
            "severities, screen ids and standards only — no screenshot or captured page text is ever pasted here; the "
            "report stays in the run directory. A rule that passes again is marked fixed in place, never ticked by a machine.")


# ---- accepted rules ------------------------------------------------------------


def load_accepted_rules(root: Optional[Path]) -> Tuple[Dict[str, dict], List[str]]:
    """`[[design.accepted]]` entries carrying a `rule` id, from the target's `.fleet.toml`.

    `{rule id: {"reason", "record"}}` plus one problem string per unusable
    entry. Entries with a `check` key are `design_lint`'s and are skipped;
    a missing file or `design` table is `({}, [])`.
    """
    if root is None:
        return {}, []
    path = Path(root) / ".fleet.toml"
    if not path.is_file():
        return {}, []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return {}, [f"could not read .fleet.toml, so no accepted rule applies: {exc}"]
    design = data.get("design")
    if not isinstance(design, dict):
        return {}, []
    raw = design.get("accepted")
    if raw is None:
        return {}, []
    if not isinstance(raw, list):
        return {}, ["[design] must hold an array of [[design.accepted]] tables"]
    out: Dict[str, dict] = {}
    problems: List[str] = []
    for index, entry in enumerate(raw, start=1):
        label = f"[[design.accepted]] #{index}"
        if not isinstance(entry, dict) or "check" in entry or "rule" not in entry:
            continue
        rule, reason = entry.get("rule"), entry.get("reason")
        if not isinstance(rule, str) or not rule.strip():
            problems.append(f"{label} ignored: `rule` must be a rubric rule id")
            continue
        if not isinstance(reason, str) or not reason.strip():
            problems.append(f"{label} ignored: `reason` is required for rule {rule}")
            continue
        record = entry.get("record")
        out[rule.strip()] = {"reason": reason.strip(), "record": str(record).strip() if isinstance(record, str) and record.strip() else None}
    return out, problems


# ---- routing -------------------------------------------------------------------


def summary_of(rule: dict, target: Optional[str] = None) -> dict:
    """The filing-safe projection of one failing rule: ids, counts, the worst screen id, the standard."""
    ev = [e for e in rule.get("evidence") or [] if isinstance(e, dict)]
    worst = worst_screen(rule)
    return {"id": str(rule.get("id")), "severity": str(rule.get("severity")), "owner": str(rule.get("owner")),
            "title": str(rule.get("title") or ""), "standard": str(rule.get("standard") or ""),
            "screens": len(ev), "worst": str(worst.get("screen")) if worst else None,
            "apps": [target] if target else []}


def route(doc: dict, accepted: Dict[str, dict], promoted: Optional[set] = None) -> Dict[str, list]:
    """Failing rules by destination.

    `suppressed` = accepted in `.fleet.toml`; `unmatched` = accepted ids that
    fail nowhere this run; `promoted` = app-owned ids fleet mode lifted to the
    scaffold list (left off this app's issue). Accepted wins over promoted:
    a repo that accepted a rule is not listed under it anywhere.
    """
    out: Dict[str, list] = {"app": [], "spec": [], "scaffold": [], "suppressed": [], "unmatched": [], "promoted": []}
    failing = {str(r.get("id")): r for r in doc.get("rules") or [] if isinstance(r, dict) and r.get("status") == "fail"}
    for rid in accepted:
        if rid not in failing:
            out["unmatched"].append(rid)
    for rid, rule in failing.items():
        s = summary_of(rule, str(doc.get("target") or ""))
        if rid in accepted:
            out["suppressed"].append({**s, "reason": accepted[rid]["reason"], "record": accepted[rid]["record"]})
            continue
        owner = s["owner"] if s["owner"] in ("spec", "scaffold") else "app"
        if owner == "app" and promoted and rid in promoted:
            out["promoted"].append(s)
            continue
        out[owner].append(s)
    for key in ("app", "spec", "scaffold", "suppressed", "promoted"):
        out[key].sort(key=lambda s: (SEVERITY_ORDER.get(s["severity"], 9), s["id"]))
    return out


def uncatalogued_of(doc: dict) -> List[dict]:
    """Uncatalogued judgment findings worth filing — status `ok` only; question, title, severity, owner."""
    j = doc.get("judgment")
    if not isinstance(j, dict) or j.get("status") != STATUS_OK:
        return []
    out = []
    for u in j.get("uncatalogued") or []:
        if isinstance(u, dict) and u.get("title"):
            out.append({"question": str(u.get("question") or ""), "title": str(u["title"]).strip(),
                        "title_norm": normalise_title(u["title"]), "severity": str(u.get("severity") or ""),
                        "owner": str(u.get("owner") or "")})
    return out


# ---- lines ---------------------------------------------------------------------


def _detail(s: dict) -> str:
    apps = list(s.get("apps") or [])
    if s.get("promoted") or len(apps) > 1:
        return f"fails in {len(apps)} apps: {', '.join(apps)}" if apps else "fails in several apps"
    if apps and s.get("owner") in ("spec", "scaffold"):
        return f"apps: {', '.join(apps)}"
    n = int(s.get("screens") or 0)
    worst = s.get("worst")
    return f"{n} screen{'s' if n != 1 else ''}" + (f", worst {worst}" if worst else "")


def finding_line(s: dict, ticked: bool = False) -> str:
    owner = s.get("owner") or "app"
    if s.get("promoted"):
        owner = "app → scaffold"
    std = f" · {s['standard']}" if s.get("standard") else ""
    return f"- [{'x' if ticked else ' '}] **{s['id']}** ({s.get('severity')}, {owner}) — {s.get('title')} — {_detail(s)}{std}"


def uncatalogued_line(u: dict, ticked: bool = False) -> str:
    return f"- [{'x' if ticked else ' '}] **{u['question']}** ({u.get('severity')}, {u.get('owner')}) — {u['title']}"


def accepted_line(s: dict) -> str:
    rec = f" ({s['record']})" if s.get("record") else ""
    return f"- **{s['id']}** ({s.get('severity')}, {s.get('owner')}) — {s.get('title')} — accepted: {s.get('reason')}{rec}"


def parse_line(line: str) -> Optional[dict]:
    """`{"id", "ticked", "rest", "tag"}` for a checklist line, else None."""
    m = _LINE_RE.match(line.strip())
    if not m:
        return None
    rest = m.group(3)
    tag = _TAG_RE.search(rest)
    return {"id": m.group(2), "ticked": m.group(1).lower() == "x", "rest": _TAG_RE.sub("", rest).strip(),
            "tag": tag.group(0).strip() if tag else None}


# ---- body parse / merge --------------------------------------------------------


def parse_body(body: str) -> dict:
    """Split a body into its intro and `## ` sections (marker lines dropped)."""
    text = (body or "").replace("\r\n", "\n").replace("\r", "\n")
    text = audit_issue._MARKER_RE.sub("", text)
    intro_lines: List[str] = []
    sections: Dict[str, List[str]] = {}
    order: List[str] = []
    current: Optional[str] = None
    for line in text.split("\n"):
        h = _HEADER_RE.match(line)
        if h:
            current = h.group(1).strip()
            sections.setdefault(current, [])
            if current not in order:
                order.append(current)
            continue
        (sections[current] if current else intro_lines).append(line)
    return {"intro": "\n".join(intro_lines).strip(), "sections": sections, "order": order}


def _existing_lines(sections: Dict[str, List[str]], name: str) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for line in sections.get(name) or []:
        p = parse_line(line)
        if p:
            out.setdefault(p["id"], {**p, "line": line.strip()})
    return out


def _unc_key(question: str, title: str) -> str:
    return f"{question}|{normalise_title(title)}"


CARRIED_TAGS = {
    "unmeasured": "_(carried — unmeasured this run)_",
    "accepted": "_(carried — accepted in .fleet.toml this run)_",
    "promoted": "_(carried — promoted to the fleet scaffold list this run)_",
    "fail": "_(carried — not filed here this run)_",
}


def merge_findings(existing: Dict[str, dict], current: List[dict], statuses: Dict[str, str],
                   stamp: str, rubric_version: object) -> List[str]:
    """Current failing lines (checkbox preserved) + carried lines for listed ids that no longer fail here.

    `statuses` maps every rubric id to `pass | fail | unmeasured`, or to
    `accepted` / `promoted` when the filing routed it elsewhere this run.
    """
    lines: List[str] = []
    seen = set()
    for s in current:
        old = existing.get(s["id"])
        lines.append(finding_line(s, ticked=bool(old and old["ticked"])))
        seen.add(s["id"])
    for rid, old in existing.items():
        if rid in seen:
            continue
        status = statuses.get(rid)
        base = f"- [{'x' if old['ticked'] else ' '}] **{rid}** {old['rest']}"
        if status == "pass":
            tag = old["tag"] if old.get("tag") and _FIXED_RE.search(old["tag"]) else f"_(fixed — passes since {stamp})_"
        elif status is None:
            tag = f"_(carried — not in rubric v{rubric_version})_"
        else:
            tag = CARRIED_TAGS.get(status, CARRIED_TAGS["fail"])
        lines.append(f"{base} {tag}")
    return lines


def merge_uncatalogued(existing_lines: List[str], current: List[dict]) -> List[str]:
    old: Dict[str, dict] = {}
    for line in existing_lines:
        p = parse_line(line)
        if p:
            title = p["rest"].split(" — ", 1)[1] if " — " in p["rest"] else p["rest"]
            old.setdefault(_unc_key(p["id"], title), {**p, "line": line.strip()})
    lines: List[str] = []
    seen = set()
    for u in current:
        key = _unc_key(u["question"], u["title"])
        prev = old.get(key)
        lines.append(uncatalogued_line(u, ticked=bool(prev and prev["ticked"])))
        seen.add(key)
    for key, p in old.items():
        if key in seen:
            continue
        tag = p["tag"] if p.get("tag") else "_(carried — not raised this run)_"
        lines.append(f"- [{'x' if p['ticked'] else ' '}] **{p['id']}** {p['rest']} {tag}")
    return lines


def _section(name: str, lines: List[str], empty: str) -> str:
    body = "\n".join(lines) if lines else empty
    return f"## {name}\n\n{body}"


def merge_body(existing: str, findings: List[dict], uncatalogued: List[dict], accepted: List[dict],
               statuses: Dict[str, str], log_line: str, header: str, stamp: str, rubric_version: object) -> str:
    """The whole issue body for this run, merged over the existing one (empty string = fresh)."""
    parsed = parse_body(existing)
    sec = parsed["sections"]
    f_lines = merge_findings(_existing_lines(sec, SEC_FINDINGS), findings, statuses, stamp, rubric_version)
    u_lines = merge_uncatalogued(sec.get(SEC_UNCATALOGUED) or [], uncatalogued)
    a_lines = [accepted_line(s) for s in accepted]
    log = [l for l in (sec.get(SEC_LOG) or []) if l.strip()] + [log_line]
    parts = [header, _section(SEC_FINDINGS, f_lines, "No failing rule owned by this repo."),
             _section(SEC_UNCATALOGUED, u_lines, "Nothing outside the rubric (or no confirmed judgment this run)."),
             _section(SEC_ACCEPTED, a_lines, "None declared."), _section(SEC_LOG, log, "")]
    return "\n\n".join(parts).rstrip() + "\n"


def changed_sections(a: str, b: str) -> bool:
    """True when Findings / Uncatalogued / Accepted differ — the run log alone is not a change."""
    pa, pb = parse_body(a), parse_body(b)

    def pick(p: dict) -> List[List[str]]:
        return [[l.strip() for l in (p["sections"].get(n) or []) if l.strip()] for n in (SEC_FINDINGS, SEC_UNCATALOGUED, SEC_ACCEPTED)]
    return pick(pa) != pick(pb)


def log_line(doc: dict, run_id: str, today: str, routed: Dict[str, list], filed: int) -> str:
    overall = doc.get("overall") or {}
    d = doc.get("diff") if isinstance(doc.get("diff"), dict) else {}
    counts = " · ".join(f"{len(d.get(k) or [])} {k}" for k in ("fixed", "regressed", "new")) if d else "no diff recorded"
    extra = []
    if routed.get("suppressed"):
        extra.append("accepted: " + ", ".join(s["id"] for s in routed["suppressed"]))
    if routed.get("spec") or routed.get("scaffold"):
        extra.append("owned elsewhere: " + ", ".join(s["id"] for s in routed.get("spec", []) + routed.get("scaffold", [])))
    if routed.get("promoted"):
        extra.append("promoted to the fleet scaffold list: " + ", ".join(s["id"] for s in routed["promoted"]))
    if routed.get("unmatched"):
        extra.append("accepted but not failing: " + ", ".join(routed["unmatched"]))
    tail = f"; {'; '.join(extra)}" if extra else ""
    return (f"- {today} @ {str(doc.get('commit') or 'none')[:7]} run {run_id}: {overall.get('grade')} "
            f"{overall.get('score')}/100 · {filed} filed · {counts}{tail}")


# ---- repo resolution -----------------------------------------------------------


def slug_from_url(url: str) -> Optional[str]:
    m = _REMOTE_RE.search(str(url or "").strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def repo_slug(root: Optional[Path]) -> Optional[str]:
    """`owner/name` from the target checkout's `origin` remote, or None."""
    if not root:
        return None
    proc = git_run.run_git(["-C", str(root), "remote", "get-url", "origin"])
    if proc.returncode != 0:
        return None
    return slug_from_url(proc.stdout)


# ---- the filing ----------------------------------------------------------------

Fetch = Callable[[str, str], dict]
Upsert = Callable[[str, str, str, str, Optional[str]], str]


def file_body(doc: dict, run_id: str, root: Optional[Path], existing: str, today: Optional[str] = None,
              accepted: Optional[Dict[str, dict]] = None, problems: Optional[List[str]] = None,
              promoted: Optional[set] = None) -> dict:
    """Pure: the merged body for one evaluate document. `{body, routed, uncatalogued, problems, changed}`."""
    if accepted is None:
        accepted, problems = load_accepted_rules(root)
    routed = route(doc, accepted, promoted)
    unc = uncatalogued_of(doc)
    statuses = {str(r.get("id")): str(r.get("status")) for r in doc.get("rules") or [] if isinstance(r, dict)}
    for s in routed["suppressed"]:
        statuses[s["id"]] = "accepted"
    for s in routed["promoted"]:
        statuses[s["id"]] = "promoted"
    today = today or _dt.date.today().isoformat()
    stamp = f"{today} @ {str(doc.get('commit') or 'none')[:7]}"
    line = log_line(doc, run_id, today, routed, len(routed["app"]))
    body = merge_body(existing, routed["app"], unc, routed["suppressed"], statuses, line,
                      intro(doc.get("rubric_version"), doc.get("target")), stamp, doc.get("rubric_version"))
    return {"body": body, "routed": routed, "uncatalogued": unc, "problems": list(problems or []),
            "changed": changed_sections(existing, body) if existing else True}


def file_run(run_dir: Path, repo: Optional[str], dry_run: bool, root: Optional[Path] = None,
             fetch: Optional[Fetch] = None, upsert: Optional[Upsert] = None, today: Optional[str] = None) -> dict:
    """File (or dry-run) one run directory's app-owned findings. Never touches an issue on `dry_run`."""
    run_dir = Path(run_dir)
    path = run_dir / "evaluate.json"
    if not path.is_file():
        raise ValueError(f"no evaluate.json in {run_dir}: run `evaluate` first")
    doc = json.loads(path.read_text(encoding="utf-8"))
    run_id = Path(str(doc.get("run_dir") or run_dir)).name
    fetch = fetch or audit_issue.get_managed
    upsert = upsert or (lambda r, k, t, b, l: audit_issue.upsert_issue(r, k, t, b, l))
    if not repo:
        raise ValueError("no --repo given and the target's origin remote is not a GitHub repo")
    existing = fetch(repo, KIND)
    out = file_body(doc, run_id, root, str(existing.get("body") or ""), today)
    out.update({"repo": repo, "issue": existing.get("number"), "duplicates": existing.get("duplicates") or [],
                "dry_run": dry_run, "url": None, "run_id": run_id, "target": doc.get("target")})
    body_path = run_dir / ISSUE_BODY_NAME
    body_path.write_text(out["body"], encoding="utf-8")
    out["body_path"] = str(body_path)
    if not dry_run:
        out["url"] = upsert(repo, KIND, TITLE, out["body"], LABEL)
    return out
