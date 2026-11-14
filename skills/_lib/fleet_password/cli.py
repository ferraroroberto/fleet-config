"""Set the same sign-in password in every fleet app that carries the vendored module.

The owner runs it by hand from a terminal; it prompts twice with `getpass`
(never an argument, never echoed or logged). Any non-empty password is
accepted. Each app gets one line: `OK`, `SKIPPED (<why>)`, `FAILED (<why>)`,
or `WOULD-SET` under `--dry-run`, which prompts for nothing and writes
nothing. Exit 0 when every app is OK (or the dry run found no failure),
1 when any app failed, 2 when the run was refused before touching an app.
Contract: `docs/fleet-password.md`.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from fleet_core.fleet_repo_scan import ProjectsTomlMissing, fleet_repos  # noqa: E402
from fleet_core.hooks_state import state_dir  # noqa: E402
from fleet_core.utf8_stdio import ensure_utf8_stdio  # noqa: E402

from fleet_password import core  # noqa: E402

EXIT_OK, EXIT_FAILED, EXIT_REFUSED = 0, 1, 2


def _read_password() -> Optional[str]:
    first = getpass.getpass("New password for every app: ")
    if not first:
        print("❌ The password must not be empty; nothing was changed.", file=sys.stderr)
        return None
    if getpass.getpass("Again: ") != first:
        print("❌ The two entries differ; nothing was changed.", file=sys.stderr)
        return None
    return first


def _select(targets: Dict[str, Path], only: List[str]) -> Optional[Dict[str, Path]]:
    if not only:
        return targets
    unknown = [name for name in only if name not in targets]
    if unknown:
        print(f"❌ Not a password app: {', '.join(unknown)}. Known: {', '.join(targets)}", file=sys.stderr)
        return None
    return {name: targets[name] for name in only}


def main(
    argv: Optional[Sequence[str]] = None,
    repos: Optional[Dict[str, Path]] = None,
    backup_root: Optional[Path] = None,
    python_for: core.PythonFor = core.venv_python,
) -> int:
    """Run the whole pass; `repos`, `backup_root` and `python_for` are the test seams."""
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Set one sign-in password in every fleet app.")
    parser.add_argument("--dry-run", action="store_true", help="list what would change; prompt for and write nothing")
    parser.add_argument("--only", default="", help="comma-separated repo names to limit the run to")
    args = parser.parse_args(argv)

    try:
        found = core.find_targets(fleet_repos() if repos is None else repos)
    except ProjectsTomlMissing as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return EXIT_REFUSED
    targets = _select(found, [n.strip() for n in args.only.split(",") if n.strip()])
    if targets is None:
        return EXIT_REFUSED
    if not targets:
        print("❌ No fleet repo declares the vendored password component.", file=sys.stderr)
        return EXIT_REFUSED

    if args.dry_run:
        results = [core.plan_one(name, path, python_for) for name, path in targets.items()]
    else:
        password = _read_password()
        if password is None:
            return EXIT_REFUSED
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backups = (backup_root or state_dir() / "fleet-password") / stamp
        results = [core.set_one(name, path, password, backups, python_for) for name, path in targets.items()]
        if any(r.detail == "backed up" for r in results):
            print(f"ℹ️ Previous hash files backed up under {backups}")

    for result in results:
        print(result.line())
    failed = any(r.outcome == core.FAILED for r in results)
    if not args.dry_run and not failed:
        print("✅ Done. No restart needed: every app reads the hash on each sign-in.")
    return EXIT_FAILED if failed else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
