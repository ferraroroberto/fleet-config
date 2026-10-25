"""Acceptance check that the suite itself can never reach Telegram.

The isolation is enforced, not asserted: a hook subprocess under
`shared.hook_env()` resolves no token, the transport refuses while the block
switch is set, and the switch is never set ambiently on the machine.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Tuple

from acceptance.shared import HOOKS, PYTHON, REPO, _Checker, hook_env


def _notify_network_isolation_unit_checks() -> Tuple[int, int]:
    """The suite cannot reach Telegram — enforced, not asserted in a comment.

    fleet-config#813: `shared.run()` closed two of `_resolve_token`'s four
    sources and a comment in `hook_matrix` claimed that put the network out of
    reach. `_token_from_dotenv` had been added since and read the token
    straight off disk, so every gate run posted four real messages to the live
    attention chat — one of them carrying a fixture session id in a clickable
    Board deep link.

    Plugging that one source would leave the same shape behind: a guarantee
    that quietly lapses the next time a source is added. So this check asserts
    the two properties that actually hold the line, and fails the suite if
    either stops:

    * the transport refuses while `FLEET_NOTIFY_BLOCK_NETWORK` is set, and
      `shared.hook_env()` really sets it — a guarantee independent of how many
      token sources exist;
    * `_resolve_token(None)` resolves to nothing under the exact environment
      the suite runs hooks in — source-count-proof by construction, since a
      fifth source that leaks makes it return a token and this check red.

    Both are measured in a subprocess under the real `hook_env()`, because the
    whole bug was that in-process monkeypatching (which the checks above use,
    correctly) says nothing about what a spawned hook resolves off disk.
    """
    sys.path.insert(0, str(HOOKS))
    import notify_send  # noqa: E402

    check = _Checker()

    env = hook_env()
    probe = (
        "import json, sys;"
        f"sys.path.insert(0, {str(HOOKS)!r});"
        "import notify_send as n;"
        "print(json.dumps({"
        "'resolves': bool(n._resolve_token(None)),"
        "'from_env': bool(n.os.getenv(n.TOKEN_ENV_VAR)),"
        "'from_dotenv': bool(n._token_from_dotenv()),"
        "'from_settings': bool(n._token_from_settings()),"
        "'blocked': n.network_blocked()}))"
    )
    res = subprocess.run([PYTHON, "-c", probe], capture_output=True, text=True,
                         timeout=30, env=env)
    try:
        seen = json.loads(res.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        seen = {}
    check("notify isolation: the probe ran under the suite's own hook_env()",
          bool(seen), f"probe failed: {res.stdout!r} {res.stderr[-300:]!r}")

    # The source-count-proof half. Named individually so a regression says
    # *which* source reopened rather than only that one did.
    check("notify isolation: no token resolves in a hook subprocess",
          seen.get("resolves") is False,
          "a token source is open: " + repr({k: v for k, v in seen.items()
                                             if k.startswith("from_") and v}))
    for source in ("from_env", "from_dotenv", "from_settings"):
        check(f"notify isolation: {source} is closed under hook_env()",
              seen.get(source) is False)

    # The transport half.
    check("notify isolation: hook_env() sets the transport block",
          seen.get("blocked") is True)
    check("notify isolation: shared.hook_env() carries FLEET_NOTIFY_BLOCK_NETWORK",
          env.get(notify_send.NETWORK_BLOCK_ENV_VAR) == "1")

    _notify_blocked_transport_cases(check, notify_send)

    # The switch is a real hazard if it ever leaks into the machine's own
    # environment: fleet alerts would go silently dead. Nothing else can catch
    # that, so the suite does.
    check("notify isolation: the block switch is NOT set ambiently on this machine",
          not notify_send.network_blocked(),
          f"{notify_send.NETWORK_BLOCK_ENV_VAR} is set in the environment — "
          "real fleet alerts are disabled")

    _notify_reopened_source_case(check)

    # State isolation (the same root cause): a hook subprocess must never be
    # pointed at the live hooks/state/, which is junctioned into ~/.claude/hooks.
    live_state_dir = str((HOOKS / "state").resolve())
    check("notify isolation: hook_env() routes hook state away from live hooks/state/",
          Path(env["CLAUDE_HOOKS_STATE_DIR"]).resolve() != Path(live_state_dir))

    return check.failures, check.total


def _notify_blocked_transport_cases(check: _Checker, notify_send: Any) -> None:
    """A blocked transport refuses before opening a socket, as the URLError callers already handle."""
    # A blocked transport must refuse *before* opening a socket, and must do it
    # as the URLError every caller already turns into a logged False — so an
    # unattended hook exits 0 rather than crashing.
    saved = os.environ.get(notify_send.NETWORK_BLOCK_ENV_VAR)
    opened: list[str] = []
    saved_urlopen = notify_send.urllib.request.urlopen
    notify_send.urllib.request.urlopen = lambda *a, **k: opened.append("opened")
    try:
        os.environ[notify_send.NETWORK_BLOCK_ENV_VAR] = "1"
        try:
            blocked_result = notify_send.notify("test", chat="-1004408175579", token="fake-token")
        except Exception as exc:  # noqa: BLE001 — a raise here is itself the failure
            blocked_result = repr(exc)
        check("notify isolation: a blocked send opens no socket", not opened,
              "the transport was reached despite the block being set")
        check("notify isolation: a blocked send reports False, never raises",
              blocked_result is False)
        check("notify isolation: NetworkBlocked is a URLError (callers already handle it)",
              issubclass(notify_send.NetworkBlocked, notify_send.urllib.error.URLError))
        os.environ[notify_send.NETWORK_BLOCK_ENV_VAR] = "0"
        check("notify isolation: the block is off for a falsy value",
              notify_send.network_blocked() is False)
    finally:
        notify_send.urllib.request.urlopen = saved_urlopen
        if saved is None:
            os.environ.pop(notify_send.NETWORK_BLOCK_ENV_VAR, None)
        else:
            os.environ[notify_send.NETWORK_BLOCK_ENV_VAR] = saved


def _notify_reopened_source_case(check: _Checker) -> None:
    """A real hook with a reopened token source is still stopped at the transport."""
    # The whole point of the transport half, proven end-to-end rather than by
    # construction: reopen a token source (exactly what adding a fifth one
    # would do) and a real hook subprocess must still send nothing and still
    # exit 0. Without the block this is precisely the run that posted to
    # Roberto's phone.
    leaky = hook_env()
    leaky.pop("FLEET_CONFIG_ENV_PATH", None)
    res = subprocess.run(
        [PYTHON, str(HOOKS / "notify_on_idle.py")],
        input=json.dumps({"hook_event_name": "Notification",
                          "notification_type": "permission_prompt",
                          "cwd": str(REPO), "message": "needs permission",
                          "session_id": "fleet-config-test-fixture-sid-not-chief-managed"}),
        capture_output=True, text=True, timeout=30, env=leaky,
    )
    check("notify isolation: a reopened token source is still stopped at the transport",
          "is set - Telegram send blocked" in res.stderr,
          f"stderr: {res.stderr[-300:]!r}")
    check("notify isolation: a blocked hook still exits 0", res.returncode == 0)
