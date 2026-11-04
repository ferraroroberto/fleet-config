"""Shared helpers for the chief-mailbox kind tests: real-clock stamps, event publishing and the real `chief_ops` CLI over a temp inbox."""

from __future__ import annotations

import contextlib
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _suite import co  # noqa: E402


def stamp(seconds_ago: int) -> str:
    # The readers filter by the real clock (24 h TTL), so events carry real stamps.
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def publish(inbox: Path, name: str, event: dict) -> None:
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / name).write_text(json.dumps(event), encoding="utf-8")


def run_cli(inbox: Path, *argv: str) -> "tuple[int, str]":
    old_dir, old_request = co.mailbox.chief_inbox_dir, co.launcher._request
    co.mailbox.chief_inbox_dir = lambda: inbox
    co.launcher._request = lambda base, path: {}  # the timeout path's board digest, never a live launcher
    try:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = co.main(list(argv))
    finally:
        co.mailbox.chief_inbox_dir, co.launcher._request = old_dir, old_request
    return code, out.getvalue()
