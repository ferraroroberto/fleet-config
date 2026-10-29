"""CLI for the visual-proof gate: `record`, `check`, `walkthrough` (see the package docstring)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import active_issue  # noqa: E402
from fleet_core.utf8_stdio import ensure_utf8_stdio  # noqa: E402
from ui_proof import proof, walkthrough  # noqa: E402


def _print_verdict(v: proof.Verdict) -> None:
    line = f"VISUAL_PROOF={v.state}"
    if v.state not in ("n/a", "fresh") and not v.enforced:
        line += f" ({proof.ENFORCING_COMPONENT} not vendored)"
    print(line)
    print(f"ENFORCED={'yes' if v.enforced else 'no'}")
    print(f"TOUCHED={v.touched}")
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
    walk = sub.add_parser("walkthrough", help="build and store the owner's private walkthrough HTML")
    walk.add_argument("repo", type=Path)
    walk.add_argument("--pr", type=int, required=True)
    walk.add_argument("--sha", required=True)
    walk.add_argument("--title", default="")
    walk.add_argument("--summary", default="")
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
        verdict = proof.evaluate(repo, args.base)
        _print_verdict(verdict)
        return 1 if verdict.blocks else 0
    lines = walkthrough.build(repo, active_issue.resolve_repo_name(repo), args.pr, args.sha,
                              args.title, args.summary)
    print("\n".join(lines))
    return 0 if not lines[0].startswith("WALKTHROUGH=not_built") else 1
