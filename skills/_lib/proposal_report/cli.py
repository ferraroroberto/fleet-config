"""`proposal_report` command line: render the scored page, then post one Board ask per suggestion.

    render <report.json> --out <page.html>
        Writes the page and prints `PAGE=<path> items=<n> quick_wins=<n>`.
    ask <report.json> --page-url <url> [--dry-run] [--plan-file P] [--ledger L]
        One `ASK=<state> id=<ask id> item=<suggestion id> link=<url#anchor>` line per
        suggestion, then `ASKS=posted:<n> dry-run:<n> already-open:<n> already-asked:<n> failed:<n>`.

Exit 0 when every suggestion is posted, listed or already asked; 1 when any post
failed; 2 for bad input (invalid JSON, a bad URL, an unreadable plan or ledger).
Contract: `docs/proposal-reports.md`.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import List, Optional

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from fleet_core.utf8_stdio import ensure_utf8_stdio  # noqa: E402

from proposal_report import asks, model, page  # noqa: E402

EXIT_OK, EXIT_FAILED, EXIT_BAD_INPUT = 0, 1, 2


def _render(args: argparse.Namespace) -> int:
    report = model.load(Path(args.report))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)  # runtime-write-exempt: the caller names the page it publishes
    # runtime-write-exempt: the caller names the page it publishes
    out.write_text(page.render(report), encoding="utf-8", newline="\n")
    wins = sum(1 for item in report.suggestions if item.quadrant == model.Quadrant.QUICK_WIN)
    print(f"PAGE={out} items={len(report.suggestions)} quick_wins={wins}")
    return EXIT_OK


def _ask(args: argparse.Namespace) -> int:
    report = model.load(Path(args.report))
    outcome = asks.run(report, args.page_url, dry_run=args.dry_run,
                       plan_path=Path(args.plan_file) if args.plan_file else None,
                       ledger_path=Path(args.ledger) if args.ledger else None)
    for ask in outcome.asks:
        reason = f" reason={ask.reason}" if ask.reason else ""
        print(f"ASK={ask.state.value} id={ask.ask_id} item={ask.item.id} "
              f"link={args.page_url}#{ask.item.anchor}{reason}")
    counts = Counter(ask.state for ask in outcome.asks)
    print("ASKS=" + " ".join(f"{state.value}:{counts.get(state, 0)}" for state in asks.AskState))
    return EXIT_FAILED if outcome.failed else EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="proposal_report", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    render = sub.add_parser("render", help="write the scored proposal page")
    render.add_argument("report", help="suggestion JSON (format: docs/proposal-reports.md)")
    render.add_argument("--out", required=True, help="the HTML page to write")
    render.set_defaults(func=_render)
    ask = sub.add_parser("ask", help="post one Board ask per suggestion")
    ask.add_argument("report")
    ask.add_argument("--page-url", required=True, help="where the published page opens (http or https)")
    ask.add_argument("--dry-run", action="store_true", help="list the asks without posting or recording any")
    ask.add_argument("--plan-file", default=None, help="override the plan path (tests)")
    ask.add_argument("--ledger", default=None, help="override the asked-before ledger path (tests)")
    ask.set_defaults(func=_ask)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    ensure_utf8_stdio()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT


if __name__ == "__main__":
    sys.exit(main())
