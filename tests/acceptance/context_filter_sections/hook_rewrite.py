"""Context-filter checks on `context_filter_hook`'s rewrite decisions: what is wrapped, what passes through, and the mode switch."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from acceptance.context_filter_sections.tables import DECISION_CASES, PASSTHROUGH_CASES
from acceptance.shared import HOOKS, REPO, _Checker, run


def rewrite_output_shape(check: _Checker) -> None:
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


def skill_helper_passthrough(check: _Checker) -> None:
    # Skill helpers are never wrapped; ordinary commands still are (rows: tables.PASSTHROUGH_CASES).
    for label, command, expect_passthrough, issue in PASSTHROUGH_CASES:
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


def rewrite_decisions(check: _Checker) -> None:
    # The shapes agents actually write are wrapped; unsafe ones still not (rows: tables.DECISION_CASES).
    sys.path.insert(0, str(HOOKS))
    import context_filter as _cf1268  # noqa: E402

    for label, command, tool, expect_wrap in DECISION_CASES:
        decision = _cf1268.rewrite_decision(command, tool=tool)
        check(
            f"context_filter: {label} -> {'wrapped' if expect_wrap else 'not wrapped'} (fleet-config#1268)",
            decision.should_wrap == expect_wrap,
            f"{command!r} -> {decision.reason}",
        )
    split = _cf1268.rewrite_decision('cd "E:/a b" && cd sub && git status | head', tool="Bash")
    check(
        "context_filter: leading cd segments split off verbatim as the prefix (fleet-config#1268)",
        split.should_wrap
        and split.prefix == 'cd "E:/a b" && cd sub && '
        and split.body == "git status | head",
        f"prefix={split.prefix!r} body={split.body!r} reason={split.reason}",
    )

    # Compound output is the last stage's, so it is never read through the
    # first command's summariser: the status filter would drop every log line.
    status_then_log = "\n".join(
        [f" M hooks/file_{i}.py" for i in range(60)] + [f"abc{i:04d} commit subject {i}" for i in range(60)]
    )
    compound = _cf1268.compress_output("cd /e/x && git status --short && git log --oneline -60", status_then_log)
    check(
        "context_filter: compound output keeps every line, never a summariser's pick (fleet-config#1268)",
        "abc0059 commit subject 59" in compound.compressed and "hooks/file_59.py" in compound.compressed,
        compound.compressed[-200:],
    )


def cd_prefix_runs_in_outer_shell(check: _Checker) -> None:
    # The rewrite runs for real: the cd stays in the harness's shell and the
    # wrapper inherits its directory, from somewhere else entirely.
    import _lib  # noqa: E402

    git_bash = _lib.git_bash_exe()
    probes = [("Bash", f'cd "{REPO.as_posix()}" && git rev-parse --show-toplevel')]
    probes.append(("PowerShell", f'cd "{REPO.as_posix()}"; git rev-parse --show-toplevel'))
    for tool, command in probes:
        code, stdout, stderr = run(
            "context_filter_hook",
            {"tool_name": tool, "cwd": str(Path.home()), "tool_input": {"command": command}},
            {"FLEET_CONTEXT_FILTER_MODE": "rewrite"},
        )
        rewritten = json.loads(stdout)["hookSpecificOutput"]["updatedInput"]["command"] if code == 0 and stdout.strip() else ""
        shell = git_bash if tool == "Bash" else "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        argv = [shell, "-c", rewritten] if tool == "Bash" else [shell, "-NoProfile", "-Command", rewritten]
        with tempfile.TemporaryDirectory() as tmp:
            res = subprocess.run(
                argv, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=tmp,
                env={**os.environ, "FLEET_CONTEXT_FILTER_DIR": tmp}, timeout=60,
            ) if rewritten and shell else None
        out = (res.stdout + res.stderr) if res else ""
        (check if shell else check.advisory)(
            f"context_filter_hook: {tool} cd prefix runs in the outer shell, the wrapper in its directory (fleet-config#1268)",
            rewritten.startswith(command.split("git rev-parse")[0].rstrip())
            and "--cwd" not in rewritten
            and res is not None and res.returncode == 0
            and REPO.as_posix().lower() in out.replace("\\", "/").lower(),
            f"rewritten={rewritten[:160]} out={out.strip()[:300]}",
        )


def cwd_reaches_wrapper_verbatim(check: _Checker) -> None:
    # The payload cwd is data: whatever characters the directory name carries,
    # the harness's shell must hand the wrapper that exact path and run nothing
    # else on the way. Driven for real through both shells, like the cd test.
    import _lib  # noqa: E402

    shells = {"Bash": _lib.git_bash_exe(), "PowerShell": "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"}
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "a$(touch m1)b$(ni m2)c'd\N{RIGHT SINGLE QUOTATION MARK}e f"
        work.mkdir()
        subprocess.run(["git", "init", "-q", str(work)], capture_output=True, timeout=60)
        for tool, shell in shells.items():
            code, stdout, stderr = run(
                "context_filter_hook",
                {"tool_name": tool, "cwd": str(work), "tool_input": {"command": "git rev-parse --show-toplevel"}},
                {"FLEET_CONTEXT_FILTER_MODE": "rewrite", "FLEET_CONTEXT_FILTER_DIR": tmp},
            )
            rewritten = json.loads(stdout)["hookSpecificOutput"]["updatedInput"]["command"] if code == 0 and stdout.strip() else ""
            argv = [shell, "-c", rewritten] if tool == "Bash" else [shell, "-NoProfile", "-Command", rewritten]
            res = subprocess.run(
                argv, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=tmp,
                env={**os.environ, "FLEET_CONTEXT_FILTER_DIR": tmp}, timeout=60,
            ) if rewritten and shell else None
            out = (res.stdout + res.stderr) if res else ""
            stray = [name for name in ("m1", "m2") if (Path(tmp) / name).exists()]
            (check if shell else check.advisory)(
                f"context_filter_hook: {tool} wrapper receives the session cwd unchanged",
                res is not None and res.returncode == 0 and not stray and work.name in out,
                f"rewritten={rewritten[:200]} stray={stray} out={out.strip()[:300]}",
            )


def mode_file_resolution(check: _Checker) -> None:
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


def grok_payload_short_circuits(check: _Checker) -> None:
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


def attribution_flags_forwarded(check: _Checker) -> None:
    base_payload = {
        "tool_name": "PowerShell",
        "cwd": str(REPO),
        "tool_input": {"command": "git status --short"},
    }

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

