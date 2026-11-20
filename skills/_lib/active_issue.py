"""Maintain the Fleet Board's cross-repo active-issue markers.

Issue workflows call this helper after an issue branch is ready and after its
successful merge.  The shared state file lets App Launcher distinguish a
backlog issue that is already being worked without treating branch presence as
the source of truth.

The file is advisory and self-healing: missing/corrupt input reads as empty,
rows older than 24 hours are pruned on every mutation, and writes use an atomic
temporary-file replacement.  A small lock directory serializes the complete
read-modify-write cycle so concurrent ``/issue-batch`` writers cannot lose one
another's rows.
"""

from __future__ import annotations

import argparse
import os
import re
import socket
import sys
from datetime import datetime
from pathlib import Path
from typing import AbstractSet, Any, Callable, Dict, Mapping, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import git_run  # noqa: E402
from fleet_core import state_rows  # noqa: E402
from worktree_residue import WT_SEP  # noqa: E402

STATE_FILENAME = "active-issues.json"
# The row field `state_rows.prune_rows` ages a marker by.
STAMP_FIELD = "started_at"

# Optional owner fields on a claim row. Rows written before
# them, or by a writer with no resolvable owner, simply omit the field.
OWNER_SESSION_FIELD = "owner_session_id"
OWNER_PID_FIELD = "owner_pid"
OWNER_HOST_FIELD = "owner_host"
# The agent process that owns the session. Never this helper's own pid (or
# its shell's): both exit seconds after the write and would read as dead.
_OWNER_PID_ENV = "CLAUDE_PID"
_OWNER_SESSION_ENV = "APP_LAUNCHER_SESSION_ID"

LIVENESS_LIVE = "live"
LIVENESS_DEAD = "dead"
LIVENESS_UNKNOWN = "unknown"


def state_file() -> Path:
    """The active-issue marker file under the shared hooks-state root."""
    return state_rows.state_file(STATE_FILENAME)


def _remote_repo_name(repo_path: Path) -> Optional[str]:
    # The bare repo name out of `owner/repo` -- `git_run.origin_slug` owns the
    # `remote get-url origin` call plus the regex, shared with the other
    # call sites across the repo that need the same parse.
    slug = git_run.origin_slug(repo_path)
    return slug.rsplit("/", 1)[-1] if slug else None


def resolve_repo_name(repo: str | Path) -> str:
    """Resolve the canonical repo name, including from a sibling worktree."""
    repo_path = Path(repo).resolve()
    remote_name = _remote_repo_name(repo_path)
    if remote_name:
        return remote_name
    return re.sub(rf"{re.escape(WT_SEP)}\d+$", "", repo_path.name, flags=re.IGNORECASE)


def marker_key(repo_name: str, issue: int) -> str:
    return f"{repo_name}#{issue}"


def current_host() -> Optional[str]:
    """This machine's host id, or None when it cannot be resolved."""
    try:
        host = socket.gethostname().strip()
    except OSError:
        return None
    return host or None


def owner_fields(environ: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """Resolve the optional owner fields for a new claim row.

    Each field is included only when positively known: the App Launcher
    session id, the agent process pid, and the host both are scoped to. A
    field that cannot be resolved is omitted, never guessed.
    """
    env = os.environ if environ is None else environ
    fields: Dict[str, Any] = {}
    session_id = (env.get(_OWNER_SESSION_ENV) or "").strip()
    if session_id:
        fields[OWNER_SESSION_FIELD] = session_id
    raw_pid = (env.get(_OWNER_PID_ENV) or "").strip()
    if raw_pid.isdigit() and int(raw_pid) > 0:
        fields[OWNER_PID_FIELD] = int(raw_pid)
    host = current_host()
    if host and fields:
        fields[OWNER_HOST_FIELD] = host
    return fields


def classify_owner(
    row: Any,
    *,
    live_session_ids: Optional[AbstractSet[str]],
    pid_probe: Callable[[int], Optional[bool]],
    host: Optional[str],
) -> str:
    """Classify a claim row's owner as ``live`` / ``dead`` / ``unknown``.

    Pure apart from the injected ``pid_probe`` (pass :func:`state_rows.pid_state` for the
    real one). ``live_session_ids`` is the set of live App Launcher session
    ids, or None when that list could not be read; ``host`` is
    :func:`current_host`. Evidence of life wins over evidence of death, and
    ``dead`` needs positive evidence: the owner session absent from a
    readable list, or the owner pid confirmed gone on the owner's own host.
    Rows without owner fields, from another host, or with any unreadable
    input are ``unknown``. Classification never mutates or prunes a row.
    """
    if not isinstance(row, dict):
        return LIVENESS_UNKNOWN
    session_id = row.get(OWNER_SESSION_FIELD)
    pid = row.get(OWNER_PID_FIELD)
    owner_host = row.get(OWNER_HOST_FIELD)
    has_session = isinstance(session_id, str) and bool(session_id)
    has_pid = isinstance(pid, int) and not isinstance(pid, bool) and pid > 0
    if not isinstance(owner_host, str) or not owner_host or not host:
        return LIVENESS_UNKNOWN
    if owner_host.casefold() != host.casefold():
        return LIVENESS_UNKNOWN

    verdicts = []
    if has_session and live_session_ids is not None:
        verdicts.append(session_id in live_session_ids)
    if has_pid:
        try:
            alive = pid_probe(pid)
        except Exception:
            alive = None
        if alive is not None:
            verdicts.append(bool(alive))
    if any(verdicts):
        return LIVENESS_LIVE
    if verdicts:
        return LIVENESS_DEAD
    return LIVENESS_UNKNOWN


def add_marker(
    repo: str | Path,
    issue: int,
    branch: str,
    *,
    now: Optional[datetime] = None,
    path: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Add/refresh one marker and return the row written."""
    if issue <= 0:
        raise ValueError("issue number must be positive")
    if not branch.strip():
        raise ValueError("branch must not be empty")
    target = path or state_file()
    moment = now or state_rows.utc_now()
    repo_name = resolve_repo_name(repo)
    row: Dict[str, Any] = {
        "repo": repo_name,
        "number": issue,
        "branch": branch,
        STAMP_FIELD: state_rows.iso_z(moment),
        **owner_fields(environ),
    }
    with state_rows.state_lock(target):
        rows = state_rows.prune_rows(state_rows.read_rows(target), stamp_field=STAMP_FIELD, now=moment)
        rows[marker_key(repo_name, issue)] = row
        state_rows.write_rows(target, rows)
    return row


def remove_marker(
    repo: str | Path,
    issue: int,
    *,
    now: Optional[datetime] = None,
    path: Optional[Path] = None,
) -> bool:
    """Remove one marker while preserving and pruning all unrelated rows."""
    if issue <= 0:
        raise ValueError("issue number must be positive")
    target = path or state_file()
    moment = now or state_rows.utc_now()
    repo_name = resolve_repo_name(repo)
    key = marker_key(repo_name, issue)
    with state_rows.state_lock(target):
        rows = state_rows.prune_rows(state_rows.read_rows(target), stamp_field=STAMP_FIELD, now=moment)
        removed = rows.pop(key, None) is not None
        state_rows.write_rows(target, rows)
    return removed


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="active_issue", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    add_parser = sub.add_parser("add", help="record an issue branch as active")
    add_parser.add_argument("repo", help="repo or worktree path")
    add_parser.add_argument("issue", type=int)
    add_parser.add_argument("branch")

    remove_parser = sub.add_parser("remove", help="clear an active issue marker")
    remove_parser.add_argument("repo", help="repo or worktree path")
    remove_parser.add_argument("issue", type=int)

    args = parser.parse_args(argv)
    if args.command == "add":
        row = add_marker(args.repo, args.issue, args.branch)
        print(f"ACTIVE_ISSUE=added key={marker_key(row['repo'], row['number'])}")
    else:
        repo_name = resolve_repo_name(args.repo)
        removed = remove_marker(args.repo, args.issue)
        status = "removed" if removed else "absent"
        print(f"ACTIVE_ISSUE={status} key={marker_key(repo_name, args.issue)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
