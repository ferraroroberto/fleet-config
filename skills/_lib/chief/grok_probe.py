"""Live capability probe: can a Grok Build session hold the chief's mechanics?

The chief leans on harness behaviour Claude Code has and Grok may not: a turn
woken by a finished background command or a monitor line, hooks that put text
in front of the model at session start, a Stop gate that keeps a turn going,
and a refusal of the question modal. Each answer changes with the Grok
version, so this probe re-measures them against the installed `grok` instead
of trusting a recorded result.

Every run happens headless (`grok -p`) in a fresh temporary folder, never a
fleet checkout. The folder carries one probe hook that calls this checkout's
real `hooks/_lib` wire (`warn()`, `block()`) plus the alternative reply shapes
Grok documents, so a failure says whether the fleet's dialect or Grok itself
is the gap. `GROK_FOLDER_TRUST=0` is set on the child only: project hooks in an
untrusted folder are otherwise skipped silently, and no trust grant is written.

    python skills/_lib/chief/grok_probe.py [--timeout 400]

Prints `GROK_VERSION=` then one `CAP_<name>=pass|fail|unknown` line each;
`unknown` means the run itself did not complete, never a pass. Exit 0 when
every capability passes, 1 when any fails, 3 when any is unknown or `grok` is
missing. Each model turn costs Grok usage, so this is opt-in, never gated.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

# A direct run starts without `skills/_lib` on the path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from no_window import NO_WINDOW  # noqa: E402

logger = logging.getLogger("fleet_chief")

REPO = Path(__file__).resolve().parents[3]
CODEWORD = "PELICAN-41"

HOOK_SOURCE = r'''
import io, json, os, sys, time
from pathlib import Path
sys.path.insert(0, os.environ["PROBE_HOOKS_DIR"])
import _lib

here = Path(__file__).resolve().parent
tag = sys.argv[1]
raw = sys.stdin.buffer.read().decode("utf-8")
sys.stdin = io.StringIO(raw)
payload = _lib.read_stdin_json()
event = payload.get("hook_event_name")
with open(here / "hook_log.jsonl", "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"tag": tag, "event": event, "tool": payload.get("tool_name"),
                         "stop_hook_active": payload.get("stop_hook_active")}) + "\n")
ss_mode = os.environ.get("PROBE_SS_MODE", "lib")
stop_mode = os.environ.get("PROBE_STOP_MODE", "lib")
ask_mode = os.environ.get("PROBE_ASK_MODE", "lib")
if event == "SessionStart":
    msg = "PROBE-SESSIONSTART: the handover code word is %s."
    if ss_mode == "systemMessage":
        print(json.dumps({"systemMessage": msg})); sys.exit(0)
    if ss_mode == "additionalContext":
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": msg}}))
        sys.exit(0)
    _lib.warn(msg)
if event == "Stop" and json.loads(raw).get("reason") in (None, "end_turn"):
    marker = here / "stop_blocked_once"
    if not payload.get("stop_hook_active") and not marker.exists():
        marker.write_text("1")
        msg = "PROBE-STOP: do not end the turn yet. Reply with exactly the line STOP-GATE-SEEN, then stop."
        if stop_mode == "block_json":
            print(json.dumps({"decision": "block", "reason": msg})); sys.exit(0)
        _lib.block(msg)
if event == "PreToolUse" and tag == "Ask":
    # "lib" refuses only what the real chief guard would: the normalized Claude name.
    if ask_mode == "lib" and payload.get("tool_name") != "AskUserQuestion":
        _lib.allow()
    _lib.block("PROBE-ASK: the question tool is blocked; state the question as plain text instead.")
_lib.allow()
''' % CODEWORD

PROMPT_WAKE = """Capability probe. Do exactly this:
1. Start `powershell -NoProfile -Command "Start-Sleep -Seconds 20; Write-Output BG-PROBE-FINISHED"` in the
   BACKGROUND (run_terminal_command with block_until_ms 0). Do not wait on it.
2. Immediately end your turn with the line `TURN1-END`. Do not call get_command_or_subagent_output, do not sleep.
3. If you are later woken because that task completed, print `WOKE output=<its output>`."""

PROMPT_HOOKS = """Capability probe, step by step:
1. If your context contains a "handover code word", print `CODEWORD=<word>`, else `CODEWORD=none`.
2. Call the ask_user_question tool once, asking "Probe question?" with options "A" and "B". If the call is
   blocked or fails, print `ASK-RESULT=<the exact error text>`; if it returns, print `ASK-RESULT=answered`.
3. End your turn with the line `DONE`."""

PROMPT_TOOLS = """Capability probe. Run each step and print the labelled line.
1. Run `gh --version` and print `GH=` followed by its first line.
2. Write the file `probe_write.txt` here containing `WRITE-OK`, read it back, and print `FILE=<contents>`.
3. Start the monitor tool (description "fake inbox") on `powershell -NoProfile -File fake_inbox.ps1`,
   then immediately end your turn with the line `TURN1-END`. Do not wait on it.
4. If a monitor event later wakes you, print `MONITOR-WOKE=<event line>`."""

FAKE_INBOX = 'Start-Sleep -Seconds 15\nWrite-Output "INBOX owner: status please"\nStart-Sleep -Seconds 2\n'


def _write_probe_folder(folder: Path) -> None:
    """Lay down the probe hook, its settings and the fake inbox script."""
    hook = folder / "probe_hook.py"
    hook.write_text(HOOK_SOURCE, encoding="utf-8")
    (folder / "fake_inbox.ps1").write_text(FAKE_INBOX, encoding="utf-8")
    base = f"{Path(sys.executable).as_posix()} {hook.as_posix()}"
    settings = {"hooks": {
        "SessionStart": [{"hooks": [{"type": "command", "command": f"{base} SS"}]}],
        "Stop": [{"hooks": [{"type": "command", "command": f"{base} Stop"}]}],
        "PreToolUse": [{"matcher": "AskUserQuestion",
                        "hooks": [{"type": "command", "command": f"{base} Ask"}]}],
    }}
    (folder / ".claude").mkdir()
    (folder / ".claude" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")


def _run_grok(folder: Path, prompt: str, timeout: int, env_extra: Dict[str, str]) -> Optional[str]:
    """One headless turn sequence; the model's text, or None when it did not complete."""
    for name in ("hook_log.jsonl", "stop_blocked_once"):
        (folder / name).unlink(missing_ok=True)
    env = dict(os.environ, GROK_FOLDER_TRUST="0", PROBE_HOOKS_DIR=str(REPO / "hooks"), **env_extra)
    try:
        proc = subprocess.run(
            ["grok", "-p", prompt, "--output-format", "streaming-json", "--always-approve"],
            cwd=folder, env=env, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("⚠️ grok run did not complete: %s", exc)
        return None
    text: List[str] = []
    ended = False
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "text":
            text.append(event.get("data", ""))
        ended = ended or event.get("type") == "end"
    if proc.returncode != 0 or not ended:
        logger.warning("⚠️ grok exited %s (end event seen: %s): %s",
                       proc.returncode, ended, proc.stderr.strip()[:300])
        return None
    return "".join(text)


def _hook_log(folder: Path) -> List[dict]:
    path = folder / "hook_log.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _verdict(text: Optional[str], needle: str) -> str:
    if text is None:
        return "unknown"
    return "pass" if needle in text else "fail"


def _ask_verdict(text: Optional[str], log: List[dict]) -> str:
    """Was the question tool refused? Unknown when the model never called it this run."""
    if text is None or not any(r["tag"] == "Ask" for r in log):
        return "unknown"
    return "pass" if "PROBE-ASK" in text and "ASK-RESULT=answered" not in text else "fail"


def probe(timeout: int) -> Dict[str, str]:
    """Run every probe in one throwaway folder; capability name → pass/fail/unknown."""
    results: Dict[str, str] = {}
    folder = Path(tempfile.mkdtemp(prefix="grok-chief-probe-"))
    try:
        _write_probe_folder(folder)
        text = _run_grok(folder, PROMPT_WAKE, timeout, {"PROBE_STOP_MODE": "none"})
        results["wake_on_background_exit"] = _verdict(text, "WOKE output=BG-PROBE-FINISHED")

        text = _run_grok(folder, PROMPT_HOOKS, timeout, {"PROBE_SS_MODE": "lib", "PROBE_STOP_MODE": "lib"})
        log = _hook_log(folder)
        results["sessionstart_fleet_wire"] = _verdict(text, CODEWORD)
        results["stop_gate_fleet_wire"] = _verdict(text, "STOP-GATE-SEEN")
        results["ask_matcher_fires"] = ("unknown" if text is None else
                                        "pass" if any(r["tag"] == "Ask" for r in log) else "fail")
        results["ask_guard_fleet_wire"] = _ask_verdict(text, log)

        text = _run_grok(folder, PROMPT_HOOKS, timeout,
                         {"PROBE_SS_MODE": "systemMessage", "PROBE_STOP_MODE": "block_json",
                          "PROBE_ASK_MODE": "any"})
        results["sessionstart_systemmessage"] = _verdict(text, CODEWORD)
        results["stop_gate_block_json"] = _verdict(text, "STOP-GATE-SEEN")
        results["ask_deny_json"] = _ask_verdict(text, _hook_log(folder))

        text = _run_grok(folder, PROMPT_HOOKS, timeout,
                         {"PROBE_SS_MODE": "additionalContext", "PROBE_STOP_MODE": "none",
                          "PROBE_ASK_MODE": "any"})
        results["sessionstart_additionalcontext"] = _verdict(text, CODEWORD)

        text = _run_grok(folder, PROMPT_TOOLS, timeout, {"PROBE_STOP_MODE": "none"})
        results["tools_gh"] = _verdict(text, "GH=gh version")
        results["tools_file_write_read"] = _verdict(text, "FILE=WRITE-OK")
        results["wake_on_monitor_line"] = _verdict(text, "MONITOR-WOKE=[fake inbox] INBOX owner")
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return results


def _grok_version() -> Optional[str]:
    try:
        out = subprocess.run(["grok", "--version"], capture_output=True, text=True, timeout=30,
                             creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("⚠️ grok --version failed: %s", exc)
        return None
    return out.stdout.strip() or None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timeout", type=int, default=400, help="seconds per headless grok run")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.stdout.reconfigure(encoding="utf-8")
    version = _grok_version()
    print(f"GROK_VERSION={version or 'missing'}")  # logging-not-print-exempt: KEY=value protocol
    if version is None:
        return 3
    results = probe(args.timeout)
    for name, verdict in results.items():
        print(f"CAP_{name}={verdict}")  # logging-not-print-exempt: KEY=value protocol
    if "unknown" in results.values():
        return 3
    return 0 if all(v == "pass" for v in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
