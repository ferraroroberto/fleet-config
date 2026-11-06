"""`chief_ops.py heal`: the chief's job self-heal ledger and its one guarded re-run.

A failed app-launcher Job reaches the chief as a `job-failed` event
(`.claude/skills/chief/job-failed.md`). With Settings > Chief
`chief_job_self_heal` on, the chief files and dispatches the fix itself, and
this command owns the two things that must not rest on the chief's memory:

1. **Switch.** `chief_job_self_heal` from `/api/board/chief/settings`, read on
   every call. Only a literal `true` is on. Off, missing or unreadable prints
   `HEAL=off reason=...` and writes nothing, so the ledger stays empty and the
   chief follows the switch-off rules unchanged.
2. **Ledger.** `chief-job-heal.json` in the hooks state dir: one entry per
   failed run with its cause class, fix PR, re-run and outcome. It never holds
   job output. `record` flags a cause class seen again for the same job.
3. **Re-run.** `rerun` fires `POST /api/jobs/<id>/run` at most once per failed
   run, and only when the job carries `rerun_safe: true` in the launcher's
   `/api/jobs` (owner-set; nothing here writes a job definition), the fix PR
   is merged, and its merge commit is in the primary checkout's `HEAD` of the
   PR's repo. It never sends `confirmed=1`. Each refusal prints its own
   `HEAL_REFUSED=<job> reason=` line; a fact that could not be read prints
   `HEAL=unknown`, never a pass.

Subcommands: `record`, `rerun`, `outcome`, `show`.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import urllib.error
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import git_run
from active_issue import _iso_z, _now, state_lock, write_rows
from fleet_core import fleet_repo_scan
from fleet_core.hooks_state import state_dir

from . import launcher

LEDGER_FILENAME = "chief-job-heal.json"
SWITCH_KEY = "chief_job_self_heal"

CAUSE_CLASSES = ("transient", "defect", "config", "dependency", "environment", "unknown")

# Two failures of one job with one cause class inside a month read as a pattern the
# next patch will not end, so the chief proposes a structural fix instead.
REPEAT_WINDOW = timedelta(days=30)
REPEAT_THRESHOLD = 2

# Written before the POST, so a re-run whose answer never arrived still uses up the
# one allowed re-run: the launcher may have started it.
RERUN_IN_FLIGHT = "in-flight"

_REF = re.compile(r"^([A-Za-z0-9_.-]+)#([0-9]+)$")
_RUN_OUTCOMES = {"success": "success", "failed": "failed"}
_PENDING_STATUSES = frozenset({"running", "pending", "queued"})


# ---- pure decisions -----------------------------------------------------------

def heal_switch(reply: Any) -> Optional[str]:
    """None when self-heal is on, else the `HEAL=off` line. Unknown is never on."""
    settings = reply.get("settings") if isinstance(reply, dict) else None
    if not isinstance(settings, dict):
        return "HEAL=off reason=settings-unreadable"
    if SWITCH_KEY not in settings:
        return "HEAL=off reason=setting-missing"
    value = settings[SWITCH_KEY]
    if value is True:
        return None
    if value is False:
        return "HEAL=off reason=switch-off"
    return f"HEAL=off reason=setting-not-boolean value={json.dumps(value)}"


def parse_ref(ref: str) -> Optional[Tuple[str, int]]:
    match = _REF.match(ref or "")
    return (match.group(1), int(match.group(2))) if match else None


def find_entry(entries: List[Dict[str, Any]], job_id: str, run_id: str) -> Optional[Dict[str, Any]]:
    return next((e for e in entries if e["job_id"] == job_id and e["run_id"] == run_id), None)


def rerun_of(entries: List[Dict[str, Any]], job_id: str, run_id: str) -> Optional[Dict[str, Any]]:
    """The entry whose re-run is `run_id`: a failure of the chief's own re-run."""
    return next((e for e in entries if e["job_id"] == job_id and e.get("rerun_run_id") == run_id), None)


def repeat_count(entries: List[Dict[str, Any]], job_id: str, cause: str, now: datetime) -> int:
    """Entries for `job_id` with `cause` recorded inside the repeat window."""
    count = 0
    for entry in entries:
        if entry["job_id"] != job_id or entry.get("cause") != cause:
            continue
        try:
            stamp = datetime.fromisoformat(entry["recorded_at"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if now - stamp <= REPEAT_WINDOW:
            count += 1
    return count


def job_rerun_safe(jobs_reply: Any, job_id: str) -> Tuple[Optional[bool], str]:
    """`(True, "")` only for a job whose `rerun_safe` is literally true; `(None, why)` when unreadable."""
    jobs = jobs_reply.get("jobs") if isinstance(jobs_reply, dict) else None
    if not isinstance(jobs, list):
        return None, "jobs-unreadable"
    job = next((j for j in jobs if isinstance(j, dict) and j.get("id") == job_id), None)
    if job is None:
        return False, "job-not-found"
    if "rerun_safe" not in job:
        return False, "rerun-safe-absent"
    if job["rerun_safe"] is True:
        return True, ""
    return False, "not-rerun-safe" if job["rerun_safe"] is False else "rerun-safe-not-boolean"


def run_outcome(record: Any) -> str:
    """`success`, `failed`, `pending` or `unknown` for a run record from the launcher."""
    status = record.get("status") if isinstance(record, dict) else None
    if status in _RUN_OUTCOMES:
        return _RUN_OUTCOMES[status]
    return "pending" if status in _PENDING_STATUSES else "unknown"


# ---- outside reads (tests replace these) ----------------------------------------

def fetch_pr_merge(repo: str, number: int) -> Tuple[Optional[str], str]:
    """`(merge sha, "")` for a merged PR, `(None, "not-merged")`, or `(None, "unknown: why")`."""
    res = git_run.run_gh(
        ["pr", "view", str(number), "--repo", f"{launcher.DEFAULT_OWNER}/{repo}",
         "--json", "state,mergeCommit"],
        timeout=30, stdin=subprocess.DEVNULL,
    )
    if res.returncode != 0:
        return None, f"unknown: gh exit {res.returncode}"
    try:
        doc = json.loads(res.stdout)
    except ValueError:
        return None, "unknown: gh reply not json"
    if doc.get("state") != "MERGED":
        return None, "not-merged"
    sha = (doc.get("mergeCommit") or {}).get("oid")
    return (sha, "") if sha else (None, "unknown: merged PR has no merge commit")


def primary_checkout(repo: str) -> Optional[Path]:
    """The repo's primary checkout from `hooks/projects.toml`, the tree its Jobs run from.

    A dormant repo has none here, so a fix landing there reads as unknown and nothing re-runs.
    """
    return fleet_repo_scan.fleet_repos().get(repo)


def fix_is_live(checkout: Path, sha: str) -> Tuple[Optional[bool], str]:
    """Is `sha` an ancestor of (or equal to) the checkout's `HEAD`? `(None, why)` when git cannot say."""
    res = git_run.run_git(["-C", str(checkout), "merge-base", "--is-ancestor", sha, "HEAD"], timeout=30)
    if res.returncode == 0:
        return True, ""
    if res.returncode == 1:
        return False, "fix-not-in-primary-head"
    return None, f"git exit {res.returncode}"


# ---- ledger ---------------------------------------------------------------------

def ledger_file() -> Path:
    return state_dir() / LEDGER_FILENAME


def load_ledger(path: Path) -> List[Dict[str, Any]]:
    """Ledger entries; a missing file is empty, an unparseable one raises ValueError."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path.name} unreadable: {exc}") from exc
    entries = doc.get("entries") if isinstance(doc, dict) else None
    if not isinstance(entries, list):
        raise ValueError(f"{path.name} has no entries list")
    return entries


def update_ledger(path: Path, change: Callable[[List[Dict[str, Any]]], Any]) -> Any:
    """Apply `change` to the entries under the state lock and write them back."""
    with state_lock(path):
        entries = load_ledger(path)
        result = change(entries)
        write_rows(path, {"version": 1, "entries": entries})
    return result


def _entry_line(entry: Dict[str, Any]) -> str:
    return (f"HEAL_ENTRY={entry['job_id']} run={entry['run_id']} cause={entry.get('cause') or '-'} "
            f"issue={entry.get('issue') or '-'} pr={entry.get('pr') or '-'} "
            f"rerun={entry.get('rerun_run_id') or '-'} outcome={entry.get('outcome') or '-'} "
            f"needs_owner={'yes' if entry.get('needs_owner') else 'no'}")


# ---- the commands ---------------------------------------------------------------

def _switch_off(args: argparse.Namespace) -> Optional[str]:
    """The `HEAL=off` line, or None when the switch reads on right now."""
    try:
        reply = launcher._request(args.base_url, "/api/board/chief/settings")
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return f"HEAL=off reason=settings-unreadable ({exc})"
    return heal_switch(reply)


def _record(args: argparse.Namespace, entries: List[Dict[str, Any]]) -> List[str]:
    rerun_entry = rerun_of(entries, args.job_id, args.run)
    if rerun_entry is not None:
        rerun_entry["outcome"] = "failed"
        rerun_entry["needs_owner"] = True
        return [f"HEAL_NEEDS_OWNER={args.job_id} failed_run={rerun_entry['run_id']} rerun={args.run} "
                "reason=rerun-failed"]
    entry = find_entry(entries, args.job_id, args.run)
    if entry is None:
        entry = {"job_id": args.job_id, "run_id": args.run, "recorded_at": _iso_z(_now())}
        entries.append(entry)
    for field in ("cause", "issue", "pr"):
        value = getattr(args, field)
        if value:
            entry[field] = value
    lines = [_entry_line(entry).replace("HEAL_ENTRY=", "HEAL_RECORDED=", 1)]
    count = repeat_count(entries, args.job_id, entry.get("cause") or "", _now()) if entry.get("cause") else 0
    if count >= REPEAT_THRESHOLD:
        lines.append(f"HEAL_REPEAT={args.job_id} cause={entry['cause']} count={count} "
                     f"window_days={REPEAT_WINDOW.days} -> propose a structural fix, not another patch")
    return lines


def cmd_record(args: argparse.Namespace) -> int:
    for ref in (args.issue, args.pr):
        if ref and parse_ref(ref) is None:
            print(f"HEAL_REFUSED={args.job_id} reason=bad-ref ({ref}; want <repo>#<n>)")
            return 2
    off = _switch_off(args)
    if off is not None:
        print(off)
        return 0
    try:
        lines = update_ledger(ledger_file(), lambda entries: _record(args, entries))
    except (ValueError, OSError) as exc:
        print(f"HEAL=unknown reason=ledger ({exc})")
        return 1
    print("\n".join(lines))
    return 0


def _rerun_refusal(args: argparse.Namespace, entry: Optional[Dict[str, Any]]) -> Optional[Tuple[str, int]]:
    """`(line, exit code)` when the re-run must not fire, else None. Cheapest facts first."""
    job = args.job_id
    if entry is None:
        return f"HEAL_REFUSED={job} reason=not-in-ledger run={args.run}", 2
    if entry.get("rerun_run_id"):
        return f"HEAL_REFUSED={job} reason=already-rerun rerun={entry['rerun_run_id']}", 2
    pr = parse_ref(entry.get("pr") or "")
    if pr is None:
        return f"HEAL_REFUSED={job} reason=no-fix-pr", 2
    try:
        jobs_reply = launcher._request(args.base_url, "/api/jobs", timeout=launcher.JOBS_TIMEOUT)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return f"HEAL=unknown job={job} reason=jobs-unreadable ({exc})", 1
    safe, why = job_rerun_safe(jobs_reply, job)
    if safe is None:
        return f"HEAL=unknown job={job} reason={why}", 1
    if not safe:
        return f"HEAL_REFUSED={job} reason={why}", 2
    sha, why = fetch_pr_merge(*pr)
    if sha is None:
        if why.startswith("unknown"):
            return f"HEAL=unknown job={job} reason=pr {entry['pr']} {why}", 1
        return f"HEAL_REFUSED={job} reason={why} pr={entry['pr']}", 2
    checkout = primary_checkout(pr[0])
    if checkout is None:
        return f"HEAL=unknown job={job} reason=no-primary-checkout repo={pr[0]}", 1
    live, why = fix_is_live(checkout, sha)
    if live is None:
        return f"HEAL=unknown job={job} reason=liveness ({why})", 1
    if not live:
        return f"HEAL_REFUSED={job} reason={why} sha={sha[:12]}", 2
    entry["merge_sha"] = sha
    return None


def _claim_rerun(path: Path, job_id: str, run_id: str, sha: str) -> bool:
    """Mark the entry's one re-run as used; False when another call already took it."""
    def claim(entries: List[Dict[str, Any]]) -> bool:
        entry = find_entry(entries, job_id, run_id)
        if entry is None or entry.get("rerun_run_id"):
            return False
        entry["rerun_run_id"] = RERUN_IN_FLIGHT
        entry["rerun_at"] = _iso_z(_now())
        entry["merge_sha"] = sha
        return True
    return update_ledger(path, claim)


def _set_fields(path: Path, job_id: str, run_id: str, **fields: Any) -> None:
    def change(entries: List[Dict[str, Any]]) -> None:
        entry = find_entry(entries, job_id, run_id)
        if entry is not None:
            entry.update(fields)
    update_ledger(path, change)


def cmd_rerun(args: argparse.Namespace) -> int:
    off = _switch_off(args)
    if off is not None:
        print(off)
        return 0
    path = ledger_file()
    try:
        entry = find_entry(load_ledger(path), args.job_id, args.run)
    except ValueError as exc:
        print(f"HEAL=unknown reason=ledger ({exc})")
        return 1
    refusal = _rerun_refusal(args, entry)
    if refusal is not None:
        print(refusal[0])
        return refusal[1]
    if not _claim_rerun(path, args.job_id, args.run, entry["merge_sha"]):
        print(f"HEAL_REFUSED={args.job_id} reason=already-rerun")
        return 2
    job_path = f"/api/jobs/{urllib.parse.quote(args.job_id, safe='')}/run"
    try:
        reply = launcher._request(args.base_url, job_path, method="POST", body={})
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        # The claim stays: a POST with no answer may have started the run.
        print(f"HEAL=unconfirmed job={args.job_id} reason=run-post ({exc}) -> check the Jobs tab; "
              "no second re-run")
        return 1
    new_run = reply.get("run_id") if isinstance(reply, dict) else None
    if not new_run:
        print(f"HEAL=unconfirmed job={args.job_id} reason=no-run-id -> check the Jobs tab; no second re-run")
        return 1
    _set_fields(path, args.job_id, args.run, rerun_run_id=new_run, outcome="pending")
    print(f"HEAL_RERUN={args.job_id} failed_run={args.run} rerun={new_run} sha={entry['merge_sha'][:12]}")
    return 0


def cmd_outcome(args: argparse.Namespace) -> int:
    off = _switch_off(args)
    if off is not None:
        print(off)
        return 0
    path = ledger_file()
    try:
        entry = find_entry(load_ledger(path), args.job_id, args.run)
    except ValueError as exc:
        print(f"HEAL=unknown reason=ledger ({exc})")
        return 1
    rerun = (entry or {}).get("rerun_run_id")
    if not rerun or rerun == RERUN_IN_FLIGHT:
        print(f"HEAL=unknown job={args.job_id} reason={'no-rerun-id' if rerun else 'no-rerun'}")
        return 1
    quoted = urllib.parse.quote(args.job_id, safe="")
    try:
        record = launcher._request(args.base_url, f"/api/jobs/{quoted}/runs/{urllib.parse.quote(rerun, safe='')}")
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"HEAL=unknown job={args.job_id} reason=run-unreadable ({exc})")
        return 1
    outcome = run_outcome(record)
    fields: Dict[str, Any] = {"outcome": outcome}
    if outcome == "failed":
        fields["needs_owner"] = True
    if outcome in ("success", "failed"):
        _set_fields(path, args.job_id, args.run, **fields)
    print(f"HEAL_OUTCOME={args.job_id} rerun={rerun} outcome={outcome}"
          + (" needs_owner=yes" if outcome == "failed" else ""))
    return 0 if outcome != "unknown" else 1


def cmd_show(args: argparse.Namespace) -> int:
    try:
        entries = load_ledger(ledger_file())
    except ValueError as exc:
        print(f"HEAL=unknown reason=ledger ({exc})")
        return 1
    shown = [e for e in entries if not args.job_id or e["job_id"] == args.job_id]
    for entry in shown:
        print(_entry_line(entry))
    print(f"HEAL_ENTRIES={len(shown)}")
    return 0


def add_parser(sub: argparse._SubParsersAction) -> None:
    """`heal record|rerun|outcome|show`, registered by `chief.cli`."""
    heal = sub.add_parser("heal", help="job self-heal ledger and its one guarded re-run (fleet-config#1897)")
    verbs = heal.add_subparsers(dest="heal_cmd", required=True)

    rec = verbs.add_parser("record", help="record a failed run, its cause and fix; flags a repeated cause")
    rec.add_argument("job_id")
    rec.add_argument("--run", required=True, help="the failed run's id from the job-failed event")
    rec.add_argument("--cause", choices=CAUSE_CLASSES, default=None)
    rec.add_argument("--issue", default=None, help="<repo>#<n> of the fix issue")
    rec.add_argument("--pr", default=None, help="<repo>#<n> of the fix PR")
    rec.set_defaults(func=cmd_record)

    rr = verbs.add_parser("rerun", help="re-run a rerun_safe job once, after its fix is live")
    rr.add_argument("job_id")
    rr.add_argument("--run", required=True, help="the failed run the fix answers")
    rr.set_defaults(func=cmd_rerun)

    oc = verbs.add_parser("outcome", help="read the re-run's result into the ledger")
    oc.add_argument("job_id")
    oc.add_argument("--run", required=True, help="the failed run that was re-run")
    oc.set_defaults(func=cmd_outcome)

    sh = verbs.add_parser("show", help="print the ledger (no job output is ever stored)")
    sh.add_argument("job_id", nargs="?", default=None)
    sh.set_defaults(func=cmd_show)

    for verb in (rec, rr, oc, sh):
        verb.add_argument("--base-url", default=launcher.DEFAULT_BASE_URL)
