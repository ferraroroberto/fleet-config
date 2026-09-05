"""Acceptance checks for the non-Claude harness wiring (fleet-config#680).

Cross-agent parity (`docs/cross-agent-parity.md`) asserted as mechanism rather
than prose: the Codex and Pi `session_state` adapters really do land a row in
the same `sessions-state.json` Claude writes, and Codex's own hook wiring really
does invoke the Python modules directly with a bounded timeout instead of
routing through `run-hook.ps1` (which hung every PreToolUse until Codex's
600-second default).

Split out of the former 2681-line `unit_checks.py`; see `checks_context_filter`
for why.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Tuple

from acceptance.shared import (
    NO_SETTINGS_JSON,
    REPO,
    _Checker,
    run,
)


_CODEX_POLICY_COVERAGE = (
    # policy, module, event, matcher, observed Codex status
    ("GitHub body quoting", "gh_body_file_guard", "PreToolUse", "Bash", "advises"),
    ("Dated docs filenames", "docs_dated_filename_guard", "PreToolUse",
     "Edit|Write|MultiEdit", "blocks"),
    ("Branch before edit", "branch_before_edit_guard", "PreToolUse",
     "Edit|Write|MultiEdit", "blocks"),
    ("Local hub routing", "hub_bypass_warn", "PostToolUse",
     "Edit|Write|MultiEdit", "advises"),
    ("Browser launch safety", "browser_stealth_lint", "PostToolUse",
     "Edit|Write|MultiEdit", "advises"),
    ("Chief question suppression", "block_askuserquestion_chief", None, None,
     "not applicable"),
)

# Every function below inserts its own sys.path entry (HOOKS or skills/_lib)
# right before its dynamic import -- matches the pre-split file's per-function
# style, so each check's dependency is visible at its own call site.


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
    ended_path = tmp / "sessions-ended.json"

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

        # Codex SessionEnd is terminal for observational events only. It removes
        # exactly its own id, repeated/unknown ends are harmless, and a late
        # Stop/PermissionRequest cannot recreate the row. A later explicit
        # prompt proves a genuine resume and may reopen the same native id.
        sibling_payloads = (
            ("session_state", {"hook_event_name": "UserPromptSubmit",
                               "session_id": "claude-sibling", "cwd": str(tmp)}),
            ("session_state_pi", {"event": "input", "session_id": "pi-sibling",
                                  "cwd": str(tmp)}),
            ("session_state", {"hookEventName": "user_prompt_submit",
                               "sessionId": "grok-sibling", "cwd": str(tmp)}),
        )
        for module, payload in sibling_payloads:
            run(module, payload, extra_env=env)
        code, _out, _err = run(
            "session_state_codex",
            {**codex_payload, "hook_event_name": "SessionEnd", "session_id": "codex-2",
             "reason": "other"},
            extra_env=env,
        )
        sibling_rows = rows()
        check("session_state_codex: SessionEnd removes only its matching same-cwd row",
              code == 0 and "codex-2" not in sibling_rows
              and {"codex-1", "claude-sibling", "pi-sibling", "grok-sibling"}
              <= set(sibling_rows)
              and sibling_rows["claude-sibling"].get("agent") == "claude"
              and sibling_rows["pi-sibling"].get("agent") == "pi"
              and sibling_rows["grok-sibling"].get("agent") == "grok")

        before_repeat = rows()
        run("session_state_codex",
            {**codex_payload, "hook_event_name": "SessionEnd", "session_id": "codex-2",
             "reason": "other"}, extra_env=env)
        run("session_state_codex",
            {**codex_payload, "hook_event_name": "SessionEnd", "session_id": "unknown-codex",
             "reason": "other"}, extra_env=env)
        check("session_state_codex: repeated and unknown SessionEnd leave live rows untouched",
              rows() == before_repeat)

        run("session_state_codex",
            {**codex_payload, "hook_event_name": "Stop", "session_id": "codex-2"},
            extra_env=env)
        run("session_state_codex",
            {**codex_payload, "hook_event_name": "PermissionRequest", "session_id": "codex-2"},
            extra_env=env)
        check("session_state_codex: late observational events do not resurrect a closed row",
              "codex-2" not in rows())

        code, _out, _err = run(
            "session_state_codex",
            {**codex_payload, "hook_event_name": "UserPromptSubmit", "session_id": "codex-2"},
            extra_env=launcher_env,
        )
        reopened = rows().get("codex-2") or {}
        check("session_state_codex: explicit prompt reopens a genuinely resumed native id",
              code == 0 and reopened.get("status") == "working"
              and reopened.get("launcher_session_id") == "launcher-codex")

        run("session_state_codex",
            {**codex_payload, "hook_event_name": "SessionEnd", "session_id": "codex-2",
             "reason": "other"}, extra_env=env)
        try:
            ended = json.loads(ended_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            ended = {}
        if "codex-2" in ended:
            ended["codex-2"] = "2020-01-01T00:00:00Z"
            ended_path.write_text(json.dumps(ended), encoding="utf-8")
            run("session_state_codex",
                {**codex_payload, "hook_event_name": "Stop", "session_id": "codex-2"},
                extra_env=launcher_env)
        check("session_state_codex: expired tombstone does not hide a resumable id forever",
              "codex-2" in ended
              and (rows().get("codex-2") or {}).get("status") == "needs-you")

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

        # ---- Pi: session_shutdown removes the row through the same shared path ----
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

    registrations = {
        (event, str(block.get("matcher", "")), match.group(1))
        for event, blocks in data.get("hooks", {}).items()
        for block in blocks
        for hook in block.get("hooks", [])
        if (match := re.search(r"/([A-Za-z0-9_]+)\.py$", str(hook.get("command", ""))))
    }
    expected = {
        (event, matcher, module)
        for _policy, module, event, matcher, status in _CODEX_POLICY_COVERAGE
        if status in {"blocks", "advises"}
    }
    missing = sorted(expected - registrations)
    check(
        "codex_hooks: explicit policy table has every applicable registration",
        not missing,
        "missing: " + repr(missing),
    )
    check(
        "codex_hooks: SessionEnd removes the matching Fleet Board row",
        ("SessionEnd", "other", "session_state_codex") in registrations,
    )
    unsupported_wired = sorted(
        module for _policy, module, _event, _matcher, status in _CODEX_POLICY_COVERAGE
        if status in {"not applicable", "unsupported", "not verified"}
        and any(registration[2] == module for registration in registrations)
    )
    check(
        "codex_hooks: unsupported/not-applicable policy surfaces stay explicit and unwired",
        not unsupported_wired,
        "unexpected registrations: " + repr(unsupported_wired),
    )

    env = {k: v for k, v in os.environ.items() if k != "TELEGRAM_BOT_TOKEN"}
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
