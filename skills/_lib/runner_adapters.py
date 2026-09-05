"""Provider-specific commands and JSONL translation for scheduled_runner.

Adapters emit evidence, never schedule, retry, run checks, or decide success.
Unknown records deliberately remain unknown even after a terminal event.
"""
from __future__ import annotations

import re
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class ProgressEvent:
    """Small provider-neutral progress boundary; no raw tool payloads."""

    kind: str
    id: str = ""
    name: str = ""
    text: str = ""
    failed: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


def error_category(text: str) -> str:
    """Classify concrete provider failures without treating prose as success."""
    if re.search(r"(?i)(?:\b401\b|authentication failed|invalid.{0,10}(?:api key|token)|not logged in|login required|please (?:log|sign) in|requires.{0,20}authentication)", text):
        return "auth"
    if re.search(r"(?i)(?:model.{0,100}(?:not found|not supported|does not exist|unavailable|not available|unsupported)|unsupported model)", text):
        return "model"
    if re.search(r"(?i)(?:required.{0,30}(?:tool|mcp).{0,80}(?:missing|unavailable|failed)|(?:tool|mcp server).{0,50}(?:not found|not available|unavailable))", text):
        return "tools"
    return ""


class ClaudeAdapter:
    label = "Claude Code"
    excluded_environment: tuple[str, ...] = ()
    retry_before_tools = True
    error_category = staticmethod(error_category)

    def environment(self) -> dict[str, str]:
        return {"CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "0"}

    def build_command(self, arguments: Sequence[str], executable: Optional[str] = None) -> list[str]:
        # Deferred import keeps the compatibility prompt helper at its old API.
        from scheduled_runner import RESERVED_FLAGS, normalize_skill_prompt

        if not arguments:
            raise ValueError("a Claude prompt is required")
        for argument in arguments[1:]:
            if argument.split("=", 1)[0] in (*RESERVED_FLAGS, "--fallback-model"):
                raise ValueError(f"{argument} is owned by claude_progress.py or enables fallback")
        return [executable or shutil.which("claude") or "claude", "-p",
                normalize_skill_prompt(arguments[0]), *arguments[1:],
                "--output-format", "stream-json", "--verbose"]

    def events(self, event: dict[str, Any]) -> list[ProgressEvent]:
        kind = event.get("type")
        if kind == "system":
            subtype = event.get("subtype")
            if subtype == "init":
                return [ProgressEvent("start", text=f"{event.get('claude_code_version', 'unknown version')} · {event.get('model', 'unknown model')}")]
            if subtype in {"thinking_tokens", "status", "compact_boundary", "hook_started", "hook_response", "hook_progress"}:
                return []
            if subtype in {"task_started", "task_progress", "task_notification"}:
                task_id = event.get("task_id")
                if not isinstance(task_id, str) or not task_id:
                    return [ProgressEvent("malformed")]
                mapped = {"task_started": "child_start", "task_progress": "child_progress", "task_notification": "child_end"}[subtype]
                status = event.get("status")
                if subtype == "task_notification" and status not in {"completed", "failed", "stopped"}:
                    return [ProgressEvent("unknown")]
                return [ProgressEvent(mapped, id=task_id, name="task", metadata=event,
                                      failed=status in {"failed", "stopped"})]
        elif kind in {"rate_limit_event", "prompt_suggestion"}:
            return []
        elif kind == "result":
            subtype, is_error = event.get("subtype"), event.get("is_error")
            if not isinstance(is_error, bool) or not isinstance(subtype, str):
                return [ProgressEvent("malformed")]
            success = subtype == "success" and is_error is False
            if not success and not (is_error or subtype.startswith("error")):
                return [ProgressEvent("unknown")]
            return [ProgressEvent("result", failed=not success, text=str(event.get("result") or "\n".join(event.get("errors") or [])))]
        elif kind in {"assistant", "user"}:
            message = event.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                return [ProgressEvent("malformed")]
            result = []
            for block in content:
                if not isinstance(block, dict):
                    result.append(ProgressEvent("malformed"))
                    continue
                block_type = block.get("type")
                if block_type in {"thinking", "redacted_thinking"}:
                    continue
                if block_type == "text":
                    result.append(ProgressEvent("text", text=block.get("text", "")))
                elif block_type == "tool_use":
                    tool_id = block.get("id")
                    if not isinstance(tool_id, str) or not tool_id:
                        result.append(ProgressEvent("malformed"))
                    else:
                        result.append(ProgressEvent("tool_start", id=tool_id, name=str(block.get("name") or "tool"), metadata=block.get("input") or {}))
                elif block_type == "tool_result":
                    tool_id = block.get("tool_use_id")
                    if not isinstance(tool_id, str) or not tool_id:
                        result.append(ProgressEvent("malformed"))
                    else:
                        result.append(ProgressEvent("tool_end", id=tool_id, failed=block.get("is_error") is True))
                else:
                    result.append(ProgressEvent("unknown"))
            return result
        return [ProgressEvent("unknown")]


class CodexAdapter:
    label = "Codex"
    excluded_environment = ("CODEX_API_KEY", "OPENAI_API_KEY")
    # exec emits some tool items only after execution; missing records cannot
    # prove no side effect. Native request retries remain the CLI's own affair.
    retry_before_tools = False
    error_category = staticmethod(error_category)

    def environment(self) -> dict[str, str]:
        return {}

    def build_command(self, arguments: Sequence[str], executable: Optional[str] = None) -> list[str]:
        if not arguments:
            raise ValueError("a Codex prompt is required")
        from scheduled_runner import normalize_skill_prompt

        prompt = normalize_skill_prompt(arguments[0]).replace(" via the Skill tool", " using its discovered SKILL.md and available native tools")
        native: list[str] = []
        model = False
        permission = False
        index = 1
        while index < len(arguments):
            flag = arguments[index]
            if flag in {"--model", "-m", "--sandbox", "-s", "--effort", "--disable", "-c", "--config"}:
                if index + 1 >= len(arguments):
                    raise ValueError(f"{flag} requires a value")
                value = arguments[index + 1]
                if flag in {"--model", "-m"}:
                    if not value or value.startswith("-"):
                        raise ValueError("--model requires an explicit model id")
                    model = True
                elif flag in {"--sandbox", "-s"}:
                    if value not in {"read-only", "workspace-write"}:
                        raise ValueError("scheduled Codex sandbox must be read-only or workspace-write")
                    permission = True
                elif flag == "--effort":
                    if value not in {"low", "medium", "high", "xhigh", "max"}:
                        raise ValueError("unsupported effort; verify the selected model's supported levels")
                    native.extend(["-c", f'model_reasoning_effort="{value}"'])
                    index += 2
                    continue
                elif flag == "--disable":
                    if value != "hooks":
                        raise ValueError("only the explicit probe override --disable hooks is supported")
                elif value not in {"project_doc_max_bytes=0", "check_for_update_on_startup=false"}:
                    raise ValueError("arbitrary config overrides are not supported by the scheduled adapter")
                native.extend([flag, value])
                index += 2
            elif flag in {"--approve-for-me", "--ephemeral", "--ignore-user-config"}:
                permission |= flag == "--approve-for-me"
                native.append(flag)
                index += 1
            else:
                raise ValueError(f"unsupported scheduled Codex flag: {flag}")
        if not model or not permission:
            raise ValueError("Codex requires --model and an explicit --sandbox or --approve-for-me")
        return [executable or shutil.which("codex") or "codex", "exec", *native,
                "-c", 'model_provider="openai"', "-c", 'forced_login_method="chatgpt"',
                "--json", "--color", "never", prompt]

    def events(self, event: dict[str, Any]) -> list[ProgressEvent]:
        kind = event.get("type")
        if kind == "thread.started":
            if not isinstance(event.get("thread_id"), str):
                return [ProgressEvent("malformed")]
            return [ProgressEvent("start", text="native exec")]
        if kind == "turn.started":
            return []
        if kind == "turn.completed":
            if not isinstance(event.get("usage"), dict):
                return [ProgressEvent("malformed")]
            return [ProgressEvent("result")]
        if kind in {"turn.failed", "error"}:
            error = event.get("error")
            text = error.get("message") if isinstance(error, dict) else event.get("message")
            if not isinstance(text, str):
                return [ProgressEvent("malformed")]
            return [ProgressEvent("result" if kind == "turn.failed" else "error", text=text, failed=True)]
        if kind not in {"item.started", "item.updated", "item.completed"}:
            return [ProgressEvent("unknown")]
        item = event.get("item")
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            return [ProgressEvent("malformed")]
        item_type = item.get("type")
        if item_type in {"reasoning", "todo_list"}:
            return []
        if item_type == "agent_message":
            return [ProgressEvent("text", text=item.get("text", ""))] if kind == "item.completed" else []
        if item_type == "error":
            return [ProgressEvent("error", text=str(item.get("message", "unknown tool error")), failed=True)]
        if item_type in {"command_execution", "file_change", "mcp_tool_call", "web_search"}:
            if kind == "item.updated":
                return []
            if kind == "item.started":
                return [ProgressEvent("tool_start", id=item["id"], name=item_type)]
            if item.get("status") not in {"completed", "failed"}:
                return [ProgressEvent("unknown")]
            return [ProgressEvent("tool_end", id=item["id"], name=item_type,
                                  failed=item.get("status") == "failed" or item.get("exit_code", 0) not in {0, None})]
        # Native delegated-child completion is a separate unproven surface.
        # A new collaboration item must never disappear into a green turn.
        return [ProgressEvent("unknown")]
