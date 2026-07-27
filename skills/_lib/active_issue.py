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
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from no_window import NO_WINDOW  # noqa: E402

STATE_FILENAME = "active-issues.json"
PRUNE_AFTER = timedelta(hours=24)
LOCK_TIMEOUT_SECONDS = 5.0
STALE_LOCK_AFTER_SECONDS = 30.0
_REPLACE_ATTEMPTS = 3


def state_file() -> Path:
    """Resolve the state path at call time so tests can override its root."""
    root = os.environ.get("CLAUDE_HOOKS_STATE_DIR")
    base = Path(root) if root else Path.home() / ".claude" / "hooks" / "state"
    return base / STATE_FILENAME


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_z(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _parse_started_at(row: Any) -> Optional[datetime]:
    if not isinstance(row, dict):
        return None
    raw = row.get("started_at")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def read_rows(path: Optional[Path] = None) -> Dict[str, Any]:
    """Return the current mapping, or an empty mapping for invalid input."""
    target = path or state_file()
    try:
        data = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def prune_rows(
    rows: Dict[str, Any], *, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Keep only well-formed records no older than :data:`PRUNE_AFTER`."""
    cutoff = (now or _now()) - PRUNE_AFTER
    kept: Dict[str, Any] = {}
    for key, row in rows.items():
        stamp = _parse_started_at(row)
        if stamp is not None and stamp >= cutoff:
            kept[str(key)] = row
    return kept


def _write_rows(path: Path, rows: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(rows, indent=2, sort_keys=True) + "\n"
    last_error: Optional[OSError] = None
    for attempt in range(_REPLACE_ATTEMPTS):
        tmp_name: Optional[str] = None
        try:
            fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
            os.replace(tmp_name, path)
            return
        except OSError as exc:
            last_error = exc
            if tmp_name:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
            time.sleep(0.05 * (attempt + 1))
    assert last_error is not None
    raise last_error


@contextmanager
def state_lock(
    path: Path,
    *,
    timeout_seconds: float = LOCK_TIMEOUT_SECONDS,
    stale_after_seconds: float = STALE_LOCK_AFTER_SECONDS,
) -> Iterator[None]:
    """Serialize one state-file read-modify-write transaction.

    ``mkdir`` is atomic on Windows and POSIX.  A crashed writer's empty lock
    directory is reclaimable after a short horizon; ordinary mutations hold it
    only for the milliseconds needed to parse and replace one small JSON file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_dir = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            lock_dir.mkdir()
            break
        except (FileExistsError, PermissionError):
            # Windows can surface PermissionError instead of FileExistsError
            # when this mkdir() races another thread's rmdir() on the same
            # path (NTFS reports "access denied" for a directory mid-deletion
            # rather than "already exists") — treat it identically: retry.
            try:
                age = time.time() - lock_dir.stat().st_mtime
                if age > stale_after_seconds:
                    shutil.rmtree(lock_dir)
                    continue
            except (FileNotFoundError, PermissionError):
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for active-issue state lock: {lock_dir}")
            time.sleep(0.025)
    try:
        yield
    finally:
        try:
            lock_dir.rmdir()
        except FileNotFoundError:
            pass


def _remote_repo_name(repo_path: Path) -> Optional[str]:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        creationflags=NO_WINDOW,
    )
    if result.returncode != 0:
        return None
    remote = result.stdout.strip().rstrip("/\\")
    name = re.split(r"[/\\:]", remote)[-1]
    return name[:-4] if name.lower().endswith(".git") else name or None


def resolve_repo_name(repo: str | Path) -> str:
    """Resolve the canonical repo name, including from a sibling worktree."""
    repo_path = Path(repo).resolve()
    remote_name = _remote_repo_name(repo_path)
    if remote_name:
        return remote_name
    return re.sub(r"-wt-\d+$", "", repo_path.name, flags=re.IGNORECASE)


def marker_key(repo_name: str, issue: int) -> str:
    return f"{repo_name}#{issue}"


def add_marker(
    repo: str | Path,
    issue: int,
    branch: str,
    *,
    now: Optional[datetime] = None,
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Add/refresh one marker and return the row written."""
    if issue <= 0:
        raise ValueError("issue number must be positive")
    if not branch.strip():
        raise ValueError("branch must not be empty")
    target = path or state_file()
    moment = now or _now()
    repo_name = resolve_repo_name(repo)
    row: Dict[str, Any] = {
        "repo": repo_name,
        "number": issue,
        "branch": branch,
        "started_at": _iso_z(moment),
    }
    with state_lock(target):
        rows = prune_rows(read_rows(target), now=moment)
        rows[marker_key(repo_name, issue)] = row
        _write_rows(target, rows)
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
    moment = now or _now()
    repo_name = resolve_repo_name(repo)
    key = marker_key(repo_name, issue)
    with state_lock(target):
        rows = prune_rows(read_rows(target), now=moment)
        removed = rows.pop(key, None) is not None
        _write_rows(target, rows)
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
