"""The `design_review` command line — `probe`, `measure`, `evaluate`, `render`.

    <python> C:/Users/rober/.claude/skills/_lib/design_review probe <repo>
        [--url URL] [--projects-toml FILE]
    <python> C:/Users/rober/.claude/skills/_lib/design_review measure <repo>
        [--url URL] [--devices iphone,desktop,android] [--python PATH]
        [--scaffold DIR] [--rubric FILE] [--spec FILE] [--projects-toml FILE]
        [--run-dir DIR] [--walk-timeout S]
    <python> C:/Users/rober/.claude/skills/_lib/design_review evaluate <metrics.json>
        [--out FILE] [--rubric FILE] [--spec FILE] [--spec-dark FILE]
    <python> C:/Users/rober/.claude/skills/_lib/design_review render <evaluate.json|metrics.json>
        [--out FILE] [--rubric FILE] [--spec FILE] [--spec-dark FILE]

`probe`, `measure` and `render` print KEY=VALUE lines (the `ux_surface` CLI
style) so a skill can read the result back without parsing JSON:

    probe    TARGET= BASE_URL= ROOT= CLAUDE_MD= PROBE=listening|NOT_LISTENING|TIMEOUT|BAD_URL DETAIL=
    measure  TARGET= BASE_URL= COMMIT= INTERPRETER= RUN_DIR= METRICS= SCREENS=<ok>/<total> UNMEASURED=<reason>|none
    render   REPORT= EVALUATE= TARGET= COMMIT= RUBRIC= GRADE= SCORE= FAILED=<n>/<total>
             UNMEASURED=<n rules>|none CATEGORIES=<cat:grade,...> MOCKUPS=<ids>|none

`measure` exits 0 whenever a `metrics.json` was written — an `unmeasured` run
is a result, not a crash; exit 2 is reserved for a target that cannot be
resolved or a rubric that does not validate. `probe` exits 0 with its verdict
on the `PROBE=` line (the skill's pre-flight; it never starts anything).
`evaluate` prints the JSON document on stdout and exits 0; with `--out` it
writes the document there and prints the `render`-style summary lines
instead. `render` accepts either an evaluate document or a raw
`metrics.json` (evaluated first, and the evaluate document written beside
the report so #974 can diff runs), and writes `report.html` into the run
directory by default.

Deliberately the only module that knows argparse and file locations —
`plan`/`capture`/`rubric`/`evaluate`/`report` are importable and unit-tested
without it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utf8_stdio import ensure_utf8_stdio  # noqa: E402

from . import capture, evaluate, measure, plan, report, rubric as rubric_mod  # noqa: E402

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

    r = sub.add_parser("render", help="the HTML report from an evaluate document (or a metrics.json, evaluated first)")
    r.add_argument("source", help="path to an evaluate.json (or a metrics.json)")
    r.add_argument("--out", help="report path (default: <run_dir>/report.html, else beside the source)")
    r.add_argument("--rubric", help="rubric file, used only when the source is a metrics.json")
    r.add_argument("--spec", help="override the light spec path (tests)")
    r.add_argument("--spec-dark", help="override the dark spec path (tests)")
    r.set_defaults(fn=cmd_render)

    args = ap.parse_args(argv)
    return int(args.fn(args))
