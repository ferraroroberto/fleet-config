"""Nudge away from re-implementing the local LLM hub with an inline `claude -p`.

Triggers on `PostToolUse` for native edits and Codex `apply_patch`.
**Non-blocking** — emits one nudge through the shared event channel and lets the edit stand. The
user decides whether the call is a legitimate one-off.

Fires when the edited file is a `*.py` anywhere EXCEPT inside a repo flagged
`is_hub = true` in `hooks/projects.toml` (the hub itself, e.g. `local-llm-hub`)
and its on-disk content spawns an inline `claude -p` subprocess. Reason: the
global "Don't duplicate hub functionality" rule — downstream apps should route
through the hub at `http://127.0.0.1:8000` via the standard Anthropic/OpenAI
SDKs, not re-roll a `claude -p` subprocess wrapper.

Reads every surviving target from disk after a confirmed successful edit, matching
`py_syntax_check.py`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402


# A subprocess-spawning indicator in the file.
SUBPROCESS_RE = re.compile(r"\b(?:subprocess|Popen|os\.system|check_output|check_call|getoutput)\b")

# `claude -p` either as a command-string fragment (`"claude -p ..."`) or as
# adjacent argv tokens (`["claude", "-p", ...]` / `('claude', '-p', ...)`).
CLAUDE_P_RE = re.compile(r"claude\s+-p\b|['\"]claude['\"]\s*,\s*['\"]-p['\"]")


def main() -> None:
    payload = _lib.read_stdin_json()
    edit = _lib.edit_event(payload)
    if edit.status == "not_edit" or edit.outcome != "success":
        _lib.allow()

    offenders: list[Path] = []
    for change in edit.targets:
        target = change.path
        if change.operation == "delete" or target.suffix.lower() != ".py" or not target.exists():
            continue
        project = _lib.detect_project(target)
        if project is not None and project.extra.get("is_hub"):
            continue
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if SUBPROCESS_RE.search(content) and CLAUDE_P_RE.search(content):
            offenders.append(target)

    if offenders:
        names = ", ".join(str(path) for path in offenders)
        _lib.warn(
            f"Nudge: {names} spawns an inline `claude -p` subprocess. The 'Don't "
            "duplicate hub functionality' rule routes LLM calls through the local hub "
            "at http://127.0.0.1:8000 via the Anthropic/OpenAI SDKs "
            "(Anthropic(api_key='local-dummy', base_url='http://127.0.0.1:8000')) "
            "instead of re-rolling a claude -p wrapper. If this is a deliberate one-off, ignore."
        )

    _lib.allow()


if __name__ == "__main__":
    main()
