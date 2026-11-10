"""Regenerates `tests/fixtures/run_outcome_golden.json` from the current `_verdict`.

Run it only to re-pin an intended change in a run's exit code or status line:
`E:/automation/fleet-config/.venv/Scripts/python.exe tests/claude_progress/record_run_outcome_golden.py`
"""
from __future__ import annotations

import json

from run_outcome_cases import FACTS, NATIVE_EXITS, ROOT, SPECIAL, all_cases, drive

GOLDEN = ROOT / "tests" / "fixtures" / "run_outcome_golden.json"


def main() -> int:
    labels: list[str] = []
    rows: dict[str, list[int]] = {}
    for key, facts, child_exit, options in all_cases():
        code, status = drive(facts, child_exit, **options)
        if status not in labels:
            labels.append(status)
        rows[key] = [code, labels.index(status)]
    document = {"facts": list(FACTS), "native_exits": list(NATIVE_EXITS),
                "special": sorted(SPECIAL), "labels": labels, "rows": rows}
    GOLDEN.write_text(json.dumps(document, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    print(f"recorded {len(rows)} cases, {len(labels)} distinct status lines -> {GOLDEN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
