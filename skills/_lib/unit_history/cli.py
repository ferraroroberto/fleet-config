"""`python skills/_lib/unit_history/cli.py summary [--runs N] [--label L] [--top N]` reads the ledger.

Prints counts and seconds only (no paths, no host names), so the output can go straight into an issue
comment. `--runs N` takes the last N runs of the label (default 3).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fleet_core.utf8_stdio import ensure_utf8_stdio  # noqa: E402

from unit_history import ledger  # noqa: E402


def main(argv: Optional[List[str]] = None) -> int:
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Read the acceptance-gate unit ledger.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    summ = sub.add_parser("summary")
    summ.add_argument("--runs", type=int, default=3)
    summ.add_argument("--label", default="gate")
    summ.add_argument("--top", type=int, default=20)
    args = parser.parse_args(argv)
    rows = ledger.load(label=args.label)
    run_ids = [r["run_id"] for r in rows if r.get("kind") == "run"][-args.runs:]
    print(json.dumps(ledger.summarize([r for r in rows if r.get("run_id") in run_ids], args.top), indent=2))
    return 0 if run_ids else 1


if __name__ == "__main__":
    sys.exit(main())
