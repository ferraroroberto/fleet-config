"""Per-hook substantive unit-check functions (fleet-config#502).

Split out of the former tests/run_acceptance.py god-module: concern (d), the
roughly forty `_x_unit_checks()` functions with real payload-building logic,
one per hook/helper. Each returns `(failures, total)`; `tests/run_acceptance.py`
sums them via its `run_unit()` dispatch loop exactly as before the split.

Not runnable standalone — imported by tests/run_acceptance.py, which also
invokes `tests/test_*.py` files directly for the pure-logic suites those
cover independently.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Tuple

from acceptance.shared import (
    HOOKS,
    NO_SETTINGS_JSON,
    PYTHON,
    REPO,
    _Checker,
    assert_exit,
    run,
)

# Every function below inserts its own sys.path entry (HOOKS or skills/_lib)
# right before its dynamic import -- matches the pre-split file's per-function
# style, so each check's dependency is visible at its own call site.


def _context_filter_unit_checks() -> Tuple[int, int]:
    check = _Checker()

    payload = {
        "tool_name": "PowerShell",
        "cwd": str(REPO),
        "tool_input": {"command": "git status --short"},
    }
    code, stdout, stderr = run("context_filter_hook", payload, {"FLEET_CONTEXT_FILTER_MODE": "rewrite"})
    check(
        "context_filter_hook: rewrite mode emits updatedInput",
        code == 0 and "context_filter_cli.py" in stdout and "updatedInput" in stdout,
        stdout + stderr,
    )

    rewritten_command = ""
    if code == 0 and stdout.strip():
        rewritten_command = json.loads(stdout)["hookSpecificOutput"]["updatedInput"]["command"]
    check(
        "context_filter_hook: rewritten command has no raw backslash paths (fleet-config#405)",
        code == 0 and "\\" not in rewritten_command,
        rewritten_command,
    )
    check(
        "context_filter_hook: PowerShell rewrite uses the call operator (fleet-config#405)",
        rewritten_command.startswith("& "),
        rewritten_command,
    )

    bash_payload = {
        "tool_name": "Bash",
        "cwd": str(REPO),
        "tool_input": {"command": "git status --short"},
    }
    code, stdout, stderr = run("context_filter_hook", bash_payload, {"FLEET_CONTEXT_FILTER_MODE": "rewrite"})
    bash_rewritten = ""
    if code == 0 and stdout.strip():
        bash_rewritten = json.loads(stdout)["hookSpecificOutput"]["updatedInput"]["command"]
    check(
        "context_filter_hook: Bash rewrite has no raw backslashes and no call operator (fleet-config#405)",
        code == 0 and "\\" not in bash_rewritten and not bash_rewritten.startswith("&"),
        bash_rewritten,
    )

    streaming = {
        "tool_name": "PowerShell",
        "cwd": str(REPO),
        "tool_input": {"command": "npm run dev -- --watch"},
    }
    code, stdout, stderr = run("context_filter_hook", streaming, {"FLEET_CONTEXT_FILTER_MODE": "rewrite"})
    check(
        "context_filter_hook: streaming command passthrough",
        code == 0 and stdout.strip() == "",
        stdout + stderr,
    )

    res = subprocess.run(
        [
            PYTHON,
            str(HOOKS / "context_filter_cli.py"),
            "eval",
            "--fixtures",
            str(REPO / "tests" / "fixtures" / "context_filter"),
            "--min-median-reduction",
            "35",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    check(
        "context_filter_eval: fixture benchmark passes",
        res.returncode == 0 and "median reduction:" in res.stdout,
        res.stdout + res.stderr,
    )

    # ---- wrapper timeout must not outlive a pipe-holding grandchild (#411) ----
    # subprocess.run(capture_output, timeout=) reacts to a timeout by killing only
    # the direct child, then collecting output with NO timeout — so a surviving
    # grandchild that inherited the pipes blocks it forever. That wedged a
    # scheduled run for eight hours. The wrapper must kill the tree and return
    # 124 well before the grandchild would have exited on its own.
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "hold_pipe.py"
        probe.write_text(
            "import subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            "time.sleep(120)\n",
            encoding="utf-8",
        )
        command = f'& "{PYTHON.replace(chr(92), "/")}" "{str(probe).replace(chr(92), "/")}"'
        encoded = base64.b64encode(command.encode("utf-8")).decode("ascii")
        started = time.monotonic()
        timed_out = False
        try:
            res = subprocess.run(
                [
                    PYTHON,
                    str(HOOKS / "context_filter_cli.py"),
                    "run",
                    "--tool",
                    "PowerShell",
                    "--mode",
                    "shadow",
                    "--encoded",
                    encoded,
                ],
                capture_output=True,
                text=True,
                env={**os.environ, "FLEET_CONTEXT_FILTER_TIMEOUT": "3"},
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            timed_out = True
        elapsed = time.monotonic() - started
        check(
            "context_filter_cli: pipe-holding grandchild does not outlive the wrapper timeout (fleet-config#411)",
            not timed_out and res.returncode == 124 and elapsed < 45,
            f"timed_out={timed_out} elapsed={elapsed:.1f}s "
            + ("" if timed_out else f"rc={res.returncode} stderr={res.stderr.strip()}"),
        )
        check(
            "context_filter_cli: timeout message names the tree kill",
            not timed_out and "process tree killed" in res.stderr,
            "" if timed_out else res.stderr.strip(),
        )

    # ---- timed-out stdout must carry an in-band truncation marker (#424) ----
    # A consumer reading stdout as the command's output (e.g. JSON from a fleet
    # sweep helper) must not be able to mistake a wrapper-timeout truncation for
    # complete output. The marker has to land on stdout itself, not just stderr.
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "slow_stdout.py"
        probe.write_text(
            "import sys, time\n"
            "for i in range(30):\n"
            "    sys.stdout.write(str(i) + chr(10))\n"
            "    sys.stdout.flush()\n"
            "    time.sleep(1)\n",
            encoding="utf-8",
        )
        command = f'& "{PYTHON.replace(chr(92), "/")}" "{str(probe).replace(chr(92), "/")}"'
        encoded = base64.b64encode(command.encode("utf-8")).decode("ascii")
        res = subprocess.run(
            [
                PYTHON,
                str(HOOKS / "context_filter_cli.py"),
                "run",
                "--tool",
                "PowerShell",
                "--mode",
                "shadow",
                "--encoded",
                encoded,
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "FLEET_CONTEXT_FILTER_TIMEOUT": "3"},
            timeout=60,
        )
        check(
            "context_filter_cli: timed-out stdout carries an in-band truncation marker (fleet-config#424)",
            res.returncode == 124 and "OUTPUT TRUNCATED" in res.stdout and "0" in res.stdout,
            res.stdout.strip() + " | " + res.stderr.strip(),
        )

    # ---- skill helpers are never wrapped; ordinary commands still are ----
    # A helper shipped with a skill produces one payload the orchestrator parses
    # directly, so wrapping it risks the #424 truncation for no compression
    # upside. #427 widened the rule from skills/_lib to every skill directory —
    # the longest-running helpers live beside their own skill (fleet-health's
    # capture.py blocks 540s against the 600s cap; system-map's build_data.py
    # crawls the whole fleet). The negative rows are the point of the table:
    # widening the pattern must not swallow ordinary work.
    python_prefix = '& "E:/automation/fleet-config/.venv/Scripts/python.exe" '
    skill_dir = '"E:/automation/fleet-config/.claude/skills/'
    passthrough_cases = [
        ("skills/_lib sweep helper",
         python_prefix + '"skills/_lib/fleet_audit_scan.py" --root E:\\automation', True, 424),
        ("fleet-health capture.py",
         python_prefix + skill_dir + 'fleet-health/capture.py" --minutes 9', True, 427),
        ("system-map build_data.py",
         python_prefix + skill_dir + 'system-map/build_data.py"', True, 427),
        ("ordinary python -c", python_prefix + '-c "print(1)"', False, 427),
        ("ordinary git", "git status --short", False, 427),
    ]
    for label, command, expect_passthrough, issue in passthrough_cases:
        payload = {"tool_name": "PowerShell", "cwd": str(REPO), "tool_input": {"command": command}}
        code, stdout, stderr = run(
            "context_filter_hook", payload, {"FLEET_CONTEXT_FILTER_MODE": "rewrite"}
        )
        verb = "passthrough, not wrapped" if expect_passthrough else "still wrapped"
        check(
            f"context_filter_hook: {label} {verb} (fleet-config#{issue})",
            code == 0 and (stdout.strip() == "") == expect_passthrough,
            stdout + stderr,
        )

    # ---- wrapper stdout re-emission survives non-cp1252 output (#426) ----
    # The wrapped child's own output is decoded as explicit UTF-8 in
    # _run_command, but context_filter_cli.py's own sys.stdout.write of that
    # output falls back to the locale codec (cp1252 here) unless reconfigured
    # to UTF-8 up front. An emoji/astral codepoint used to crash the wrapper
    # with UnicodeEncodeError instead of passing the output through.
    emoji_command = "Write-Output ([System.Char]::ConvertFromUtf32(0x1F4CA))"
    encoded = base64.b64encode(emoji_command.encode("utf-8")).decode("ascii")
    with tempfile.TemporaryDirectory() as tmp:
        for mode in ("shadow", "rewrite"):
            res = subprocess.run(
                [
                    PYTHON,
                    str(HOOKS / "context_filter_cli.py"),
                    "run",
                    "--tool",
                    "PowerShell",
                    "--mode",
                    mode,
                    "--encoded",
                    encoded,
                ],
                capture_output=True,
                text=True,
                # FLEET_CONTEXT_FILTER_DIR: keep test rows out of the machine's
                # real telemetry — the launcher stats panel reads it now (#541).
                env={**os.environ, "PYTHONUTF8": "0", "FLEET_CONTEXT_FILTER_DIR": tmp},
                timeout=30,
            )
            check(
                f"context_filter_cli: {mode} mode survives non-cp1252 output (fleet-config#426)",
                res.returncode == 0 and "UnicodeEncodeError" not in res.stderr,
                res.stdout.strip() + " | " + res.stderr.strip(),
            )

    # ---- mode file resolution: env override -> mode.json -> off (#541) ----
    # The machine-wide switch is ~/.fleet-context-filter/mode.json (written by
    # the app-launcher toggle); the env var stays the per-process override and
    # kill switch. FLEET_CONTEXT_FILTER_MODE is cleared explicitly because the
    # acceptance run itself may be inside a session that still carries it.
    base_payload = {
        "tool_name": "PowerShell",
        "cwd": str(REPO),
        "tool_input": {"command": "git status --short"},
    }
    with tempfile.TemporaryDirectory() as tmp:
        mode_file = Path(tmp) / "mode.json"
        isolated = {"FLEET_CONTEXT_FILTER_MODE": "", "FLEET_CONTEXT_FILTER_DIR": tmp}

        code, stdout, stderr = run("context_filter_hook", base_payload, isolated)
        check(
            "context_filter_hook: no env, no mode.json -> allow (fleet-config#541)",
            code == 0 and stdout.strip() == "",
            stdout + stderr,
        )

        mode_file.write_text(json.dumps({"mode": "rewrite"}), encoding="utf-8")
        code, stdout, stderr = run("context_filter_hook", base_payload, isolated)
        check(
            "context_filter_hook: mode.json rewrite -> wraps (fleet-config#541)",
            code == 0 and "updatedInput" in stdout and "--mode rewrite" in stdout,
            stdout + stderr,
        )

        code, stdout, stderr = run(
            "context_filter_hook", base_payload, {**isolated, "FLEET_CONTEXT_FILTER_MODE": "off"}
        )
        check(
            "context_filter_hook: env off overrides mode.json rewrite (fleet-config#541)",
            code == 0 and stdout.strip() == "",
            stdout + stderr,
        )

        mode_file.write_text("{not json", encoding="utf-8")
        code, stdout, stderr = run("context_filter_hook", base_payload, isolated)
        check(
            "context_filter_hook: malformed mode.json degrades to off (fleet-config#541)",
            code == 0 and stdout.strip() == "",
            stdout + stderr,
        )

    # ---- grok payloads short-circuit: its PreToolUse ignores updatedInput ----
    grok_payload = {
        "hookEventName": "PreToolUse",
        "cwd": str(REPO),
        "toolName": "run_terminal_command",
        "toolInput": {"command": "git status --short"},
    }
    code, stdout, stderr = run(
        "context_filter_hook", grok_payload, {"FLEET_CONTEXT_FILTER_MODE": "rewrite"}
    )
    check(
        "context_filter_hook: grok payload short-circuits to allow (fleet-config#541)",
        code == 0 and stdout.strip() == "",
        stdout + stderr,
    )

    # ---- telemetry row schema is pinned, and rewrite mode logs too (#541) ----
    # The app-launcher stats panel consumes these rows; a silent schema drift or
    # a rewrite flip that stops logging would blank the panel with no error.
    expected_keys = {
        "ts", "mode", "agent", "session_id", "cwd", "command", "tool",
        "raw_tokens", "compressed_tokens", "reduction_pct", "duration_ms", "exit_code",
    }
    log_command = 'Write-Output "schema probe"'
    encoded = base64.b64encode(log_command.encode("utf-8")).decode("ascii")
    for mode in ("shadow", "rewrite"):
        with tempfile.TemporaryDirectory() as tmp:
            res = subprocess.run(
                [
                    PYTHON,
                    str(HOOKS / "context_filter_cli.py"),
                    "run",
                    "--tool", "PowerShell",
                    "--mode", mode,
                    "--encoded", encoded,
                    "--session-id", "test-session-541",
                    "--agent", "codex",
                ],
                capture_output=True,
                text=True,
                env={**os.environ, "FLEET_CONTEXT_FILTER_DIR": tmp},
                timeout=30,
            )
            log_path = Path(tmp) / "shadow.jsonl"
            rows = []
            if log_path.exists():
                rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            row = rows[0] if rows else {}
            check(
                f"context_filter_cli: {mode} mode logs a row with the pinned schema (fleet-config#541)",
                res.returncode == 0
                and len(rows) == 1
                and set(row.keys()) == expected_keys
                and row["mode"] == mode
                and row["agent"] == "codex"
                and row["session_id"] == "test-session-541"
                and row["ts"].endswith("+00:00"),
                f"rc={res.returncode} rows={len(rows)} keys={sorted(row.keys())} | {res.stderr.strip()}",
            )

    # ---- rewritten command carries attribution flags (#541) ----
    code, stdout, stderr = run(
        "context_filter_hook",
        {**base_payload, "session_id": "abc-123"},
        # APP_LAUNCHER_AGENT cleared: the acceptance run itself may be inside a
        # launcher-spawned session, which would win the attribution precedence.
        {"FLEET_CONTEXT_FILTER_MODE": "rewrite", "APP_LAUNCHER_AGENT": ""},
    )
    check(
        "context_filter_hook: rewritten command forwards --session-id and --agent (fleet-config#541)",
        code == 0 and "--session-id abc-123" in stdout and "--agent claude" in stdout,
        stdout + stderr,
    )

    # ---- blob GC: rewrite prunes cache entries older than the TTL (#541) ----
    with tempfile.TemporaryDirectory() as tmp:
        blobs = Path(tmp) / "blobs"
        blobs.mkdir(parents=True)
        stale = blobs / "deadbeefdeadbeef.txt"
        stale.write_text("old raw output", encoding="utf-8")
        old = time.time() - (8 * 24 * 3600)
        os.utime(stale, (old, old))
        res = subprocess.run(
            [
                PYTHON,
                str(HOOKS / "context_filter_cli.py"),
                "run",
                "--tool", "PowerShell",
                "--mode", "rewrite",
                "--encoded", encoded,
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "FLEET_CONTEXT_FILTER_DIR": tmp},
            timeout=30,
        )
        check(
            "context_filter_cli: blob older than 7 days is pruned on rewrite (fleet-config#541)",
            res.returncode == 0 and not stale.exists(),
            f"rc={res.returncode} stale_exists={stale.exists()} | {res.stderr.strip()}",
        )
    return check.failures, check.total




def _codex_hooks_config_check() -> Tuple[int, int]:
    """Codex hooks should run Python directly and fail fast.

    The Claude side still goes through ``run-hook.ps1`` because Claude Code runs
    settings commands through Git Bash on this Windows machine. Codex does not
    need that shim, and routing it through PowerShell caused all PreToolUse
    hooks to hang until Codex's default 600-second timeout. This check keeps the
    Codex wiring on the direct-Python path and proves the configured commands
    return promptly when driven with a minimal hook payload.
    """
    check = _Checker()

    data = json.loads((REPO / "codex-hooks.json").read_text(encoding="utf-8"))
    hook_entries = [
        hook
        for blocks in data.get("hooks", {}).values()
        for block in blocks
        for hook in block.get("hooks", [])
    ]
    commands = [str(hook.get("command", "")) for hook in hook_entries]
    timeouts = [hook.get("timeout") for hook in hook_entries]

    check(
        "codex_hooks: every hook has a <=15s timeout",
        bool(hook_entries) and all(isinstance(t, int) and 1 <= t <= 15 for t in timeouts),
        f"timeouts: {timeouts}",
    )
    check(
        "codex_hooks: commands bypass run-hook.ps1 / PowerShell",
        all("run-hook.ps1" not in c and "powershell" not in c.lower() for c in commands),
        "\n".join(commands),
    )
    check(
        "codex_hooks: commands invoke hook modules directly",
        all(re.search(r"^E:/automation/fleet-config/\.venv/Scripts/python\.exe\s+C:/Users/rober/\.codex/hooks/\w+\.py$", c) for c in commands),
        "\n".join(commands),
    )

    env = {k: v for k, v in os.environ.items() if k != "SLACK_BOT_TOKEN"}
    env["CLAUDE_SETTINGS_JSON_PATH"] = NO_SETTINGS_JSON
    smoke_failures: list[str] = []
    for command in commands:
        try:
            res = subprocess.run(
                command,
                input="{}",
                capture_output=True,
                text=True,
                timeout=5,
                env=env,
                shell=True,
            )
        except subprocess.TimeoutExpired:
            smoke_failures.append(f"{command} -> timed out")
            continue
        if res.returncode != 0:
            smoke_failures.append(
                f"{command} -> exit {res.returncode}: {(res.stderr or res.stdout).strip()}"
            )

    check(
        "codex_hooks: configured commands return promptly",
        not smoke_failures,
        "\n".join(smoke_failures),
    )

    return check.failures, check.total


def _slack_notify_unit_checks() -> Tuple[int, int]:
    """Exercise slack_notify without touching the network. Returns failure count."""
    sys.path.insert(0, str(HOOKS))
    import slack_notify  # noqa: E402

    check = _Checker()

    check(
        "slack_notify: archive URL -> bare id",
        slack_notify.parse_channel("https://x.slack.com/archives/C0B76GBA0LS") == "C0B76GBA0LS",
    )
    check(
        "slack_notify: bare id passes through",
        slack_notify.parse_channel("  C0B76GBA0LS  ") == "C0B76GBA0LS",
    )

    # Missing token must return False (never raise, never post). Force-unset the
    # env var AND neutralize the settings.json fallback around the call, so
    # neither a real token in the dev box's env nor one in ~/.claude/settings.json
    # can trigger a post — this exercises the genuine "no token anywhere" path.
    saved = os.environ.pop(slack_notify.TOKEN_ENV_VAR, None)
    saved_from_settings = slack_notify._token_from_settings
    slack_notify._token_from_settings = lambda: None
    try:
        result = slack_notify.notify("test", channel="C0B76GBA0LS", token=None)
    finally:
        slack_notify._token_from_settings = saved_from_settings
        if saved is not None:
            os.environ[slack_notify.TOKEN_ENV_VAR] = saved
    check("slack_notify: missing token -> False (graceful)", result is False)

    # The settings.json fallback resolves a token when the env var is unset —
    # this is the launcher-agnostic behaviour (#192). Stub the file reader so the
    # check is hermetic (independent of whether the dev box's settings.json has a
    # token) and confirm the resolution order: env var wins, else settings.json.
    saved_env = os.environ.pop(slack_notify.TOKEN_ENV_VAR, None)
    saved_reader = slack_notify._token_from_settings
    slack_notify._token_from_settings = lambda: "xoxb-from-settings"
    try:
        from_settings = slack_notify._resolve_token(None)
        os.environ[slack_notify.TOKEN_ENV_VAR] = "xoxb-from-env"
        env_wins = slack_notify._resolve_token(None)
    finally:
        slack_notify._token_from_settings = saved_reader
        os.environ.pop(slack_notify.TOKEN_ENV_VAR, None)
        if saved_env is not None:
            os.environ[slack_notify.TOKEN_ENV_VAR] = saved_env
    check("slack_notify: settings.json fallback resolves token when env unset",
          from_settings == "xoxb-from-settings")
    check("slack_notify: env var wins over settings.json fallback",
          env_wins == "xoxb-from-env")

    return check.failures, check.total


def _notify_mention_unit_checks() -> Tuple[int, int]:
    """The single-sourced @mention decision in slack_notify (off by default).

    Mentioning now lives in exactly one place — ``slack_notify.notify()`` — via
    two pure helpers. No caller hand-assembles ``<@U…>`` anymore.
    """
    sys.path.insert(0, str(HOOKS))
    import slack_notify  # noqa: E402

    check = _Checker()

    check("mention_prefix: enabled + user -> tag",
          slack_notify._mention_prefix("U0B71PQEL6S", True) == "<@U0B71PQEL6S> ")
    check("mention_prefix: disabled -> no tag",
          slack_notify._mention_prefix("U0B71PQEL6S", False) == "")
    check("mention_prefix: enabled but no user -> no tag",
          slack_notify._mention_prefix(None, True) == "")
    check("resolve_mention: explicit override wins",
          slack_notify._resolve_mention(True) is True
          and slack_notify._resolve_mention(False) is False)
    # None -> read the [global] slack_notify_mention toggle, which ships off.
    check("resolve_mention: None -> global toggle (off by default)",
          slack_notify._resolve_mention(None) is False)

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
            gate_trigger_globs=(), gate_cmd=None, tray_cmd=None, restart_cmd=None,
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

        # ---- board_link: configured + session_id -> mrkdwn deep link ----
        payload = {"session_id": "abc-123", "cwd": "E:/automation/x"}
        check("board_link: configured -> Slack mrkdwn deep link",
              notify_on_idle.board_link(payload, registry=reg)
              == "📋 <https://proj.example:8445/?board=abc-123|Open on the Board>")

        # ---- board_link: trailing slash on board_url is stripped ----
        trailing = _lib.Registry(
            projects=[], globals=_lib.GlobalConfig(never_kill_ports=(), board_url="https://global.example:8445/"),
        )
        check("board_link: trailing slash on board_url stripped",
              notify_on_idle.board_link(payload, registry=trailing)
              == "📋 <https://global.example:8445/?board=abc-123|Open on the Board>")

        # ---- board_link: board_url with an existing query string merges, not concatenates (fleet-config#273) ----
        tokened = _lib.Registry(
            projects=[], globals=_lib.GlobalConfig(never_kill_ports=(), board_url="https://global.example:8445?token=secret"),
        )
        check("board_link: existing ?token= on board_url survives alongside ?board=",
              notify_on_idle.board_link(payload, registry=tokened)
              == "📋 <https://global.example:8445/?token=secret&board=abc-123|Open on the Board>")

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

    return check.failures, check.total


def _notify_chief_routing_unit_checks() -> Tuple[int, int]:
    """`is_chief_managed`/`parse_chief_sid` — the pure decision logic behind
    routing a chief-dispatched worker's blocked-on-input notification to
    chief instead of Slack (fleet-config#443).

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


def _block_askuserquestion_chief_unit_checks() -> Tuple[int, int]:
    """`block_askuserquestion_chief.py` (fleet-config#463): drives the real
    hook subprocess against a temp `CLAUDE_HOOKS_STATE_DIR` carrying a
    `chief-managed.json` marker, so a managed sid's `AskUserQuestion` blocks
    (exit 2) while everything else -- an unmanaged sid, a non-`AskUserQuestion`
    tool, a missing `session_id`, and a corrupt state file -- fails open
    (exit 0), never stranding an ordinary session over a bad read.
    """
    check = _Checker()

    tmp = Path(tempfile.mkdtemp(prefix="block_askuserquestion_"))
    try:
        marker = tmp / "chief-managed.json"
        marker.write_text(json.dumps({
            "sid-managed": {"repo": "fleet-config", "number": 463,
                             "dispatched_at": "2026-07-27T12:00:00Z"},
        }), encoding="utf-8")
        env = {"CLAUDE_HOOKS_STATE_DIR": str(tmp)}

        code, _out, stderr = run(
            "block_askuserquestion_chief",
            {"tool_name": "AskUserQuestion", "session_id": "sid-managed"},
            extra_env=env,
        )
        check("block_askuserquestion: managed sid + AskUserQuestion -> block (exit 2)", code == 2)
        check("block_askuserquestion: block reason mentions the say/exchange fallback",
              "chief_ops.py say" in stderr or "say" in stderr.lower())

        code, _out, _err = run(
            "block_askuserquestion_chief",
            {"tool_name": "AskUserQuestion", "session_id": "sid-unmanaged"},
            extra_env=env,
        )
        check("block_askuserquestion: unmanaged sid -> allow (exit 0)", code == 0)

        code, _out, _err = run(
            "block_askuserquestion_chief",
            {"tool_name": "Bash", "session_id": "sid-managed"},
            extra_env=env,
        )
        check("block_askuserquestion: managed sid but non-AskUserQuestion tool -> allow (exit 0)", code == 0)

        code, _out, _err = run(
            "block_askuserquestion_chief",
            {"tool_name": "AskUserQuestion"},
            extra_env=env,
        )
        check("block_askuserquestion: missing session_id -> allow (exit 0)", code == 0)

        corrupt = tmp / "corrupt"
        corrupt.mkdir()
        corrupt_marker = corrupt / "chief-managed.json"
        corrupt_marker.write_text("{not json", encoding="utf-8")
        code, _out, _err = run(
            "block_askuserquestion_chief",
            {"tool_name": "AskUserQuestion", "session_id": "sid-managed"},
            extra_env={"CLAUDE_HOOKS_STATE_DIR": str(corrupt)},
        )
        check("block_askuserquestion: corrupt state file -> fail open, allow (exit 0)", code == 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return check.failures, check.total


def _lib_detect_project_unit_checks() -> Tuple[int, int]:
    """`_lib.detect_project` resolves a `<repo>-wt-<N>` sibling worktree cwd
    (`worktree_claim.py setup-worktree`'s naming convention for every second
    session on a claimed repo) to the same project as its primary checkout
    (fleet-config#471)."""
    sys.path.insert(0, str(HOOKS))
    import _lib  # noqa: E402

    check = _Checker()

    registry = _lib.Registry(
        projects=[
            _lib.ProjectConfig(
                name="fleet-config", cwd_prefix=Path("E:/automation/fleet-config"),
                webapp_port=None, gate_trigger_globs=(), gate_cmd=None,
                tray_cmd=None, restart_cmd=None, api_version_path=None, extra={},
            ),
            _lib.ProjectConfig(
                name="app-launcher", cwd_prefix=Path("E:/automation/app-launcher"),
                webapp_port=None, gate_trigger_globs=(), gate_cmd=None,
                tray_cmd=None, restart_cmd=None, api_version_path=None, extra={},
            ),
        ],
        globals=_lib.GlobalConfig(never_kill_ports=()),
    )

    def name_of(cwd: str) -> Any:
        project = _lib.detect_project(Path(cwd), registry)
        return project.name if project else None

    check("detect_project: primary checkout still matches",
          name_of("E:/automation/fleet-config") == "fleet-config")
    check("detect_project: sibling worktree root resolves to the primary project",
          name_of("E:/automation/fleet-config-wt-464") == "fleet-config")
    check("detect_project: nested path inside a sibling worktree still resolves",
          name_of("E:/automation/fleet-config-wt-464/hooks") == "fleet-config")
    check("detect_project: unrelated sibling worktree does not cross-match",
          name_of("E:/automation/app-launcher-wt-9") == "app-launcher")
    check("detect_project: no match for a path outside every prefix",
          name_of("E:/automation/unrelated-repo") is None)

    return check.failures, check.total


def _chief_handover_sessionstart_unit_checks() -> Tuple[int, int]:
    """`build_context`/`handover_path` pure logic, plus one real end-to-end
    hook run with an isolated state dir (fleet-config#442).

    Unlike `notify_on_idle`'s chief-routing, this hook has no network or
    subprocess call at all -- a plain file read + one `print()` -- so the
    end-to-end case below carries none of that module's live-side-effect
    risk and is exercised fully via `run()`.
    """
    sys.path.insert(0, str(HOOKS))
    import chief_handover_sessionstart as chs  # noqa: E402

    check = _Checker()

    # ---- build_context: pure string assembly + tail-truncation ----
    short = chs.build_context("current batch: #442, #443 shipped.", Path("X:/log.md"))
    check("build_context: short content passes through, carries the fleet-config#442 preamble",
          "current batch: #442, #443 shipped." in short and "fleet-config#442" in short)

    log_path = Path("X:/log.md")
    long_content = "x" * (chs.MAX_INLINE_CHARS + 500)
    truncated = chs.build_context(long_content, log_path)
    check("build_context: over-ceiling content is truncated to the tail",
          truncated.count("x") <= chs.MAX_INLINE_CHARS + 20)  # + a little preamble slop, never the full length
    check("build_context: truncation points at the full-log path",
          str(log_path) in truncated)  # str(Path) renders with the platform's own separator

    # ---- handover_path: CLAUDE_HOOKS_STATE_DIR override (mirrors session_state.py) ----
    saved_env = os.environ.get("CLAUDE_HOOKS_STATE_DIR")
    try:
        os.environ["CLAUDE_HOOKS_STATE_DIR"] = "X:/fake-state-dir"
        check("handover_path: honors CLAUDE_HOOKS_STATE_DIR",
              chs.handover_path() == Path("X:/fake-state-dir") / "chief-handover.md")
    finally:
        if saved_env is None:
            os.environ.pop("CLAUDE_HOOKS_STATE_DIR", None)
        else:
            os.environ["CLAUDE_HOOKS_STATE_DIR"] = saved_env

    # ---- end-to-end: fleet-config cwd + a real handover file -> additionalContext ----
    tmp = Path(tempfile.mkdtemp(prefix="chief_handover_e2e_"))
    try:
        (tmp / "chief-handover.md").write_text(
            "## 2026-07-27\ncurrent batch: #445 shipped, #443 in review.\n", encoding="utf-8"
        )
        code, stdout, stderr = run(
            "chief_handover_sessionstart",
            {"hook_event_name": "SessionStart", "source": "compact", "cwd": str(REPO)},
            extra_env={"CLAUDE_HOOKS_STATE_DIR": str(tmp)},
        )
        check(f"chief_handover_sessionstart e2e: exits 0 ({stderr.strip()})", code == 0)
        check("chief_handover_sessionstart e2e: stdout carries the SessionStart hookSpecificOutput envelope",
              '"hookEventName": "SessionStart"' in stdout)
        check("chief_handover_sessionstart e2e: additionalContext carries the log content",
              "#445 shipped, #443 in review" in stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return check.failures, check.total


def _session_state_unit_checks() -> Tuple[int, int]:
    """sessions-state.json persistence (fleet-config#91): event → status mapping,
    same-session flip, pruning, corrupt-file recovery, the notify_on_idle
    piggyback, and the live session-name lookup (fleet-config#302) — all
    against a temp CLAUDE_HOOKS_STATE_DIR / CLAUDE_SESSIONS_DIR so nothing
    touches the real ~/.claude/hooks/state or ~/.claude/sessions."""
    sys.path.insert(0, str(HOOKS))
    import session_state  # noqa: E402

    check = _Checker()

    tmp = Path(tempfile.mkdtemp(prefix="session_state_"))
    sessions_dir = Path(tempfile.mkdtemp(prefix="session_state_sessions_"))
    env = {
        "CLAUDE_HOOKS_STATE_DIR": str(tmp),
        "CLAUDE_SESSIONS_DIR": str(sessions_dir),
        "CLAUDE_SETTINGS_JSON_PATH": NO_SETTINGS_JSON,
        # Keep the fixture external by default even when this acceptance run
        # itself was started inside App Launcher.
        "APP_LAUNCHER_SESSION_ID": "",
        "APP_LAUNCHER_AGENT": "",
    }
    state_path = tmp / session_state.STATE_FILENAME

    def rows() -> Dict[str, Any]:
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    # A live per-process registry fixture (~/.claude/sessions/<pid>.json-style):
    # one file whose sessionId matches "sid-1" (the generic-fallback case,
    # nameSource:"derived") and one unrelated file that must not match.
    (sessions_dir / "70212.json").write_text(json.dumps({
        "pid": 70212, "sessionId": "sid-1", "cwd": str(tmp), "kind": "interactive",
        "entrypoint": "cli", "name": "fleet-config-c4", "nameSource": "derived",
        "status": "busy",
    }), encoding="utf-8")
    (sessions_dir / "99999.json").write_text(json.dumps({
        "pid": 99999, "sessionId": "sid-unrelated", "name": "other-session",
    }), encoding="utf-8")
    # A malformed fixture alongside the good ones must not break the scan.
    (sessions_dir / "bad.json").write_text("{not json", encoding="utf-8")

    saved_env = os.environ.get("CLAUDE_HOOKS_STATE_DIR")
    saved_sessions_env = os.environ.get("CLAUDE_SESSIONS_DIR")
    os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(tmp)
    os.environ["CLAUDE_SESSIONS_DIR"] = str(sessions_dir)
    try:
        # ---- subprocess: the two wired events, same session flips status ----
        payload = {"hook_event_name": "UserPromptSubmit", "session_id": "sid-1",
                   "transcript_path": str(tmp / "t.jsonl"), "cwd": str(tmp)}
        launcher_env = {
            **env,
            "APP_LAUNCHER_SESSION_ID": "launcher-abc",
            "APP_LAUNCHER_AGENT": "claude",
        }
        code, _out, _err = run("session_state", payload, extra_env=launcher_env)
        row = rows().get("sid-1") or {}
        check("session_state: UserPromptSubmit -> exit 0 + row 'working' with cwd",
              code == 0 and row.get("status") == "working" and row.get("cwd") == str(tmp))
        check("session_state: matching sessionId -> row carries live name + nameSource (#302)",
              row.get("name") == "fleet-config-c4" and row.get("name_source") == "derived")
        check("session_state: launcher env -> exact launcher id + agent (#345)",
              row.get("launcher_session_id") == "launcher-abc" and row.get("agent") == "claude")

        # ---- no matching sessionId in the registry -> name/name_source stay None ----
        code, _out, _err = run(
            "session_state",
            {"hook_event_name": "UserPromptSubmit", "session_id": "sid-no-match",
             "transcript_path": str(tmp / "t2.jsonl"), "cwd": str(tmp)},
            extra_env=env,
        )
        no_match_row = rows().get("sid-no-match") or {}
        check("session_state: no matching sessionId -> name/name_source omitted (None)",
              code == 0 and no_match_row.get("name") is None and no_match_row.get("name_source") is None)
        check("session_state: external Claude row -> explicit agent, no launcher id (#345)",
              no_match_row.get("agent") == "claude" and no_match_row.get("launcher_session_id") is None)

        # ---- missing sessions registry directory entirely -> still exit 0, no name ----
        missing_dir = sessions_dir / "does-not-exist"
        code, _out, _err = run(
            "session_state",
            {"hook_event_name": "UserPromptSubmit", "session_id": "sid-no-registry",
             "transcript_path": str(tmp / "t3.jsonl"), "cwd": str(tmp)},
            extra_env={**env, "CLAUDE_SESSIONS_DIR": str(missing_dir)},
        )
        no_registry_row = rows().get("sid-no-registry") or {}
        check("session_state: missing sessions registry dir -> exit 0, name omitted",
              code == 0 and no_registry_row.get("name") is None)

        code, _out, _err = run(
            "session_state", {**payload, "hook_event_name": "Stop"},
            extra_env=launcher_env,
        )
        stopped_row = rows().get("sid-1") or {}
        check("session_state: Stop flips the same session to 'needs-you'",
              code == 0 and stopped_row.get("status") == "needs-you")
        check("session_state: Stop retains exact launcher identity (#345)",
              stopped_row.get("launcher_session_id") == "launcher-abc"
              and stopped_row.get("agent") == "claude")

        rows_before_missing_sid = set(rows())
        code, _out, _err = run("session_state", {"hook_event_name": "Stop", "cwd": str(tmp)}, extra_env=env)
        check("session_state: missing session_id -> exit 0, no row added",
              code == 0 and set(rows()) == rows_before_missing_sid)

        code, _out, _err = run("session_state", {**payload, "hook_event_name": "PreToolUse"}, extra_env=env)
        check("session_state: unwired event -> exit 0, state untouched",
              code == 0 and (rows().get("sid-1") or {}).get("status") == "needs-you")

        # ---- in-process: multi-row, pruning, corrupt-file recovery ----
        rows_before_sid2 = set(rows())
        session_state.upsert("sid-2", status="working", project="p2",
                             transcript_path=None, cwd_path=str(tmp))
        check("session_state: second session -> two distinct rows",
              set(rows()) == rows_before_sid2 | {"sid-2"})

        stale = rows()
        stale["sid-old"] = {"project": "old", "status": "idle", "transcript_path": None,
                            "cwd": str(tmp), "updated_at": "2020-01-01T00:00:00Z"}
        state_path.write_text(json.dumps(stale), encoding="utf-8")
        session_state.upsert("sid-2", status="needs-you", project="p2",
                             transcript_path=None, cwd_path=str(tmp))
        check("session_state: >24h-old row pruned on next write", "sid-old" not in rows())

        state_path.write_text("{not json", encoding="utf-8")
        session_state.upsert("sid-3", status="working", project="p3",
                             transcript_path=None, cwd_path=str(tmp))
        check("session_state: corrupt state file recovered by next upsert",
              (rows().get("sid-3") or {}).get("status") == "working")

        # ---- notify_on_idle piggyback: persists the row, ping path unchanged ----
        idle_payload = {"session_id": "sid-4", "transcript_path": str(tmp / "t.jsonl"),
                        "cwd": str(tmp), "notification_type": "permission_prompt",
                        "message": "Claude needs your permission"}
        code, _out, _err = run("notify_on_idle", idle_payload, extra_env=env)
        check("notify_on_idle: permission_prompt persists a 'needs-you' row (exit 0)",
              code == 0 and (rows().get("sid-4") or {}).get("status") == "needs-you")

        # fleet-config#354: idle_prompt is a periodic "still waiting on you" nag,
        # not a new state -- it must not downgrade an existing 'needs-you' row.
        code, _out, _err = run("notify_on_idle", {**idle_payload, "notification_type": "idle_prompt"}, extra_env=env)
        check("notify_on_idle: idle_prompt after needs-you -> row stays 'needs-you' (exit 0)",
              code == 0 and (rows().get("sid-4") or {}).get("status") == "needs-you")

        # Also true from a cold start (no prior row at all) -- idle_prompt writes nothing.
        cold_payload = {"session_id": "sid-5", "transcript_path": str(tmp / "t2.jsonl"),
                        "cwd": str(tmp), "notification_type": "idle_prompt",
                        "message": "Claude is waiting for your input"}
        code, _out, _err = run("notify_on_idle", cold_payload, extra_env=env)
        check("notify_on_idle: idle_prompt with no prior row -> exit 0, no row created",
              code == 0 and "sid-5" not in rows())

        # ---- SessionEnd (#241): deletes the row instead of leaving it to the 24h prune ----
        code, _out, _err = run(
            "session_state",
            {"hook_event_name": "SessionEnd", "session_id": "sid-1", "cwd": str(tmp)},
            extra_env=env,
        )
        check("session_state: SessionEnd removes the row (exit 0)",
              code == 0 and "sid-1" not in rows())

        before = set(rows())
        code, _out, _err = run(
            "session_state",
            {"hook_event_name": "SessionEnd", "session_id": "sid-does-not-exist", "cwd": str(tmp)},
            extra_env=env,
        )
        check("session_state: SessionEnd for an unknown sid -> exit 0, file untouched",
              code == 0 and set(rows()) == before)
    finally:
        if saved_env is None:
            os.environ.pop("CLAUDE_HOOKS_STATE_DIR", None)
        else:
            os.environ["CLAUDE_HOOKS_STATE_DIR"] = saved_env
        if saved_sessions_env is None:
            os.environ.pop("CLAUDE_SESSIONS_DIR", None)
        else:
            os.environ["CLAUDE_SESSIONS_DIR"] = saved_sessions_env
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(sessions_dir, ignore_errors=True)

    return check.failures, check.total


def _session_state_agent_adapter_unit_checks() -> Tuple[int, int]:
    """session_state_codex / session_state_pi (fleet-config#349): each
    adapter's own event->status map, the default_agent fallback when no
    launcher env is present, launcher env still winning when it is, and an
    unwired/unknown event staying a no-op — against a temp
    CLAUDE_HOOKS_STATE_DIR so nothing touches the real state file."""
    check = _Checker()

    tmp = Path(tempfile.mkdtemp(prefix="session_state_agents_"))
    env = {
        "CLAUDE_HOOKS_STATE_DIR": str(tmp),
        "CLAUDE_SESSIONS_DIR": str(tmp / "no-sessions-dir"),
        "CLAUDE_SETTINGS_JSON_PATH": NO_SETTINGS_JSON,
        "APP_LAUNCHER_SESSION_ID": "",
        "APP_LAUNCHER_AGENT": "",
    }
    state_path = tmp / "sessions-state.json"

    def rows() -> Dict[str, Any]:
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    try:
        # ---- Codex: UserPromptSubmit -> working, default_agent applied ----
        codex_payload = {"hook_event_name": "UserPromptSubmit", "session_id": "codex-1",
                          "cwd": str(tmp), "transcript_path": None}
        code, _out, _err = run("session_state_codex", codex_payload, extra_env=env)
        row = rows().get("codex-1") or {}
        check("session_state_codex: UserPromptSubmit -> working, agent defaults to codex",
              code == 0 and row.get("status") == "working" and row.get("agent") == "codex")

        code, _out, _err = run(
            "session_state_codex", {**codex_payload, "hook_event_name": "Stop"}, extra_env=env,
        )
        check("session_state_codex: Stop -> needs-you",
              code == 0 and (rows().get("codex-1") or {}).get("status") == "needs-you")

        code, _out, _err = run(
            "session_state_codex", {**codex_payload, "hook_event_name": "PermissionRequest"}, extra_env=env,
        )
        check("session_state_codex: PermissionRequest -> needs-you",
              code == 0 and (rows().get("codex-1") or {}).get("status") == "needs-you")

        code, _out, _err = run(
            "session_state_codex", {**codex_payload, "hook_event_name": "PreToolUse"}, extra_env=env,
        )
        check("session_state_codex: unwired event -> exit 0, state untouched",
              code == 0 and (rows().get("codex-1") or {}).get("status") == "needs-you")

        launcher_env = {**env, "APP_LAUNCHER_SESSION_ID": "launcher-codex", "APP_LAUNCHER_AGENT": "codex"}
        code, _out, _err = run(
            "session_state_codex",
            {**codex_payload, "hook_event_name": "UserPromptSubmit", "session_id": "codex-2"},
            extra_env=launcher_env,
        )
        codex2_row = rows().get("codex-2") or {}
        check("session_state_codex: launcher env still wins over the default_agent fallback",
              code == 0 and codex2_row.get("agent") == "codex"
              and codex2_row.get("launcher_session_id") == "launcher-codex")

        # ---- Pi: input -> working, agent_settled -> needs-you, default_agent ----
        pi_event = {"event": "input", "session_id": "pi-1", "cwd": str(tmp)}
        code, _out, _err = run("session_state_pi", pi_event, extra_env=env)
        pi_row = rows().get("pi-1") or {}
        check("session_state_pi: input -> working, agent defaults to pi",
              code == 0 and pi_row.get("status") == "working" and pi_row.get("agent") == "pi")

        code, _out, _err = run(
            "session_state_pi", {**pi_event, "event": "agent_settled"}, extra_env=env,
        )
        check("session_state_pi: agent_settled -> needs-you",
              code == 0 and (rows().get("pi-1") or {}).get("status") == "needs-you")

        code, _out, _err = run(
            "session_state_pi", {**pi_event, "event": "some_unwired_event"}, extra_env=env,
        )
        check("session_state_pi: unwired event -> exit 0, state untouched",
              code == 0 and (rows().get("pi-1") or {}).get("status") == "needs-you")

        # ---- Pi: session_shutdown removes the row (the Codex adapter has no analog) ----
        code, _out, _err = run(
            "session_state_pi", {**pi_event, "event": "session_shutdown"}, extra_env=env,
        )
        check("session_state_pi: session_shutdown removes the row",
              code == 0 and "pi-1" not in rows())

        before = set(rows())
        code, _out, _err = run(
            "session_state_pi", {"event": "session_shutdown", "session_id": "pi-does-not-exist", "cwd": str(tmp)},
            extra_env=env,
        )
        check("session_state_pi: session_shutdown for an unknown sid -> exit 0, file untouched",
              code == 0 and set(rows()) == before)

        # Two agents in the same project stay independent rows (fleet-config#349
        # acceptance: "Two agents in one project remain independent") — same
        # cwd, distinct session ids and agent fields, neither writer clobbers
        # the other's row.
        code, _out, _err = run(
            "session_state_pi", {"event": "input", "session_id": "pi-2", "cwd": str(tmp)}, extra_env=env,
        )
        codex2_after = rows().get("codex-2") or {}
        pi2_row = rows().get("pi-2") or {}
        check("session_state: Codex and Pi rows for the same cwd stay independent",
              code == 0 and codex2_after.get("agent") == "codex" and pi2_row.get("agent") == "pi"
              and codex2_after.get("cwd") == pi2_row.get("cwd") == str(tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return check.failures, check.total

    return check.failures, check.total


def _gh_body_file_guard_unit_checks() -> Tuple[int, int]:
    """The warn-only nudge fires on the two payload traps and stays silent
    otherwise. Exit is always 0, so these assert on STDOUT, not the exit code:
    a nudge present (non-empty stdout) for the risky forms, empty for the safe
    ones."""
    check = _Checker()

    def stdout_for(command: str) -> str:
        code, out, _err = run("gh_body_file_guard", {"tool_name": "Bash", "tool_input": {"command": command}})
        # warn-only: the hook must never block (exit non-zero) regardless of input.
        return out.strip() if code == 0 else f"__NONZERO_EXIT_{code}__"

    check("gh_guard: gh pr create --body with backtick -> nudge",
          bool(stdout_for('gh pr create --title x --body "see `uname -a`"')))
    check("gh_guard: gh issue comment --body with heredoc -> nudge",
          bool(stdout_for('gh issue comment 5 --body "$(cat <<EOF\nhi\nEOF\n)"')))
    check("gh_guard: PowerShell here-string through Bash -> nudge",
          bool(stdout_for("printf '%s' @'\nhello\n'@")))
    check("gh_guard: gh pr create --body-file -> silent",
          stdout_for("gh pr create --title x --body-file E:/tmp/pr-116.md") == "")
    check("gh_guard: gh issue list (read) -> silent",
          stdout_for("gh issue list --state open --limit 20") == "")
    check("gh_guard: gh pr create plain inline body (no risky construct) -> silent",
          stdout_for('gh pr create --title x --body "plain text, nothing to expand"') == "")

    return check.failures, check.total


def _bash_cmdexe_syntax_guard_unit_checks() -> Tuple[int, int]:
    """The guard blocks MSYS-mangled cmd /c, nudges cmd-only syntax, and stays
    silent on Bash-native or explicitly MSYS-safe equivalents."""
    check = _Checker()

    def stdout_for(command: str) -> str:
        code, out, _err = run("bash_cmdexe_syntax_guard", {"tool_name": "Bash", "tool_input": {"command": command}})
        # These legacy syntax checks remain warn-only; cmd.exe /c is exercised
        # separately below because that caller shape is now a hard block.
        return out.strip() if code == 0 else f"__NONZERO_EXIT_{code}__"

    code, out, err = run(
        "bash_cmdexe_syntax_guard",
        {"tool_name": "Bash", "tool_input": {"command": 'cmd.exe /c "tray.bat --restart" 2>&1'}},
    )
    check("cmdexe_guard: Bash cmd.exe /c tray restart -> block with root cause",
          code == 2 and not out and "C:/" in err and "PowerShell" in err,
          out + err)

    code, _out, _err = run(
        "bash_cmdexe_syntax_guard",
        {"tool_name": "Bash", "tool_input": {"command": 'cmd.exe /d /s /c "echo safe"'}},
    )
    check("cmdexe_guard: Bash cmd.exe with leading flags then /c -> block", code == 2)

    code, out, err = run(
        "bash_cmdexe_syntax_guard",
        {"tool_name": "Bash", "tool_input": {"command": 'cmd.exe //d //c "echo safe"'}},
    )
    check("cmdexe_guard: Bash cmd.exe //c MSYS-safe spelling -> silent allow",
          code == 0 and not out and not err, out + err)

    code, out, err = run(
        "bash_cmdexe_syntax_guard",
        {"tool_name": "Bash", "tool_input": {"command": 'rg -n "cmd.exe /c" skills'}},
    )
    check("cmdexe_guard: quoted search text containing cmd.exe /c -> silent allow",
          code == 0 and not out and not err, out + err)

    code, out, err = run(
        "bash_cmdexe_syntax_guard",
        {"tool_name": "PowerShell", "tool_input": {"command": 'cmd.exe /c "tray.bat --restart"'}},
    )
    check("cmdexe_guard: PowerShell cmd.exe /c -> outside Bash guard",
          code == 0 and not out and not err, out + err)

    check("cmdexe_guard: %VAR% env reference -> nudge",
          bool(stdout_for("echo %USERPROFILE%")))
    check("cmdexe_guard: dir /s -> nudge",
          bool(stdout_for("dir /s")))
    check("cmdexe_guard: del /f -> nudge",
          bool(stdout_for("del /f file.txt")))
    check("cmdexe_guard: caret line-continuation -> nudge",
          bool(stdout_for("echo hello ^\necho world")))
    check("cmdexe_guard: printf %s (bare percent, no close) -> silent",
          stdout_for('printf "%s\\n" hello') == "")
    check("cmdexe_guard: URL path with /s (no cmd builtin) -> silent",
          stdout_for("curl https://example.com/s/path") == "")
    check("cmdexe_guard: date +%Y%m%d (single-letter format run) -> silent",
          stdout_for("date +%Y%m%d") == "")
    check("cmdexe_guard: plain git log -> silent",
          stdout_for("git log --oneline") == "")

    yolo_skill = (REPO / "skills" / "issue-yolo" / "SKILL.md").read_text(encoding="utf-8")
    yolo_skill_flat = re.sub(r"\s+", " ", yolo_skill.replace("**", ""))
    check("cmdexe_guard: issue-yolo mandates a real Windows shell for tray restart",
          "real Windows shell" in yolo_skill_flat and "cmd /c" in yolo_skill_flat)

    return check.failures, check.total


def _tier23_hooks_unit_checks() -> Tuple[int, int]:
    """The three Tier 2/3 hooks (issue #158): docs-guard env override, plus the
    two warn-only hooks whose output is on STDOUT (exit always 0), so these
    assert nudge-present / silent rather than the exit code. The warn hooks read
    the file from disk, so each case writes a real temp file first.
    """
    check = _Checker()

    # ---- docs_dated_filename_guard: env override flips block -> allow ----
    os.environ["CLAUDE_HOOKS_ALLOW_DATED_DOCS"] = "1"
    try:
        code, _out, _err = run("docs_dated_filename_guard",
                               {"tool_name": "Write",
                                "tool_input": {"file_path": "E:/automation/foo/docs/2026-06-18-retro.md"}})
        check("docs_guard: CLAUDE_HOOKS_ALLOW_DATED_DOCS=1 -> allow (override)", code == 0)
    finally:
        os.environ.pop("CLAUDE_HOOKS_ALLOW_DATED_DOCS", None)

    tmp = Path(tempfile.mkdtemp(prefix="tier23_"))
    try:
        def nudged(hook: str, path: Path, body: str, extra_env: Dict[str, str] | None = None) -> bool:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
            code, out, _err = run(hook, {"tool_name": "Write", "tool_input": {"file_path": str(path)}},
                                   extra_env=extra_env)
            return code == 0 and bool(out.strip())

        # ---- hub_bypass_warn ----
        check("hub_bypass: inline `claude -p` command string -> nudge",
              nudged("hub_bypass_warn", tmp / "wrapper.py",
                     'import subprocess\nsubprocess.run("claude -p hello", shell=True)\n'))
        check("hub_bypass: argv-form ['claude','-p'] -> nudge",
              nudged("hub_bypass_warn", tmp / "argv.py",
                     'from subprocess import Popen\nPopen(["claude", "-p", "hi"])\n'))
        check("hub_bypass: subprocess but no claude -p -> silent",
              not nudged("hub_bypass_warn", tmp / "other.py",
                         'import subprocess\nsubprocess.run(["ls", "-la"])\n'))
        # Points hub_bypass_warn.py at a throwaway projects.toml (via
        # CLAUDE_HOOKS_PROJECTS_TOML) flagging tmp/local-llm-hub as `is_hub`,
        # so the exemption is exercised through the real cwd_prefix-match path
        # instead of a hardcoded directory-name check.
        hub_projects_toml = tmp / "hub_projects.toml"
        hub_projects_toml.write_text(
            '[hub]\ncwd_prefix = "%s"\nis_hub = true\n' % (tmp / "local-llm-hub").as_posix(),
            encoding="utf-8",
        )
        check("hub_bypass: inside a repo flagged is_hub in projects.toml -> silent",
              not nudged("hub_bypass_warn", tmp / "local-llm-hub" / "server.py",
                         'import subprocess\nsubprocess.run("claude -p hello", shell=True)\n',
                         extra_env={"CLAUDE_HOOKS_PROJECTS_TOML": str(hub_projects_toml)}))

        # ---- browser_stealth_lint ----
        bare_launch = 'ctx = p.chromium.launch_persistent_context(user_data_dir="x")\n'
        full_launch = (
            'ctx = p.chromium.launch_persistent_context(\n'
            '    user_data_dir="x", channel="chrome",\n'
            '    ignore_default_args=["--enable-automation"],\n'
            '    args=["--disable-blink-features=AutomationControlled"],\n'
            ')\n'
            'page.add_init_script("Object.defineProperty(navigator, \'webdriver\', {get: () => undefined})")\n'
        )
        check("browser_stealth: chrome_launch.py missing markers -> nudge",
              nudged("browser_stealth_lint", tmp / "chrome_launch.py", bare_launch))
        check("browser_stealth: chrome_launch.py with all markers -> silent",
              not nudged("browser_stealth_lint", tmp / "ok_launch" / "chrome_launch.py", full_launch))
        check("browser_stealth: *_session.py with a launch missing a marker -> nudge",
              nudged("browser_stealth_lint", tmp / "x_session.py", bare_launch + 'channel="chrome"\n'))
        check("browser_stealth: watched name but no launch call -> silent",
              not nudged("browser_stealth_lint", tmp / "browser.py", "PORT = 9222\n"))
        check("browser_stealth: non-watched filename with a launch -> silent",
              not nudged("browser_stealth_lint", tmp / "helper.py", bare_launch))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return check.failures, check.total


def _branch_before_edit_guard_unit_checks() -> Tuple[int, int]:
    """branch_before_edit_guard.py: real temp git repos/worktrees on
    main/master/a feature branch, crossed with APP_LAUNCHER_SESSION_ID
    presence and the CLAUDE_HOOKS_ALLOW_MAIN_EDIT override (fleet-config#464,
    take 2). Every fixture below deliberately sets `cwd` and the edit
    `file_path`'s directory to *different* paths — the take-1 guard resolved
    the branch from `cwd` and was reverted for exactly the false positives
    that shape hides: a worktree worker judged by the primary checkout's
    branch, and a write outside any repo blocked by the session's cwd repo.
    None of these fixtures configure a git remote, so the master-branch case
    also proves `resolve_default_branch_ref`'s candidate probing (not
    `dirty_tree_check`'s `candidates=()` variant) still detects `master` as
    the protected branch with no `origin` configured. The gitignored-target
    fixtures cover take 2's own false positive (fleet-config#489) and pin the
    exemption to ignored paths only."""
    sys.path.insert(0, str(HOOKS))
    import _lib  # noqa: E402

    check = _Checker()
    launcher_env = {"APP_LAUNCHER_SESSION_ID": "launcher-test"}

    def git_repo(branch: str) -> Path:
        repo = Path(tempfile.mkdtemp(prefix="branch_guard_"))
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True, creationflags=_lib.NO_WINDOW)
        subprocess.run(
            ["git", "config", "user.email", "35553560+ferraroroberto@users.noreply.github.com"],
            cwd=repo, check=True, creationflags=_lib.NO_WINDOW,
        )
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True, creationflags=_lib.NO_WINDOW)
        subprocess.run(["git", "checkout", "-q", "-b", branch], cwd=repo, check=True, creationflags=_lib.NO_WINDOW)
        subprocess.run(
            ["git", "commit", "-q", "--allow-empty", "-m", "init"],
            cwd=repo, check=True, creationflags=_lib.NO_WINDOW,
        )
        (repo / "sub").mkdir(exist_ok=True)
        return repo

    def edit_payload(cwd: Path, target_dir: Path, tool: str = "Edit") -> Dict[str, Any]:
        # cwd (session dir) and the edit target's directory are deliberately
        # different paths — see the docstring above.
        return {"tool_name": tool, "cwd": str(cwd), "tool_input": {"file_path": str(target_dir / "f.py")}}

    main_repo = git_repo("main")
    try:
        code, _out, err = run(
            "branch_before_edit_guard", edit_payload(main_repo, main_repo / "sub"), extra_env=launcher_env
        )
        check("branch_guard: main (target != cwd dir) + launcher env -> block", code == 2, err)

        # Explicit empty-string override (not just an omitted extra_env) --
        # the ambient session this suite runs under may itself carry a real
        # APP_LAUNCHER_SESSION_ID, which `run()` would otherwise pass through.
        code, _out, _err = run(
            "branch_before_edit_guard", edit_payload(main_repo, main_repo / "sub"),
            extra_env={"APP_LAUNCHER_SESSION_ID": ""},
        )
        check("branch_guard: main + no launcher env -> allow", code == 0)

        code, _out, _err = run(
            "branch_before_edit_guard", edit_payload(main_repo, main_repo / "sub"),
            extra_env={**launcher_env, "CLAUDE_HOOKS_ALLOW_MAIN_EDIT": "1"},
        )
        check("branch_guard: main + launcher env + override -> allow", code == 0)

        code, _out, err = run(
            "branch_before_edit_guard", edit_payload(main_repo, main_repo / "sub", tool="Write"), extra_env=launcher_env
        )
        check("branch_guard: Write tool covered same as Edit -> block", code == 2, err)

        code, _out, err = run(
            "branch_before_edit_guard", edit_payload(main_repo, main_repo / "sub", tool="MultiEdit"), extra_env=launcher_env
        )
        check("branch_guard: MultiEdit tool covered same as Edit -> block", code == 2, err)

        code, _out, _err = run(
            "branch_before_edit_guard", edit_payload(main_repo, main_repo / "sub", tool="Bash"), extra_env=launcher_env
        )
        check("branch_guard: Bash tool_name -> allow (only guards Edit/Write/MultiEdit)", code == 0)

        # ---- take-1's actual bug: a worktree worker judged by the primary's branch ----
        worktree = main_repo.parent / f"{main_repo.name}-wt-1"
        subprocess.run(
            ["git", "worktree", "add", "-q", "-b", "feat/464-x", str(worktree)],
            cwd=main_repo, check=True, creationflags=_lib.NO_WINDOW,
        )
        try:
            # cwd is the PRIMARY repo (still on main) -- the exact shape that
            # broke take 1. file_path targets the worktree, on its own branch.
            code, _out, _err = run(
                "branch_before_edit_guard", edit_payload(main_repo, worktree), extra_env=launcher_env
            )
            check("branch_guard: worktree target on feature branch, cwd=primary(main) -> allow", code == 0)
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "-f", str(worktree)],
                cwd=main_repo, check=False, creationflags=_lib.NO_WINDOW,
            )

        # ---- take-2's own bug (fleet-config#489): a gitignored target *inside*
        # the repo, on the default branch. Both live repros are covered: a
        # single-file rule (life-os's `.active-skill`) and a directory rule
        # (fleet-config's `hooks/state/`, reached by the chief through a
        # junction). Neither file exists on disk -- `check-ignore` matches the
        # pathname, which is what makes a creating `Write` resolve correctly.
        (main_repo / ".gitignore").write_text(".active-skill\nstate/\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=main_repo, check=True, creationflags=_lib.NO_WINDOW)
        subprocess.run(
            ["git", "commit", "-q", "-m", "ignore rules"],
            cwd=main_repo, check=True, creationflags=_lib.NO_WINDOW,
        )
        (main_repo / "state").mkdir(exist_ok=True)

        def payload_for(target: Path) -> Dict[str, Any]:
            return {"tool_name": "Write", "cwd": str(main_repo), "tool_input": {"file_path": str(target)}}

        code, _out, _err = run(
            "branch_before_edit_guard", payload_for(main_repo / ".active-skill"), extra_env=launcher_env
        )
        check("branch_guard: gitignored file target on main + launcher env -> allow", code == 0)

        code, _out, _err = run(
            "branch_before_edit_guard", payload_for(main_repo / "state" / "chief-handover.md"),
            extra_env=launcher_env,
        )
        check("branch_guard: target under a gitignored directory rule -> allow", code == 0)

        # The exemption is gitignored-only: an untracked, non-ignored new file
        # in the same repo can still be committed to main, so it must block.
        code, _out, err = run(
            "branch_before_edit_guard", payload_for(main_repo / "state.py"), extra_env=launcher_env
        )
        check("branch_guard: untracked but NOT ignored target on main -> still block", code == 2, err)

        # ---- the junction shape (fleet-config#489's second live repro) ----
        # `~/.claude/hooks/` is a junction into this repo, so the chief's write
        # to its gitignored handover file arrives spelled under the junction.
        # git follows a junction for `-C` but matches the *pathname argument*
        # lexically against the worktree root, so the unresolved spelling exits
        # 128 ("is outside repository at ...") -- the fail-closed path. Only
        # the guard's `target.resolve()` keeps this case allowed.
        if sys.platform == "win32":
            link = main_repo.parent / f"{main_repo.name}-junction"
            mk = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(main_repo)],
                capture_output=True, text=True, creationflags=_lib.NO_WINDOW,
            )
            check("branch_guard: junction fixture created", mk.returncode == 0, mk.stdout + mk.stderr)
            if mk.returncode == 0:
                try:
                    code, _out, _err = run(
                        "branch_before_edit_guard",
                        payload_for(link / "state" / "chief-handover.md"), extra_env=launcher_env,
                    )
                    check("branch_guard: gitignored target via a junction path -> allow", code == 0)

                    code, _out, err = run(
                        "branch_before_edit_guard",
                        payload_for(link / "tracked.py"), extra_env=launcher_env,
                    )
                    check("branch_guard: non-ignored target via a junction path -> still block", code == 2, err)
                finally:
                    subprocess.run(
                        ["cmd", "/c", "rmdir", str(link)],
                        capture_output=True, creationflags=_lib.NO_WINDOW,
                    )

        # ---- take-1's other bug: a write target entirely outside any repo ----
        non_repo = Path(tempfile.mkdtemp(prefix="branch_guard_norepo_"))
        try:
            # cwd is a repo on main (e.g. chief's cwd); file_path targets a
            # plain non-git directory (e.g. E:\tmp\chief).
            code, _out, _err = run(
                "branch_before_edit_guard", edit_payload(main_repo, non_repo), extra_env=launcher_env
            )
            check("branch_guard: non-git target dir, cwd=repo(main) -> allow (fail open)", code == 0)
        finally:
            shutil.rmtree(non_repo, ignore_errors=True)
    finally:
        shutil.rmtree(main_repo, ignore_errors=True)

    master_repo = git_repo("master")
    try:
        code, _out, err = run(
            "branch_before_edit_guard", edit_payload(master_repo, master_repo / "sub"), extra_env=launcher_env
        )
        check("branch_guard: master (no origin configured) + launcher env -> block", code == 2, err)
    finally:
        shutil.rmtree(master_repo, ignore_errors=True)

    feature_repo = git_repo("feat/464-x")
    try:
        code, _out, _err = run(
            "branch_before_edit_guard", edit_payload(feature_repo, feature_repo / "sub"), extra_env=launcher_env
        )
        check("branch_guard: feature branch + launcher env -> allow", code == 0)
    finally:
        shutil.rmtree(feature_repo, ignore_errors=True)

    return check.failures, check.total




def _learning_log_unit_checks() -> Tuple[int, int]:
    """The pure window / section / bucketing / stats / ledger logic of
    learning-log/gather.py.

    No gh, no sub-agents — exercises last-run-at parsing, window resolution,
    section slicing, work-type bucketing, the exact stats computation + table
    render, and the archive-growing ledger body the unattended weekly run
    depends on."""
    import datetime as dt

    sys.path.insert(0, str(REPO / ".claude" / "skills" / "learning-log"))
    import gather as ll  # noqa: E402

    check = _Checker()

    # ---- last-run-at parsing ----
    check("learning_log: parse_last_run reads the stamp",
          ll.parse_last_run("<!-- learning-log-state -->\nlast-run-at: 2026-06-12\n") == "2026-06-12")
    check("learning_log: parse_last_run absent -> None",
          ll.parse_last_run("no stamp here") is None)

    # ---- window resolution: (since, source) — arg > ledger > trailing 7d ----
    today = dt.date(2026, 6, 15)
    check("learning_log: resolve_since explicit arg wins",
          ll.resolve_since("2026-05-01", "last-run-at: 2026-06-12", today) == ("2026-05-01", "arg"))
    check("learning_log: resolve_since falls to ledger last-run-at",
          ll.resolve_since(None, "last-run-at: 2026-06-12", today) == ("2026-06-12", "ledger"))
    check("learning_log: resolve_since first run -> trailing 7d",
          ll.resolve_since(None, "", today) == ("2026-06-08", "default"))

    # ---- section slicing ----
    text = "## TL;DR\n- a\n- b\n\n## Horizon → next week\n- [ ] x\n- [ ] y\n"
    check("learning_log: slice_section bounded by next H2",
          ll.slice_section(text, "## TL;DR") == "- a\n- b")
    check("learning_log: slice_section missing header -> ''",
          ll.slice_section(text, "## Nope") == "")

    # ---- discovery bullets get dated, non-bullets dropped ----
    bullets = ll.dated_discovery_bullets("- learned X (repo#1)\n- learned Y (repo#2)\nnoise", "2026-06-15")
    check("learning_log: dated_discovery_bullets dates + drops non-bullets",
          bullets == ["- 2026-06-15: learned X (repo#1)", "- 2026-06-15: learned Y (repo#2)"])

    # ---- work-type bucketing: PR title prefix, issue label ----
    check("learning_log: pr_bucket maps feat -> Features, fix -> Bug fixes, unknown -> Other",
          ll.pr_bucket("feat(api)!: x") == "Features & enhancements"
          and ll.pr_bucket("fix: y") == "Bug fixes"
          and ll.pr_bucket("random title") == "Other")
    check("learning_log: issue_bucket maps by label, none -> Other",
          ll.issue_bucket(["bug"]) == "Bug fixes"
          and ll.issue_bucket(["enhancement"]) == "Features & enhancements"
          and ll.issue_bucket([]) == "Other")

    # ---- exact stats: per-repo + per-bucket + grand total ----
    prs = [
        {"repo": "a", "bucket": "Bug fixes", "additions": 10, "deletions": 2},
        {"repo": "a", "bucket": "Features & enhancements", "additions": 5, "deletions": 0},
        {"repo": "b", "bucket": "Bug fixes", "additions": 3, "deletions": 1},
    ]
    issues = [{"repo": "a", "bucket": "Bug fixes"}, {"repo": "b", "bucket": "Other"}]
    stats = ll.compute_stats(prs, issues)
    check("learning_log: compute_stats grand totals (PRs/issues/LOC)",
          stats["total"] == {"prs": 3, "issues": 2, "add": 18, "del": 3})
    check("learning_log: compute_stats per-repo + per-bucket counts",
          stats["repos"]["a"]["prs"] == 2 and stats["repos"]["a"]["issues"] == 1
          and stats["repos"]["a"]["add"] == 15
          and stats["buckets"]["Bug fixes"]["prs"] == 2)
    table = ll.render_stats(stats, "2026-05-01", "2026-06-15")
    check("learning_log: render_stats has TOTAL row, a repo row, and a bucket row",
          "**TOTAL**" in table and "| a |" in table and "Bug fixes" in table)

    # ---- ledger body: new stamp + horizon, new discoveries prepended, old preserved ----
    prior = ("<!-- learning-log-state -->\nlast-run-at: 2026-06-08\n\n"
             "## Horizon → next week (set 2026-06-08)\n- [ ] old item\n\n"
             "## Decision / discovery archive\n- 2026-06-08: prior learning (repo#9)\n")
    body = ll.build_ledger_body(prior, "2026-06-15",
                                "- [ ] new horizon a\n- [ ] new horizon b",
                                "- fresh learning (repo#3)")
    check("learning_log: build_ledger_body stamps new last-run-at",
          "last-run-at: 2026-06-15" in body)
    check("learning_log: build_ledger_body carries the next horizon",
          "- [ ] new horizon a" in body and "## Horizon → next week (set 2026-06-15)" in body)
    check("learning_log: build_ledger_body prepends new discovery, preserves prior archive",
          "- 2026-06-15: fresh learning (repo#3)" in body
          and "- 2026-06-08: prior learning (repo#9)" in body
          and body.index("2026-06-15: fresh") < body.index("2026-06-08: prior learning"))

    return check.failures, check.total


def _conversation_capture_unit_checks() -> Tuple[int, int]:
    """The per-session dedup logic: stable token, filename shape, and the
    supersede-prior sweep that collapses a session's many Stop captures to one."""
    sys.path.insert(0, str(HOOKS))
    import conversation_capture as cc  # noqa: E402

    check = _Checker()

    check("session_token: last 8 alnum of a uuid-ish id",
          cc.session_token("01HNYE6TF-AbCd-1234") == "abcd1234")
    check("session_token: no id -> empty (dedup skipped)",
          cc.session_token("") == "" and cc.session_token(None) == "")
    check("capture_filename: session token only (degenerate content)",
          cc.capture_filename("2026-06-02-2020", "day-today", "abcd1234", "")
          == "2026-06-02-2020-day-today-abcd1234.md")
    check("capture_filename: both tokens -> session then signature",
          cc.capture_filename("2026-06-02-2020", "day-today", "abcd1234", "cafe9999")
          == "2026-06-02-2020-day-today-abcd1234-cafe9999.md")
    check("capture_filename: no tokens -> plain timestamped name",
          cc.capture_filename("2026-06-02-2020", "day-today", "", "")
          == "2026-06-02-2020-day-today.md")

    # content_signature is the resume-stable identity: it keys off the first real
    # user turn (copied forward verbatim on --resume), not the session id. So two
    # transcripts sharing that opening turn — but with different later turns and a
    # different session_id — hash identically; a preamble-only turn yields "".
    preamble = ("user", "Base directory for this skill: E:/automation/life-os/x")
    turn1 = ("user", "I want to record today's licenses and GPS for the ferry trip")
    orig = [preamble, turn1, ("assistant", "ok")]
    resumed = [preamble, turn1, ("assistant", "ok"),
               ("user", "and add the return ferry time"), ("assistant", "done")]
    check("content_signature: stable across resume (same first turn), non-empty",
          cc.content_signature(orig) == cc.content_signature(resumed) != "")
    check("content_signature: preamble-only turn -> empty (falls back to session token)",
          cc.content_signature([preamble]) == "")

    # conversation_slug keys off the WHOLE conversation's salient words, not the
    # opener — issue #84. A vague opening line ("tell me about your day") must not
    # decide the slug when a topic word recurs throughout the exchange.
    convo = [
        preamble,
        ("user", "Let me tell you about my day, I want to share what happened"),
        ("assistant", "Sure — how was the ferry crossing?"),
        ("user", "The ferry crossing was rough and the licenses paperwork slipped"),
        ("assistant", "Did you sort the ferry licenses after the crossing?"),
        ("user", "Yes, renewed the licenses once the ferry crossing ended"),
    ]
    slug = cc.conversation_slug(convo)
    check("conversation_slug: topic words beat the vague opener",
          "ferry" in slug and "licenses" in slug and "share" not in slug)
    check("conversation_slug: frequency ordering, ties by first appearance",
          slug == "ferry-crossing-licenses")
    check("conversation_slug: no significant words -> first-turn fallback",
          cc.conversation_slug([preamble]) == "session")
    check("conversation_slug: command tags / preamble stripped before counting",
          cc.conversation_slug([
              preamble,
              ("user", "<command-name>/journal-daily</command-name> logbook logbook entries"),
          ]) == "logbook-entries")

    # supersede_prior removes this session's earlier captures, leaves others.
    tmp = Path(tempfile.mkdtemp(prefix="cc_dedup_"))
    try:
        (tmp / "2026-06-02-2016-session-abcd1234.md").write_text("early", encoding="utf-8")
        (tmp / "2026-06-02-2018-other-abcd1234.md").write_text("mid", encoding="utf-8")
        (tmp / "2026-06-02-2020-real-deadbeef.md").write_text("other session", encoding="utf-8")
        cc.supersede_prior(tmp, "abcd1234", "")
        remaining = sorted(p.name for p in tmp.iterdir())
        check("supersede_prior: drops same-session files, keeps other sessions",
              remaining == ["2026-06-02-2020-real-deadbeef.md"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # supersede_prior on resume: the predecessor carries the SAME content
    # signature but a DIFFERENT (now-rewritten) session token, so only the
    # signature match collapses it. An unrelated conversation is left untouched.
    tmp = Path(tempfile.mkdtemp(prefix="cc_resume_"))
    try:
        (tmp / "2026-06-05-1606-licenses-and-gps-aaaa1111-cafe9999.md").write_text("v1", encoding="utf-8")
        (tmp / "2026-06-08-2105-other-topic-bbbb2222-dead8888.md").write_text("unrelated", encoding="utf-8")
        # resumed capture: new session token, same content signature.
        cc.supersede_prior(tmp, "eeee5555", "cafe9999")
        remaining = sorted(p.name for p in tmp.iterdir())
        check("supersede_prior: resume (new session id, same signature) drops predecessor",
              remaining == ["2026-06-08-2105-other-topic-bbbb2222-dead8888.md"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return check.failures, check.total


def _conversation_index_unit_checks() -> Tuple[int, int]:
    """Config-driven capture routing + the indexer's digest/upsert/decay logic.

    Hermetic: the hub is stubbed, so no network is touched. Covers the opt-in
    gate (a non-registered project captures nothing), routing resolution, and
    the index round-trip including the preserved decay zone."""
    sys.path.insert(0, str(HOOKS))
    import conversation_capture as cc  # noqa: E402
    import conversation_index as ci  # noqa: E402
    import hub_client  # noqa: E402

    check = _Checker()

    # ---- config resolution / opt-in gate ----
    lo = cc.resolve_capture_config({"cwd": "E:/automation/life-os"})
    check("capture_config: life-os -> skills routing",
          lo is not None and lo.routing == "skills" and lo.active_marker == ".active-skill")
    check("capture_config: non-opted project -> None",
          cc.resolve_capture_config({"cwd": "E:/automation/app-launcher"}) is None)

    # ---- conversations_dirs: flat -> one dir labelled by project ----
    flat = cc.CaptureConfig(root=Path(tempfile.gettempdir()) / "proj", routing="flat",
                            conversations_dir="conversations", skills_dir=".claude/skills",
                            active_marker=".active-skill")
    dirs = ci.conversations_dirs(flat)
    check("conversations_dirs: flat -> single dir labelled by project",
          len(dirs) == 1 and dirs[0][1] == "proj")

    # ---- index_dir: hermetic digest/upsert + decay-zone preservation ----
    saved = hub_client.complete
    ci.hub_client.complete = lambda *a, **k: "Topic: t\nDecisions: none\nOpen loops: none"
    tmp = Path(tempfile.mkdtemp(prefix="idx_unit_"))
    try:
        cap = tmp / "2026-06-10-1200-foo-aaaa1111.md"
        cap.write_text("d\n\n**You**: x\n\n**Claude**: y\n", encoding="utf-8")
        os.utime(cap, (time.time() - 600, time.time() - 600))  # settled
        n = ci.index_dir(tmp, "t")
        idx = (tmp / "index.md").read_text(encoding="utf-8")
        check("index_dir: writes one <!-- idx --> entry",
              n == 1 and "<!-- idx" in idx and "**Topic:**" in idx)
        check("index_dir: idempotent re-run -> 0", ci.index_dir(tmp, "t") == 0)
        with open(tmp / "index.md", "a", encoding="utf-8") as fh:
            fh.write("\n" + ci.DECAY_MARKER + "\n### 2026-04 · period\n- squashed\n")
        cap2 = tmp / "2026-06-11-1300-bar-bbbb2222.md"
        cap2.write_text("d\n\n**You**: x\n\n**Claude**: y\n", encoding="utf-8")
        os.utime(cap2, (time.time() - 600, time.time() - 600))
        ci.index_dir(tmp, "t")
        check("index_dir: decay zone preserved across re-index",
              "squashed" in (tmp / "index.md").read_text(encoding="utf-8"))
    finally:
        ci.hub_client.complete = saved
        shutil.rmtree(tmp, ignore_errors=True)

    return check.failures, check.total


def _restart_webapp_unit_checks() -> Tuple[int, int]:
    """The tray-owned restart strategy: projects.toml carries a `restart_cmd`
    for the three tray apps, and the recovery hint stays actionable and
    :8446-safe. Both are pure (no tray needed), so they're gate-testable."""
    sys.path.insert(0, str(HOOKS))
    import restart_and_verify_webapp as rw  # noqa: E402
    import _lib  # noqa: E402

    check = _Checker()

    reg = _lib.load_registry()
    by_name = {p.name: p for p in reg.projects}

    check("restart_cmd: app-launcher respawns through WebappManager",
          "WebappManager" in (by_name["app-launcher"].restart_cmd or ""))
    check("restart_cmd: voice-transcriber now has webapp_port 8443 + respawn cmd",
          by_name["voice-transcriber"].webapp_port == 8443
          and "WebappManager" in (by_name["voice-transcriber"].restart_cmd or ""))
    check("restart_cmd: local-llm-hub keeps the tray_cmd path (no restart_cmd)",
          by_name["local-llm-hub"].restart_cmd is None)

    hint = rw.recovery_hint(
        "app-launcher", 8445, Path("E:/automation/app-launcher"),
        by_name["app-launcher"].restart_cmd, "tray.bat",
    )
    check("recovery_hint: leads with the manager respawn + flags it :8446-safe",
          "WebappManager" in hint and "spares :8446" in hint)
    check("recovery_hint: tray --restart present but flagged a :8446-destroying last resort",
          "tray.bat --restart" in hint and "destroys :8446" in hint)

    tray_only = rw.recovery_hint("local-llm-hub", 8000, Path("E:/automation/local-llm-hub"), None, "tray.bat")
    check("recovery_hint: no restart_cmd -> option 1 is the tray, no respawn line",
          "WebappManager" not in tray_only and "1) Full clean restart" in tray_only)

    captured = {}
    saved_popen = rw.subprocess.Popen
    rw.subprocess.Popen = lambda *a, **kw: captured.update(kw)
    try:
        rw._start_tray("tray.bat", Path("E:/automation/app-launcher"))
    finally:
        rw.subprocess.Popen = saved_popen
    flags = captured.get("creationflags", 0)
    check(
        "_start_tray: creationflags carries both CREATE_NEW_PROCESS_GROUP and "
        "CREATE_NO_WINDOW (fleet-config#409)",
        bool(flags & getattr(rw.subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        and bool(flags & getattr(rw.subprocess, "CREATE_NO_WINDOW", 0)),
    )

    return check.failures, check.total


def _notify_complete_unit_checks() -> Tuple[int, int]:
    """Canonical per-kind message assembly + the shared slack-target resolver."""
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
    # not UTF-8 safe on Windows: a literal `·` reached Slack as `??`
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

    # The shared resolver: unknown cwd -> [global] channel/user + 'claude' name.
    ch, usr, nm = _lib.resolve_slack_target(Path("E:/does/not/match/anything"))
    check("resolve_slack_target: global fallback + claude name",
          ch == "C0B76GBA0LS" and usr == "U0B71PQEL6S" and nm == "claude")

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


def _work_summary_unit_checks() -> Tuple[int, int]:
    """The work-summary roll-up block + per-file table (hooks/work_summary.py).

    Pure / no gh: feed the formatters a synthetic ``gh pr view`` payload (an
    added/modified/renamed/deleted mix) and assert the exact rendered roll-up and
    table, the empty-bucket drop, and the no-files degrade-to-empty path that
    keeps a finish ping block-less instead of crashing."""
    sys.path.insert(0, str(HOOKS))
    import work_summary as ws  # noqa: E402

    check = _Checker()

    M = ws.MINUS  # U+2212, as the formatters emit

    # changeType -> bucket, source-agnostic (GraphQL DELETED + REST removed both deleted).
    check("work_summary: bucket_for maps add/copy->new, del/removed->deleted, else->changed",
          ws.bucket_for("ADDED") == "new" and ws.bucket_for("COPIED") == "new"
          and ws.bucket_for("DELETED") == "deleted" and ws.bucket_for("removed") == "deleted"
          and ws.bucket_for("MODIFIED") == "changed" and ws.bucket_for("RENAMED") == "changed"
          and ws.bucket_for(None) == "changed")

    # Consistent synthetic PR: 2 new (+210), 2 changed (+98 -40), 1 deleted (-7).
    data = {
        "additions": 308, "deletions": 47, "changedFiles": 5,
        "files": [
            {"path": "a_new.py", "additions": 110, "deletions": 0, "changeType": "ADDED"},
            {"path": "b_new.py", "additions": 100, "deletions": 0, "changeType": "ADDED"},
            {"path": "c_mod.py", "additions": 50, "deletions": 30, "changeType": "MODIFIED"},
            {"path": "d_ren.py", "additions": 48, "deletions": 10, "changeType": "RENAMED"},
            {"path": "e_del.py", "additions": 0, "deletions": 7, "changeType": "DELETED"},
        ],
    }
    check("work_summary: format_block renders the exact roll-up",
          ws.format_block(data) ==
          f"📊 +308 {M}47 · 5 files\n"
          f"   🆕 2 new (+210)  ✏️ 2 changed (+98 {M}40)  🗑️ 1 deleted ({M}7)")

    check("work_summary: format_table is churn-sorted with status icons",
          ws.format_table(data) ==
          "| | File | + | − |\n"
          "|---|---|--:|--:|\n"
          f"| 🆕 | `a_new.py` | +110 | {M}0 |\n"
          f"| 🆕 | `b_new.py` | +100 | {M}0 |\n"
          f"| ✏️ | `c_mod.py` | +50 | {M}30 |\n"
          f"| ✏️ | `d_ren.py` | +48 | {M}10 |\n"
          f"| 🗑️ | `e_del.py` | +0 | {M}7 |")

    # Single modified file: empty new/deleted buckets dropped, singular "1 file".
    one = {"additions": 8, "deletions": 1, "changedFiles": 1,
           "files": [{"path": "x.py", "additions": 8, "deletions": 1, "changeType": "MODIFIED"}]}
    check("work_summary: empty buckets dropped + singular 'file'",
          ws.format_block(one) == f"📊 +8 {M}1 · 1 file\n   ✏️ 1 changed (+8 {M}1)")

    # Degrade path: no files (or a {} from a failed gh call) → "" both renderings.
    check("work_summary: no files → empty block and empty table",
          ws.format_block({}) == "" and ws.format_table({}) == ""
          and ws.format_block({"files": []}) == "")

    return check.failures, check.total


def _pi_usage_stats_unit_checks() -> Tuple[int, int]:
    """Pi JSONL usage collector: model/provider + tokens, no prompt text."""
    sys.path.insert(0, str(HOOKS))
    import pi_usage_stats as pi_stats  # noqa: E402

    check = _Checker()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "sessions"
        sess_dir = root / "--E--automation-fleet-config--"
        sess_dir.mkdir(parents=True)
        path = sess_dir / "2026-06-24T09-09-16-155Z_abc.jsonl"
        path.write_text("\n".join([
            json.dumps({"type": "session", "id": "abc", "timestamp": "2026-06-24T09:09:16.155Z", "cwd": "E:\\automation\\fleet-config"}),
            json.dumps({"type": "model_change", "timestamp": "2026-06-24T09:09:17.000Z", "provider": "openai-codex", "modelId": "gpt-5.5"}),
            json.dumps({"type": "message", "timestamp": "2026-06-24T09:10:00.000Z", "message": {"role": "assistant", "provider": "openai-codex", "model": "gpt-5.5", "content": [{"type": "toolCall"}], "usage": {"input": 10, "output": 2, "cacheRead": 3, "cacheWrite": 4, "totalTokens": 19, "cost": {"total": 0.12}}}}),
        ]), encoding="utf-8")

        sessions = pi_stats.collect(root)
        summary = pi_stats.aggregate(sessions)
        row = sessions[0]
        check("pi_usage_stats: parses cwd/project/provider/model",
              len(sessions) == 1 and row.project == "fleet-config"
              and row.provider == "openai-codex" and row.model == "gpt-5.5")
        check("pi_usage_stats: aggregates token totals and tool calls",
              summary["usage"]["total"] == 19 and summary["usage"]["input"] == 10
              and row.tool_calls == 1 and summary["by_model"]["openai-codex/gpt-5.5"]["total"] == 19)
        check("pi_usage_stats: JSON rows omit prompt text",
              "content" not in row.as_dict() and "message" not in row.as_dict())

    return check.failures, check.total


def _slack_routing_unit_checks() -> Tuple[int, int]:
    """Category → channel routing (issue #139): the resolver picks the dedicated
    channel per category, falls back to the single channel when a category is
    unset, and the kind → category map sends action-needed pings to attention."""
    sys.path.insert(0, str(HOOKS))
    import _lib  # noqa: E402
    import notify_complete  # noqa: E402

    check = _Checker()

    cwd = Path("E:/does/not/match/anything")  # global-only resolution

    # ---- category routes to its dedicated [global] channel ----
    ch, _u, _n = _lib.resolve_slack_target(cwd, category="attention")
    check("route: attention -> #attention channel", ch == "C0BAGNEQ163")
    ch, _u, _n = _lib.resolve_slack_target(cwd, category="log")
    check("route: log -> #log channel", ch == "C0BARRUBG03")
    # No category -> the plain channel (back-compat: existing callers unchanged).
    ch, _u, _n = _lib.resolve_slack_target(cwd)
    check("route: no category -> slack_notify_channel", ch == "C0B76GBA0LS")

    # ---- graceful degradation: category channels unset -> single-channel fallback ----
    single = _lib.Registry(
        projects=[],
        globals=_lib.GlobalConfig(never_kill_ports=(), slack_notify_channel="C_ONLY"),
    )
    ch, _u, _n = _lib.resolve_slack_target(cwd, registry=single, category="attention")
    check("route: unset category channel -> falls back to single channel", ch == "C_ONLY")

    # ---- per-project override of a category channel wins over [global] ----
    proj = _lib.ProjectConfig(
        name="x", cwd_prefix=Path("E:/automation/x"), webapp_port=None,
        gate_trigger_globs=(), gate_cmd=None, tray_cmd=None, restart_cmd=None,
        api_version_path=None, extra={"slack_channel_log": "C_PROJ_LOG"},
    )
    reg = _lib.Registry(
        projects=[proj],
        globals=_lib.GlobalConfig(never_kill_ports=(), slack_notify_channel="C_G",
                                  slack_channel_log="C_GLOBAL_LOG"),
    )
    ch, _u, _n = _lib.resolve_slack_target(Path("E:/automation/x"), registry=reg, category="log")
    check("route: per-project category channel overrides [global]", ch == "C_PROJ_LOG")

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


