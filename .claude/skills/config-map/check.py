"""Run only the config-map acceptance check, strictly: a stale snapshot fails.

`/config-map` step 1 needs the `config_map:` lines of `tests/run_acceptance.py`
and nothing else; the whole run takes many minutes and outlives the job's
watchdog while other lanes gate. This calls `_config_map_check` directly. That
check reports a stale `config.data.js` as a SKIP because no fleet-config commit
can clear it; this skill is the one that clears it, so here a SKIP is a failure.

Usage (run from the repo root)::

    E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/config-map/check.py

Exit 0 only when every case passed and none was skipped; 1 otherwise.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "tests"))
sys.path.insert(0, str(REPO_ROOT / "skills" / "_lib"))

from acceptance.architecture_guards import _config_map_check  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Run only the config-map check; a stale snapshot is a failure.")
    ap.add_argument("--data-js", type=Path, default=None,
                    help="snapshot to check instead of architecture/config.data.js")
    args = ap.parse_args(argv)

    failures, total, skipped = _config_map_check(args.data_js)
    verdict = "FAIL" if failures or skipped else "OK"
    print(f"{verdict}: config_map check - {total} run, {failures} failed, {skipped} skipped (a skip is a failure here)")
    return 1 if failures or skipped else 0


if __name__ == "__main__":
    sys.exit(main())
