"""CLI for the visual-proof gate: `record`, `check`, `capture`, `capture-before|after`, `build`, `prune` (see the package docstring)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import active_issue  # noqa: E402
from fleet_core.utf8_stdio import ensure_utf8_stdio  # noqa: E402
from ui_proof import capture_run, proof, store, walkthrough  # noqa: E402


def _print_verdict(v: proof.Verdict) -> None:
    line = f"VISUAL_PROOF={v.state}"
    if v.state not in ("n/a", "fresh") and not v.enforced:
        line += f" ({proof.ENFORCING_COMPONENT} not vendored)"
    print(line)
    print(f"ENFORCED={'yes' if v.enforced else 'no'}")
    print(f"TOUCHED={v.touched}")
    print(f"PROOF_PAGE={v.page}")
    print("NEED=" + " | ".join(v.need))
    print(f"DETAIL={v.detail}")


def main(argv: Optional[List[str]] = None) -> int:
    ensure_utf8_stdio()
    ap = argparse.ArgumentParser(description="Visual proof for UI-touching diffs.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rec = sub.add_parser("record", help="bind a layout-lint manifest to the current tree")
    rec.add_argument("repo", type=Path)
    rec.add_argument("--manifest", type=Path, required=True)
    rec.add_argument("--criterion", action="append", default=[], help="'<state>=<criterion text>', repeatable")
    chk = sub.add_parser("check", help="is the recorded proof fresh and clean for this diff?")
    chk.add_argument("repo", type=Path)
    chk.add_argument("--base", default=None)
    chk.add_argument("--pr", type=int, default=None,
                     help="require .proof/<PR>/index.html; a built page passes without a recorded manifest")
    for phase in store.PHASES:
        cap = sub.add_parser(f"capture-{phase}", help=f"copy the {phase} screenshots into .proof/")
        cap.add_argument("repo", type=Path)
        cap.add_argument("--manifest", type=Path, default=None, help="a layout_lint capture_states manifest")
        cap.add_argument("--image", action="append", default=[], help="'<state>:<viewport>-<theme>=<png>', repeatable")
    shoot = sub.add_parser("capture", help="screenshot a running app's state at named viewports and themes")
    shoot.add_argument("repo", type=Path)
    shoot.add_argument("--phase", choices=store.PHASES, required=True)
    shoot.add_argument("--url", required=True, help="the app instance to drive (the repo's gate instance, loopback)")
    shoot.add_argument("--state", required=True, help="the state name a --criterion maps to")
    shoot.add_argument("--viewport", action="append", default=[],
                       help="phone (390x844), desktop (1280x900), <W>x<H> or <label>=<W>x<H>; repeatable, default phone+desktop")
    shoot.add_argument("--theme", action="append", choices=capture_run.THEMES, default=[],
                       help="repeatable, default light+dark")
    shoot.add_argument("--click", action="append", default=[], help="a selector to click, in order, to reach the state")
    shoot.add_argument("--wait-ms", type=int, default=None, help="settle time after load")
    bld = sub.add_parser("build", help="write .proof/<PR>/index.html and its .proof/index.json row")
    bld.add_argument("repo", type=Path)
    bld.add_argument("--pr", type=int, required=True)
    bld.add_argument("--criterion", action="append", default=[],
                     help="'<state>=<what changed and where to look>', repeatable")
    bld.add_argument("--no-visual", action="append", default=[], help="a criterion with no visual change")
    bld.add_argument("--title", default="")
    bld.add_argument("--issue", type=int, default=None)
    bld.add_argument("--sha", default="", help="the merge sha, once merged; also copies the proof to the primary's .proof/")
    prn = sub.add_parser("prune", help="remove .proof/ folders older than the threshold")
    prn.add_argument("repo", type=Path)
    prn.add_argument("--older-than", default="30d")
    args = ap.parse_args(argv)
    repo = args.repo.resolve()

    if args.cmd == "record":
        try:
            criteria = proof.parse_criteria(args.criterion)
        except ValueError as exc:
            print(f"PROOF=not_recorded reason={exc}")
            return 2
        ok, line = proof.record(repo, args.manifest, criteria)
        print(line)
        return 0 if ok else 1
    if args.cmd == "check":
        verdict = proof.evaluate(repo, args.base, args.pr)
        _print_verdict(verdict)
        return 1 if verdict.blocks else 0
    return _store_command(repo, args)


def _store_command(repo: Path, args: argparse.Namespace) -> int:
    """`capture`, `capture-before|after`, `prune` and `build`: the `.proof/` store."""
    if args.cmd == "capture":
        try:
            ok, line = capture_run.run(repo, args.phase, args.url, args.state,
                                       args.viewport or list(capture_run.VIEWPORTS), args.theme or list(capture_run.THEMES),
                                       args.click, args.wait_ms)
        except ValueError as exc:
            print(f"CAPTURED=none reason={exc}")
            return 2
        print(line)
        return 0 if ok else 1
    if args.cmd.startswith("capture-"):
        try:
            images = store.parse_images(args.image)
        except ValueError as exc:
            print(f"CAPTURED=none reason={exc}")
            return 2
        ok, line = store.capture(repo, args.cmd.split("-", 1)[1], args.manifest, images)
        print(line)
        return 0 if ok else 1
    if args.cmd == "prune":
        try:
            age = store.parse_age(args.older_than)
        except ValueError as exc:
            print(f"PRUNED=0 reason={exc}")
            return 2
        print("\n".join(store.prune(repo, age)))
        return 0
    try:
        criteria = proof.parse_criteria(args.criterion)
    except ValueError as exc:
        print(f"PROOF_PAGE=not_built reason={exc}")
        return 2
    lines = walkthrough.build(repo, active_issue.resolve_repo_name(repo), args.pr, criteria,
                              args.no_visual, args.title, args.issue, args.sha)
    print("\n".join(lines))
    return 1 if lines[0].startswith("PROOF_PAGE=not_built") else 0
