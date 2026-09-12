"""Provider-specific commands and JSONL translation for scheduled_runner.

Adapters emit evidence, never schedule, retry, run checks, or decide success.
Unknown records deliberately remain unknown even after a terminal event.

An unknown record names what it was, so the next schema drift is diagnosable
from the run log instead of being a bare count (fleet-config#841).
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
    # Tool/child name on a lifecycle event; on an "unknown" event, the
    # `describe_record` shape descriptor of the record that was not recognised.
    name: str = ""
    text: str = ""
    failed: bool = False
    # Deliberately stopped, which is not a failure and must never be reported as
    # one: an orchestrator that cancels its own background task has made a
    # decision, not hit a fault (fleet-config#808). Kept separate from `failed`
    # rather than folded into it so the two stay tellable apart downstream.
    cancelled: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    # Who authored a "text" event's content. Defaults to the model itself
    # ("assistant") because every adapter's own generated-message events are
    # genuinely model output. The one deliberate override is Claude's "user"
    # stream-kind: a skill invocation is delivered back into the conversation
    # as a fresh user-role text block (the SKILL.md body), not a tool_result
    # tied to the Skill call — indistinguishable from real assistant text at
    # the block level, but never something the run itself asserted
    # (fleet-config#829). Only a genuinely model-authored block may ever set
    # a delivery-failure verdict.
    role: str = "assistant"


_DESCRIPTOR_UNSAFE = re.compile(r"[^A-Za-z0-9_.:=-]")
DESCRIPTOR_PART_LIMIT = 40
DESCRIPTOR_LIMIT = 80


def describe_record(*parts: object) -> str:
    """Name an unrecognised record by shape alone -- never by its payload.

    Only the discriminator fields a schema owns (`type`, `subtype`, a content
    block's own `type`) are ever passed in; anything else on the record can
    carry a prompt, a path or a secret. Sanitized and length-capped on top of
    that, because this string reaches the run log, which is read by people and
    scraped by the Board. The cap is applied to each part *and* to the joined
    result, so no number of parts can widen one log line without bound.
    """
    cleaned = [_DESCRIPTOR_UNSAFE.sub("?", str(part))[:DESCRIPTOR_PART_LIMIT]
               for part in parts if part not in (None, "")]
    return ("/".join(cleaned) or "unlabelled")[:DESCRIPTOR_LIMIT]


def error_category(text: str) -> str:
    """Classify concrete provider failures without treating prose as success."""
    if re.search(r"(?i)(?:\b401\b|authentication failed|invalid.{0,10}(?:api key|token)|not logged in|login required|please (?:log|sign) in|requires.{0,20}authentication)", text):
        return "auth"
    if re.search(r"(?i)(?:model.{0,100}(?:not found|not supported|does not exist|unavailable|not available|unsupported)|unsupported model)", text):
        return "model"
    if re.search(r"(?i)(?:required.{0,30}(?:tool|mcp).{0,80}(?:missing|unavailable|failed)|(?:tool|mcp server).{0,50}(?:not found|not available|unavailable))", text):
        return "tools"
    return ""


# `system` records Claude Code emits as ambient state rather than as a progress
# boundary. Kept as an explicit allowlist -- anything absent from it stays
# unknown, which is the whole point: the parser must go red on a schema it has
# not been taught, not quietly wave it through.
#
# Pinned against Claude Code **2.1.269** (captured 2026-09-12). The last two
# entries are the fleet-config#841 fix: `--output-format stream-json` began
# emitting the background-task *store* alongside the task lifecycle, so every
# backgrounded tool call produced three records the parser had never seen. With
# `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0` -- which this runner sets, so every
# slow shell command becomes a background task -- that made `unverified_stream`
# true on literally every scheduled run, and exit 122 stopped carrying
# information. Neither record is evidence of anything:
#   * `background_tasks_changed` is a full snapshot of the in-flight set
#     (`{"tasks": [...]}`), re-sent on every change and empty once drained.
#   * `task_updated` is a UI patch (`{"patch": {"status": ..., "end_time": ...}}`)
#     that arrives immediately *before* the `task_notification` for the same
#     `task_id`. `task_notification` stays the one authoritative terminal
#     boundary -- observed for both `local_bash` and `local_agent` tasks -- so
#     consuming the patch too would double-count every child. A task that ever
#     did end on the patch alone would leave its child open, which `finish()`
#     already reports as unfinished work (118) rather than as success.
KNOWN_IGNORED_SYSTEM_SUBTYPES = frozenset({
    "thinking_tokens",
    "status",
    "compact_boundary",
    "hook_started",
    "hook_response",
    "hook_progress",
    "background_tasks_changed",
    "task_updated",
})


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
            if subtype in KNOWN_IGNORED_SYSTEM_SUBTYPES:
                return []
            if subtype in {"task_started", "task_progress", "task_notification"}:
                task_id = event.get("task_id")
                if not isinstance(task_id, str) or not task_id:
                    return [ProgressEvent("malformed")]
                mapped = {"task_started": "child_start", "task_progress": "child_progress", "task_notification": "child_end"}[subtype]
                status = event.get("status")
                if subtype == "task_notification" and status not in {"completed", "failed", "stopped"}:
                    return [ProgressEvent("unknown", name=describe_record(kind, subtype, status))]
                # `stopped` is what `TaskStop` reports, and the run that exposed
                # this used it exactly as intended: a background disk scan
                # pointed at the wrong volume, cancelled the moment the agent
                # noticed (fleet-config#808). Reporting that as `failed` was
                # both a wrong render (`✗ task failed`) and, downstream, a
                # wrong verdict. Only `failed` is a failure here.
                return [ProgressEvent(mapped, id=task_id, name="task", metadata=event,
                                      failed=status == "failed",
                                      cancelled=status == "stopped")]
        elif kind in {"rate_limit_event", "prompt_suggestion"}:
            return []
        elif kind == "result":
            subtype, is_error = event.get("subtype"), event.get("is_error")
            if not isinstance(is_error, bool) or not isinstance(subtype, str):
                return [ProgressEvent("malformed")]
            success = subtype == "success" and is_error is False
            if not success and not (is_error or subtype.startswith("error")):
                return [ProgressEvent("unknown", name=describe_record(kind, subtype))]
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
                    # `kind` here is the outer stream-event type ("assistant" or
                    # "user"), not this block's own type — see the `role` field
                    # docstring for why the "user" case must never be scored.
                    result.append(ProgressEvent("text", text=block.get("text", ""),
                                                 role="assistant" if kind == "assistant" else "user"))
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
                    result.append(ProgressEvent("unknown", name=describe_record(kind, f"block={block_type}")))
            return result
        return [ProgressEvent("unknown", name=describe_record(kind, event.get("subtype")))]


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
            return [ProgressEvent("unknown", name=describe_record(kind))]
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
                return [ProgressEvent("unknown", name=describe_record(kind, item_type, item.get("status")))]
            return [ProgressEvent("tool_end", id=item["id"], name=item_type,
                                  failed=item.get("status") == "failed" or item.get("exit_code", 0) not in {0, None})]
        # Native delegated-child completion is a separate unproven surface.
        # A new collaboration item must never disappear into a green turn.
        return [ProgressEvent("unknown", name=describe_record(kind, item_type))]
