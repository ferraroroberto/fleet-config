"""Opt-in live Codex SessionEnd lifecycle probe; see issue #747."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
from _lib import NO_WINDOW, run_git  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True,
                        help="New disposable directory (must not exist)")
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    root = args.workspace.absolute()
    codex = shutil.which("codex")
    if os.name != "nt" or root.exists() or not codex:
        parser.error("Requires Windows, installed Codex and a new workspace directory")

    root.mkdir(parents=True)
    if run_git(["init", "-q", "-b", "main", str(root)], timeout=15).returncode:
        parser.error("disposable git init failed")
    hook_dir = root / ".codex" / "hooks"
    hook_dir.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / "hooks"
    observations = root / "hook-observations.jsonl"
    wrapper = hook_dir / "session_state_codex.py"
    wrapper.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {str(source)!r})\n"
        "import _lib, session_state_codex\n"
        "raw = json.load(sys.stdin)\n"
        f"observations = Path({str(observations)!r})\n"
        "payload = _lib.normalize_payload(raw)\n"
        "_lib._ACTIVE_EVENT = payload.get('hook_event_name')\n"
        "_lib.read_stdin_json = lambda: payload\n"
        "code = 0\n"
        "try:\n"
        "    session_state_codex.main()\n"
        "except SystemExit as exc:\n"
        "    code = int(exc.code or 0)\n"
        "state_path = Path(os.environ['CLAUDE_HOOKS_STATE_DIR']) / 'sessions-state.json'\n"
        "try:\n"
        "    rows = json.loads(state_path.read_text(encoding='utf-8'))\n"
        "except (OSError, ValueError):\n"
        "    rows = {}\n"
        "with observations.open('a', encoding='utf-8') as log:\n"
        "    log.write(json.dumps({'payload': raw, 'row_status': "
        "(rows.get(str(raw.get('session_id'))) or {}).get('status')}) + '\\n')\n"
        "raise SystemExit(code)\n",
        encoding="utf-8",
    )
    command = subprocess.list2cmdline([sys.executable, str(wrapper)])
    handler = {"matcher": "", "hooks": [{"type": "command", "command": command,
                                              "timeout": 15}]}
    hooks = {event: [handler] for event in ("UserPromptSubmit", "Stop", "SessionEnd")}
    (root / ".codex" / "hooks.json").write_text(
        json.dumps({"hooks": hooks}, indent=2), encoding="utf-8",
    )

    prompt = "Reply with exactly: lifecycle probe complete. Do not call any tools."
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "CLAUDE_HOOKS_STATE_DIR": str(root / "state"),
        "CLAUDE_SESSIONS_DIR": str(root / "no-sessions"),
        "CLAUDE_SETTINGS_JSON_PATH": str(root / "no-settings.json"),
        "APP_LAUNCHER_SESSION_ID": "",
        "APP_LAUNCHER_AGENT": "",
        "FLEET_CONTEXT_FILTER_MODE": "off",
    }
    version = subprocess.run(
        [codex, "--version"], capture_output=True, text=True, encoding="utf-8",
        timeout=15, creationflags=NO_WINDOW,
    ).stdout.strip()
    argv = [
        codex, "exec", "--ephemeral", "--json", "--approve-for-me",
        "--dangerously-bypass-hook-trust",
        "-c", f'projects."{root.as_posix()}".trust_level="trusted"',
        "-m", args.model, "-C", str(root), "-",
    ]
    with (root / "run.jsonl").open("w", encoding="utf-8") as out, (
            root / "stderr.txt").open("w", encoding="utf-8") as err:
        try:
            proc = subprocess.run(
                argv, input=prompt, text=True, encoding="utf-8", stdout=out, stderr=err,
                env=env, timeout=180, creationflags=NO_WINDOW,
            )
        except subprocess.TimeoutExpired:
            print("LIFECYCLE=unknown reason=Codex timed out after 180 seconds")
            return 1

    try:
        events = [
            json.loads(line) for line in observations.read_text(encoding="utf-8").splitlines()
        ]
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        events = []
    names = [(event.get("payload") or {}).get("hook_event_name") for event in events]
    states = [event.get("row_status") for event in events]
    reasons = [
        (event.get("payload") or {}).get("reason")
        for event in events
        if (event.get("payload") or {}).get("hook_event_name") == "SessionEnd"
    ]
    state_path = root / "state" / "sessions-state.json"
    try:
        rows = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        rows = {}
    passed = (
        proc.returncode == 0
        and names == ["UserPromptSubmit", "Stop", "SessionEnd"]
        and states == ["working", "needs-you", None]
        and reasons == ["other"]
        and rows == {}
    )
    print(json.dumps({
        "version": version,
        "mode": "codex exec --ephemeral (normal process exit)",
        "lifecycle": "pass" if passed else "fail",
        "events": names,
        "row_states": states,
        "session_end_reasons": reasons,
        "remaining_rows": sorted(rows),
        "returncode": proc.returncode,
        "evidence": str(root),
    }))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
