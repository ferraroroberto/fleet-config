"""PreToolUse adapter for the local fleet context filter.

Disabled by default. Set `FLEET_CONTEXT_FILTER_MODE=shadow` to collect real
command metrics without changing returned output, or `rewrite` to return the
compressed output to the agent. In both modes the original command is executed
by `context_filter_cli.py run`, so unsafe/streaming commands are skipped.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402
import context_filter  # noqa: E402


def _python_command() -> str:
    return shutil.which("py") or shutil.which("python") or sys.executable


def _quote_path(path: Path) -> str:
    return '"' + str(path).replace("\\", "/") + '"'


def main() -> None:
    mode = os.environ.get("FLEET_CONTEXT_FILTER_MODE", "off").strip().lower()
    if mode not in {"shadow", "rewrite"}:
        _lib.allow()

    payload = _lib.read_stdin_json()
    tool = _lib.tool_name(payload)
    if tool not in {"Bash", "PowerShell", "bash", "powershell"}:
        _lib.allow()

    command = _lib.command_string(payload)
    decision = context_filter.rewrite_decision(command)
    if not decision.should_wrap:
        _lib.allow()

    encoded = base64.b64encode(command.encode("utf-8")).decode("ascii")
    cli = Path(__file__).resolve().parent / "context_filter_cli.py"
    py_cmd = _python_command()
    cwd = str(_lib.cwd(payload))
    rewritten = (
        f'{py_cmd} {_quote_path(cli)} run --tool {tool} --mode {mode} '
        f'--encoded {encoded} --cwd {_quote_path(Path(cwd))}'
    )

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
