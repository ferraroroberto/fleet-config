"""Fleet mode of /design-review — every declared web app, serially, one digest (fleet-config#974).

`run_fleet` walks every `hooks/projects.toml` table with a `webapp_port`,
in file order, **one at a time**: `probe` first, then `measure` (which
never starts, restarts or kills anything — an app that is `NOT_LISTENING`
or `TIMEOUT` yields an `unmeasured` metrics envelope and an `unmeasured`
ledger entry, nothing more), `evaluate`, the ledger + diff, and `render`.
One browser at a time by construction: there is one loop and the walk is
a foreground child. The judgment stage is **skipped** in fleet mode (a
checklist is answered per app, on demand) and the digest says so.

The digest, under `<hooks state>/design-review/_fleet/<UTC stamp>/`:

    fleet-digest.json
      {"schema_version": 1, "stamp", "generated_at", "rubric_version",
       "judgment": "skipped in fleet mode ...",
       "apps":      [{"target", "run_id", "run_dir", "probe", "unmeasured": reason|null, "commit",
                      "live_build", "overall": {score, grade}|null, "failed": [id], "unmeasured_rules": n,
                      "diff": {fixed, regressed, new, unchanged, unmeasured}, "filed": [id],
                      "accepted": [id], "problems": [str]}],
       "spec":      [{"id", "severity", "owner", "title", "standard", "apps": [target]}],   # deduped by id
       "scaffold":  [{... same, "promoted": bool}],                                       # incl. promoted app rules
       "promoted":  [id],       # app-owned rules failing in two or more apps
       "filing":    {"mode": "dry-run" | "filed", "issues": {repo: url | body path}}}
    fleet-digest.html   the same, as one self-contained page (report CSS)
    issue-<repo>.md     each would-be body on a dry run

Promotion: an app-owned rule failing in two or more measured apps is
listed once under `scaffold` (`promoted: true`) and left off every app
issue — the fix belongs in the shared base, not in each app. Spec- and
scaffold-owned rules are deduped by id with the failing apps listed.
`[[design.accepted]]` (`rule` + `reason`) in an app's `.fleet.toml`
removes that app from a rule everywhere, so an accepted rule failing in
one other app is not promoted.

Filing is opt-in (`--file`): the app issues through `filing.file_body` and
the two digest issues (fleet-config's `design-review` issue carries the
spec list, project-scaffolding's the scaffold list) through the same merge.
The default is a dry run: every body is written beside the digest and
nothing reaches GitHub.

stdlib only; spawns nothing itself — `capture.measure_target` owns the walk.
"""
from __future__ import annotations

import datetime as _dt
import html
import json
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import fleet_repo_scan  # noqa: E402
import hooks_state  # noqa: E402

from . import capture, evaluate as evaluate_mod, filing, ledger, plan, report  # noqa: E402
from .report import SEVERITY_ORDER  # noqa: E402
from .rubric import Rubric  # noqa: E402

JUDGMENT_NOTE = "skipped in fleet mode — run /design-review <app> for the bounded checklist"
SPEC_HOME = "fleet-config"
SCAFFOLD_HOME = "project-scaffolding"
DIGEST_JSON = "fleet-digest.json"
DIGEST_HTML = "fleet-digest.html"


def fleet_targets(projects_toml: Optional[Path] = None) -> List[Tuple[str, dict]]:
    """Every projects.toml table with a `webapp_port`, in file order."""
    tables = fleet_repo_scan.fleet_repo_tables(projects_toml)
    return [(name, tbl) for name, tbl in tables.items() if tbl.get("webapp_port")]


def fleet_dir(now: Optional[_dt.datetime] = None) -> Path:
    stamp = (now or capture.utc_now()).astimezone(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = hooks_state.state_dir() / "design-review" / "_fleet" / stamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def review_app(name: str, table: dict, rb: Rubric, specs: Dict[str, Dict[str, str]], devices: List[str],
               projects_toml: Optional[Path] = None, scaffold: str = capture.DEFAULT_SCAFFOLD,
               walk_timeout: float = capture.WALK_TIMEOUT_S, python_override: Optional[str] = None,
               run_dir: Optional[Path] = None) -> dict:
    """probe -> measure -> evaluate -> ledger + diff -> render, for one app. Returns the digest row + the evaluate doc."""
    target = plan.resolve_target(name, projects_toml)
    probe = capture.probe_listening(target.base_url)
    run_dir = run_dir or capture.run_dir_for(target.name)
    metrics = capture.measure_target(target, rb, specs["light"], devices, python_override=python_override,
                                     scaffold=scaffold, run_dir=run_dir, walk_timeout=walk_timeout)
    doc = evaluate_mod.evaluate(metrics, rb, specs)
    doc["source"] = str(run_dir / "metrics.json")
    run_id = ledger.run_id_of(run_dir, doc)
    prev = ledger.previous(target.name, run_id)
    doc["diff"] = ledger.diff(doc, prev)
    (run_dir / "evaluate.json").write_text(json.dumps(doc, indent=2, ensure_ascii=True), encoding="utf-8")
    live = ledger.live_build(target.base_url, table.get("api_version_path")) if probe["status"] == "listening" else None
    entry = ledger.record(run_dir, doc, live)
    report.write_report(doc, run_dir / "report.html")
    unm = doc.get("unmeasured")
    rules = doc.get("rules") or []
    row = {
        "target": target.name, "root": str(target.root) if target.root else None, "run_id": run_id, "run_dir": str(run_dir),
        "probe": probe["status"], "probe_detail": probe.get("detail"),
        "unmeasured": (unm.get("reason") if isinstance(unm, dict) else None),
        "commit": doc.get("commit"), "live_build": entry.get("live_build"),
        "overall": None if unm else dict(doc.get("overall") or {}),
        "failed": sorted(str(r["id"]) for r in rules if r.get("status") == "fail"),
        "unmeasured_rules": sum(1 for r in rules if r.get("status") == "unmeasured"),
        "diff": ledger.diff_counts(doc["diff"]), "previous_run": doc["diff"].get("previous_run"),
        "filed": [], "accepted": [], "problems": [],
    }
    return {"row": row, "doc": doc, "root": target.root}


def _merge_summary(into: Dict[str, dict], s: dict, promoted: bool = False) -> None:
    cur = into.get(s["id"])
    if cur is None:
        cur = {k: s[k] for k in ("id", "severity", "owner", "title", "standard")}
        cur["apps"] = []
        cur["promoted"] = promoted
        into[s["id"]] = cur
    for app in s.get("apps") or []:
        if app not in cur["apps"]:
            cur["apps"].append(app)


def digest(reviews: List[dict], accepted_by_target: Dict[str, Dict[str, dict]], stamp: str,
           rubric_version: object, now: Optional[_dt.datetime] = None) -> dict:
    """Pure: the fleet digest from per-app `{row, doc}` results. Promotion decided here, once."""
    first = {}
    for r in reviews:
        acc = accepted_by_target.get(r["row"]["target"], {})
        first[r["row"]["target"]] = filing.route(r["doc"], acc)
    app_count: Dict[str, int] = {}
    for routed in first.values():
        for s in routed["app"]:
            app_count[s["id"]] = app_count.get(s["id"], 0) + 1
    promoted = {rid for rid, n in app_count.items() if n >= 2}

    spec: Dict[str, dict] = {}
    scaffold: Dict[str, dict] = {}
    apps: List[dict] = []
    for r in reviews:
        row = dict(r["row"])
        acc = accepted_by_target.get(row["target"], {})
        routed = filing.route(r["doc"], acc, promoted)
        for s in routed["spec"]:
            _merge_summary(spec, s)
        for s in routed["scaffold"]:
            _merge_summary(scaffold, s)
        for s in routed["promoted"]:
            _merge_summary(scaffold, s, promoted=True)
        row["filed"] = [s["id"] for s in routed["app"]]
        row["accepted"] = [s["id"] for s in routed["suppressed"]]
        row["promoted"] = [s["id"] for s in routed["promoted"]]
        apps.append(row)

    def ordered(m: Dict[str, dict]) -> List[dict]:
        return sorted(m.values(), key=lambda s: (SEVERITY_ORDER.get(s["severity"], 9), s["id"]))
    return {
        "schema_version": 1, "stamp": stamp, "generated_at": capture.iso(now or capture.utc_now()),
        "rubric_version": rubric_version, "judgment": JUDGMENT_NOTE,
        "apps": apps, "spec": ordered(spec), "scaffold": ordered(scaffold), "promoted": sorted(promoted),
        "filing": {"mode": "dry-run", "issues": {}},
    }


# ---- the digest issues -----------------------------------------------------------


_FLEET_RANK = {"unmeasured": 0, "pass": 1, "fail": 2}


def _digest_statuses(reviews: List[dict]) -> Dict[str, str]:
    """Per rule id across the fleet: fail if any app fails it, else pass if any app measured it, else unmeasured."""
    out: Dict[str, str] = {}
    for r in reviews:
        for rule in r["doc"].get("rules") or []:
            rid, st = str(rule.get("id")), str(rule.get("status"))
            if _FLEET_RANK.get(st, 0) >= _FLEET_RANK.get(out.get(rid, "unmeasured"), 0):
                out[rid] = st if st in _FLEET_RANK else "unmeasured"
    return out


def digest_body(existing: str, findings: List[dict], home: str, dig: dict, statuses: Dict[str, str], today: str) -> str:
    header = (f"Surfaced by `/design-review fleet` for the {'design system' if home == SPEC_HOME else 'shared base'} "
              f"(`{home}`), kept up to date across sweeps. Rubric `design.rubric.toml` v{dig.get('rubric_version')}. "
              "Each line is a rule failing in the listed apps; an app-owned rule failing in two or more apps is promoted "
              "here (`app → scaffold`) and left off the app issues. Rule ids, severities and standards only — no "
              "screenshot or captured page text.")
    measured = [a["target"] for a in dig["apps"] if not a.get("unmeasured")]
    unmeasured = [f"{a['target']} ({a.get('unmeasured')})" for a in dig["apps"] if a.get("unmeasured")]
    line = (f"- {today} sweep {dig['stamp']}: {len(findings)} listed · measured {', '.join(measured) or 'none'}"
            + (f" · unmeasured {', '.join(unmeasured)}" if unmeasured else "") + f" · {dig['judgment']}")
    stamp = f"{today} sweep {dig['stamp']}"
    return filing.merge_body(existing, findings, [], [], statuses, line, header, stamp, dig.get("rubric_version"))


# ---- rendering -------------------------------------------------------------------


def _esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def render_digest(dig: dict) -> str:
    rows = []
    for a in dig.get("apps") or []:
        o = a.get("overall") or {}
        d = a.get("diff") or {}
        state = a.get("unmeasured") or "measured"
        grade = f'<span class="grade grade-sm {report._grade_class(o.get("grade"))}">{_esc(o.get("grade"))}</span> {_esc(report._compact(o.get("score")))}' if o else f'<span class="badge badge-unm">{_esc(state)}</span>'
        rows.append(f'<tr><th scope="row">{_esc(a.get("target"))}</th><td>{_esc(a.get("probe"))}</td><td>{grade}</td>'
                    f'<td class="ids">{_esc(", ".join(a.get("filed") or []) or "—")}</td>'
                    f'<td class="ids">{_esc(", ".join(a.get("promoted") or []) or "—")}</td>'
                    f'<td class="ids">+{d.get("new", 0)} new · {d.get("fixed", 0)} fixed · {d.get("regressed", 0)} regressed · {d.get("unmeasured", 0)} unmeasured</td>'
                    f'<td class="ids">{_esc(str(a.get("commit") or "none")[:7])} / {_esc(str(a.get("live_build") or "unknown")[:7])}</td></tr>')
    apps_table = ('<div class="tbl"><table><thead><tr><th>App</th><th>Probe</th><th>Grade</th><th>App-owned (filed)</th>'
                  '<th>Promoted</th><th>Since previous run</th><th>commit / live</th></tr></thead><tbody>'
                  + "".join(rows) + "</tbody></table></div>")

    def lst(items: List[dict], empty: str) -> str:
        if not items:
            return f'<p class="muted">{_esc(empty)}</p>'
        lis = "".join(
            f'<li><span class="badge sev sev-{_esc(s.get("severity"))}">{_esc(s.get("severity"))}</span> <code class="rid">{_esc(s["id"])}</code> '
            f'{_esc(s.get("title"))}{" <span class=\"badge badge-unm\">promoted</span>" if s.get("promoted") else ""} '
            f'<span class="muted small">— {_esc(", ".join(s.get("apps") or []))} · {_esc(s.get("standard"))}</span></li>' for s in items)
        return f'<ul class="plain">{lis}</ul>'
    filing_info = dig.get("filing") or {}
    issues = filing_info.get("issues") or {}
    fil = "".join(f"<li><code>{_esc(k)}</code> — {_esc(v)}</li>" for k, v in issues.items()) or "<li>nothing filed</li>"
    body = [
        f"<h1>Design review — fleet sweep {_esc(dig.get('stamp'))}</h1>",
        f'<p class="muted">rubric v{_esc(dig.get("rubric_version"))} &middot; generated {_esc(dig.get("generated_at"))} &middot; judgment: {_esc(dig.get("judgment"))}</p>',
        report._section("apps", "Apps", apps_table, "one app at a time; an app not listening is unmeasured, never restarted"),
        report._section("spec", f"Spec-owned — {SPEC_HOME}", lst(dig.get("spec") or [], "No spec-owned rule fails in any measured app.")),
        report._section("scaffold", f"Scaffold-owned and promoted — {SCAFFOLD_HOME}", lst(dig.get("scaffold") or [], "No scaffold-owned rule fails and nothing was promoted.")),
        report._section("filing", f"Filing ({_esc(filing_info.get('mode'))})", f"<ul>{fil}</ul>"),
        f'<footer>/design-review fleet &middot; rubric v{_esc(dig.get("rubric_version"))} &middot; {_esc(dig.get("generated_at"))}</footer>',
    ]
    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"<title>Design review — fleet {_esc(dig.get('stamp'))}</title>\n<style>{report.CSS}</style>\n</head>\n<body>\n"
            + "\n".join(body) + "\n</body>\n</html>\n")


# ---- the run ---------------------------------------------------------------------

Reviewer = Callable[[str, dict], dict]


def run_fleet(rb: Rubric, specs: Dict[str, Dict[str, str]], devices: List[str], projects_toml: Optional[Path] = None,
              file_issues: bool = False, out_dir: Optional[Path] = None, reviewer: Optional[Reviewer] = None,
              fetch: Optional[filing.Fetch] = None, upsert: Optional[filing.Upsert] = None,
              today: Optional[str] = None, **review_kw: object) -> dict:
    """The whole sweep. `reviewer` (tests) replaces `review_app`; `fetch` / `upsert` replace the `gh` legs."""
    out_dir = out_dir or fleet_dir()
    stamp = out_dir.name
    targets = fleet_targets(projects_toml)
    reviews: List[dict] = []
    accepted_by_target: Dict[str, Dict[str, dict]] = {}
    problems_by_target: Dict[str, List[str]] = {}
    for name, table in targets:
        r = (reviewer or (lambda n, t: review_app(n, t, rb, specs, devices, projects_toml, **review_kw)))(name, table)  # type: ignore[arg-type]
        acc, probs = filing.load_accepted_rules(r.get("root"))
        accepted_by_target[r["row"]["target"]] = acc
        problems_by_target[r["row"]["target"]] = probs
        reviews.append(r)
    dig = digest(reviews, accepted_by_target, stamp, rb.version)
    for a in dig["apps"]:
        a["problems"] = problems_by_target.get(a["target"], [])
    dig["filing"]["mode"] = "filed" if file_issues else "dry-run"

    tables = dict(fleet_repo_scan.fleet_repo_tables(projects_toml))
    fetch = fetch or filing.audit_issue.get_managed
    upsert = upsert or (lambda r, k, t, b, l: filing.audit_issue.upsert_issue(r, k, t, b, l))
    today = today or _dt.date.today().isoformat()
    promoted = set(dig["promoted"])

    def land(repo: Optional[str], key: str, body: str) -> None:
        path = out_dir / f"issue-{key}.md"
        path.write_text(body, encoding="utf-8")
        if not repo:
            dig["filing"]["issues"][key] = f"no GitHub remote resolved — body at {path}"
        elif file_issues:
            dig["filing"]["issues"][repo] = upsert(repo, filing.KIND, filing.TITLE, body, filing.LABEL)
        else:
            dig["filing"]["issues"][repo] = str(path)

    for r in reviews:
        row = next(a for a in dig["apps"] if a["target"] == r["row"]["target"])
        if row.get("unmeasured"):
            continue  # nothing measured: no finding to file, no line to carry
        repo = filing.repo_slug(r.get("root"))
        existing = fetch(repo, filing.KIND) if repo else {"number": None, "body": ""}
        fb = filing.file_body(r["doc"], row["run_id"], r.get("root"), str(existing.get("body") or ""), today,
                              accepted=accepted_by_target.get(row["target"], {}),
                              problems=problems_by_target.get(row["target"], []), promoted=promoted)
        row["issue"] = existing.get("number")
        row["changed"] = fb["changed"]
        land(repo, row["target"], fb["body"])

    statuses = _digest_statuses(reviews)
    for home, key in ((SPEC_HOME, "spec"), (SCAFFOLD_HOME, "scaffold")):
        findings = dig[key]
        root = Path(str(tables.get(home, {}).get("cwd_prefix", ""))) if tables.get(home) else None
        repo = filing.repo_slug(root if root and root.is_dir() else None)
        existing = fetch(repo, filing.KIND) if repo else {"number": None, "body": ""}
        land(repo, home, digest_body(str(existing.get("body") or ""), findings, home, dig, statuses, today))

    (out_dir / DIGEST_JSON).write_text(json.dumps(dig, indent=2, ensure_ascii=True), encoding="utf-8")
    (out_dir / DIGEST_HTML).write_text(render_digest(dig), encoding="utf-8")
    dig["out_dir"] = str(out_dir)
    return dig
