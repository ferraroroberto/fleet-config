"""`/retro-fleet` command line: mine every session log for repeat errors, then render the scored page.

    scan [--out-dir D] [--top N] [--workers N] [--home H]
        Digests every Claude Code, Codex and Pi log changed since the cached
        watermark and writes `candidates.json`. Prints
        `LOGS=claude:<n> codex:<n> pi:<n>`, `DIGESTED=new:<n> unchanged:<n> gone:<n>`,
        `UNMEASURED=<n> [<reason>:<n> ...]`, `EMPTY=<n>` (logs with no turn),
        `SIGNATURES=<n> recurring:<n> enforced:<n> owner:<n>` and `CANDIDATES=<path>`.
    report <candidates.json> <verdicts.json> [--out-dir D]
        Joins the classifier's verdicts to the counts, writes `report.json` and
        `page.html`, prints `REPORT=<path>`, `PAGE=<path> items=<n> quick_wins=<n>`
        and `REPORTED_ONLY=<n>`.

The default out dir is `retro-fleet/` in the hooks state dir, machine-local.
Exit 0 on success, 2 for bad input. Contract: `docs/skills/retro-fleet.md`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from fleet_core.hooks_state import atomic_write_text, state_dir  # noqa: E402
from fleet_core.runtime_files import make_dirs  # noqa: E402
from fleet_core.state_rows import write_rows  # noqa: E402
from fleet_core.utf8_stdio import ensure_utf8_stdio  # noqa: E402
from proposal_report import model, page  # noqa: E402

from retro import miner, proposals  # noqa: E402

EXIT_OK, EXIT_BAD_INPUT = 0, 2
OUT_DIRNAME = "retro-fleet"
#: The classifier judges this many recurring signatures per run, most sessions first.
TOP = 15


def _write(path: Path, text: str) -> None:
    make_dirs(path.parent, parents=True, exist_ok=True)
    atomic_write_text(path, text, newline="\n")


def _scan(args: argparse.Namespace) -> int:
    cache_path = state_dir() / miner.CACHE_FILENAME
    home = Path(args.home) if args.home else Path.home()
    result = miner.scan(miner.default_roots(home), miner.load_cache(cache_path), workers=args.workers)
    write_rows(cache_path, result.cache)
    today = miner.today_utc()
    rows = miner.aggregate(result.cache, today, home.name)
    doc = miner.candidates_doc(rows, result, today, args.top)
    out = Path(args.out_dir) / "candidates.json"
    _write(out, json.dumps(doc, indent=1) + "\n")
    unmeasured = doc["unmeasured"]
    print("LOGS=" + " ".join(f"{agent}:{result.logs.get(agent, 0)}" for agent, _ in miner.AGENT_ROOTS))
    print(f"DIGESTED=new:{result.digested} unchanged:{result.unchanged} gone:{result.gone}")
    print(f"UNMEASURED={sum(unmeasured.values())}"
          + "".join(f" {reason}:{n}" for reason, n in sorted(unmeasured.items())))
    print(f"EMPTY={doc['empty']}")
    print(f"SIGNATURES={len(rows)} recurring:{doc['flagged_candidates']} "
          f"enforced:{len(doc['enforced'])} owner:{len(doc['owner_declined'])}")
    print(f"CANDIDATES={out}")
    return EXIT_OK


def _report(args: argparse.Namespace) -> int:
    candidates = proposals.load_json(Path(args.candidates).read_text(encoding="utf-8"), args.candidates)
    verdicts = proposals.load_json(Path(args.verdicts).read_text(encoding="utf-8"), args.verdicts)
    if not isinstance(verdicts, list):
        raise ValueError(f"{args.verdicts}: must be a list of verdicts")
    doc, reported = proposals.build(candidates, verdicts)
    report = model.parse(doc)
    out_dir = Path(args.out_dir)
    report_path, page_path = out_dir / "report.json", out_dir / "page.html"
    _write(report_path, json.dumps(doc, indent=1) + "\n")
    _write(page_path, page.render(report))
    wins = sum(1 for item in report.suggestions if item.quadrant == model.Quadrant.QUICK_WIN)
    print(f"REPORT={report_path}")
    print(f"PAGE={page_path} items={len(report.suggestions)} quick_wins={wins}")
    print(f"REPORTED_ONLY={len(reported)}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    default_out = str(state_dir() / OUT_DIRNAME)
    parser = argparse.ArgumentParser(prog="retro-fleet", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan", help="digest changed logs and write candidates.json")
    scan.add_argument("--out-dir", default=default_out)
    scan.add_argument("--top", type=int, default=TOP, help="recurring candidates handed to the classifier")
    scan.add_argument("--workers", type=int, default=miner.WORKERS)
    scan.add_argument("--home", default=None, help="read the agents' logs under this home (tests)")
    scan.set_defaults(func=_scan)
    report = sub.add_parser("report", help="render the scored page from the classifier's verdicts")
    report.add_argument("candidates")
    report.add_argument("verdicts")
    report.add_argument("--out-dir", default=default_out)
    report.set_defaults(func=_report)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    ensure_utf8_stdio()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT


if __name__ == "__main__":
    sys.exit(main())
