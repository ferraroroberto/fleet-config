"""Acceptance checks for the notification surface (fleet-config#680).

Everything that decides *whether, where and how* something reaches Roberto:
`notify_send`'s transport (no network touched), `notify_on_idle`'s message
construction / prompt classification / idle suppression, its Fleet-Board deep
link and chief-managed routing, `notify_complete`'s deterministic message
assembly, and the category -> chat routing table.

Split out of the former 2681-line `unit_checks.py` (see `checks_context_filter`
for why). Each function is self-contained and returns its own
`(failures, total)`; `tests/run_acceptance.py` sums them exactly as before.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Tuple

from acceptance.shared import (
    HOOKS,
    REPO,
    _Checker,
    run,
)

# Every function below inserts its own sys.path entry (HOOKS or skills/_lib)
# right before its dynamic import -- matches the pre-split file's per-function
# style, so each check's dependency is visible at its own call site.


def _notify_send_unit_checks() -> Tuple[int, int]:
    """Exercise notify_send without touching the network. Returns failure count."""
    sys.path.insert(0, str(HOOKS))
    import notify_send  # noqa: E402

    check = _Checker()

    check(
        "notify_send: t.me link -> @publicname",
        notify_send.parse_chat("https://t.me/somechannel") == "@somechannel",
    )
    check(
        "notify_send: bare chat id passes through",
        notify_send.parse_chat("  -1004408175579  ") == "-1004408175579",
    )

    # Missing token must return False (never raise, never post). Force-unset the
    # env var AND neutralize both file fallbacks around the call, so no real
    # developer-machine secret can trigger a post.
    saved = os.environ.pop(notify_send.TOKEN_ENV_VAR, None)
    saved_from_dotenv = notify_send._token_from_dotenv
    saved_from_settings = notify_send._token_from_settings
    notify_send._token_from_dotenv = lambda: None
    notify_send._token_from_settings = lambda: None
    try:
        result = notify_send.notify("test", chat="-1004408175579", token=None)
    finally:
        notify_send._token_from_dotenv = saved_from_dotenv
        notify_send._token_from_settings = saved_from_settings
        if saved is not None:
            os.environ[notify_send.TOKEN_ENV_VAR] = saved
    check("notify_send: missing token -> False (graceful)", result is False)

    # The settings.json fallback resolves a token when the env var is unset —
    # this is the launcher-agnostic behaviour (#192). Stub the file reader so the
    # check is hermetic (independent of whether the dev box's settings.json has a
    # token) and confirm the resolution order: env var wins, else settings.json.
    saved_env = os.environ.pop(notify_send.TOKEN_ENV_VAR, None)
    saved_dotenv_reader = notify_send._token_from_dotenv
    saved_reader = notify_send._token_from_settings
    notify_send._token_from_dotenv = lambda: None
    notify_send._token_from_settings = lambda: "tg-from-settings"
    try:
        from_settings = notify_send._resolve_token(None)
        os.environ[notify_send.TOKEN_ENV_VAR] = "tg-from-env"
        env_wins = notify_send._resolve_token(None)
    finally:
        notify_send._token_from_dotenv = saved_dotenv_reader
        notify_send._token_from_settings = saved_reader
        os.environ.pop(notify_send.TOKEN_ENV_VAR, None)
        if saved_env is not None:
            os.environ[notify_send.TOKEN_ENV_VAR] = saved_env
    check("notify_send: settings.json fallback resolves token when env unset",
          from_settings == "tg-from-settings")
    check("notify_send: env var wins over settings.json fallback",
          env_wins == "tg-from-env")

    # The ignored fleet-config .env is the launcher-neutral local source. It
    # wins over settings.json but never over an explicit process environment.
    dotenv_dir = Path(tempfile.mkdtemp(prefix="notify_dotenv_"))
    dotenv = dotenv_dir / ".env"
    dotenv.write_text('TELEGRAM_BOT_TOKEN="tg-from-dotenv"\n', encoding="utf-8")
    saved_dotenv_path = os.environ.get(notify_send.DOTENV_PATH_ENV_VAR)
    saved_env = os.environ.pop(notify_send.TOKEN_ENV_VAR, None)
    saved_reader = notify_send._token_from_settings
    notify_send._token_from_settings = lambda: "tg-from-settings"
    os.environ[notify_send.DOTENV_PATH_ENV_VAR] = str(dotenv)
    try:
        check("notify_send: ignored .env resolves the token",
              notify_send._resolve_token(None) == "tg-from-dotenv")
        os.environ[notify_send.TOKEN_ENV_VAR] = "tg-from-env"
        check("notify_send: process env wins over ignored .env",
              notify_send._resolve_token(None) == "tg-from-env")
    finally:
        notify_send._token_from_settings = saved_reader
        os.environ.pop(notify_send.TOKEN_ENV_VAR, None)
        if saved_env is not None:
            os.environ[notify_send.TOKEN_ENV_VAR] = saved_env
        if saved_dotenv_path is None:
            os.environ.pop(notify_send.DOTENV_PATH_ENV_VAR, None)
        else:
            os.environ[notify_send.DOTENV_PATH_ENV_VAR] = saved_dotenv_path
        shutil.rmtree(dotenv_dir, ignore_errors=True)

    return check.failures, check.total


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


def _notify_chunking_unit_checks() -> Tuple[int, int]:
    """Telegram's two hard size limits, enforced in the transport (fleet-config#540).

    Replaces the former @mention checks. Slack needed an ``<@user>`` tag to
    guarantee a mobile push; Telegram pushes to any chat you are in, so that
    machinery was deleted rather than ported. What genuinely replaced it is size
    handling: ``sendMessage`` rejects — not truncates — a body over 4096, and a
    ``sendDocument`` caption over 1024, and both limits are already exceeded by
    the weekly digests. A regression here loses a whole digest silently, which is
    exactly the failure mode this suite exists to catch.
    """
    sys.path.insert(0, str(HOOKS))
    import notify_send  # noqa: E402

    check = _Checker()

    check("chunks: a short body stays one unmarked message",
          notify_send._chunks("hello") == ["hello"])

    long_body = "line of text\n" * 600
    parts = notify_send._chunks(long_body)
    check("chunks: an over-long body splits", len(parts) > 1)
    check("chunks: every part fits the Bot API limit",
          all(len(p) <= notify_send.MESSAGE_LIMIT for p in parts))
    check("chunks: each part is marked [i/n]",
          all(p.endswith("[%d/%d]" % (i, len(parts))) for i, p in enumerate(parts, 1)))

    # A single line longer than the limit has no newline to split on, so it is
    # hard-split. Byte-for-byte recoverable: dropping the markers must rebuild
    # the original exactly, or a digest is being silently truncated.
    one_line = "x" * 9000
    hard = notify_send._chunks(one_line)
    check("chunks: a single over-long line is hard-split",
          len(hard) > 1 and all(len(p) <= notify_send.MESSAGE_LIMIT for p in hard))
    check("chunks: a hard split loses no content",
          "".join(p.rsplit("\n\n[", 1)[0] for p in hard) == one_line)

    check("limits: message and caption limits match the Bot API",
          notify_send.MESSAGE_LIMIT == 4096 and notify_send.CAPTION_LIMIT == 1024)

    # Slack link markup must not reach the chat as literal noise — notify_on_idle
    # still emits a board line, and any straggler caller may too.
    check("flatten: labelled link -> 'label: url'",
          notify_send._flatten_markup("<https://x.dev/a|Open on the Board>")
          == "Open on the Board: https://x.dev/a")
    check("flatten: bare autolink -> bare url",
          notify_send._flatten_markup("<https://y.dev>") == "https://y.dev")

    return check.failures, check.total


def _notify_classify_unit_checks() -> Tuple[int, int]:
    """Per-type icon/wording and bridge session-link parsing — the two
    deterministic pieces of the notification logic."""
    sys.path.insert(0, str(HOOKS))
    import notify_on_idle  # noqa: E402

    check = _Checker()

    # ---- classify: icon per notification_type, message passed through ----
    icon, text = notify_on_idle.classify(
        {"notification_type": "permission_prompt", "message": "Claude needs your permission"}
    )
    check("classify: permission -> bell icon + 'awaits your input'",
          icon == "🔔" and text == "Claude Code awaits your input")
    icon, text = notify_on_idle.classify(
        {"notification_type": "idle_prompt", "message": "Claude is waiting for your input"}
    )
    check("classify: idle -> sleep icon + passthrough",
          icon == "💤" and "waiting" in text)
    icon, _ = notify_on_idle.classify({"message": "x"})
    check("classify: unknown type -> bell fallback", icon == "🔔")

    # ---- session_link: bridge id -> web url, local session -> None ----
    tmp = Path(tempfile.mkdtemp(prefix="notify_link_"))
    try:
        def transcript(*entries: dict) -> str:
            path = tmp / f"t{len(list(tmp.iterdir()))}.jsonl"
            path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
            return str(path)

        link = notify_on_idle.session_link(transcript(
            {"type": "mode", "mode": "normal"},
            {"type": "bridge-session", "bridgeSessionId": "cse_01HNYE6TFWrUXEGcY8oUiGFr"},
        ))
        check("session_link: bridge id -> claude.ai url",
              link == "https://claude.ai/code/session_01HNYE6TFWrUXEGcY8oUiGFr")
        check("session_link: local session -> None",
              notify_on_idle.session_link(transcript({"type": "user"})) is None)
        check("session_link: missing path -> None", notify_on_idle.session_link(None) is None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return check.failures, check.total


def _notify_board_link_unit_checks() -> Tuple[int, int]:
    """Fleet-Board deep-link line (fleet-config#242): _lib.resolve_board_url's
    project-override/global-fallback/unset resolution, and notify_on_idle's
    board_link() message assembly — all against synthetic registries so
    nothing touches the real projects.toml."""
    sys.path.insert(0, str(HOOKS))
    import _lib  # noqa: E402
    import notify_on_idle  # noqa: E402

    check = _Checker()

    # The real machine may have FLEET_BOARD_URL genuinely set (fleet-config#271
    # is meant to be configured this way) — clear it for the duration of these
    # checks so "unset"/"[global] fallback" expectations aren't at the mercy of
    # the ambient environment, then restore whatever was there.
    env_key = _lib.BOARD_URL_ENV_VAR
    old_env = os.environ.pop(env_key, None)
    old_dotenv_path = os.environ.get(_lib.DOTENV_PATH_ENV_VAR)
    dotenv_dir = Path(tempfile.mkdtemp(prefix="board_dotenv_"))
    dotenv_path = dotenv_dir / ".env"
    dotenv_path.write_text("", encoding="utf-8")
    os.environ[_lib.DOTENV_PATH_ENV_VAR] = str(dotenv_path)
    try:
        # ---- resolve_board_url: unset -> None (byte-identical default behavior) ----
        unset = _lib.Registry(projects=[], globals=_lib.GlobalConfig(never_kill_ports=()))
        check("resolve_board_url: neither set -> None",
              _lib.resolve_board_url(Path("E:/does/not/match"), registry=unset) is None)

        # ---- resolve_board_url: [global] fallback ----
        glob_only = _lib.Registry(
            projects=[], globals=_lib.GlobalConfig(never_kill_ports=(), board_url="https://global.example:8445"),
        )
        check("resolve_board_url: [global] fallback",
              _lib.resolve_board_url(Path("E:/does/not/match"), registry=glob_only) == "https://global.example:8445")

        # ---- resolve_board_url: per-project override wins ----
        proj = _lib.ProjectConfig(
            name="x", cwd_prefix=Path("E:/automation/x"), webapp_port=None,
            tray_cmd=None, restart_cmd=None,
            api_version_path=None, extra={"board_url": "https://proj.example:8445"},
        )
        reg = _lib.Registry(
            projects=[proj],
            globals=_lib.GlobalConfig(never_kill_ports=(), board_url="https://global.example:8445"),
        )
        check("resolve_board_url: per-project override wins over [global]",
              _lib.resolve_board_url(Path("E:/automation/x"), registry=reg) == "https://proj.example:8445")

        # ---- resolve_board_url: FLEET_BOARD_URL env var precedence (fleet-config#271) ----
        # public-repo-safe indirection: env var sits between the project override
        # and the committed [global] fallback.
        os.environ[env_key] = "https://env.example:8445"
        check("resolve_board_url: env var alone -> resolves",
              _lib.resolve_board_url(Path("E:/does/not/match"), registry=unset) == "https://env.example:8445")
        check("resolve_board_url: env var wins over [global]",
              _lib.resolve_board_url(Path("E:/does/not/match"), registry=glob_only) == "https://env.example:8445")
        check("resolve_board_url: per-project override still wins over env var",
              _lib.resolve_board_url(Path("E:/automation/x"), registry=reg) == "https://proj.example:8445")
        os.environ.pop(env_key, None)

        dotenv_path.write_text("FLEET_BOARD_URL=https://dotenv.example:8445?token=x\n",
                               encoding="utf-8")
        check("resolve_board_url: ignored .env resolves for Codex",
              _lib.resolve_board_url(Path("E:/does/not/match"), registry=unset)
              == "https://dotenv.example:8445?token=x")
        check("resolve_board_url: ignored .env wins over committed global fallback",
              _lib.resolve_board_url(Path("E:/does/not/match"), registry=glob_only)
              == "https://dotenv.example:8445?token=x")
        os.environ[env_key] = "https://env.example:8445"
        check("resolve_board_url: process env wins over ignored .env",
              _lib.resolve_board_url(Path("E:/does/not/match"), registry=unset)
              == "https://env.example:8445")
        os.environ.pop(env_key, None)
        dotenv_path.write_text("", encoding="utf-8")

        # ---- board_link: configured + session_id -> plain-text deep link ----
        payload = {"session_id": "abc-123", "cwd": "E:/automation/x"}
        check("board_link: configured -> plain-text deep link",
              notify_on_idle.board_link(payload, registry=reg)
              == "📋 Open on the Board: https://proj.example:8445/?board=abc-123")

        # ---- board_link: trailing slash on board_url is stripped ----
        trailing = _lib.Registry(
            projects=[], globals=_lib.GlobalConfig(never_kill_ports=(), board_url="https://global.example:8445/"),
        )
        check("board_link: trailing slash on board_url stripped",
              notify_on_idle.board_link(payload, registry=trailing)
              == "📋 Open on the Board: https://global.example:8445/?board=abc-123")

        # ---- board_link: board_url with an existing query string merges, not concatenates (fleet-config#273) ----
        tokened = _lib.Registry(
            projects=[], globals=_lib.GlobalConfig(never_kill_ports=(), board_url="https://global.example:8445?token=secret"),
        )
        check("board_link: existing ?token= on board_url survives alongside ?board=",
              notify_on_idle.board_link(payload, registry=tokened)
              == "📋 Open on the Board: https://global.example:8445/?token=secret&board=abc-123")

        # ---- board_link: unconfigured -> None (default, current behavior unchanged) ----
        check("board_link: board_url unset -> None",
              notify_on_idle.board_link(payload, registry=unset) is None)

        # ---- board_link: missing session_id -> None, even when configured ----
        check("board_link: missing session_id -> None",
              notify_on_idle.board_link({"cwd": "E:/automation/x"}, registry=reg) is None)
    finally:
        if old_env is None:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = old_env
        if old_dotenv_path is None:
            os.environ.pop(_lib.DOTENV_PATH_ENV_VAR, None)
        else:
            os.environ[_lib.DOTENV_PATH_ENV_VAR] = old_dotenv_path
        shutil.rmtree(dotenv_dir, ignore_errors=True)

    return check.failures, check.total


def _notify_chief_routing_unit_checks() -> Tuple[int, int]:
    """`is_chief_managed`/`parse_chief_sid` — the pure decision logic behind
    routing a chief-dispatched worker's blocked-on-input notification to
    chief instead of the human ping (fleet-config#443).

    Deliberately does NOT exercise `notify_chief`'s live subprocess/network
    call here (or via a `run()` end-to-end hook invocation with a genuinely
    chief-managed sid): doing so would require a real `chief-managed.json`
    entry and could actually shell out to `chief_ops.py chief-sid`/`say`
    against whatever launcher happens to be listening on 127.0.0.1:8445 on
    the machine running this suite — risking a real post into a real live
    chief session as a side effect of a unit test. The two pure functions
    below are the entire decision surface; the I/O wrapper composing them is
    exercised by hand against a real launcher, the same way `chief_ops.py`'s
    own network-touching CLI commands are.
    """
    sys.path.insert(0, str(HOOKS))
    import notify_on_idle  # noqa: E402

    check = _Checker()

    # ---- is_chief_managed: file-based, fully isolated from the real state dir ----
    tmp = Path(tempfile.mkdtemp(prefix="chief_managed_route_"))
    try:
        target = tmp / "chief-managed.json"
        check("is_chief_managed: missing state file -> False",
              notify_on_idle.is_chief_managed("sid-1", path=target) is False)

        target.write_text(json.dumps({"sid-1": {"repo": "app-launcher", "number": 528,
                                                  "dispatched_at": "2026-07-27T12:00:00Z"}}),
                           encoding="utf-8")
        check("is_chief_managed: marked sid -> True",
              notify_on_idle.is_chief_managed("sid-1", path=target) is True)
        check("is_chief_managed: unrelated sid -> False",
              notify_on_idle.is_chief_managed("sid-2", path=target) is False)

        target.write_text("{not json", encoding="utf-8")
        check("is_chief_managed: corrupt state file -> False (no crash)",
              notify_on_idle.is_chief_managed("sid-1", path=target) is False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- parse_chief_sid: pure stdout-line parsing ----
    check("parse_chief_sid: CHIEF_SID=<sid> -> the sid",
          notify_on_idle.parse_chief_sid("CHIEF_SID=abc-123\n") == "abc-123")
    check("parse_chief_sid: CHIEF_SID=none -> empty (no chief live)",
          notify_on_idle.parse_chief_sid("CHIEF_SID=none\n") == "")
    check("parse_chief_sid: no matching line -> empty",
          notify_on_idle.parse_chief_sid("some other output\n") == "")
    check("parse_chief_sid: line among other output -> still extracted",
          notify_on_idle.parse_chief_sid("noise\nCHIEF_SID=xyz-789\nmore noise\n") == "xyz-789")

    return check.failures, check.total


def _notify_complete_unit_checks() -> Tuple[int, int]:
    """Canonical per-kind message assembly + the shared notify-target resolver."""
    sys.path.insert(0, str(HOOKS))
    import notify_complete  # noqa: E402
    import _lib  # noqa: E402

    check = _Checker()

    bm = notify_complete.build_message
    check("build: add -> filed + issue link",
          bm("add", issue="5", title="T", url="http://u") == "🆕 Filed #5 T · http://u")
    check("build: start -> ready-to-validate + summary",
          bm("start", issue="5", title="T", summary="do X") == "🚦 #5 T — ready to validate. do X")
    check("build: start -> ready-to-validate + summary + issue link",
          bm("start", issue="5", title="T", url="http://u", summary="do X")
          == "🚦 #5 T — ready to validate. do X · http://u")
    check("build: finish -> done + PR link",
          bm("finish", issue="5", title="T", url="http://u") == "✅ Done #5 T — PR merged · http://u")
    check("build: yolo -> shipped + PR link",
          bm("yolo", issue="5", title="T", url="http://u") == "🚀 Shipped #5 T — PR · http://u")
    check("build: batch -> passed/total",
          bm("batch", passed="2", total="3") == "🏁 Batch done: 2/3 passed — /issue-finish each branch to ship")
    check("build: finish with no url/title degrades cleanly",
          bm("finish", issue="5") == "✅ Done #5 — PR merged")
    check("build: audit -> fleet audit + summary + comment link",
          bm("audit", summary="3 audited, 2 issues", url="http://gh/comment") == "📊 Fleet audit — 3 audited, 2 issues · http://gh/comment")
    check("build: audit with no url degrades cleanly",
          bm("audit", summary="0 audited") == "📊 Fleet audit — 0 audited")
    check("build: cleanup -> bucket + merged + review counts",
          bm("cleanup", summary="documentation", merged="5", review="2")
          == "🧹 Cleanup documentation: 5 merged, 2 awaiting review")
    check("build: cleanup easy-mode (0 review) drops the review clause",
          bm("cleanup", summary="documentation", merged="3", review="0")
          == "🧹 Cleanup documentation: 3 merged")
    check("build: recap -> weekly recap + summary",
          bm("recap", summary="5 skills swept, 3 proposals") == "🔄 Weekly recap — 5 skills swept, 3 proposals")
    check("build: recap with no summary degrades cleanly",
          bm("recap") == "🔄 Weekly recap")
    check("build: design -> design sweep + summary",
          bm("design", summary="8 swept · 3 drifted · 11 findings filed")
          == "🎨 Design sweep — 8 swept · 3 drifted · 11 findings filed")
    check("build: design with no summary degrades cleanly",
          bm("design") == "🎨 Design sweep")
    check("category: design routes to the activity log, not attention",
          notify_complete.category_for("design") == "log")
    check("build: learning -> log + summary + comment link",
          bm("learning", summary="12 PRs / 8 issues · 2/3 horizon", url="http://gh/c")
          == "📓 Learning log — 12 PRs / 8 issues · 2/3 horizon · http://gh/c")
    check("build: learning with no url degrades cleanly",
          bm("learning", summary="quiet week") == "📓 Learning log — quiet week")
    check("build: finish-batch -> merged + blocked counts",
          bm("finish-batch", merged="4", blocked="1") == "🏁 Finished batch: 4 merged, 1 blocked")
    check("build: finish-batch (0 blocked) drops the blocked clause",
          bm("finish-batch", merged="5", blocked="0") == "🏁 Finished batch: 5 merged")
    check("build: security -> lock + summary + PR link",
          bm("security", issue="42", title="audit: security findings", url="http://pr", summary="auto-merged, review the diff")
          == "🔒 Security #42 audit: security findings — auto-merged, review the diff · http://pr")
    check("build: security with no summary defaults to review-the-diff",
          bm("security", issue="42", url="http://pr") == "🔒 Security #42 — review the diff · http://pr")

    # --summary crosses the harness -> shell -> CreateProcess boundary, which is
    # not UTF-8 safe on Windows: a literal `·` reached the chat as `??`
    # (fleet-config#507). Skills spell the separator with the ASCII token `|`,
    # and whatever mojibake is still recoverable is repaired on the way in.
    ns = notify_complete.normalize_summary
    check("normalize_summary: ASCII token renders as the middle-dot separator",
          ns("8 swept | 2 drifted | 4 findings filed") == "8 swept · 2 drifted · 4 findings filed")
    check("normalize_summary: token spacing is normalised either way",
          ns("8 swept|2 drifted") == "8 swept · 2 drifted")
    check("normalize_summary: cp1252-mangled middle-dot is repaired",
          ns("8 swept Â· 2 drifted") == "8 swept · 2 drifted")
    check("normalize_summary: an intact middle-dot survives untouched",
          ns("8 swept · 2 drifted") == "8 swept · 2 drifted")
    check("normalize_summary: plain ASCII prose is untouched",
          ns("review the diff, then /issue-finish") == "review the diff, then /issue-finish")
    check("normalize_summary: None stays None", ns(None) is None)
    check("build: design accepts the ASCII separator token",
          bm("design", summary="8 swept | 3 drifted | 11 findings filed")
          == "🎨 Design sweep — 8 swept · 3 drifted · 11 findings filed")

    rm = _lib.repair_mojibake
    check("repair_mojibake: mangled em-dash restored", rm("a â€” b") == "a — b")
    check("repair_mojibake: genuine accented prose left alone", rm("não é") == "não é")
    check("repair_mojibake: pure ASCII short-circuits", rm("plain text") == "plain text")
    check("repair_mojibake: empty/None pass through", rm("") == "" and rm(None) is None)

    # The separator token only exists because non-ASCII must not be authored into
    # an argv string — a SKILL.md (or the doc a model copies the command from)
    # that re-inlines a literal `·` puts the corruption straight back.
    # Emoji (>= U+2600) are exempt: they are the glanceable status cue, and the
    # reported corruption was of punctuation. Everything else non-ASCII is an
    # offender — separators, dashes, quotes.
    offenders: list[str] = []
    arg_text = re.compile(r'--(?:summary|text)\s+"([^"]*)"')
    sources = sorted((REPO / ".claude" / "skills").rglob("SKILL.md"))
    sources += sorted((REPO / "skills").rglob("SKILL.md"))
    sources += sorted((REPO / "docs").rglob("*.md"))
    sources += [REPO / "README.md", REPO / "CLAUDE.md", REPO / "global-CLAUDE.md"]
    for source in sources:
        if not source.is_file():
            continue
        for lineno, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            for value in arg_text.findall(line):
                bad = sorted({c for c in value if not c.isascii() and ord(c) < 0x2600})
                if bad:
                    offenders.append(f"{source.relative_to(REPO).as_posix()}:{lineno} {bad}")
    check("skills + docs author only ASCII punctuation into --summary/--text argv"
          + (f" (offenders: {offenders})" if offenders else ""),
          not offenders)

    # The shared resolver: unknown cwd -> [global] chat + 'claude' name.
    ch, nm = _lib.resolve_notify_target(Path("E:/does/not/match/anything"))
    check("resolve_notify_target: global fallback + claude name",
          ch == "-1004408175579" and nm == "claude")

    # lookup(): --repo threads onto the gh invocation as `-R repo`, for both the
    # issue path and the pr-by-number path, so a cross-repo ping can't silently
    # resolve against the caller's CWD repo instead (fleet-config#497).
    captured_args = []
    saved_gh_json = notify_complete.gh_json
    notify_complete.gh_json = lambda a: (captured_args.append(a), {"title": "T", "url": "http://u"})[1]
    try:
        notify_complete.lookup("add", "496", None, repo="ferraroroberto/fleet-config")
        check("lookup: issue path threads -R <repo> onto gh issue view",
              captured_args[-1] == ["issue", "view", "496", "-R", "ferraroroberto/fleet-config", "--json", "title,url"])

        notify_complete.lookup("add", "30", None)
        check("lookup: issue path omits -R when repo not supplied (CWD-relative, unchanged)",
              captured_args[-1] == ["issue", "view", "30", "--json", "title,url"])

        notify_complete.lookup("finish", None, "31", repo="ferraroroberto/fleet-config")
        check("lookup: pr-by-number path threads -R <repo> onto gh pr view",
              captured_args[-1] == ["pr", "view", "31", "-R", "ferraroroberto/fleet-config", "--json", "title,url"])

        notify_complete.lookup("finish", None, None, pr_url="http://pr", repo="ferraroroberto/fleet-config")
        check("lookup: pr_url path ignores repo (absolute URL already CWD-independent)",
              captured_args[-1] == ["pr", "view", "http://pr", "--json", "title"])
    finally:
        notify_complete.gh_json = saved_gh_json

    return check.failures, check.total


def _notify_routing_unit_checks() -> Tuple[int, int]:
    """Category → chat routing (issue #139): the resolver picks the dedicated
    chat per category, falls back to the single chat when a category is
    unset, and the kind → category map sends action-needed pings to attention."""
    sys.path.insert(0, str(HOOKS))
    import _lib  # noqa: E402
    import notify_complete  # noqa: E402

    check = _Checker()

    cwd = Path("E:/does/not/match/anything")  # global-only resolution

    # ---- category routes to its dedicated [global] chat ----
    ch, _n = _lib.resolve_notify_target(cwd, category="attention")
    check("route: attention -> the attention chat", ch == "-1004408175579")
    ch, _n = _lib.resolve_notify_target(cwd, category="log")
    check("route: log -> the log chat", ch == "-1004387099086")
    # No category -> the plain chat (back-compat: existing callers unchanged).
    ch, _n = _lib.resolve_notify_target(cwd)
    check("route: no category -> telegram_chat", ch == "-1004408175579")

    # ---- graceful degradation: category chats unset -> single-chat fallback ----
    single = _lib.Registry(
        projects=[],
        globals=_lib.GlobalConfig(never_kill_ports=(), telegram_chat="C_ONLY"),
    )
    ch, _n = _lib.resolve_notify_target(cwd, registry=single, category="attention")
    check("route: unset category chat -> falls back to single chat", ch == "C_ONLY")

    # ---- per-project override of a category chat wins over [global] ----
    proj = _lib.ProjectConfig(
        name="x", cwd_prefix=Path("E:/automation/x"), webapp_port=None,
        tray_cmd=None, restart_cmd=None,
        api_version_path=None, extra={"telegram_chat_log": "C_PROJ_LOG"},
    )
    reg = _lib.Registry(
        projects=[proj],
        globals=_lib.GlobalConfig(never_kill_ports=(), telegram_chat="C_G",
                                  telegram_chat_log="C_GLOBAL_LOG"),
    )
    ch, _n = _lib.resolve_notify_target(Path("E:/automation/x"), registry=reg, category="log")
    check("route: per-project category chat overrides [global]", ch == "C_PROJ_LOG")

    # ---- kind -> category map ----
    cat = notify_complete.category_for
    check("category_for: start -> attention", cat("start") == "attention")
    check("category_for: batch -> attention", cat("batch") == "attention")
    check("category_for: security -> attention", cat("security") == "attention")
    check("category_for: cleanup with review>0 -> attention", cat("cleanup", review="2") == "attention")
    check("category_for: cleanup with review=0 -> log", cat("cleanup", review="0") == "log")
    check("category_for: log kinds -> log",
          all(cat(k) == "log" for k in ("add", "finish", "yolo", "audit", "recap", "learning", "finish-batch")))

    return check.failures, check.total
