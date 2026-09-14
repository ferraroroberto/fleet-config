"""Acceptance checks for the non-Claude harness wiring (fleet-config#680).

Cross-agent parity (`docs/cross-agent-parity.md`) asserted as mechanism rather
than prose: the Codex and Pi `session_state` adapters really do land a row in
the same `sessions-state.json` Claude writes, and Codex's own hook wiring really
does invoke the Python modules directly with a bounded timeout instead of
routing through `run-hook.ps1` (which hung every PreToolUse until Codex's
600-second default). Both payload transports — Claude's `run-hook.ps1` shim and
the direct-Python invocation Codex and Pi use — must hand a hook the payload's
exact codepoints (fleet-config#912).

Split out of the former 2681-line `unit_checks.py`; see `checks_context_filter`
for why.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Tuple

from acceptance.shared import (
    HOOKS,
    NO_SETTINGS_JSON,
    PYTHON,
    REPO,
    _Checker,
    hook_env,
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

    # These smoke runs shell out directly rather than through shared.run(), so
    # they take the same isolated environment from its one builder — a second
    # hand-rolled copy of the env is how #813's hole outlived the fix to its
    # sibling.
    env = hook_env()
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


_POWERSHELL = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")

# Every non-ASCII class the shim mangled: BMP punctuation, Latin-1, an astral
# emoji (a surrogate pair in .NET) and CJK.
_UTF8_SAMPLE = "a\u2014b \u00b7 \U0001F600 \u4e2d\u6587"

# Echoes the decoded command back as `ascii()`, so the assertion compares
# codepoints and never depends on how stdout itself is decoded on the way out.
_UTF8_PROBE_HOOK = '''
import json, sys
text = sys.stdin.buffer.read().decode("utf-8", errors="strict")
print(ascii(json.loads(text)["tool_input"]["command"]))
'''

# Refuses with a reason carrying every sample class, through the real
# `_lib.block()` (fleet-config#924).
_BLOCK_PROBE_REASON = f"Blocked: probe {_UTF8_SAMPLE}"
_BLOCK_PROBE_HOOK = f'''
import sys
sys.path.insert(0, {str(HOOKS)!r})
import _lib
_lib.read_stdin_json()
_lib.block({_BLOCK_PROBE_REASON!r})
'''


def _shim_console_code_pages() -> list[int]:
    """The console code pages every run-hook.ps1 case runs under (fleet-config#920).

    Windows PowerShell 5.1 writes a native child's stdin with
    `Console.InputEncoding`, which gains a UTF-8 BOM when the console is on code
    page 65001 -- the page a pwsh 7 parent hands down, while Git Bash hands
    down the OEM page. Pinning both makes the result independent of whoever
    launched the gate.
    """
    import ctypes

    return list(dict.fromkeys([ctypes.windll.kernel32.GetOEMCP(), 65001]))


def _shim(shim: Path, hook: str, payload: Dict[str, Any], env: Dict[str, str],
          code_page: int) -> subprocess.CompletedProcess:
    """Invoke `run-hook.ps1` exactly as settings.json does, fed UTF-8 bytes.

    CREATE_NO_WINDOW gives the spawn a private console, so `chcp` pins the code
    page the shim sees without touching the console of the process running the
    gate.
    """
    inner = (f'"{_POWERSHELL}" -NoProfile -NonInteractive -ExecutionPolicy Bypass '
             f'-File "{shim}" -Hook {hook}')
    return subprocess.run(
        f'cmd.exe /d /s /c "chcp {code_page} >nul & {inner}"',
        input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        capture_output=True,
        timeout=60,
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def _hook_transport_utf8_check() -> Tuple[int, int, int]:
    """Non-ASCII survives both hook transports (fleet-config#912).

    Returns `(failures, total, skipped)` -- run via `run_unit3`: without Windows
    PowerShell the shim cannot be driven at all, and that is a skip, never a pass.

    `run-hook.ps1` read stdin through `[Console]::In` (OEM code page) and piped
    it on with `$OutputEncoding` (us-ascii), so an em dash reached every hook
    as `???` -- and `context_filter_hook`'s rewrite ran the corrupted command.
    Behind the shim, `_lib.read_stdin_json()` decoded the pipe as cp1252.

    Under a code-page-65001 console the shim also prepended a UTF-8 BOM, which
    `json.loads` rejects, so hooks read `{}` and failed open -- which is why the
    shim cases run under both console code pages (fleet-config#920).

    On the way out, `_lib.block()` printed the refusal to a stderr pipe, which
    Python encodes with the ANSI code page unless `PYTHONUTF8` /
    `PYTHONIOENCODING` is set, so Claude Code read the em dash as U+FFFD
    (fleet-config#924). The refusal cases strip both variables.
    """
    check = _Checker()
    env = hook_env({"FLEET_CONTEXT_FILTER_MODE": "rewrite"})
    no_py_encoding_env = {k: v for k, v in env.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    guard_payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(REPO),
                     "tool_input": {"command": r"ls C:\Windows\System32\drivers\etc"}}
    payload = {"tool_name": "Bash", "cwd": str(REPO),
               "tool_input": {"command": f"python -c \"print('{_UTF8_SAMPLE}')\""}}
    command = payload["tool_input"]["command"]

    # Direct-Python transport (Codex, Pi): no PowerShell needed.
    res = subprocess.run(
        [PYTHON, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import _lib; "
                       "print(ascii(_lib.read_stdin_json()['tool_input']['command']))", str(HOOKS)],
        input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        capture_output=True, timeout=15, env=env,
    )
    check(
        "hook_transport: _lib.read_stdin_json decodes a UTF-8 pipe exactly",
        res.returncode == 0 and res.stdout.decode("ascii", "replace").strip() == ascii(command),
        res.stdout.decode("utf-8", "replace") + res.stderr.decode("utf-8", "replace"),
    )

    # A BOM-prefixed pipe used to fail `json.loads` and come back `{}`, so every
    # guard behind it failed open (fleet-config#920).
    res = subprocess.run(
        [PYTHON, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import _lib; "
                       "print(ascii(_lib.read_stdin_json().get('tool_input', {}).get('command')))", str(HOOKS)],
        input=b"\xef\xbb\xbf" + json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        capture_output=True, timeout=15, env=env,
    )
    check(
        "hook_transport: _lib.read_stdin_json parses a BOM-prefixed UTF-8 pipe",
        res.returncode == 0 and res.stdout.decode("ascii", "replace").strip() == ascii(command),
        res.stdout.decode("utf-8", "replace") + res.stderr.decode("utf-8", "replace"),
    )

    if not _POWERSHELL.exists():
        check.skipped += 8
        print("SKIP  hook_transport: run-hook.ps1 cases (Windows PowerShell not found)")
        return check.failures, check.total, check.skipped

    for code_page in _shim_console_code_pages():
        with tempfile.TemporaryDirectory(prefix="fleet-config-912-") as tmp:
            shim_copy = Path(tmp) / "run-hook.ps1"
            shutil.copyfile(HOOKS / "run-hook.ps1", shim_copy)
            (Path(tmp) / "utf8_probe.py").write_text(_UTF8_PROBE_HOOK, encoding="utf-8")
            (Path(tmp) / "block_probe.py").write_text(_BLOCK_PROBE_HOOK, encoding="utf-8")
            res = _shim(shim_copy, "utf8_probe", payload, env, code_page)
            refusal = _shim(shim_copy, "block_probe", guard_payload, no_py_encoding_env, code_page)
        check(
            f"hook_transport: run-hook.ps1 hands the hook the payload's exact codepoints (console cp {code_page})",
            res.returncode == 0 and res.stdout.decode("ascii", "replace").strip() == ascii(command),
            res.stdout.decode("utf-8", "replace") + res.stderr.decode("utf-8", "replace"),
        )
        check(
            f"hook_transport: _lib.block() refusal via run-hook.ps1 is exact UTF-8 on stderr (console cp {code_page})",
            refusal.returncode == 2 and refusal.stderr == (_BLOCK_PROBE_REASON + "\r\n").encode("utf-8"),
            f"rc={refusal.returncode} stderr={refusal.stderr!r}",
        )

        # A real guard through the real shim still blocks, and its em dash arrives intact.
        refusal = _shim(HOOKS / "run-hook.ps1", "bash_windows_path_guard", guard_payload, no_py_encoding_env, code_page)
        try:
            reason = refusal.stderr.decode("utf-8")
        except UnicodeDecodeError:
            reason = ""
        check(
            f"hook_transport: bash_windows_path_guard refusal via run-hook.ps1 keeps its em dash (console cp {code_page})",
            refusal.returncode == 2 and " — Git Bash strips backslashes" in reason,
            f"rc={refusal.returncode} stderr={refusal.stderr!r}",
        )

        # End to end through the real shim and the real rewrite hook: the command
        # the wrapper will execute is base64 of what the hook decoded.
        res = _shim(HOOKS / "run-hook.ps1", "context_filter_hook", payload, env, code_page)
        executed = ""
        try:
            rewritten = json.loads(res.stdout)["hookSpecificOutput"]["updatedInput"]["command"]
            encoded = re.search(r"--encoded (\S+)", rewritten)
            executed = base64.b64decode(encoded.group(1)).decode("utf-8") if encoded else ""
        except (ValueError, KeyError, TypeError):
            pass
        check(
            f"hook_transport: context_filter_hook rewrite via run-hook.ps1 keeps the command intact (console cp {code_page})",
            res.returncode == 0 and executed == command,
            f"executed={executed!r}\n" + res.stdout.decode("utf-8", "replace") + res.stderr.decode("utf-8", "replace"),
        )
    return check.failures, check.total, check.skipped


def _copilot_hook_shells() -> list[Tuple[str, str]]:
    """Every PowerShell Copilot's `powershell` hook key may run under.

    Copilot 1.0.83 on this host ran it under PowerShell 7.6.6 (a live probe
    hook logged the host; fleet-config#913), but which shell it picks is not
    documented, so the command must hold in both.
    """
    shells = [("powershell 5.1", str(_POWERSHELL))] if _POWERSHELL.exists() else []
    pwsh = shutil.which("pwsh")
    if pwsh:
        shells.append(("pwsh", pwsh))
    return shells


def _copilot_statusline_utf8_check() -> Tuple[int, int, int]:
    """The other two PowerShell stdin readers keep non-ASCII exact (fleet-config#913).

    Returns `(failures, total, skipped)` -- run via `run_unit3`; a missing
    PowerShell is a skip, never a pass.

    - `copilot-hooks/fleet-context-filter.json` piped `[Console]::In.ReadToEnd()`
      into the hook: an em dash reached `context_filter_hook` as `???` (5.1) or
      cp850 mojibake (pwsh), and the rewrite ran that. It now invokes Python
      directly, which inherits the raw stdin bytes in either shell.
    - `statusline-command.ps1` decoded the same way. The basename still printed
      right (Write-Host re-encoded with the same code page), but `Test-Path`
      missed the mangled cwd, so the branch segment silently vanished.
    """
    check = _Checker()
    shells = _copilot_hook_shells()
    if not shells:
        check.skipped += 3
        print("SKIP  copilot/statusline utf8: no Windows PowerShell or pwsh found")
        return check.failures, check.total, check.skipped

    command = f"python -c \"print('{_UTF8_SAMPLE}')\""
    wiring = json.loads((REPO / "copilot-hooks" / "fleet-context-filter.json").read_text(encoding="utf-8"))
    hook_cmd = wiring["hooks"]["preToolUse"][0]["powershell"]
    hook_path = re.compile(r"'[^']*context_filter_hook\.py'")
    copilot_payload = {"sessionId": "cop-913", "timestamp": 1789340000000, "cwd": str(REPO),
                       # Raw codepoints, as Copilot sends them -- a default
                       # json.dumps would \u-escape them and the case would pass on anything.
                       "toolName": "powershell", "toolArgs": json.dumps({"command": command}, ensure_ascii=False)}

    with tempfile.TemporaryDirectory(prefix="fleet-config-913-") as tmp:
        probe = Path(tmp) / "utf8_probe.py"
        probe.write_text(_UTF8_PROBE_HOOK.replace('["tool_input"]["command"]', '["toolArgs"]'), encoding="utf-8")
        filter_dir = Path(tmp) / "filter"
        filter_dir.mkdir()
        env = hook_env({"FLEET_CONTEXT_FILTER_MODE": "rewrite", "FLEET_CONTEXT_FILTER_DIR": str(filter_dir)})
        stdin = json.dumps(copilot_payload, ensure_ascii=False).encode("utf-8")
        for name, shell in shells:
            # Copilot's own hook command, pointed at a probe that echoes codepoints.
            res = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command",
                                  hook_path.sub(f"'{probe.as_posix()}'", hook_cmd)],
                                 input=stdin, capture_output=True, timeout=60, env=env)
            check(
                f"copilot hook ({name}): the hook receives the payload's exact codepoints",
                bool(hook_path.search(hook_cmd)) and res.returncode == 0
                and res.stdout.decode("ascii", "replace").strip() == ascii(copilot_payload["toolArgs"]),
                res.stdout.decode("utf-8", "replace") + res.stderr.decode("utf-8", "replace"),
            )
            # End to end through the real rewrite hook: the command Copilot will
            # execute is base64 of what the hook decoded.
            res = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command",
                                  hook_path.sub(f"'{(HOOKS / 'context_filter_hook.py').as_posix()}'", hook_cmd)],
                                 input=stdin, capture_output=True, timeout=60, env=env)
            executed = ""
            try:
                rewritten = json.loads(json.loads(res.stdout)["modifiedArgs"])["command"]
                encoded = re.search(r"--encoded (\S+)", rewritten)
                executed = base64.b64decode(encoded.group(1)).decode("utf-8") if encoded else ""
            except (ValueError, KeyError, TypeError):
                pass
            check(
                f"copilot hook ({name}): context_filter_hook rewrite keeps the command intact",
                res.returncode == 0 and executed == command,
                f"executed={executed!r}\n" + res.stdout.decode("utf-8", "replace") + res.stderr.decode("utf-8", "replace"),
            )

        if not _POWERSHELL.exists():
            check.skipped += 1
            print("SKIP  statusline utf8: Windows PowerShell not found")
            return check.failures, check.total, check.skipped
        # settings.json runs the statusline under Windows PowerShell 5.1 with -File.
        cwd = Path(tmp) / "café—中"
        subprocess.run(["git", "init", "-q", "-b", "bré", str(cwd)], check=True, capture_output=True, timeout=30)
        status = {"workspace": {"current_dir": str(cwd)}, "model": {"display_name": "Opus 5"}}
        outputs = []
        for stdin in (json.dumps(status, ensure_ascii=False).encode("utf-8"), b"", b"{not json"):
            res = subprocess.run([str(_POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                  "-File", str(REPO / "statusline-command.ps1")],
                                 input=stdin, capture_output=True, timeout=60,
                                 env=hook_env({"CLAUDE_HOOKS_STATE_DIR": str(Path(tmp) / "state")}))
            outputs.append((res.returncode, res.stdout.decode("utf-8", "replace"), res.stderr.decode("utf-8", "replace")))
    check(
        "statusline: a non-ASCII cwd renders with its branch; empty/malformed stdin exit 0 silently",
        outputs[0][:2] == (0, f"opus | café—中 (bré)\n")
        and all(out == (0, "", "") for out in outputs[1:]),
        repr(outputs),
    )
    return check.failures, check.total, check.skipped
