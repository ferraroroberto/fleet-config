"""The `design_review` command line — `probe`, `measure`, `evaluate`, `judge-prompt`, `judge-merge`, `ledger`, `render`, `file`, `fleet`.

    <python> C:/Users/rober/.claude/skills/_lib/design_review probe <repo>
        [--url URL] [--projects-toml FILE]
    <python> C:/Users/rober/.claude/skills/_lib/design_review measure <repo>
        [--url URL] [--devices iphone,desktop,android] [--python PATH]
        [--scaffold DIR] [--rubric FILE] [--spec FILE] [--projects-toml FILE]
        [--run-dir DIR] [--walk-timeout S]
    <python> C:/Users/rober/.claude/skills/_lib/design_review evaluate <metrics.json>
        [--out FILE] [--rubric FILE] [--spec FILE] [--spec-dark FILE]
    <python> C:/Users/rober/.claude/skills/_lib/design_review judge-prompt <run_dir>
        [--rubric FILE]
    <python> C:/Users/rober/.claude/skills/_lib/design_review judge-merge <run_dir> <answers.json> [<answers2.json> ...]
        [--rubric FILE] [--spec FILE] [--spec-dark FILE]
    <python> C:/Users/rober/.claude/skills/_lib/design_review ledger <run_dir>
        [--no-live] [--projects-toml FILE] [--rubric FILE] [--spec FILE] [--spec-dark FILE]
    <python> C:/Users/rober/.claude/skills/_lib/design_review render <evaluate.json|metrics.json>
        [--out FILE] [--rubric FILE] [--spec FILE] [--spec-dark FILE]
    <python> C:/Users/rober/.claude/skills/_lib/design_review file <run_dir>
        [--file] [--repo OWNER/NAME] [--projects-toml FILE]
    <python> C:/Users/rober/.claude/skills/_lib/design_review fleet
        [--devices iphone,desktop,android] [--file | --dry-run] [--projects-toml FILE] [--out-dir DIR] ...

`probe`, `measure`, `judge-prompt`, `judge-merge`, `ledger`, `render`,
`file` and `fleet` print KEY=VALUE lines (the `ux_surface` CLI style) so a
skill can read the result back without parsing JSON:

    probe         TARGET= BASE_URL= ROOT= CLAUDE_MD= PROBE=listening|NOT_LISTENING|TIMEOUT|BAD_URL DETAIL=
    measure       TARGET= BASE_URL= COMMIT= INTERPRETER= RUN_DIR= METRICS= SCREENS=<ok>/<total> UNMEASURED=<reason>|none
    judge-prompt  PROMPT=<run_dir>/judge-prompt.md SCREENS=<n> QUESTIONS=<n>
    judge-merge   JUDGMENT=ok|unmeasured|not_confirmed ANSWERS=<yes>/<no>/<na> UNCATALOGUED=<n> ERRORS=<n>
                  [RUBRIC_MISMATCH=evaluate:<v> judgment:<v>] EVALUATE=
    ledger        LEDGER=<ledger.json> RUN_ID= PREVIOUS=<run_id>|none FIXED=<n> [ids] REGRESSED=<n> [ids] NEW=<n> [ids]
                  UNCHANGED=<n> UNMEASURED=<n> RUBRIC_CHANGED=<from>-><to>|none LIVE_BUILD=<sha>|unknown COMMIT= EVALUATE=
    render        REPORT= EVALUATE= TARGET= COMMIT= RUBRIC= GRADE= SCORE= FAILED=<n>/<total>
                  UNMEASURED=<n rules>|none CATEGORIES=<cat:grade,...> MOCKUPS=<ids>|none JUDGMENT=<status>|none
    file          FILE=dry-run|filed REPO= ISSUE=<n>|none CHANGED=yes|no FILED=<n> [ids] UNCATALOGUED=<n>
                  ACCEPTED=<ids>|none SPEC=<ids>|none SCAFFOLD=<ids>|none [PROBLEM=...] BODY=<issue-body.md> URL=<url>|none
    fleet         FLEET_DIR= APPS=<n> APP=<name> probe= unmeasured= grade= score= failed= fixed= regressed= new= run=
                  MEASURED=<n> UNMEASURED_APPS=<name:reason,...>|none SPEC= SCAFFOLD= PROMOTED= JUDGMENT=skipped
                  FILING=dry-run|filed [ISSUE=<repo> <url|body path>] DIGEST= DIGEST_HTML=

`ledger` records the run (`ledger.py`) and writes `diff` into
`evaluate.json`, so it runs after `judge-merge` and before `render`. `file`
is a dry run unless `--file` is passed: the merged body is always written
as `<run_dir>/issue-body.md`, and only `--file` upserts it through
`audit_issue.py`. `fleet` sweeps every declared web app serially (one
browser at a time, an app not listening is `unmeasured`, nothing is ever
started), skips the judgment stage, and writes `fleet-digest.json` +
`fleet-digest.html` under `<state>/design-review/_fleet/<stamp>/`; filing
there is likewise opt-in with `--file`.

`measure` exits 0 whenever a `metrics.json` was written — an `unmeasured` run
is a result, not a crash; exit 2 is reserved for a target that cannot be
resolved or a rubric that does not validate. `probe` exits 0 with its verdict
on the `PROBE=` line (the skill's pre-flight; it never starts anything).
`evaluate` prints the JSON document on stdout and exits 0; with `--out` it
writes the document there and prints the `render`-style summary lines
instead. `judge-prompt` writes the deterministic judge prompt (#973) beside
`metrics.json`; `judge-merge` validates one or more judge answer files,
merges them, and writes the `judgment` document into the run's
`evaluate.json` (evaluated from `metrics.json` first if absent) — an
`unmeasured` judgment is a result, exit 0, never a partial acceptance. The
Python side spawns no agent: the skill does. `render` accepts either an
evaluate document (with or without `judgment`) or a raw `metrics.json`
(evaluated first, and the evaluate document written beside the report so
#974 can diff runs), and writes `report.html` into the run directory by
default.

Deliberately the only module that knows argparse and file locations —
`plan`/`capture`/`rubric`/`evaluate`/`judgment`/`report` are importable and
unit-tested without it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import fleet_repo_scan  # noqa: E402
from utf8_stdio import ensure_utf8_stdio  # noqa: E402

from . import capture, evaluate, filing, fleet, judgment, ledger, measure, plan, report, rubric as rubric_mod  # noqa: E402

ensure_utf8_stdio()


def cmd_probe(args: argparse.Namespace) -> int:
    try:
        target = plan.resolve_target(args.repo, Path(args.projects_toml) if args.projects_toml else None, args.url)
    except plan.PlanError as exc:
        print(f"ERROR={exc}")
        return 2
    verdict = capture.probe_listening(target.base_url)
    claude_md = (target.root / "CLAUDE.md") if target.root else None
    print(f"TARGET={target.name}")
    print(f"BASE_URL={target.base_url}")
    print(f"ROOT={target.root or 'none'}")
    print(f"CLAUDE_MD={claude_md if claude_md and claude_md.is_file() else 'none'}")
    print(f"PROBE={verdict['status']}")
    print(f"DETAIL={verdict.get('detail') or 'none'}")
    return 0


def cmd_measure(args: argparse.Namespace) -> int:
    try:
        rb = rubric_mod.load_rubric(Path(args.rubric) if args.rubric else None)
    except rubric_mod.RubricError as exc:
        print(f"ERROR=rubric: {exc}")
        return 2
    bad = rubric_mod.check_metric_names(rb, measure.metric_paths())
    if bad:
        print(f"ERROR=rubric names unknown metrics on rules {bad}")
        return 2
    try:
        target = plan.resolve_target(args.repo, Path(args.projects_toml) if args.projects_toml else None, args.url)
        devices = plan.device_list([d for d in (args.devices or "").split(",") if d])
    except plan.PlanError as exc:
        print(f"ERROR={exc}")
        return 2
    specs = rubric_mod.load_specs(Path(args.spec) if args.spec else None, None)
    run_dir = Path(args.run_dir) if args.run_dir else None
    if run_dir:
        run_dir.mkdir(parents=True, exist_ok=True)
    doc = capture.measure_target(target, rb, specs["light"], devices, python_override=args.python,
                                 scaffold=args.scaffold, run_dir=run_dir, walk_timeout=args.walk_timeout)
    screens = doc.get("screens") or []
    ok = sum(1 for s in screens if s.get("status") == "ok")
    print(f"TARGET={doc['target']}")
    print(f"BASE_URL={doc['base_url']}")
    print(f"COMMIT={doc.get('commit') or 'none'}")
    print(f"INTERPRETER={doc.get('interpreter') or 'none'}")
    print(f"RUN_DIR={doc['run_dir']}")
    print(f"METRICS={Path(str(doc['run_dir'])) / 'metrics.json'}")
    print(f"SCREENS={ok}/{len(screens)}")
    print(f"ABSENT={sum(1 for s in screens if s.get('status') == 'absent')}")  # step targets not in this app state (#995)
    unm = doc.get("unmeasured")
    print(f"UNMEASURED={unm['reason'] if unm else 'none'}")
    if unm and unm.get("detail"):
        print(f"UNMEASURED_DETAIL={unm['detail']}")
    return 0


def _load_json(path: Path) -> Dict[str, object]:
    """A JSON object from disk; raises ValueError with a one-line reason."""
    if not path.is_file():
        raise ValueError(f"not a file: {path}")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unreadable JSON {path}: {exc}") from exc
    if not isinstance(doc, dict):
        raise ValueError(f"not a JSON object: {path}")
    return doc


def _evaluate_file(metrics_path: Path, args: argparse.Namespace) -> Dict[str, object]:
    rb = rubric_mod.load_rubric(Path(args.rubric) if args.rubric else None)
    doc = _load_json(metrics_path)
    specs = rubric_mod.load_specs(Path(args.spec) if args.spec else None,
                                  Path(args.spec_dark) if args.spec_dark else None)
    out = evaluate.evaluate(doc, rb, specs)
    out["source"] = str(metrics_path)
    return out


def _print_summary(doc: Dict[str, object]) -> None:
    s = report.report_summary(doc)
    print(f"TARGET={doc.get('target')}")
    print(f"COMMIT={doc.get('commit') or 'none'}")
    print(f"RUBRIC={s['rubric_version']}")
    print(f"GRADE={s['grade']}")
    print(f"SCORE={s['score']}")
    print(f"FAILED={s['failed']}/{s['total']}")
    print(f"UNMEASURED={s['unmeasured_rules'] or 'none'}")
    print("CATEGORIES=" + ",".join(f"{c}:{g}" for c, g in s["categories"].items()))
    print("MOCKUPS=" + (",".join(s["mockups"]) or "none"))
    j = doc.get("judgment")
    print(f"JUDGMENT={(j.get('status') or 'unknown') if isinstance(j, dict) else 'none'}")


def cmd_evaluate(args: argparse.Namespace) -> int:
    try:
        out = _evaluate_file(Path(args.metrics), args)
    except rubric_mod.RubricError as exc:
        print(json.dumps({"error": f"rubric: {exc}"}))
        return 2
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(out, indent=2, ensure_ascii=True), encoding="utf-8")
        print(f"EVALUATE={out_path}")
        _print_summary(out)
        return 0
    print(json.dumps(out, indent=2, ensure_ascii=True))
    return 0


def cmd_judge_prompt(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    try:
        rb = rubric_mod.load_rubric(Path(args.rubric) if args.rubric else None)
        doc = _load_json(run_dir / "metrics.json")
    except rubric_mod.RubricError as exc:
        print(f"ERROR=rubric: {exc}")
        return 2
    except ValueError as exc:
        print(f"ERROR={exc}")
        return 2
    if not rb.judgment:
        print("ERROR=rubric has no [[judgment]] entries")
        return 2
    text = judgment.judge_prompt(doc, run_dir, rb)
    out = run_dir / "judge-prompt.md"
    try:
        out.write_text(text, encoding="utf-8")
    except OSError as exc:
        print(f"ERROR=cannot write prompt: {exc}")
        return 2
    print(f"PROMPT={out}")
    print(f"SCREENS={len(judgment.screen_rows(doc))}")
    print(f"QUESTIONS={len(rb.judgment)}")
    return 0


def cmd_judge_merge(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    metrics_path = run_dir / "metrics.json"
    evaluate_path = run_dir / "evaluate.json"
    try:
        rb = rubric_mod.load_rubric(Path(args.rubric) if args.rubric else None)
        metrics = _load_json(metrics_path)
        if evaluate_path.is_file():
            ev_doc = _load_json(evaluate_path)
        else:
            ev_doc = _evaluate_file(metrics_path, args)
    except rubric_mod.RubricError as exc:
        print(f"ERROR=rubric: {exc}")
        return 2
    except ValueError as exc:
        print(f"ERROR={exc}")
        return 2
    docs: List[Dict[str, object]] = []
    for name in args.answers:
        p = Path(name)
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"ERROR=cannot read answers {p}: {exc}")
            return 2
        payload, perr = judgment.parse_payload(text)
        if perr:
            jdoc = judgment.unmeasured_doc(rb, [f"{p.name}: {perr}"])
        else:
            jdoc, _errs = judgment.validate_answers(payload, rb, metrics)
        docs.append(jdoc)
    merged = judgment.merge_judges(docs)
    ev_doc["judgment"] = merged
    try:
        evaluate_path.write_text(json.dumps(ev_doc, indent=2, ensure_ascii=True), encoding="utf-8")
    except OSError as exc:
        print(f"ERROR=cannot write {evaluate_path}: {exc}")
        return 2
    yes, no, na = judgment.answer_counts(merged)
    print(f"JUDGMENT={merged['status']}")
    print(f"ANSWERS={yes}/{no}/{na}")
    print(f"UNCATALOGUED={len(merged.get('uncatalogued') or [])}")
    print(f"ERRORS={len(merged.get('errors') or [])}")
    if str(ev_doc.get("rubric_version")) != rb.version:
        # The grades were scored under another rubric version than the checklist
        # was answered against; #974's diff must read both stamps, not one.
        print(f"RUBRIC_MISMATCH=evaluate:{ev_doc.get('rubric_version')} judgment:{rb.version}")
    for e in merged.get("errors") or []:
        print(f"ERROR_DETAIL={e}")
    for d in merged.get("disagreements") or []:
        print(f"NOT_CONFIRMED={d['id']}:{'/'.join(str(v) for v in d['answers'])}")
    print(f"EVALUATE={evaluate_path}")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    src = Path(args.source)
    try:
        doc = _load_json(src)
        if "rules" in doc and "categories" in doc:
            evaluate_path: Optional[Path] = src
        else:
            doc = _evaluate_file(src, args)
            evaluate_path = src.parent / "evaluate.json"
            evaluate_path.write_text(json.dumps(doc, indent=2, ensure_ascii=True), encoding="utf-8")
    except rubric_mod.RubricError as exc:
        print(f"ERROR=rubric: {exc}")
        return 2
    except (ValueError, OSError) as exc:
        print(f"ERROR={exc}")
        return 2
    run_dir = Path(str(doc.get("run_dir") or "")) if doc.get("run_dir") else None
    out = Path(args.out) if args.out else ((run_dir if run_dir and run_dir.is_dir() else src.parent) / "report.html")
    try:
        report.write_report(doc, out)
    except OSError as exc:
        print(f"ERROR=cannot write report: {exc}")
        return 2
    print(f"REPORT={out}")
    print(f"EVALUATE={evaluate_path}")
    _print_summary(doc)
    return 0


def _tables(projects_toml: Optional[str]) -> Dict[str, dict]:
    return fleet_repo_scan.fleet_repo_tables(Path(projects_toml) if projects_toml else None)


def _target_root(target: str, projects_toml: Optional[str]) -> Optional[Path]:
    """The target's checkout from projects.toml (`.fleet.toml` and the origin remote live there), or None."""
    root = Path(str(_tables(projects_toml).get(target, {}).get("cwd_prefix", "")))
    return root if str(root) not in ("", ".") and root.is_dir() else None


def cmd_ledger(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    metrics_path = run_dir / "metrics.json"
    evaluate_path = run_dir / "evaluate.json"
    try:
        if evaluate_path.is_file():
            doc = _load_json(evaluate_path)
        else:
            doc = _evaluate_file(metrics_path, args)
    except rubric_mod.RubricError as exc:
        print(f"ERROR=rubric: {exc}")
        return 2
    except ValueError as exc:
        print(f"ERROR={exc}")
        return 2
    target = str(doc.get("target") or run_dir.parent.name)
    run_id = ledger.run_id_of(run_dir, doc)
    prev = ledger.previous(target, run_id)
    d = ledger.diff(doc, prev)
    doc["diff"] = d
    try:
        evaluate_path.write_text(json.dumps(doc, indent=2, ensure_ascii=True), encoding="utf-8")
    except OSError as exc:
        print(f"ERROR=cannot write {evaluate_path}: {exc}")
        return 2
    live = None
    if not args.no_live and not doc.get("unmeasured"):
        live = ledger.live_build(str(doc.get("base_url") or ""), _tables(args.projects_toml).get(target, {}).get("api_version_path"))
    entry = ledger.record(run_dir, doc, live)
    counts = ledger.diff_counts(d)
    print(f"LEDGER={ledger.ledger_path(target)}")
    print(f"RUN_ID={run_id}")
    print(f"PREVIOUS={d.get('previous_run') or 'none'}")
    for key in ("fixed", "regressed", "new", "unchanged", "unmeasured"):
        print(f"{key.upper()}={counts[key]}" + (f" {','.join(d[key])}" if key in ("fixed", "regressed", "new") and d[key] else ""))
    rc = d.get("rubric_changed")
    print(f"RUBRIC_CHANGED={rc['from']}->{rc['to']}" if rc else "RUBRIC_CHANGED=none")
    print(f"LIVE_BUILD={entry.get('live_build') or 'unknown'}")
    print(f"COMMIT={entry.get('commit') or 'none'}")
    print(f"EVALUATE={evaluate_path}")
    return 0


def cmd_file(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    try:
        doc = _load_json(run_dir / "evaluate.json")
    except ValueError as exc:
        print(f"ERROR={exc}")
        return 2
    root = _target_root(str(doc.get("target") or run_dir.parent.name), args.projects_toml)
    repo = args.repo or filing.repo_slug(root)
    fetch = None
    if args.existing_body:
        # Tests: merge over this body instead of reading the managed issue (no `gh`).
        try:
            existing_text = Path(args.existing_body).read_text(encoding="utf-8") if Path(args.existing_body).is_file() else ""
        except OSError as exc:
            print(f"ERROR=cannot read {args.existing_body}: {exc}")
            return 2
        fetch = lambda r, k: {"number": None, "body": existing_text, "duplicates": []}  # noqa: E731
    try:
        out = filing.file_run(run_dir, repo, dry_run=not args.file, root=root, fetch=fetch)
    except ValueError as exc:
        print(f"ERROR={exc}")
        return 2
    routed = out["routed"]
    print(f"FILE={'filed' if args.file else 'dry-run'}")
    print(f"REPO={out['repo']}")
    print(f"ISSUE={out['issue'] if out['issue'] is not None else 'none'}")
    print(f"CHANGED={'yes' if out['changed'] else 'no'}")
    print(f"FILED={len(routed['app'])} {','.join(s['id'] for s in routed['app']) or ''}".rstrip())
    print(f"UNCATALOGUED={len(out['uncatalogued'])}")
    print(f"ACCEPTED={','.join(s['id'] for s in routed['suppressed']) or 'none'}")
    print(f"SPEC={','.join(s['id'] for s in routed['spec']) or 'none'}")
    print(f"SCAFFOLD={','.join(s['id'] for s in routed['scaffold']) or 'none'}")
    for p in out["problems"] + [f"accepted rule {rid} fails nowhere this run" for rid in routed["unmatched"]]:
        print(f"PROBLEM={p}")
    print(f"BODY={out['body_path']}")
    print(f"URL={out['url'] or 'none'}")
    return 0


def cmd_fleet(args: argparse.Namespace) -> int:
    try:
        rb = rubric_mod.load_rubric(Path(args.rubric) if args.rubric else None)
        devices = plan.device_list([d for d in (args.devices or "").split(",") if d])
    except rubric_mod.RubricError as exc:
        print(f"ERROR=rubric: {exc}")
        return 2
    except plan.PlanError as exc:
        print(f"ERROR={exc}")
        return 2
    specs = rubric_mod.load_specs(Path(args.spec) if args.spec else None, Path(args.spec_dark) if args.spec_dark else None)
    projects_toml = Path(args.projects_toml) if args.projects_toml else None
    out_dir = Path(args.out_dir) if args.out_dir else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    dig = fleet.run_fleet(rb, specs, devices, projects_toml, file_issues=bool(args.file), out_dir=out_dir,
                          scaffold=args.scaffold, walk_timeout=args.walk_timeout, python_override=args.python)
    print(f"FLEET_DIR={dig['out_dir']}")
    print(f"APPS={len(dig['apps'])}")
    for a in dig["apps"]:
        o = a.get("overall") or {}
        d = a.get("diff") or {}
        print(f"APP={a['target']} probe={a['probe']} unmeasured={a.get('unmeasured') or 'none'} grade={o.get('grade') or '-'} "
              f"score={o.get('score') if o else '-'} failed={len(a.get('failed') or [])} "
              f"fixed={d.get('fixed', 0)} regressed={d.get('regressed', 0)} new={d.get('new', 0)} run={a['run_id']}")
    measured = [a["target"] for a in dig["apps"] if not a.get("unmeasured")]
    unm = [f"{a['target']}:{a['unmeasured']}" for a in dig["apps"] if a.get("unmeasured")]
    print(f"MEASURED={len(measured)}")
    print(f"UNMEASURED_APPS={','.join(unm) or 'none'}")
    print(f"SPEC={','.join(s['id'] for s in dig['spec']) or 'none'}")
    print(f"SCAFFOLD={','.join(s['id'] for s in dig['scaffold'] if not s.get('promoted')) or 'none'}")
    print(f"PROMOTED={','.join(dig['promoted']) or 'none'}")
    print("JUDGMENT=skipped")
    print(f"FILING={dig['filing']['mode']}")
    for repo, where in dig["filing"]["issues"].items():
        print(f"ISSUE={repo} {where}")
    print(f"DIGEST={Path(dig['out_dir']) / fleet.DIGEST_JSON}")
    print(f"DIGEST_HTML={Path(dig['out_dir']) / fleet.DIGEST_HTML}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="/design-review deterministic core: probe, measure a live app, evaluate its metrics, render the report.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe", help="pre-flight: is the target listening? never starts, restarts or kills anything")
    p.add_argument("repo", help="hooks/projects.toml repo name, or a repo path")
    p.add_argument("--url", help="override the base URL")
    p.add_argument("--projects-toml", help="override hooks/projects.toml (tests)")
    p.set_defaults(fn=cmd_probe)

    m = sub.add_parser("measure", help="walk a running app read-only; write metrics.json + screenshots to a run dir")
    m.add_argument("repo", help="hooks/projects.toml repo name, or a repo path")
    m.add_argument("--url", help="override the base URL (a static fixture page, a non-fleet target)")
    m.add_argument("--devices", default=",".join(plan.DEFAULT_DEVICES), help="subset of iphone,desktop,android")
    m.add_argument("--python", help="interpreter to run the walk with (default: the target repo's .venv)")
    m.add_argument("--scaffold", default=capture.DEFAULT_SCAFFOLD, help="project-scaffolding root (tests/e2e/_geometry.py)")
    m.add_argument("--rubric", help="rubric file (default: this repo's design.rubric.toml)")
    m.add_argument("--spec", help="override the light spec path (tests)")
    m.add_argument("--projects-toml", help="override hooks/projects.toml (tests)")
    m.add_argument("--run-dir", help="override the run directory (tests); default is under the hooks state dir")
    m.add_argument("--walk-timeout", type=float, default=capture.WALK_TIMEOUT_S)
    m.set_defaults(fn=cmd_measure)

    e = sub.add_parser("evaluate", help="rule results + category grades from a metrics.json, as JSON")
    e.add_argument("metrics", help="path to a metrics.json written by `measure`")
    e.add_argument("--out", help="write the evaluate document here and print summary lines instead of the JSON")
    e.add_argument("--rubric", help="rubric file (default: this repo's design.rubric.toml)")
    e.add_argument("--spec", help="override the light spec path (tests)")
    e.add_argument("--spec-dark", help="override the dark spec path (tests)")
    e.set_defaults(fn=cmd_evaluate)

    jp = sub.add_parser("judge-prompt", help="write the deterministic judge prompt (#973) as <run_dir>/judge-prompt.md")
    jp.add_argument("run_dir", help="a run directory holding metrics.json and shots/")
    jp.add_argument("--rubric", help="rubric file (default: this repo's design.rubric.toml)")
    jp.set_defaults(fn=cmd_judge_prompt)

    jm = sub.add_parser("judge-merge", help="validate + merge judge answer files; write `judgment` into <run_dir>/evaluate.json")
    jm.add_argument("run_dir", help="a run directory holding metrics.json (evaluate.json is written if absent)")
    jm.add_argument("answers", nargs="+", help="one JSON answers file per judge")
    jm.add_argument("--rubric", help="rubric file (default: this repo's design.rubric.toml)")
    jm.add_argument("--spec", help="override the light spec path (tests; used only when evaluate.json is absent)")
    jm.add_argument("--spec-dark", help="override the dark spec path (tests)")
    jm.set_defaults(fn=cmd_judge_merge)

    r = sub.add_parser("render", help="the HTML report from an evaluate document (or a metrics.json, evaluated first)")
    r.add_argument("source", help="path to an evaluate.json (or a metrics.json)")
    r.add_argument("--out", help="report path (default: <run_dir>/report.html, else beside the source)")
    r.add_argument("--rubric", help="rubric file, used only when the source is a metrics.json")
    r.add_argument("--spec", help="override the light spec path (tests)")
    r.add_argument("--spec-dark", help="override the dark spec path (tests)")
    r.set_defaults(fn=cmd_render)

    lg = sub.add_parser("ledger", help="record the run in <state>/design-review/<target>/ledger.json and write `diff` into evaluate.json")
    lg.add_argument("run_dir", help="a run directory holding evaluate.json (evaluated from metrics.json if absent)")
    lg.add_argument("--no-live", action="store_true", help="skip the best-effort GET of the live build's version endpoint")
    lg.add_argument("--projects-toml", help="override hooks/projects.toml (tests)")
    lg.add_argument("--rubric", help="rubric file, used only when evaluate.json is absent")
    lg.add_argument("--spec", help="override the light spec path (tests)")
    lg.add_argument("--spec-dark", help="override the dark spec path (tests)")
    lg.set_defaults(fn=cmd_ledger)

    f = sub.add_parser("file", help="upsert the run's app-owned findings into the repo's managed design-review issue (dry run unless --file)")
    f.add_argument("run_dir", help="a run directory holding evaluate.json")
    f.add_argument("--file", action="store_true", help="actually upsert; without it the would-be body is written beside evaluate.json and nothing reaches GitHub")
    f.add_argument("--repo", help="OWNER/NAME (default: the target checkout's origin remote)")
    f.add_argument("--projects-toml", help="override hooks/projects.toml (tests)")
    f.add_argument("--existing-body", help="merge over this file instead of the managed issue's body (tests; no gh call, implies dry run)")
    f.set_defaults(fn=cmd_file)

    fl = sub.add_parser("fleet", help="every projects.toml web app, serially: probe, measure, evaluate, ledger, render; one digest; judgment skipped")
    fl.add_argument("--devices", default=",".join(plan.DEFAULT_DEVICES), help="subset of iphone,desktop,android")
    fl.add_argument("--file", action="store_true", help="upsert the app issues and the two digest issues; default is a dry run")
    fl.add_argument("--dry-run", action="store_true", help="the default, spelled out: write every would-be body beside the digest, file nothing")
    fl.add_argument("--python", help="interpreter to run every walk with (tests)")
    fl.add_argument("--scaffold", default=capture.DEFAULT_SCAFFOLD, help="project-scaffolding root (tests/e2e/_geometry.py)")
    fl.add_argument("--rubric", help="rubric file (default: this repo's design.rubric.toml)")
    fl.add_argument("--spec", help="override the light spec path (tests)")
    fl.add_argument("--spec-dark", help="override the dark spec path (tests)")
    fl.add_argument("--projects-toml", help="override hooks/projects.toml (tests)")
    fl.add_argument("--out-dir", help="override the fleet digest directory (tests); default is under the hooks state dir")
    fl.add_argument("--walk-timeout", type=float, default=capture.WALK_TIMEOUT_S)
    fl.set_defaults(fn=cmd_fleet)

    args = ap.parse_args(argv)
    if getattr(args, "cmd", None) == "fleet" and args.file and args.dry_run:
        ap.error("--file and --dry-run are mutually exclusive")
    if getattr(args, "cmd", None) == "file" and args.file and args.existing_body:
        ap.error("--existing-body is a test override and implies a dry run; drop --file")
    return int(args.fn(args))
