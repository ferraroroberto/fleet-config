"""Nudge away from re-implementing the local LLM hub with an inline `claude -p`.

Triggers on `PostToolUse` for `Edit`/`Write`/`MultiEdit`. **Non-blocking** —
emits a single one-line nudge on stdout (exit 0) and lets the edit stand. The
user decides whether the call is a legitimate one-off.

Fires when the edited file is a `*.py` anywhere EXCEPT inside the hub repo
itself (`local-llm-hub` / `claude-local-calls`) and its on-disk content spawns
an inline `claude -p` subprocess. Reason: the global "Don't duplicate hub
functionality" rule — downstream apps should route through the hub at
`http://127.0.0.1:8000` via the standard Anthropic/OpenAI SDKs, not re-roll a
`claude -p` subprocess wrapper.

Reads the file from disk (the PostToolUse target already exists), matching
`py_syntax_check.py`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402


# Repos that legitimately own a `claude -p` subprocess wrapper — the hub itself.
HUB_DIR_SEGMENTS = {"local-llm-hub", "claude-local-calls"}

# A subprocess-spawning indicator in the file.
SUBPROCESS_RE = re.compile(r"\b(?:subprocess|Popen|os\.system|check_output|check_call|getoutput)\b")

# `claude -p` either as a command-string fragment (`"claude -p ..."`) or as
# adjacent argv tokens (`["claude", "-p", ...]` / `('claude', '-p', ...)`).
CLAUDE_P_RE = re.compile(r"claude\s+-p\b|['\"]claude['\"]\s*,\s*['\"]-p['\"]")


def main() -> None:
    payload = _lib.read_stdin_json()
    if _lib.tool_name(payload) not in {"Edit", "Write", "MultiEdit"}:
        _lib.allow()

    target = _lib.file_path(payload)
    if target is None or target.suffix.lower() != ".py" or not target.exists():
        _lib.allow()

    parts = {p.lower() for p in target.parts}
    if parts & HUB_DIR_SEGMENTS:
        _lib.allow()

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        _lib.allow()

    if SUBPROCESS_RE.search(content) and CLAUDE_P_RE.search(content):
        _lib.warn(
            "Nudge: this file spawns an inline `claude -p` subprocess. The 'Don't "
            "duplicate hub functionality' rule routes LLM calls through the local hub "
            "at http://127.0.0.1:8000 via the Anthropic/OpenAI SDKs "
            "(Anthropic(api_key='local-dummy', base_url='http://127.0.0.1:8000')) "
            "instead of re-rolling a claude -p wrapper. If this is a deliberate one-off, ignore."
        )

    _lib.allow()


if __name__ == "__main__":
    main()
