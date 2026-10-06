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
`py_syntax_check.py`. Comments and docstrings are dropped before matching
(fleet-config#1289): prose *about* `claude -p` is not a spawn, and a nudge that
fires on prose trains lanes to ignore it. Ordinary string literals stay, since
`"claude -p ..."` as a command string is exactly the real case.
"""

from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402


# A subprocess-spawning indicator in the file.
SUBPROCESS_RE = re.compile(r"\b(?:subprocess|Popen|os\.system|check_output|check_call|getoutput)\b")

# `claude -p` either as a command-string fragment (`"claude -p ..."`) or as
# adjacent argv tokens (`["claude", "-p", ...]` / `('claude', '-p', ...)`).
CLAUDE_P_RE = re.compile(r"claude\s+-p\b|['\"]claude['\"]\s*,\s*['\"]-p['\"]")

_DOCSTRING_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _char_col(line: str, byte_col: int) -> int:
    """`ast` columns count UTF-8 bytes; slicing `line` needs characters."""
    return len(line.encode("utf-8")[:byte_col].decode("utf-8", "replace"))


def code_without_prose(content: str) -> str:
    """`content` with comments and docstrings blanked out. A file that fails to
    tokenize or parse comes back unchanged, so the match falls back to the
    whole file rather than the hook breaking an edit."""
    try:
        # The rows tokenize reads (split on `\n` only, unlike `str.splitlines`).
        lines = io.StringIO(content).readlines()
        # (start row, start col, end row, end col): 1-based rows, str columns.
        spans = [(*tok.start, *tok.end)
                 for tok in tokenize.generate_tokens(io.StringIO(content).readline)
                 if tok.type == tokenize.COMMENT]
        for node in ast.walk(ast.parse(content)):
            body = node.body if isinstance(node, _DOCSTRING_OWNERS) else None
            first = body[0] if body else None
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                spans.append((first.lineno, _char_col(lines[first.lineno - 1], first.col_offset),
                              first.end_lineno, _char_col(lines[first.end_lineno - 1], first.end_col_offset)))
        # Bottom-up, so each cut leaves the rows and columns still to come intact.
        for start_row, start_col, end_row, end_col in sorted(spans, reverse=True):
            head, tail = lines[start_row - 1][:start_col], lines[end_row - 1][end_col:]
            lines[start_row - 1:end_row] = [head + tail]
    # IndexError: a lone-`\r` line ending, which `ast` counts as a row and
    # `readlines` does not.
    except (SyntaxError, ValueError, RecursionError, IndexError, tokenize.TokenError):
        return content
    return "".join(lines)


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
        code = code_without_prose(content)
        if SUBPROCESS_RE.search(code) and CLAUDE_P_RE.search(code):
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
