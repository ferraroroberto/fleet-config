"""Turn one coding agent's session log into the event stream the retro digest counts.

Each adapter reads one harness's JSONL records and yields the same five events, so
the digest never learns a harness's key names:

  ("call", call_id, tool_name, args)     a tool call; args is a dict
  ("result", call_id, text, is_error)    that call's result text
  ("prompt", text, is_meta)              user-role text; meta text is injected, not typed
  ("interrupt",)                         the owner stopped the agent mid-turn
  ("turn_ms", milliseconds)              recorded turn time

Claude Code writes `{type: user|assistant, message: {...}}`; Codex writes
`{type, payload}` rollout records; Pi writes `{type: session|message|...}` entries
whose messages carry `toolCall` blocks and `toolResult` roles. A log none of them
recognises is unmeasured, never an empty digest. stdlib only.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterator, List, Optional, Tuple

Event = Tuple[Any, ...]

#: A Codex shell result that leads with this line, nonzero, failed.
_CODEX_EXIT = re.compile(r"^(?:Exit code:|Process exited with code) (\d+)\s*$", re.M)
_CODEX_FAILED = re.compile(r"^(?:apply_patch verification failed|failed to parse function arguments|unsupported call)")
#: Codex injects the repo's AGENTS.md as a user message; it is context, not a prompt.
_CODEX_INJECTED = ("# AGENTS.md instructions", "<environment_context>", "<user_instructions>")


class Unmeasured(Exception):
    """No adapter recognises the log's shape."""


def block_text(content: Any) -> str:
    """The text of a string or a list of text-bearing content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(b.get("text") or "") for b in content if isinstance(b, dict))
    return ""


def detect(records: List[Dict[str, Any]]) -> str:
    """`codex`, `pi` or `claude` from the records' shape; raises Unmeasured otherwise."""
    if any(isinstance(r.get("payload"), dict) and r.get("type") in ("session_meta", "response_item")
           for r in records):
        return "codex"
    if any(r.get("type") == "message" and isinstance(r.get("message"), dict) for r in records):
        return "pi"
    if any(r.get("type") in ("user", "assistant") and isinstance(r.get("message"), dict) for r in records):
        return "claude"
    raise Unmeasured("no Claude Code, Codex or Pi message records in the recognised shape")


def session_cwd(agent: str, records: List[Dict[str, Any]]) -> str:
    """The working directory the session started in, or empty."""
    for rec in records:
        if agent == "codex":
            payload = rec.get("payload")
            cwd = payload.get("cwd") if isinstance(payload, dict) else None
        else:
            cwd = rec.get("cwd")
        if cwd:
            return str(cwd)
    return ""


def events(agent: str, records: List[Dict[str, Any]]) -> Iterator[Event]:
    """Every event of the log, in order."""
    reader = {"claude": _claude, "codex": _codex, "pi": _pi}[agent]
    for rec in records:
        yield from reader(rec)


def _claude(rec: Dict[str, Any]) -> Iterator[Event]:
    kind = rec.get("type")
    if kind == "system" and rec.get("subtype") == "turn_duration":
        yield ("turn_ms", int(rec.get("durationMs") or 0))
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return
    content = msg.get("content")
    if kind == "assistant":
        for block in content or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                args = block.get("input") if isinstance(block.get("input"), dict) else {}
                yield ("call", str(block.get("id") or ""), str(block.get("name") or "unknown"), args)
    elif kind == "user":
        meta = bool(rec.get("isMeta"))
        if isinstance(content, str):
            yield ("prompt", content, meta)
            return
        for block in content or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                yield ("prompt", str(block.get("text") or ""), meta)
            elif block.get("type") == "tool_result":
                yield ("result", str(block.get("tool_use_id") or ""), block_text(block.get("content")),
                       bool(block.get("is_error")))


def _codex(rec: Dict[str, Any]) -> Iterator[Event]:
    payload = rec.get("payload")
    if not isinstance(payload, dict):
        return
    kind = payload.get("type")
    if rec.get("type") == "event_msg":
        if kind == "turn_aborted":
            yield ("interrupt",)
        elif kind == "task_complete" and payload.get("duration_ms") is not None:
            yield ("turn_ms", int(payload.get("duration_ms") or 0))
        return
    if rec.get("type") != "response_item":
        return
    if kind == "message" and payload.get("role") == "user":
        text = block_text(payload.get("content"))
        yield ("prompt", text, text.lstrip().startswith(_CODEX_INJECTED))
    elif kind in ("function_call", "custom_tool_call", "local_shell_call"):
        yield ("call", str(payload.get("call_id") or ""), str(payload.get("name") or kind),
               _codex_args(payload))
    elif kind in ("function_call_output", "custom_tool_call_output"):
        text, failed = _codex_output(payload.get("output"))
        yield ("result", str(payload.get("call_id") or ""), text, failed)


def _codex_args(payload: Dict[str, Any]) -> Dict[str, Any]:
    raw = payload.get("arguments", payload.get("input", payload.get("action")))
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        parsed = None
    return parsed if isinstance(parsed, dict) else {"input": raw}


def _codex_output(output: Any) -> Tuple[str, bool]:
    """The result text Codex showed the model, and whether the call failed."""
    exit_code: Optional[int] = None
    if isinstance(output, str) and output.lstrip().startswith("{"):
        try:
            parsed = json.loads(output)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict) and "output" in parsed:
            meta = parsed.get("metadata") if isinstance(parsed.get("metadata"), dict) else {}
            exit_code = meta.get("exit_code") if isinstance(meta.get("exit_code"), int) else None
            body = str(parsed.get("output") or "")
            output = f"Exit code: {exit_code}\n{body}" if exit_code is not None else body
    text = block_text(output.get("content") if isinstance(output, dict) else output)
    if exit_code is None:
        found = _CODEX_EXIT.search(text)
        exit_code = int(found.group(1)) if found else None
    failed = bool(exit_code) or bool(_CODEX_FAILED.match(text.lstrip()))
    return text, failed


def _pi(rec: Dict[str, Any]) -> Iterator[Event]:
    msg = rec.get("message")
    if rec.get("type") != "message" or not isinstance(msg, dict):
        return
    role = msg.get("role")
    if role == "user":
        text = block_text(msg.get("content"))
        # A `/skill:<name>` prompt runs a skill, as a Claude Code command does: not counted as typed.
        yield ("prompt", text, text.lstrip().startswith("/skill:"))
    elif role == "assistant":
        for block in msg.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "toolCall":
                args = block.get("arguments") if isinstance(block.get("arguments"), dict) else {}
                yield ("call", str(block.get("id") or ""), str(block.get("name") or "unknown"), args)
        if msg.get("stopReason") == "aborted":
            yield ("interrupt",)
    elif role == "toolResult":
        yield ("result", str(msg.get("toolCallId") or ""), block_text(msg.get("content")),
               bool(msg.get("isError")))
