"""Acceptance checks for `codex_attention.py`: when a Codex session needs Roberto.

A native PermissionRequest always alerts; a Stop alerts only when the hub
classifier is confident the turn ended on a question. A chief-managed worker's
request goes to the chief first. No network is touched: the hub client and the
Telegram transport are replaced for the duration of each check.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from acceptance.shared import HOOKS, REPO, _Checker


def _codex_attention_unit_checks() -> Tuple[int, int]:
    """Codex native approval routing and conservative Stop classification."""
    sys.path.insert(0, str(HOOKS))
    import codex_attention  # noqa: E402

    check = _Checker()
    tmp = Path(tempfile.mkdtemp(prefix="codex_attention_"))
    registry = tmp / "projects.toml"
    registry.write_text(
        f'[probe]\ncwd_prefix = "{tmp.as_posix()}"\n'
        'telegram_chat_attention = "CHAT"\nboard_url = "https://board.example/?token=x"\n',
        encoding="utf-8",
    )
    saved_registry = os.environ.get("CLAUDE_HOOKS_PROJECTS_TOML")
    saved_state = os.environ.get("CLAUDE_HOOKS_STATE_DIR")
    saved_complete = codex_attention.hub_client.complete
    saved_notify = codex_attention.notify_send.notify
    calls: list[tuple[str, str]] = []
    hub_kwargs: list[dict] = []

    def fake_notify(message: str, chat: str, token: str | None = None) -> bool:
        calls.append((message, chat))
        return True

    try:
        os.environ["CLAUDE_HOOKS_PROJECTS_TOML"] = str(registry)
        os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(tmp / "state")
        codex_attention.notify_send.notify = fake_notify

        excerpt = codex_attention.bounded_excerpt(" ".join(f"w{i}" for i in range(75)))
        check("codex_attention: final excerpt is bounded to 70 words", len(excerpt.split()) == 70)

        def question_reply(*args, **kwargs):
            hub_kwargs.append(kwargs)
            return ('{"verdict":"awaiting_input","confidence":0.96,'
                    '"request":"choose the deployment target"}')

        codex_attention.hub_client.complete = question_reply
        check("codex_attention: strict question classification passes",
              codex_attention.classify_stop("Which option should I use?") ==
              ("awaiting_input", 0.96, "choose the deployment target"))
        check("codex_attention: classifier uses agentic_light with a bounded timeout",
              hub_kwargs[-1]["model"] == "agentic_light" and hub_kwargs[-1]["timeout"] == 3.0)

        codex_attention.hub_client.complete = lambda *a, **k: '{"verdict":"finished","confidence":0.99}'
        check("codex_attention: clean completion classifies finished",
              codex_attention.classify_stop("Completed the requested task.") ==
              ("finished", 0.99, None))
        for raw in (None, "not json", '{"verdict":"awaiting_input","confidence":true}',
                    '{"verdict":"awaiting_input","confidence":0.99,"extra":1}'):
            codex_attention.hub_client.complete = lambda *a, _raw=raw, **k: _raw
            check(f"codex_attention: degraded classifier {raw!r} -> uncertain",
                  codex_attention.classify_stop("A final response") == ("uncertain", 0.0, None))

        codex_attention.hub_client.complete = lambda *a, **k: (
            '{"verdict":"awaiting_input","confidence":0.97,"request":false}')
        check("codex_attention: malformed request preserves a valid verdict",
              codex_attention.classify_stop("Which option?") == ("awaiting_input", 0.97, None))
        live_secret = "sk-" + "A" * 24
        normalized = codex_attention.normalize_context(
            f"  approve\n  this {live_secret}  " + "x" * 200)
        check("codex_attention: context is one line, bounded, and credential-redacted",
              "\n" not in normalized and len(normalized) <= codex_attention.MAX_CONTEXT_CHARS
              and live_secret not in normalized and "[REDACTED_SECRET]" in normalized)

        permission = {"hook_event_name": "PermissionRequest", "session_id": "s1", "turn_id": "t1",
                      "cwd": str(tmp), "tool_name": "apply_patch",
                      "tool_input": {"patch": f"untrusted {live_secret}"}}
        check("codex_attention: PermissionRequest sends an alert", codex_attention.handle(permission))
        check("codex_attention: permission context, routing, and exact message order",
              calls[-1] == ("🔔 probe Codex awaits your input\n"
                            "Waiting for: approval to edit files\n"
                            "📋 Open on the Board: https://board.example/?token=x&board=s1", "CHAT"))
        check("codex_attention: raw permission input never reaches the notification",
              live_secret not in calls[-1][0] and "untrusted" not in calls[-1][0])
        check("codex_attention: duplicate session/turn sends no second alert",
              not codex_attention.handle(permission) and len(calls) == 1)

        _codex_attention_stop_cases(check, codex_attention, permission, calls, question_reply, live_secret, tmp)
    finally:
        codex_attention.hub_client.complete = saved_complete
        codex_attention.notify_send.notify = saved_notify
        if saved_registry is None:
            os.environ.pop("CLAUDE_HOOKS_PROJECTS_TOML", None)
        else:
            os.environ["CLAUDE_HOOKS_PROJECTS_TOML"] = saved_registry
        if saved_state is None:
            os.environ.pop("CLAUDE_HOOKS_STATE_DIR", None)
        else:
            os.environ["CLAUDE_HOOKS_STATE_DIR"] = saved_state
        shutil.rmtree(tmp, ignore_errors=True)
    return check.failures, check.total


def _codex_attention_stop_cases(check: _Checker, codex_attention: Any, permission: Dict[str, Any],
                                calls: List[Tuple[str, str]], question_reply: Callable[..., str],
                                live_secret: str, tmp: Path) -> None:
    """Stop events: dedupe before classifying, question context, redaction, and the generic fallbacks."""
    classifier_calls = 0

    def counted_question_reply(*args, **kwargs):
        nonlocal classifier_calls
        classifier_calls += 1
        return '{"verdict":"awaiting_input","confidence":0.96}'

    codex_attention.hub_client.complete = counted_question_reply
    duplicate_stop = {**permission, "hook_event_name": "Stop",
                      "last_assistant_message": "What should I do next?"}
    check("codex_attention: duplicate Stop skips the classifier itself",
          not codex_attention.handle(duplicate_stop) and classifier_calls == 0 and len(calls) == 1)

    codex_attention.hub_client.complete = question_reply
    question = {"hook_event_name": "Stop", "session_id": "s1", "turn_id": "t2",
                "cwd": str(tmp), "last_assistant_message": "What should I do next?"}
    check("codex_attention: high-confidence question-shaped Stop sends once",
          codex_attention.handle(question) and len(calls) == 2)
    check("codex_attention: question context precedes the Board link",
          calls[-1][0] == "🔔 probe Codex awaits your input\n"
          "Waiting for: choose the deployment target\n"
          "📋 Open on the Board: https://board.example/?token=x&board=s1")

    codex_attention.hub_client.complete = lambda *a, **k: (
        '{"verdict":"awaiting_input","confidence":0.98,'
        f'"request":"choose {live_secret} now"}}')
    secret_question = {**question, "turn_id": "t-secret"}
    check("codex_attention: delivered question context redacts credentials",
          codex_attention.handle(secret_question)
          and "Waiting for: choose [REDACTED_SECRET] now" in calls[-1][0]
          and live_secret not in calls[-1][0])

    codex_attention.hub_client.complete = lambda *a, **k: (
        '{"verdict":"awaiting_input","confidence":0.97,"request":false}')
    malformed_question = {**question, "turn_id": "t-malformed"}
    check("codex_attention: malformed question context keeps the generic alert",
          codex_attention.handle(malformed_question) and calls[-1][0] ==
          "🔔 probe Codex awaits your input\n"
          "📋 Open on the Board: https://board.example/?token=x&board=s1")

    fallback = {**permission, "turn_id": "t-fallback", "tool_name": "unknown_tool"}
    check("codex_attention: unknown permission context keeps the generic alert",
          codex_attention.handle(fallback) and calls[-1][0] ==
          "🔔 probe Codex awaits your input\n"
          "📋 Open on the Board: https://board.example/?token=x&board=s1")

    codex_attention.hub_client.complete = lambda *a, **k: '{"verdict":"finished","confidence":0.99}'
    finished = {**question, "turn_id": "t3", "last_assistant_message": "clean completion."}
    check("codex_attention: clean Stop remains Board-only",
          not codex_attention.handle(finished) and len(calls) == 5)
    check("codex_attention: non-Codex lifecycle event never classifies or alerts",
          not codex_attention.handle({**question, "hook_event_name": "Notification"}) and len(calls) == 5)


def _codex_attention_chief_unit_checks() -> Tuple[int, int]:
    """A chief-managed Codex worker's PermissionRequest reaches the chief first,
    falling back to Telegram when delivery is not confirmed (fleet-config#999).
    The mailbox write itself is pinned by tests/test_chief_needs_input.py
    (#1456); this covers the routing decision around it.
    Every managed-path case here sent Telegram only on pre-#999 code, and the
    no-chat case sent nothing at all."""
    sys.path.insert(0, str(HOOKS))
    import codex_attention  # noqa: E402

    check = _Checker()
    noi = codex_attention.notify_on_idle
    tmp = Path(tempfile.mkdtemp(prefix="codex_attention_chief_"))
    chat_dir = tmp / "with-chat"
    bare_dir = tmp / "no-chat"
    chat_dir.mkdir()
    bare_dir.mkdir()
    registry = tmp / "projects.toml"
    registry.write_text(f'[probe]\ncwd_prefix = "{chat_dir.as_posix()}"\ntelegram_chat_attention = "CHAT"\n',
                        encoding="utf-8")
    saved_env = {k: os.environ.get(k) for k in ("CLAUDE_HOOKS_PROJECTS_TOML", "CLAUDE_HOOKS_STATE_DIR")}
    saved = (noi.chief_managed_state, noi.chief_needs_input, codex_attention.notify_send.notify,
             codex_attention.hub_client.complete)
    telegram: list[str] = []
    chief: list[str] = []
    state = {"managed": (True, noi.CHIEF_MANAGED), "delivered": True}

    def payload(cwd: Path, turn: str, event: str = "PermissionRequest") -> dict:
        return {"hook_event_name": event, "session_id": "codex-s", "turn_id": turn,
                "tool_name": "apply_patch", "cwd": str(cwd)}

    try:
        os.environ["CLAUDE_HOOKS_PROJECTS_TOML"] = str(registry)
        os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(tmp / "state")
        noi.chief_managed_state = lambda path=None: state["managed"]
        noi.chief_needs_input = lambda payload, text, default_agent="claude": (
            chief.append(text) or state["delivered"])
        codex_attention.notify_send.notify = lambda message, chat, token=None: telegram.append(message) or True

        check("codex chief: managed + delivered -> the chief gets it, Telegram does not",
              codex_attention.handle(payload(chat_dir, "t1")) and len(chief) == 1 and telegram == [])
        check("codex chief: the chief message names the worker and what it awaits",
              chief and chief[0].startswith("Codex in ") and "approval to edit files" in chief[0])
        check("codex chief: the same turn is not re-delivered",
              not codex_attention.handle(payload(chat_dir, "t1")) and len(chief) == 1)
        check("codex chief: works with no Telegram chat configured for the project",
              codex_attention.handle(payload(bare_dir, "t2")) and len(chief) == 2 and telegram == [])

        state["delivered"] = False
        check("codex chief: delivery not confirmed -> falls back to the Telegram ping",
              codex_attention.handle(payload(chat_dir, "t3")) and len(chief) == 3 and len(telegram) == 1)

        state["delivered"] = True
        state["managed"] = (False, noi.CHIEF_NOT_DISPATCHED)
        check("codex chief: not chief-managed -> Telegram only, the chief is never tried",
              codex_attention.handle(payload(chat_dir, "t4")) and len(chief) == 3 and len(telegram) == 2)
        state["managed"] = (False, noi.CHIEF_UNDETERMINED)
        check("codex chief: undetermined registry -> Telegram fallback, not the chief",
              codex_attention.handle(payload(chat_dir, "t5")) and len(chief) == 3 and len(telegram) == 3)

        state["managed"] = (True, noi.CHIEF_MANAGED)
        codex_attention.hub_client.complete = lambda *a, **k: '{"verdict":"finished","confidence":0.99}'
        codex_attention.handle({**payload(chat_dir, "t6", event="Stop"), "last_assistant_message": "Done."})
        check("codex chief: a Stop never writes a needs-input (the inbox turn-end owns it)",
              len(chief) == 3)
    finally:
        (noi.chief_managed_state, noi.chief_needs_input, codex_attention.notify_send.notify,
         codex_attention.hub_client.complete) = saved
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(tmp, ignore_errors=True)
    return check.failures, check.total


def _codex_native_probe_unit_checks() -> Tuple[int, int]:
    """The native lifecycle evidence verifier rejects false-positive sequences."""
    from probe_codex_permission_request import evaluate

    check = _Checker()
    fixture = json.loads((REPO / "tests" / "fixtures" /
                          "codex_permission_request_events.json").read_text(encoding="utf-8"))
    check("codex probe: committed fixture preserves the native approval sequence",
          fixture["approval_request"] == ["UserPromptSubmit", "PermissionRequest", "Stop"])
    check("codex probe: committed fixture preserves question/completion separation",
          fixture["prose_question"] == ["UserPromptSubmit", "Stop"]
          and fixture["clean_completion"] == ["UserPromptSubmit", "Stop"])
    check("codex probe: sanitized fixture proves tool_name without tool_input values",
          fixture["permission_field_types"].get("tool_name") == "str"
          and fixture["permission_field_types"].get("tool_input") == "dict"
          and fixture["permission_safe_values"] == {"tool_name": "apply_patch"})

    def turn(case: str, names: list[str], message: str = "") -> list[dict[str, str]]:
        return [{"hook_event_name": name, "session_id": "session-1", "turn_id": case,
                 "last_assistant_message": message if name == "Stop" else ""}
                for name in names]

    valid = [
        *turn("approval", fixture["approval_request"]),
        *turn("question", fixture["prose_question"], "What would you like me to do next?"),
        *turn("completion", fixture["clean_completion"], "clean completion."),
    ]
    check("codex probe: exact native sequences pass", evaluate(valid)["probe"] == "pass")
    missing_submit = [event for event in valid
                      if not (event["turn_id"] == "approval"
                              and event["hook_event_name"] == "UserPromptSubmit")]
    check("codex probe: approval missing UserPromptSubmit is not confirmed",
          evaluate(missing_submit)["probe"] == "not_confirmed")
    forbidden_permission = [*valid]
    forbidden_permission.insert(
        next(index for index, event in enumerate(forbidden_permission)
             if event["turn_id"] == "question" and event["hook_event_name"] == "Stop"),
        {"hook_event_name": "PermissionRequest", "session_id": "session-1",
         "turn_id": "question", "last_assistant_message": ""},
    )
    check("codex probe: question carrying PermissionRequest is not confirmed",
          evaluate(forbidden_permission)["probe"] == "not_confirmed")
    mixed_identity = [dict(event) for event in valid]
    mixed_identity[1]["session_id"] = "session-2"
    check("codex probe: mixed native identity is not confirmed",
          evaluate(mixed_identity)["probe"] == "not_confirmed")
    stitched_sessions = [dict(event) for event in valid]
    for event in stitched_sessions:
        event["session_id"] = f'session-{event["turn_id"]}'
    check("codex probe: turns stitched across sessions are not confirmed",
          evaluate(stitched_sessions)["probe"] == "not_confirmed")
    repeated_turn = [dict(event) for event in valid]
    for event in repeated_turn:
        if event["turn_id"] == "completion":
            event["turn_id"] = "question"
    check("codex probe: three cases require distinct native turn ids",
          evaluate(repeated_turn)["probe"] == "not_confirmed")
    return check.failures, check.total
