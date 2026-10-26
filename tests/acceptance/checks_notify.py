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

import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Tuple

from acceptance.shared import (
    HOOKS,
    PYTHON,
    REPO,
    _Checker,
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


def _notify_media_group_unit_checks() -> Tuple[int, int]:
    """`upload_files` (fleet-config#976): the sendMediaGroup path for several
    files in one Telegram message. Pure-logic validation (arg count, missing
    files) plus the multipart builder's structure, without touching the
    network - mirrors `_notify_send_unit_checks`'s no-network discipline."""
    sys.path.insert(0, str(HOOKS))
    import notify_send  # noqa: E402

    check = _Checker()

    check(
        "upload_files: fewer than 2 files is rejected before any I/O",
        notify_send.upload_files(["a.png"], chat="-1004408175579", token="fake-token") is False,
    )
    check(
        "upload_files: more than 10 files is rejected before any I/O",
        notify_send.upload_files(["a.png"] * 11, chat="-1004408175579", token="fake-token") is False,
    )
    check(
        "upload_files: a missing file is caught before any network call",
        notify_send.upload_files(
            ["E:/does/not/exist/a.png", "E:/does/not/exist/b.png"],
            chat="-1004408175579", token="fake-token",
        ) is False,
    )

    # _multipart_media_group: structure a real caller relies on - one part per
    # file named file0/file1/..., each carrying its own bytes verbatim, plus
    # whatever top-level fields (chat_id, media) were passed through.
    body, content_type = notify_send._multipart_media_group(
        {"chat_id": "-1004408175579", "media": '[{"type":"document","media":"attach://file0"}]'},
        [("first.png", b"\x89PNGfirst"), ("second.png", b"\x89PNGsecond")],
    )
    check("multipart media group: boundary in content-type matches the body",
          content_type.split("boundary=")[1].encode() in body)
    check("multipart media group: both filenames present",
          b'filename="first.png"' in body and b'filename="second.png"' in body)
    check("multipart media group: both payloads present, byte-exact",
          b"\x89PNGfirst" in body and b"\x89PNGsecond" in body)
    check("multipart media group: fields named file0/file1 per attach:// index",
          b'name="file0"' in body and b'name="file1"' in body)
    check("multipart media group: top-level fields (chat_id, media) present",
          b'name="chat_id"' in body and b'name="media"' in body
          and b"-1004408175579" in body)

    # Network isolation, mirroring _notify_network_isolation_unit_checks's
    # blocked-send check for notify(): a valid 2-file call must still open no
    # socket and return False while FLEET_NOTIFY_BLOCK_NETWORK is set.
    tmp = Path(tempfile.mkdtemp(prefix="notify_media_group_"))
    try:
        one = tmp / "one.png"
        two = tmp / "two.png"
        one.write_bytes(b"\x89PNGone")
        two.write_bytes(b"\x89PNGtwo")
        saved_block = os.environ.get(notify_send.NETWORK_BLOCK_ENV_VAR)
        opened: list[str] = []
        saved_urlopen = notify_send.urllib.request.urlopen
        notify_send.urllib.request.urlopen = lambda *a, **k: opened.append("opened")
        try:
            os.environ[notify_send.NETWORK_BLOCK_ENV_VAR] = "1"
            result = notify_send.upload_files([str(one), str(two)], chat="-1004408175579",
                                              token="fake-token")
            check("upload_files: a blocked send opens no socket", not opened)
            check("upload_files: a blocked send reports False, never raises", result is False)
        finally:
            notify_send.urllib.request.urlopen = saved_urlopen
            if saved_block is None:
                os.environ.pop(notify_send.NETWORK_BLOCK_ENV_VAR, None)
            else:
                os.environ[notify_send.NETWORK_BLOCK_ENV_VAR] = saved_block
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return check.failures, check.total


class _CannedReply:
    """A context-manager stand-in for the `urlopen` response, serving one canned Bot API body."""

    def __init__(self, body: dict) -> None:
        self._raw = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None

    def read(self) -> bytes:
        return self._raw


def _notify_message_ids_unit_checks() -> Tuple[int, int]:
    """`sent_ids` / `delete_messages` / `--print-ids` (fleet-config#981): the
    message-id plumbing a caller needs to delete what it sent. `_urlopen` is
    replaced by a recorder serving canned Bot API bodies, so nothing leaves the
    process and every request the transport *would* make is visible."""
    sys.path.insert(0, str(HOOKS))
    import notify_send  # noqa: E402

    check = _Checker()
    requests: list[tuple[str, dict]] = []
    replies: list[dict] = []

    def fake_urlopen(request, timeout):
        method = request.full_url.rsplit("/", 1)[-1]
        payload = {}
        if request.get_header("Content-type", "").startswith("application/json"):
            payload = json.loads(request.data.decode("utf-8"))
        requests.append((method, payload))
        return _CannedReply(replies.pop(0))

    saved_urlopen = notify_send._urlopen
    notify_send._urlopen = fake_urlopen
    tmp = Path(tempfile.mkdtemp(prefix="notify_ids_"))
    try:
        replies[:] = [{"ok": True, "result": {"message_id": 11}},
                      {"ok": True, "result": {"message_id": 12}}]
        ids: list[int] = []
        ok = notify_send.notify("x" * (notify_send.MESSAGE_LIMIT + 10), chat="-100123",
                                token="fake-token", sent_ids=ids)
        check("notify: every chunk's message_id is collected", ok and ids == [11, 12])

        replies[:] = [{"ok": True, "result": {"message_id": 5}}]
        check("notify: omitting sent_ids still works (existing callers unchanged)",
              notify_send.notify("hi", chat="-100123", token="fake-token") is True)

        one, two = tmp / "one.png", tmp / "two.png"
        one.write_bytes(b"PNGone")
        two.write_bytes(b"PNGtwo")
        replies[:] = [{"ok": True, "result": [{"message_id": 21}, {"message_id": 22}]}]
        ids = []
        ok = notify_send.upload_files([str(one), str(two)], chat="-100123", token="fake-token",
                                      caption="cap", sent_ids=ids)
        check("upload_files: every media-group item's message_id is collected",
              ok and ids == [21, 22])

        replies[:] = [{"ok": True, "result": [{"message_id": 31}, {"message_id": 32}]},
                      {"ok": True, "result": {"message_id": 33}}]
        ids = []
        ok = notify_send.upload_files([str(one), str(two)], chat="-100123", token="fake-token",
                                      caption="c" * (notify_send.CAPTION_LIMIT + 1), sent_ids=ids)
        check("upload_files: the caption-overflow follow-up's id is collected too",
              ok and ids == [31, 32, 33])

        replies[:] = [{"ok": True, "result": {"message_id": 41}}]
        ids = []
        ok = notify_send.upload_file(str(one), chat="-100123", token="fake-token",
                                     comment="cap", sent_ids=ids)
        check("upload_file: the document's message_id is collected", ok and ids == [41])

        replies[:] = [{"ok": False, "description": "Bad Request"}]
        ids = []
        ok = notify_send.notify("hi", chat="-100123", token="fake-token", sent_ids=ids)
        check("notify: a rejected send collects no id", ok is False and ids == [])

        requests.clear()
        replies[:] = [{"ok": True, "result": True}, {"ok": True, "result": True}]
        ok = notify_send.delete_messages(list(range(1, 151)), chat="-100123", token="fake-token")
        check("delete_messages: batches at 100 via deleteMessages",
              ok and [m for m, _ in requests] == ["deleteMessages", "deleteMessages"]
              and len(requests[0][1]["message_ids"]) == 100
              and len(requests[1][1]["message_ids"]) == 50)

        replies[:] = [{"ok": False, "description": "Bad Request"}]
        check("delete_messages: a rejection reports False",
              notify_send.delete_messages([1], chat="-100123", token="fake-token") is False)

        requests.clear()
        check("delete_messages: an empty list is a no-op success with no request",
              notify_send.delete_messages([], chat="-100123", token="fake-token") is True
              and not requests)
    finally:
        notify_send._urlopen = saved_urlopen
        shutil.rmtree(tmp, ignore_errors=True)

    _notify_message_ids_offline_cases(check, notify_send)

    return check.failures, check.total


def _notify_message_ids_offline_cases(check: _Checker, notify_send: Any) -> None:
    """A blocked transport refuses the delete, and `--print-ids` keeps stdout to the JSON list."""
    # Blocked transport: the real _urlopen must refuse the delete too.
    saved_block = os.environ.get(notify_send.NETWORK_BLOCK_ENV_VAR)
    opened: list[str] = []
    saved_real = notify_send.urllib.request.urlopen
    notify_send.urllib.request.urlopen = lambda *a, **k: opened.append("opened")
    try:
        os.environ[notify_send.NETWORK_BLOCK_ENV_VAR] = "1"
        result = notify_send.delete_messages([1, 2], chat="-100123", token="fake-token")
        check("delete_messages: a blocked delete opens no socket and reports False",
              not opened and result is False)
    finally:
        notify_send.urllib.request.urlopen = saved_real
        if saved_block is None:
            os.environ.pop(notify_send.NETWORK_BLOCK_ENV_VAR, None)
        else:
            os.environ[notify_send.NETWORK_BLOCK_ENV_VAR] = saved_block

    # --print-ids: stdout carries only the JSON list, even when the send fails.
    env = dict(os.environ, **{notify_send.NETWORK_BLOCK_ENV_VAR: "1", "PYTHONUTF8": "1"})
    proc = subprocess.run(
        [PYTHON, str(HOOKS / "notify_send.py"), "--chat", "-100123", "--text", "hi", "--print-ids"],
        capture_output=True, text=True, env=env, timeout=60,
    )
    check("--print-ids: stdout is exactly a JSON list (logs on stderr)",
          proc.stdout.strip() == "[]" and proc.returncode == 1)


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

        _board_link_message_cases(check, notify_on_idle, _lib, reg, unset)
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


def _board_link_message_cases(check: _Checker, notify_on_idle: Any, _lib: Any, reg: Any,
                              unset: Any) -> None:
    """`board_link()` assembles the deep-link line, or returns None when it cannot."""
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


def _notify_chief_routing_unit_checks() -> Tuple[int, int]:
    """`chief_managed_state`/`parse_chief_sid` — the pure decision logic behind
    routing a chief-dispatched worker's blocked-on-input notification to
    chief instead of the human ping (fleet-config#443, fleet-config#835).

    Deliberately does NOT exercise `chief_live`'s real `chief-sid` lookup
    here (or via a `run()` end-to-end hook invocation with a genuinely
    chief-managed sid): that would query whatever launcher happens to be
    listening on 127.0.0.1:8445 on the machine running this suite. The
    managed path end to end -- lookup stubbed, mailbox write real -- is
    tests/chief_ops/test_chief_needs_input.py (fleet-config#1456).
    """
    sys.path.insert(0, str(HOOKS))
    import notify_on_idle  # noqa: E402

    check = _Checker()

    # ---- chief_managed_state: env identity + file read, fully isolated ----
    # The sid comes from the *environment*, not an argument: keying this on the
    # hook payload's `session_id` is what left both callers inert
    # (fleet-config#835), so the env var is what these checks drive.
    tmp = Path(tempfile.mkdtemp(prefix="chief_managed_route_"))
    old_launcher_sid = os.environ.get("APP_LAUNCHER_SESSION_ID")

    def _as_launcher(sid: str) -> None:
        if sid:
            os.environ["APP_LAUNCHER_SESSION_ID"] = sid
        else:
            os.environ.pop("APP_LAUNCHER_SESSION_ID", None)

    try:
        target = tmp / "chief-managed.json"

        _as_launcher("")
        check("chief_managed_state: no launcher env -> (False, not-launcher-spawned)",
              notify_on_idle.chief_managed_state(path=target)
              == (False, notify_on_idle.CHIEF_NOT_LAUNCHER))

        _as_launcher("sid-1")
        check("chief_managed_state: missing state file -> (False, not-dispatched)",
              notify_on_idle.chief_managed_state(path=target)
              == (False, notify_on_idle.CHIEF_NOT_DISPATCHED))

        target.write_text(json.dumps({"sid-1": {"repo": "app-launcher", "number": 528,
                                                  "dispatched_at": "2026-07-27T12:00:00Z"}}),
                           encoding="utf-8")
        check("chief_managed_state: marked launcher sid -> (True, managed)",
              notify_on_idle.chief_managed_state(path=target)
              == (True, notify_on_idle.CHIEF_MANAGED))

        _as_launcher("sid-2")
        check("chief_managed_state: unmarked launcher sid -> (False, not-dispatched)",
              notify_on_idle.chief_managed_state(path=target)
              == (False, notify_on_idle.CHIEF_NOT_DISPATCHED))

        target.write_text("{not json", encoding="utf-8")
        _as_launcher("sid-1")
        check("chief_managed_state: corrupt state file -> (False, undetermined), no crash",
              notify_on_idle.chief_managed_state(path=target)
              == (False, notify_on_idle.CHIEF_UNDETERMINED))

        target.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
        check("chief_managed_state: non-dict state file -> (False, undetermined)",
              notify_on_idle.chief_managed_state(path=target)
              == (False, notify_on_idle.CHIEF_UNDETERMINED))

        # "could not tell" must never be spelled the same as any determination.
        check("chief_managed_state: undetermined is distinct from every determination",
              len({notify_on_idle.CHIEF_MANAGED, notify_on_idle.CHIEF_NOT_LAUNCHER,
                   notify_on_idle.CHIEF_NOT_DISPATCHED, notify_on_idle.CHIEF_UNDETERMINED}) == 4)

        # The #835 regression, stated as a property: the payload's session_id is
        # not an input to this decision at all.
        check("chief_managed_state: takes no payload sid (env is the only identity)",
              "sid" not in inspect.signature(notify_on_idle.chief_managed_state).parameters)
    finally:
        if old_launcher_sid is None:
            os.environ.pop("APP_LAUNCHER_SESSION_ID", None)
        else:
            os.environ["APP_LAUNCHER_SESSION_ID"] = old_launcher_sid
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

    _notify_complete_summary_cases(check, notify_complete, _lib)

    _argv_ascii_punctuation_check(check)

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


def _notify_complete_summary_cases(check: _Checker, notify_complete: Any, _lib: Any) -> None:
    """Summary text survives the argv boundary: the ASCII separator token and mojibake repair."""
    # --summary crosses the harness -> shell -> CreateProcess boundary, which is
    # not UTF-8 safe on Windows: a literal `·` reached the chat as `??`
    # (fleet-config#507). Skills spell the separator with the ASCII token `|`,
    # and whatever mojibake is still recoverable is repaired on the way in.
    bm = notify_complete.build_message
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


def _argv_ascii_punctuation_check(check: _Checker) -> None:
    """No skill or doc authors non-ASCII punctuation into a `--summary`/`--text` argv."""
    # The separator token only exists because non-ASCII must not be authored into
    # an argv string — a SKILL.md (or the doc a model copies the command from)
    # that re-inlines a literal `·` puts the corruption straight back.
    # Emoji (>= U+2600) are exempt: they are the glanceable status cue, and the
    # reported corruption was of punctuation. Everything else non-ASCII is an
    # offender — separators, dashes, quotes.
    offenders: list[str] = []
    arg_text = re.compile(r'--(?:summary|text)\s+"([^"]*)"')
    # `*.md`, not `SKILL.md`: a skill's on-demand companion files carry commands too.
    sources = sorted((REPO / ".claude" / "skills").rglob("*.md"))
    sources += sorted((REPO / "skills").rglob("*.md"))
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
