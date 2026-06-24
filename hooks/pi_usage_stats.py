"""Pi coding-agent usage collector.

Reads Pi's JSONL session files (``~/.pi/agent/sessions``) and emits the same
kind of deterministic, content-free usage facts app-launcher can ingest for the
Coding stats surface: session, cwd/project, provider, model, and token totals.
No prompt/response text is printed.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

DEFAULT_SESSIONS_DIR = Path.home() / ".pi" / "agent" / "sessions"


@dataclass
class UsageTotals:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total: int = 0
    cost: float = 0.0

    def add(self, usage: dict[str, Any]) -> None:
        self.input += int(usage.get("input") or 0)
        self.output += int(usage.get("output") or 0)
        self.cache_read += int(usage.get("cacheRead") or 0)
        self.cache_write += int(usage.get("cacheWrite") or 0)
        self.total += int(usage.get("totalTokens") or 0)
        cost = usage.get("cost") or {}
        self.cost += float(cost.get("total") or 0.0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "input": self.input,
            "output": self.output,
            "cache_read": self.cache_read,
            "cache_write": self.cache_write,
            "total": self.total,
            "cost": round(self.cost, 6),
        }


@dataclass
class PiSessionUsage:
    session_id: str
    path: str
    started_at: str = ""
    last_at: str = ""
    cwd: str = ""
    project: str = ""
    provider: str = ""
    model: str = ""
    messages: int = 0
    assistant_messages: int = 0
    tool_calls: int = 0
    usage: UsageTotals = field(default_factory=UsageTotals)

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent": "pi",
            "session_id": self.session_id,
            "path": self.path,
            "started_at": self.started_at,
            "last_at": self.last_at,
            "cwd": self.cwd,
            "project": self.project,
            "provider": self.provider,
            "model": self.model,
            "messages": self.messages,
            "assistant_messages": self.assistant_messages,
            "tool_calls": self.tool_calls,
            "usage": self.usage.as_dict(),
        }


def _project_name(cwd: str) -> str:
    if not cwd:
        return ""
    return Path(cwd.replace("\\", "/")).name


def _iter_session_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return []
    return sorted(root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime)


def parse_session_file(path: Path) -> Optional[PiSessionUsage]:
    summary: Optional[PiSessionUsage] = None
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_type = event.get("type")
        timestamp = str(event.get("timestamp") or "")
        if summary is None:
            sid = str(event.get("id") or path.stem.split("_", 1)[-1])
            summary = PiSessionUsage(session_id=sid, path=str(path))
        if timestamp:
            summary.last_at = timestamp
            if not summary.started_at:
                summary.started_at = timestamp
        if event_type == "session":
            summary.session_id = str(event.get("id") or summary.session_id)
            summary.cwd = str(event.get("cwd") or summary.cwd)
            summary.project = _project_name(summary.cwd)
        elif event_type == "model_change":
            summary.provider = str(event.get("provider") or summary.provider)
            summary.model = str(event.get("modelId") or summary.model)
        elif event_type == "message":
            msg = event.get("message") or {}
            role = msg.get("role")
            summary.messages += 1
            if role == "assistant":
                summary.assistant_messages += 1
                content = msg.get("content") or []
                summary.tool_calls += sum(1 for item in content if (item or {}).get("type") == "toolCall")
                if msg.get("provider"):
                    summary.provider = str(msg.get("provider"))
                if msg.get("model"):
                    summary.model = str(msg.get("model"))
                usage = msg.get("usage")
                if isinstance(usage, dict):
                    summary.usage.add(usage)
    return summary


def collect(root: Path = DEFAULT_SESSIONS_DIR) -> list[PiSessionUsage]:
    return [s for p in _iter_session_files(root) for s in [parse_session_file(p)] if s]


def aggregate(sessions: Iterable[PiSessionUsage]) -> dict[str, Any]:
    totals = UsageTotals()
    by_model: dict[str, UsageTotals] = {}
    by_project: dict[str, UsageTotals] = {}
    session_count = 0
    for session in sessions:
        session_count += 1
        totals.input += session.usage.input
        totals.output += session.usage.output
        totals.cache_read += session.usage.cache_read
        totals.cache_write += session.usage.cache_write
        totals.total += session.usage.total
        totals.cost += session.usage.cost
        model_key = "/".join(x for x in (session.provider, session.model) if x) or "unknown"
        project_key = session.project or "unknown"
        for bucket, key in ((by_model, model_key), (by_project, project_key)):
            acc = bucket.setdefault(key, UsageTotals())
            acc.input += session.usage.input
            acc.output += session.usage.output
            acc.cache_read += session.usage.cache_read
            acc.cache_write += session.usage.cache_write
            acc.total += session.usage.total
            acc.cost += session.usage.cost
    return {
        "agent": "pi",
        "sessions": session_count,
        "usage": totals.as_dict(),
        "by_model": {k: v.as_dict() for k, v in sorted(by_model.items())},
        "by_project": {k: v.as_dict() for k, v in sorted(by_project.items())},
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Summarise Pi session token/model usage without prompt content.")
    parser.add_argument("--sessions-dir", type=Path, default=DEFAULT_SESSIONS_DIR)
    parser.add_argument("--json", action="store_true", help="emit JSON instead of the text summary")
    parser.add_argument("--include-sessions", action="store_true", help="include per-session rows in JSON output")
    args = parser.parse_args(argv)

    sessions = collect(args.sessions_dir)
    payload = aggregate(sessions)
    if args.include_sessions:
        payload["session_rows"] = [s.as_dict() for s in sessions]
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    usage = payload["usage"]
    print(f"pi sessions: {payload['sessions']} · tokens: {usage['total']} · cost: {usage['cost']}")
    for model, stats in payload["by_model"].items():
        print(f"model {model}: {stats['total']} tokens")
    return 0


if __name__ == "__main__":
    sys.exit(main())
