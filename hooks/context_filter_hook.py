"""PreToolUse adapter for the local fleet context filter.

Disabled by default. The machine-wide switch is `~/.fleet-context-filter/
mode.json` (`off | shadow | rewrite`, written by the app-launcher toggle);
the `FLEET_CONTEXT_FILTER_MODE` env var overrides it per process and doubles
as the kill switch (fleet-config#541). `shadow` collects real command metrics
without changing returned output; `rewrite` returns the compressed output to
the agent. In both modes the original command is executed by
`context_filter_cli.py run`, so unsafe/streaming commands are skipped.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402
import context_filter  # noqa: E402


def _python_command() -> str:
    return _lib.find_python_executable() or sys.executable


def _quote_path(path: Path) -> str:
    return '"' + str(path).replace("\\", "/") + '"'


def _invoking_agent(payload: dict) -> str:
    """Which harness this hook is serving, for telemetry attribution only.

    Precedence mirrors session_state's: the launcher's explicit stamp, then the
    payload hint normalize_payload() set, then the wiring path — Codex invokes
    this file by its `~/.codex/hooks/` junction path (codex-hooks.json), which
    is an install-location fact, not payload vocabulary, so it doesn't belong
    in _lib.normalize_payload().
    """
    stamped = os.environ.get("APP_LAUNCHER_AGENT", "").strip().lower()
    if stamped:
        return stamped
    hinted = _lib.payload_agent(payload)
    if hinted:
        return hinted
    if "/.codex/" in __file__.replace("\\", "/").lower():
        return "codex"
    return "claude"


def main() -> None:
    mode = context_filter.resolve_mode()
    if mode not in {"shadow", "rewrite"}:
        _lib.allow()

    payload = _lib.read_stdin_json()
    if _lib.payload_agent(payload) == "grok":
        # Grok's PreToolUse honors only allow/deny — `updatedInput` is ignored,
        # so the wrap would never substitute and the emitted JSON would be dead
        # weight on its runner. Explicitly inert there (fleet-config#541).
        _lib.allow()
    tool = _lib.tool_name(payload)
    if tool not in {"Bash", "PowerShell", "bash", "powershell"}:
        _lib.allow()

    command = _lib.command_string(payload)
    decision = context_filter.rewrite_decision(command)
    if not decision.should_wrap:
        _lib.allow()

    encoded = base64.b64encode(command.encode("utf-8")).decode("ascii")
    cli = Path(__file__).resolve().parent / "context_filter_cli.py"
    py_cmd = _quote_path(Path(_python_command()))
    if tool.lower() == "powershell":
        # A quoted path as a bare statement isn't invocable in PowerShell
        # without the call operator -- unlike Bash, which strips the quotes
        # and executes directly.
        py_cmd = f"& {py_cmd}"
    cwd = str(_lib.cwd(payload))
    rewritten = (
        f'{py_cmd} {_quote_path(cli)} run --tool {tool} --mode {mode} '
        f'--encoded {encoded} --cwd {_quote_path(Path(cwd))}'
    )
    # Sanitized to a shell-neutral charset rather than quoted: the rewritten
    # string is re-parsed by whichever shell the tool runs, and Bash and
    # PowerShell disagree on quoting rules.
    session_id = re.sub(r"[^A-Za-z0-9_.-]", "", str(payload.get("session_id") or ""))
    if session_id:
        rewritten += f" --session-id {session_id}"
    agent = re.sub(r"[^a-z0-9_-]", "", _invoking_agent(payload)) or "claude"
    rewritten += f" --agent {agent}"

    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": f"fleet-context-filter: {mode}",
            "updatedInput": {"command": rewritten},
        }
    }
    print(json.dumps(output, separators=(",", ":")), flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
