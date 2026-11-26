"""Mine every agent session log on this machine for error signatures that keep coming back.

Each Claude Code, Codex and Pi log is reduced by `digest.build` to its error
signatures; only the signature counts, the agent, the repo and the log's last
change are kept, in a machine-local cache keyed by path. The cache is the
watermark: a log whose size and modification time match its cached row is not
read again, so the first run is a full backfill and a later one reads only the
logs that changed since.

A log no adapter recognises, or one that cannot be read, is cached as
`unmeasured` and counted as such, never as a clean session. stdlib only.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from retro import digest

CACHE_FILENAME = "retro-fleet-cache.json"
CACHE_VERSION = 1
#: A signature counts as recurring once this many sessions hit it inside the window.
MIN_SESSIONS = 3
WINDOW_DAYS = 30
#: Logs digested in parallel; the backfill is CPU-bound JSON parsing and shares the box.
WORKERS = 4
#: The classes whose check already exists and fired: reported, never proposed again.
ENFORCED_CLASSES = ("hook_refusal",)
#: The owner said no; nothing a check could add.
OWNER_CLASSES = ("user_rejected",)
#: One log per session, under each agent's own sessions folder.
AGENT_ROOTS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("claude", (".claude", "projects")),
    ("codex", (".codex", "sessions")),
    ("pi", (".pi", "agent", "sessions")),
)


def default_roots(home: Path) -> List[Tuple[str, Path]]:
    """Each agent's session-log root under `home`, existing or not."""
    return [(agent, home.joinpath(*parts)) for agent, parts in AGENT_ROOTS]


def list_logs(roots: Iterable[Tuple[str, Path]]) -> Dict[str, Tuple[str, int, int, float]]:
    """`{path: (root agent, mtime_ns, size, mtime)}` for every `.jsonl` under the roots."""
    found: Dict[str, Tuple[str, int, int, float]] = {}
    for agent, root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.jsonl"):
            try:
                st = path.stat()
            except OSError:
                continue
            found[str(path)] = (agent, st.st_mtime_ns, st.st_size, st.st_mtime)
    return found


def _has_messages(path: Path) -> bool:
    """Whether any record could carry a conversation in one of the three agents' shapes."""
    records, _ = digest.read_records(path)
    return any(isinstance(r.get("message"), dict) or isinstance(r.get("payload"), dict) for r in records)


def digest_row(path: str) -> Dict[str, Any]:
    """The cached row for one log: its signatures, `empty`, or `unmeasured` with the reason.

    `empty` is a log of metadata records only (a session opened and closed with
    no turn): nothing to measure, so it is neither clean nor unmeasured.
    """
    try:
        out = digest.build(Path(path), digest.DEFAULT_PRIVATE)
    except digest.NotConfirmed as exc:
        reason = str(exc).split(":", 1)[0]
        if reason == "unmeasured" and not _has_messages(Path(path)):
            return {"status": "empty"}
        return {"status": "unmeasured", "reason": reason}
    except (OSError, ValueError, RecursionError) as exc:
        return {"status": "unmeasured", "reason": type(exc).__name__}
    return {"status": "ok", "agent": out["agent"], "repo": out["repo"],
            "signatures": out["error_signatures"]}


@dataclass
class ScanResult:
    cache: Dict[str, Any]
    logs: Counter = field(default_factory=Counter)
    digested: int = 0
    unchanged: int = 0
    gone: int = 0


def scan(roots: Iterable[Tuple[str, Path]], cache: Dict[str, Any], workers: int = WORKERS) -> ScanResult:
    """Bring the cache up to date with the logs on disk; returns it with the run's counts.

    A log that disappeared keeps its row (marked gone) so its history still
    counts toward first-seen and the 30-day window.
    """
    files: Dict[str, Any] = dict(cache.get("files") or {}) if cache.get("version") == CACHE_VERSION else {}
    on_disk = list_logs(roots)
    result = ScanResult(cache={"version": CACHE_VERSION, "files": files})
    todo: List[str] = []
    for path, (agent, mtime_ns, size, _) in on_disk.items():
        result.logs[agent] += 1
        row = files.get(path)
        if isinstance(row, dict) and row.get("mtime_ns") == mtime_ns and row.get("size") == size:
            result.unchanged += 1
        else:
            todo.append(path)
    for path, row in files.items():
        if path not in on_disk and isinstance(row, dict) and not row.get("gone"):
            row["gone"] = True
            result.gone += 1
    rows = _digest_all(todo, workers)
    for path, row in zip(todo, rows):
        _, mtime_ns, size, mtime = on_disk[path]
        row.update(mtime_ns=mtime_ns, size=size,
                   when=datetime.fromtimestamp(mtime, timezone.utc).date().isoformat())
        files[path] = row
    result.digested = len(todo)
    return result


def _digest_all(paths: List[str], workers: int) -> List[Dict[str, Any]]:
    if workers <= 1 or len(paths) < 2:
        return [digest_row(p) for p in paths]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(digest_row, paths, chunksize=16))


def signature_key(signature: str) -> str:
    """A short stable id for one signature: the suggestion id and page anchor build on it."""
    return "sig-" + hashlib.sha256(signature.encode("utf-8")).hexdigest()[:10]


def repo_label(repo: str, home_name: str) -> str:
    """A session started in the home folder would name the account; it shows as `<home>`."""
    return "<home>" if repo.lower() == home_name.lower() else repo


def _trend(recent: int, earlier: int) -> str:
    if recent > earlier:
        return "rising"
    if recent < earlier:
        return "falling"
    return "steady"


def aggregate(cache: Dict[str, Any], today: datetime, home_name: str,
              window_days: int = WINDOW_DAYS, min_sessions: int = MIN_SESSIONS) -> List[Dict[str, Any]]:
    """One row per error signature across every cached log, most sessions in the window first.

    `home_name` is the home folder's name, shown as `<home>` wherever it is a session's repo.

    `flagged` means at least `min_sessions` sessions hit it in the last
    `window_days`; `kind` is `enforced` (a check already refused it), `owner`
    (the owner declined the call) or `candidate`. `trend` compares sessions in
    the window's newer half with its older half.
    """
    start = (today - timedelta(days=window_days)).date().isoformat()
    half = (today - timedelta(days=window_days / 2)).date().isoformat()
    rows: Dict[str, Dict[str, Any]] = {}
    for row in (cache.get("files") or {}).values():
        if not isinstance(row, dict) or row.get("status") != "ok":
            continue
        when = str(row.get("when") or "")
        for sig, count in (row.get("signatures") or {}).items():
            agg = rows.setdefault(sig, {"count": 0, "sessions": 0, "sessions_window": 0, "count_window": 0,
                                        "newer": 0, "older": 0, "repos": Counter(), "agents": Counter(),
                                        "first_seen": when, "last_seen": when})
            agg["count"] += int(count)
            agg["sessions"] += 1
            agg["repos"][repo_label(str(row.get("repo") or "unknown"), home_name)] += 1
            agg["agents"][str(row.get("agent") or "unknown")] += 1
            agg["first_seen"] = min(agg["first_seen"], when)
            agg["last_seen"] = max(agg["last_seen"], when)
            if when >= start:
                agg["sessions_window"] += 1
                agg["count_window"] += int(count)
                agg["newer" if when >= half else "older"] += 1
    out = [_finish(sig, agg, min_sessions) for sig, agg in rows.items()]
    out.sort(key=lambda r: (-r["sessions_window"], -r["count_window"], r["signature"]))
    return out


def _finish(sig: str, agg: Dict[str, Any], min_sessions: int) -> Dict[str, Any]:
    tool, cls, shown = (sig.split("|", 2) + ["", ""])[:3]
    kind = "enforced" if cls in ENFORCED_CLASSES else "owner" if cls in OWNER_CLASSES else "candidate"
    return {
        "key": signature_key(sig), "signature": sig, "tool": tool, "class": cls, "template": shown,
        "kind": kind, "flagged": agg["sessions_window"] >= min_sessions,
        "count": agg["count"], "sessions": agg["sessions"],
        "count_window": agg["count_window"], "sessions_window": agg["sessions_window"],
        "trend": _trend(agg["newer"], agg["older"]),
        "repos": dict(agg["repos"].most_common()), "agents": dict(agg["agents"].most_common()),
        "first_seen": agg["first_seen"], "last_seen": agg["last_seen"],
    }


def unmeasured(cache: Dict[str, Any]) -> Counter:
    """Unmeasured logs by reason."""
    return Counter(str(r.get("reason") or "unknown") for r in (cache.get("files") or {}).values()
                   if isinstance(r, dict) and r.get("status") == "unmeasured")


def empty(cache: Dict[str, Any]) -> int:
    """Logs with no conversation records at all."""
    return sum(1 for r in (cache.get("files") or {}).values() if isinstance(r, dict) and r.get("status") == "empty")


def candidates_doc(rows: List[Dict[str, Any]], scan_result: ScanResult, today: datetime,
                   top: int) -> Dict[str, Any]:
    """The classifier's input: the top flagged candidates and the enforced ones, counts only."""
    flagged = [r for r in rows if r["flagged"]]
    return {
        "generated": today.date().isoformat(),
        "window_days": WINDOW_DAYS, "min_sessions": MIN_SESSIONS,
        "logs": dict(scan_result.logs), "digested": scan_result.digested,
        "unchanged": scan_result.unchanged, "unmeasured": dict(unmeasured(scan_result.cache)),
        "empty": empty(scan_result.cache),
        "signatures": len(rows),
        "candidates": [r for r in flagged if r["kind"] == "candidate"][:top],
        "flagged_candidates": sum(1 for r in flagged if r["kind"] == "candidate"),
        "enforced": [r for r in flagged if r["kind"] == "enforced"],
        "owner_declined": [r for r in flagged if r["kind"] == "owner"],
    }


def load_cache(path: Path) -> Dict[str, Any]:
    """The cache, or an empty one when it is missing or unreadable (a full rescan follows)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def today_utc(now: Optional[datetime] = None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
