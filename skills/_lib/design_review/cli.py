"""The `design_review` command line — `measure` and `evaluate`.

    <python> C:/Users/rober/.claude/skills/_lib/design_review measure <repo>
        [--url URL] [--devices iphone,desktop,android] [--python PATH]
        [--scaffold DIR] [--rubric FILE] [--spec FILE] [--projects-toml FILE]
        [--run-dir DIR] [--walk-timeout S]
    <python> C:/Users/rober/.claude/skills/_lib/design_review evaluate <metrics.json>
        [--rubric FILE] [--spec FILE] [--spec-dark FILE]

`measure` prints KEY=VALUE lines (the `ux_surface` CLI style) so a skill can
read the run directory back without parsing JSON:

    RUN_DIR=<run dir>            METRICS=<run dir>/metrics.json
    SCREENS=<n ok>/<n total>     UNMEASURED=<reason>|none
    INTERPRETER=<python>|none    COMMIT=<sha>|none

and exits 0 whenever a `metrics.json` was written — an `unmeasured` run is a
result, not a crash; exit 2 is reserved for a target that cannot be
resolved or a rubric that does not validate. `evaluate` prints the JSON
document on stdout and exits 0; the caller reads `overall`/`rules`.

Deliberately the only module that knows argparse and file locations —
`plan`/`capture`/`rubric`/`evaluate` are importable and unit-tested without it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utf8_stdio import ensure_utf8_stdio  # noqa: E402

from . import capture, evaluate, measure, plan, rubric as rubric_mod  # noqa: E402

ensure_utf8_stdio()


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


def cmd_evaluate(args: argparse.Namespace) -> int:
    try:
        rb = rubric_mod.load_rubric(Path(args.rubric) if args.rubric else None)
    except rubric_mod.RubricError as exc:
        print(json.dumps({"error": f"rubric: {exc}"}))
        return 2
    path = Path(args.metrics)
    if not path.is_file():
        print(json.dumps({"error": f"not a file: {path}"}))
        return 2
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": f"unreadable metrics: {exc}"}))
        return 2
    specs = rubric_mod.load_specs(Path(args.spec) if args.spec else None,
                                  Path(args.spec_dark) if args.spec_dark else None)
    out = evaluate.evaluate(doc, rb, specs)
    out["source"] = str(path)
    print(json.dumps(out, indent=2, ensure_ascii=True))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="/design-review deterministic core: measure a live app, evaluate its metrics.")
    sub = ap.add_subparsers(dest="cmd", required=True)

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
    e.add_argument("--rubric", help="rubric file (default: this repo's design.rubric.toml)")
    e.add_argument("--spec", help="override the light spec path (tests)")
    e.add_argument("--spec-dark", help="override the dark spec path (tests)")
    e.set_defaults(fn=cmd_evaluate)

    args = ap.parse_args(argv)
    return int(args.fn(args))
