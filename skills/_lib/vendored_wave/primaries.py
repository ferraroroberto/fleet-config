"""Land each fleet primary and restart what serves it, then say which stayed stale.

A merged wave is not live until the primary checkout moves and the process serving it
restarts. `worktree_claim.py land-primary` does the first half and refuses a tree a live
service is serving; this command does both halves per repo with the repo's own declared
recipe (`hooks/projects.toml` `restart_cmd`, run through `hooks/restart_and_verify_webapp.py`,
which kills only that repo's own webapp port and verifies the live build), and ends with one
`STALE_PRIMARIES=` line so a primary left behind cannot hide in one repo's report.

Per repo, one line:

  PRIMARY=live behind=0                          landed (or already level), nothing to restart
  PRIMARY=live behind=0 restarted=yes build=<h>  landed, restarted, live build equals HEAD
  PRIMARY=stale reason=owner-restart             the hub (:8000) and the session host (:8446)
                                                 are never restarted here; the owner does it
  PRIMARY=stale reason=no-restart-recipe         a service is declared but neither `restart_cmd` nor `api_version_path`
  PRIMARY=stale reason=<guard or restart detail> dirty tree, off main, claim held, restart
                                                 failed or its build identity not confirmed

Restart only happens when the service was listening before the landing and HEAD moved; a
service that is down is not started, and a level tree is not restarted.

CLI:

  land <repo>... [--all-behind] [--issue KEY] [--dry-run]
      Land and restart. `<repo>` is a fleet-registry name. `--all-behind` adds every fleet
      primary behind `origin/<default>`. `--dry-run` fetches and reports `WOULD=...` per
      repo and changes nothing. Exit 0 when every repo is live, 1 when any is stale.
  stale
      Read-only: the fleet primaries behind `origin/<default>`, then `STALE_PRIMARIES=`.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import git_run  # noqa: E402
import worktree_claim as wcl  # noqa: E402
from fleet_core import fleet_repo_scan  # noqa: E402
from fleet_core.utf8_stdio import ensure_utf8_stdio  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402
from worktree import service_probe as sp  # noqa: E402

ensure_utf8_stdio()

RESTART, OWNER, NO_RECIPE, NO_SERVICE = "restart", "owner-restart", "no-restart-recipe", "no-service"
# The floor under `[global] never_kill_ports`: the hub, whisper, the session host. A registry
# that fails to list them must not turn a restart of one of them on.
DEFAULT_NEVER_KILL = frozenset({8000, 8090, 8446})
RESTART_TIMEOUT_S = 240
HEAD7 = 7


@dataclass
class Outcome:
    repo: str
    line: str
    live: bool


def never_kill_ports(toml: Mapping[str, object]) -> frozenset:
    declared = (toml.get("global") or {}).get("never_kill_ports") or []
    return DEFAULT_NEVER_KILL | {int(p) for p in declared if isinstance(p, int)}


def recipe_kind(tbl: Mapping[str, object], never_kill: frozenset) -> str:
    """What the registry lets this command do about the process serving `tbl`'s tree."""
    if {tbl.get("webapp_port"), tbl.get("session_host_port")} & never_kill:
        return OWNER
    if not tbl.get("tray_cmd"):
        return NO_SERVICE
    # restart_and_verify_webapp.py relaunches `tray_cmd` when there is no `restart_cmd`, and
    # confirms the build through `api_version_path`; both together are a recipe it can run.
    return RESTART if tbl.get("restart_cmd") or tbl.get("api_version_path") else NO_RECIPE


def service_verdict(kind: str, probe: str, detail: str) -> Tuple[bool, str]:
    """The live-service guard's answer for a repo whose restart this command owns (or doesn't).

    A restartable service that is listening may be landed because the restart follows at once;
    one this command may not restart is refused while it listens or while that is unknown.
    """
    if kind == RESTART and probe == sp.SERVICE_LIVE:
        return True, "service restarts after landing"
    if kind in (OWNER, NO_RECIPE) and probe != sp.SERVICE_ABSENT:
        return False, kind
    return sp.live_service_check(kind != NO_SERVICE, probe, detail=detail)


def run_restart(repo: Path) -> Tuple[int, str]:
    """The repo's declared recipe through the shared restart-and-verify hook: (exit, last line)."""
    hook = Path(__file__).resolve().parents[3] / "hooks" / "restart_and_verify_webapp.py"
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
    try:
        res = subprocess.run([sys.executable, str(hook), "--cwd", str(repo)], capture_output=True,
                             encoding="utf-8", errors="replace", timeout=RESTART_TIMEOUT_S,
                             env=env, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        return 2, f"restart did not run: {exc}"
    tail = [ln for ln in (res.stderr + res.stdout).splitlines() if ln.strip()]
    return res.returncode, (tail[-1].strip() if tail else f"exit {res.returncode}")


def _head(repo: Path) -> Optional[str]:
    res = git_run.run_git(["-C", str(repo), "rev-parse", "HEAD"])
    return res.stdout.strip() or None if res.returncode == 0 else None


def land_one(name: str, tbl: Mapping[str, object], never_kill: frozenset, issue: str, *,
             dry_run: bool = False,
             probe: Callable[[Optional[int], Optional[str]], Tuple[str, Optional[str], str]] = sp.probe_service,
             land: Callable[..., Tuple[int, List[str]]] = wcl.land_primary,
             restart: Callable[[Path], Tuple[int, str]] = run_restart,
             head: Callable[[Path], Optional[str]] = _head,
             behind: Optional[Callable[[Path], Optional[int]]] = None) -> Outcome:
    repo = Path(str(tbl["cwd_prefix"]))
    kind = recipe_kind(tbl, never_kill)
    port = tbl.get("webapp_port") if isinstance(tbl.get("webapp_port"), int) else None
    state, _sha, detail = probe(port, tbl.get("api_version_path")) if kind != NO_SERVICE else (sp.SERVICE_ABSENT, None, "")
    verdict = service_verdict(kind, state, detail)
    if dry_run:
        return _would(name, repo, kind, state, verdict, behind)
    before = head(repo)
    code, lines = land(repo, issue, service=verdict)
    first = next((ln for ln in lines if ln.startswith("PRIMARY=")), "PRIMARY=stale reason=no verdict from land-primary")
    extra = [ln for ln in lines if not ln.startswith("PRIMARY=")]
    if code != 0 or not first.startswith("PRIMARY=live"):
        return Outcome(name, " | ".join([first, *extra]), False)
    after = head(repo)
    if kind != RESTART or state != sp.SERVICE_LIVE or not before or before == after:
        return Outcome(name, " | ".join([first, *extra]), True)
    rc, last = restart(repo)
    if rc == 0:
        return Outcome(name, " | ".join([f"{first} restarted=yes build={(after or '')[:HEAD7]}", *extra]), True)
    why = "restart not confirmed (build identity unknown)" if rc == 3 else f"restart failed (exit {rc})"
    return Outcome(name, f"PRIMARY=stale reason=landed but {why}: {last}", False)


def _would(name: str, repo: Path, kind: str, state: str, verdict: Tuple[bool, str],
           behind: Optional[Callable[[Path], Optional[int]]]) -> Outcome:
    n = behind(repo) if behind else None
    if n == 0:
        return Outcome(name, "WOULD=nothing (already level)", True)
    if n is None:
        return Outcome(name, "WOULD=stale reason=could not count commits behind origin", False)
    if not verdict[0]:
        return Outcome(name, f"WOULD=stale reason={verdict[1]}", False)
    restart = kind == RESTART and state == sp.SERVICE_LIVE
    return Outcome(name, f"WOULD=land {n} commit(s)" + (" then restart" if restart else ""), True)


def stale_line(outcomes: Sequence[Outcome]) -> str:
    """The wave report's single stale-primaries line; `none` is a positive finding, not a default."""
    stale = [o for o in outcomes if not o.live]
    if not stale:
        return f"STALE_PRIMARIES=none (checked {len(outcomes)})"
    detail = "; ".join(f"{o.repo} ({_reason(o.line)})" for o in stale)
    return f"STALE_PRIMARIES={len(stale)}: {detail}"


def _reason(line: str) -> str:
    head = line.split(" | ", 1)[0]
    return head.split("reason=", 1)[1] if "reason=" in head else head


def behind_counts(tables: Mapping[str, Mapping[str, object]], names: Sequence[str],
                  count: Callable[[Path], Optional[int]]) -> Dict[str, Optional[int]]:
    """`{repo: commits behind}` for the checkouts that exist; None where it could not be counted."""
    present = [n for n in names if (Path(str(tables[n]["cwd_prefix"])) / ".git").exists()]
    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(zip(present, pool.map(lambda n: count(Path(str(tables[n]["cwd_prefix"]))), present)))


def count_behind_fetched(repo: Path) -> Optional[int]:
    return wcl.count_behind(repo, wcl.main_ref(repo))


def stale_report(counts: Mapping[str, Optional[int]]) -> List[str]:
    """One line per primary behind or uncountable, then the summary. Unknown is its own state."""
    flagged = sorted(n for n, c in counts.items() if c is None or c > 0)
    lines = [f"UNKNOWN {n}  could not count commits behind origin" if counts[n] is None
             else f"BEHIND {n}  {counts[n]} commit(s) behind origin" for n in flagged]
    lines.append(f"STALE_PRIMARIES={len(flagged)}: {', '.join(flagged)}" if flagged
                 else f"STALE_PRIMARIES=none (checked {len(counts)})")
    return lines


def stale_lines(count: Callable[[Path], Optional[int]] = count_behind_fetched) -> List[str]:
    """`stale_report` over the in-scope fleet; an unreadable registry is `unknown`, never `none`."""
    try:
        tables = fleet_repo_scan.fleet_repo_tables()
        ignored = fleet_repo_scan.ignored_repos()
    except (OSError, ValueError) as exc:
        return [f"STALE_PRIMARIES=unknown (registry unreadable: {exc})"]
    return stale_report(behind_counts(tables, sorted(n for n in tables if n not in ignored), count))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("land")
    lp.add_argument("repos", nargs="*")
    lp.add_argument("--all-behind", action="store_true")
    lp.add_argument("--issue", default="propagate-vendored")
    lp.add_argument("--dry-run", action="store_true")
    sub.add_parser("stale")
    args = ap.parse_args(argv)

    toml = fleet_repo_scan.load_projects_toml()
    tables = fleet_repo_scan.fleet_repo_tables()
    if args.cmd == "stale":
        print("\n".join(stale_report(behind_counts(tables, sorted(tables), count_behind_fetched))))
        return 0
    names = list(args.repos)
    unknown = [n for n in names if n not in tables]
    if unknown:
        print(f"REFUSED=unknown-repo {', '.join(unknown)} (not in hooks/projects.toml)")
        return 2
    if args.all_behind:
        counts = behind_counts(tables, sorted(tables), count_behind_fetched)
        names += [n for n, c in counts.items() if c != 0 and n not in names]
    never_kill = never_kill_ports(toml)
    outcomes = []
    for name in names:
        out = land_one(name, tables[name], never_kill, args.issue, dry_run=args.dry_run,
                       behind=count_behind_fetched)
        print(f"{name}: {out.line}")
        outcomes.append(out)
    print(stale_line(outcomes))
    return 0 if all(o.live for o in outcomes) else 1


if __name__ == "__main__":
    sys.exit(main())
