"""Acceptance checks for the context-filter surface (fleet-config#680).

Split out of the former 2681-line `unit_checks.py`, which was the one member of
this package still holding every concern at once while its siblings
(`architecture_guards`, `hook_matrix`, `spawn_scanner`, `standalone_dispatch`,
`tree_boundary`) had each been given their own file. The functions were always
independent -- nothing couples them but `shared.py` -- so this is the straggler
finally following the package's own convention, not an untangling.

One concern, one very large function: the compressor's JSON contract, its
fixture eval, and the *foreign* wirings (the Codex hooks junction, the Copilot
CLI hook, the `agy` plugin) that must not drift from the source of truth in this
repo. Those three probe integrations installed outside this tree, which is why
this is the package's only `run_unit3` (three-state) check: a machine missing
one cannot establish the fact, and reports it as skipped rather than folding it
into a pass (fleet-config#679).
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Tuple

from acceptance.shared import (
    HOOKS,
    PYTHON,
    REPO,
    _Checker,
    run,
)

# Every function below inserts its own sys.path entry (HOOKS or skills/_lib)
# right before its dynamic import -- matches the pre-split file's per-function
# style, so each check's dependency is visible at its own call site.


def _context_filter_unit_checks() -> Tuple[int, int, int]:
    """Returns `(failures, total, skipped)` -- run via `run_unit3`.

    Three of these cases probe integrations that live *outside* this repo
    (the ~/.codex hooks junction, the installed copilot hook, the installed
    agy plugin). On a machine without one of them the fact cannot be
    established, so it reports as `skipped` via `check.advisory(..., False)`
    -- never folded into the pass count, which is what made a machine that
    verified nothing read identical to one that verified all three
    (fleet-config#679).
    """
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
        # #564: design_lint is an executable package directory now, so the
        # /design-sync invocation carries no `.py` — it must still pass through.
        ("design_lint package directory",
         python_prefix + '"C:/Users/rober/.claude/skills/_lib/design_lint" all E:/automation/home-automation',
         True, 564),
        ("ordinary python -c", python_prefix + '-c "print(1)"', False, 427),
        ("ordinary git", "git status --short", False, 427),
        # ...and the extension-less branch must not swallow ordinary work under
        # a skill directory, which matches the same bare `skills/<x>/<y>` shape.
        ("ordinary rg under a skill directory",
         "rg design_lint E:/automation/fleet-config/skills/design-sync/", False, 564),
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

    # ---- codex wraps for its real shell, not its reported tool name (#541) ----
    # Codex reports a Bash-flavored tool but executes under PowerShell on
    # Windows — a Bash-form wrap died with a PowerShell ParserError in a live
    # codex exec probe. Detection is the ~/.codex/hooks wiring path (the env
    # stamp APP_LAUNCHER_AGENT inherits across process trees and lied in that
    # same probe), so drive the hook through the real junction path.
    codex_hook = Path.home() / ".codex" / "hooks" / "context_filter_hook.py"
    if codex_hook.exists():
        res = subprocess.run(
            [PYTHON, str(codex_hook)],
            input=json.dumps(
                {"tool_name": "Bash", "cwd": str(REPO), "tool_input": {"command": "git status --short"}}
            ),
            capture_output=True,
            text=True,
            timeout=15,
            env={**os.environ, "FLEET_CONTEXT_FILTER_MODE": "rewrite"},
        )
        codex_rewritten = ""
        if res.returncode == 0 and res.stdout.strip():
            codex_rewritten = json.loads(res.stdout)["hookSpecificOutput"]["updatedInput"]["command"]
        check(
            "context_filter_hook: codex-wired wrap is PowerShell-shaped on win32 (fleet-config#541)",
            res.returncode == 0
            and codex_rewritten.startswith("& ")
            and "--tool PowerShell" in codex_rewritten
            and "--agent codex" in codex_rewritten,
            codex_rewritten or (res.stdout + res.stderr),
        )
    else:
        check.advisory(
            "context_filter_hook: codex-wired wrap NOT verified — ~/.codex/hooks junction absent",
            False,
            f"no such path: {codex_hook} — run install.ps1 to wire the codex hooks junction",
        )

    # ---- agy (Antigravity CLI) payload -> decision-allow + overwrite (#546) ----
    # agy's PreToolUse dialect: `toolCall` envelope in, `overwrite` merged into
    # the tool args out (Claude's updatedInput equivalent, verified live). The
    # wrap must be PowerShell-shaped on win32 — the live probe expanded
    # `$env:OS` and left `%OS%` literal.
    agy_payload = {
        "toolCall": {"name": "run_command", "args": {"CommandLine": "git status --short", "Cwd": str(REPO)}},
        "conversationId": "agy-conv-1",
        "stepIdx": 3,
    }
    code, stdout, stderr = run(
        "context_filter_hook", agy_payload, {"FLEET_CONTEXT_FILTER_MODE": "rewrite"}
    )
    reply = json.loads(stdout) if code == 0 and stdout.strip() else {}
    agy_rewritten = str((reply.get("overwrite") or {}).get("CommandLine") or "")
    check(
        "context_filter_hook: agy payload -> allow + overwrite, PowerShell-shaped (fleet-config#546)",
        code == 0
        and reply.get("decision") == "allow"
        and agy_rewritten.startswith("& ")
        and "--tool PowerShell" in agy_rewritten
        and "--agent antigravity" in agy_rewritten
        and "--session-id agy-conv-1" in agy_rewritten
        and "hookSpecificOutput" not in stdout,
        stdout + stderr,
    )

    # agy streaming/skip commands must fail open with NO overwrite emitted
    agy_skip = {
        "toolCall": {"name": "run_command", "args": {"CommandLine": "npm run dev -- --watch", "Cwd": str(REPO)}},
        "conversationId": "agy-conv-2",
    }
    code, stdout, stderr = run(
        "context_filter_hook", agy_skip, {"FLEET_CONTEXT_FILTER_MODE": "rewrite"}
    )
    check(
        "context_filter_hook: agy streaming command passthrough (fleet-config#546)",
        code == 0 and stdout.strip() == "",
        stdout + stderr,
    )

    # ---- copilot payload -> permissionDecision allow + modifiedArgs (#547) ----
    # Copilot's dialect: string toolArgs in, string modifiedArgs out replacing
    # the WHOLE args object — other keys must be echoed, only command rewritten.
    copilot_payload = {
        "sessionId": "cop-sess-9",
        "timestamp": 1785694280490,
        "cwd": str(REPO),
        "toolName": "powershell",
        "toolArgs": json.dumps({"command": "git status --short", "description": "d", "mode": "sync"}),
    }
    code, stdout, stderr = run(
        "context_filter_hook", copilot_payload, {"FLEET_CONTEXT_FILTER_MODE": "rewrite"}
    )
    reply = json.loads(stdout) if code == 0 and stdout.strip() else {}
    try:
        cop_args = json.loads(reply.get("modifiedArgs") or "{}")
    except json.JSONDecodeError:
        cop_args = {}
    cop_rewritten = str(cop_args.get("command") or "")
    check(
        "context_filter_hook: copilot payload -> allow + modifiedArgs, other keys echoed (fleet-config#547)",
        code == 0
        and reply.get("permissionDecision") == "allow"
        and cop_rewritten.startswith("& ")
        and "--tool PowerShell" in cop_rewritten
        and "--agent copilot" in cop_rewritten
        and "--session-id cop-sess-9" in cop_rewritten
        and cop_args.get("mode") == "sync"
        and cop_args.get("description") == "d"
        and "hookSpecificOutput" not in stdout,
        stdout + stderr,
    )

    # copilot streaming/skip commands fail open with no JSON emitted
    copilot_skip = dict(copilot_payload, toolArgs=json.dumps({"command": "npm run dev -- --watch"}))
    code, stdout, stderr = run(
        "context_filter_hook", copilot_skip, {"FLEET_CONTEXT_FILTER_MODE": "rewrite"}
    )
    check(
        "context_filter_hook: copilot streaming command passthrough (fleet-config#547)",
        code == 0 and stdout.strip() == "",
        stdout + stderr,
    )

    # ---- copilot hook wiring: installed copy must match the repo source (#547) ----
    copilot_installed = Path.home() / ".copilot" / "hooks" / "fleet-context-filter.json"
    copilot_source = REPO / "copilot-hooks" / "fleet-context-filter.json"
    def _normalized(path: Path) -> bytes:
        # Newline-insensitive: git renormalizes the repo copy to CRLF while the
        # installed copy keeps the bytes it was installed with — that is not
        # drift (fleet-config#547).
        return path.read_bytes().replace(b"\r\n", b"\n")

    if copilot_installed.exists():
        check(
            "copilot hook: installed copy matches repo source (fleet-config#547)",
            _normalized(copilot_installed) == _normalized(copilot_source),
            "re-run install.ps1 to refresh the drift-guarded copy",
        )
    else:
        check.advisory(
            "copilot hook: NOT verified — no installed copy on this machine (fleet-config#547)",
            False,
            f"no such path: {copilot_installed} — run install.ps1 to install the copilot hook",
        )

    # ---- agy plugin: installed copy must match the repo source (#546) ----
    # agy's wiring is registry + copy (its Go plugin scanner does not descend
    # junctions), so drift between the repo source and the installed copy is
    # possible between installs — this is the same anti-staleness contract as
    # .fleet.toml. Skips when agy (or the plugin) is not on this machine.
    agy_installed = Path.home() / ".gemini" / "config" / "plugins" / "fleet-context-filter"
    agy_source = REPO / "agy" / "plugins" / "fleet-context-filter"
    if agy_installed.exists():
        drifted = [
            name for name in ("plugin.json", "hooks.json")
            if not (agy_installed / name).exists()
            or (agy_installed / name).read_bytes().replace(b"\r\n", b"\n")
            != (agy_source / name).read_bytes().replace(b"\r\n", b"\n")
        ]
        check(
            f"agy plugin: installed copy matches repo source (drift: {drifted or 'none'}) (fleet-config#546)",
            not drifted,
            "re-run install.ps1 (or: agy plugin install " + str(agy_source) + ")",
        )
    else:
        check.advisory(
            "agy plugin: NOT verified — no installed copy on this machine (fleet-config#546)",
            False,
            f"no such path: {agy_installed} — run install.ps1 (or: agy plugin install {agy_source})",
        )

    # ---- compress subcommand: the Pi port's entry point (#545) ----
    # Pi's tool_result middleware already holds the output, so compress reads
    # stdin JSON and never executes anything. Mode comes from the same
    # resolve_mode(); rows log agent "pi"; wrap only in rewrite.
    big_output = "\n".join(f"PASS test_case_{i:03d} ok" for i in range(400))
    compress_payload = json.dumps(
        {"command": "git status --short", "output": big_output, "session_id": "pi-sess-1", "cwd": str(REPO), "exit_code": 0}
    )
    for mode, expect_wrap in (("shadow", False), ("rewrite", True)):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "mode.json").write_text(json.dumps({"mode": mode}), encoding="utf-8")
            res = subprocess.run(
                [PYTHON, str(HOOKS / "context_filter_cli.py"), "compress", "--agent", "pi"],
                input=compress_payload,
                capture_output=True,
                text=True,
                env={**os.environ, "FLEET_CONTEXT_FILTER_MODE": "", "FLEET_CONTEXT_FILTER_DIR": tmp},
                timeout=30,
            )
            reply = json.loads(res.stdout) if res.returncode == 0 and res.stdout.strip() else {}
            log_path = Path(tmp) / "shadow.jsonl"
            rows = [json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()] if log_path.exists() else []
            row = rows[0] if rows else {}
            check(
                f"context_filter_cli: compress {mode} -> wrap={expect_wrap}, row agent=pi (fleet-config#545)",
                res.returncode == 0
                and reply.get("wrap") is expect_wrap
                and (not expect_wrap or "[fleet-context-filter:" in reply.get("text", ""))
                and len(rows) == 1
                and row.get("agent") == "pi"
                and row.get("mode") == mode
                and row.get("session_id") == "pi-sess-1",
                f"rc={res.returncode} reply={reply} rows={len(rows)} | {res.stderr.strip()}",
            )

    # skip rule still applies post-hoc: a streaming command's output is left alone
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "mode.json").write_text(json.dumps({"mode": "rewrite"}), encoding="utf-8")
        res = subprocess.run(
            [PYTHON, str(HOOKS / "context_filter_cli.py"), "compress"],
            input=json.dumps({"command": "npm run dev -- --watch", "output": big_output}),
            capture_output=True,
            text=True,
            env={**os.environ, "FLEET_CONTEXT_FILTER_MODE": "", "FLEET_CONTEXT_FILTER_DIR": tmp},
            timeout=30,
        )
        reply = json.loads(res.stdout) if res.returncode == 0 and res.stdout.strip() else {}
        check(
            "context_filter_cli: compress honors rewrite_decision skips (fleet-config#545)",
            res.returncode == 0 and reply.get("wrap") is False,
            f"rc={res.returncode} reply={reply} | {res.stderr.strip()}",
        )

    # malformed stdin fails open with a well-formed no-wrap reply
    res = subprocess.run(
        [PYTHON, str(HOOKS / "context_filter_cli.py"), "compress"],
        input="{not json",
        capture_output=True,
        text=True,
        timeout=30,
    )
    reply = json.loads(res.stdout) if res.returncode == 0 and res.stdout.strip() else {}
    check(
        "context_filter_cli: compress malformed stdin fails open (fleet-config#545)",
        res.returncode == 0 and reply.get("wrap") is False,
        f"rc={res.returncode} reply={reply} | {res.stderr.strip()}",
    )

    # ---- shadow.jsonl rotates at the size cap, keeping one generation (#549) ----
    with tempfile.TemporaryDirectory() as tmp:
        sys.path.insert(0, str(HOOKS))
        import context_filter as _cf  # noqa: E402

        log = Path(tmp) / "shadow.jsonl"
        log.write_text("x" * (_cf.SHADOW_LOG_MAX_BYTES + 1), encoding="utf-8")
        prior_gen = Path(tmp) / "shadow.jsonl.1"
        prior_gen.write_text("older generation\n", encoding="utf-8")
        res = subprocess.run(
            [
                PYTHON,
                str(HOOKS / "context_filter_cli.py"),
                "run",
                "--tool", "PowerShell",
                "--mode", "shadow",
                "--encoded", encoded,
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "FLEET_CONTEXT_FILTER_DIR": tmp},
            timeout=30,
        )
        rotated = prior_gen.exists() and prior_gen.stat().st_size > len("older generation\n")
        fresh_rows = [l for l in log.read_text(encoding="utf-8").splitlines() if l.strip()] if log.exists() else []
        check(
            "context_filter: oversized shadow.jsonl rotates to .1 and the row lands fresh (fleet-config#549)",
            res.returncode == 0 and rotated and len(fresh_rows) == 1 and fresh_rows[0].startswith("{"),
            f"rc={res.returncode} rotated={rotated} fresh_rows={len(fresh_rows)} | {res.stderr.strip()}",
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
    return check.failures, check.total, check.skipped
