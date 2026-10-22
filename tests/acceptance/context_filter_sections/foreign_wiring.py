"""Context-filter checks on the foreign harness wirings (Codex, agy, Copilot) that must not drift from this repo's source."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from acceptance.shared import PYTHON, REPO, _Checker, run


def codex_wired_hook_fails_open(check: _Checker) -> None:
    # ---- codex must not rewrite commands (#755) ----
    # Codex attributes a rewritten command's ordinary nonzero exit to the hook
    # that supplied updatedInput. That makes a healthy hook look broken and
    # cannot be repaired without erasing the user's real command status, so
    # drive the hook through its real junction path and pin fail-open behavior.
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
        check(
            "context_filter_hook: codex-wired commands fail open without rewrite (fleet-config#755)",
            res.returncode == 0 and res.stdout.strip() == "",
            res.stdout + res.stderr,
        )
    else:
        check.advisory(
            "context_filter_hook: codex-wired wrap NOT verified — ~/.codex/hooks junction absent",
            False,
            f"no such path: {codex_hook} — run install.ps1 to wire the codex hooks junction",
        )


def agy_payload_dialect(check: _Checker) -> None:
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


def copilot_payload_dialect(check: _Checker) -> None:
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

    # Copilot 1.0.83 sends toolArgs as an object, not a JSON string; the
    # string-shaped modifiedArgs reply is still honored there (fleet-config#918).
    copilot_object = dict(
        copilot_payload,
        toolArgs={"command": "git status --short", "description": "d", "mode": "sync", "initial_wait": 30},
    )
    code, stdout, stderr = run(
        "context_filter_hook", copilot_object, {"FLEET_CONTEXT_FILTER_MODE": "rewrite"}
    )
    reply = json.loads(stdout) if code == 0 and stdout.strip() else {}
    try:
        cop_args = json.loads(reply.get("modifiedArgs") or "{}")
    except (json.JSONDecodeError, TypeError):
        cop_args = {}
    check(
        "context_filter_hook: copilot object toolArgs -> allow + string modifiedArgs (fleet-config#918)",
        code == 0
        and reply.get("permissionDecision") == "allow"
        and str(cop_args.get("command") or "").startswith("& ")
        and "--agent copilot" in str(cop_args.get("command") or "")
        and cop_args.get("initial_wait") == 30
        and cop_args.get("description") == "d",
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


def copilot_installed_copy_matches_source(check: _Checker) -> None:
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


def agy_installed_copy_matches_source(check: _Checker) -> None:
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

