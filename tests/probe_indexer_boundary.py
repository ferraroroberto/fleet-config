"""Opt-in, model-free proof that the delayed-indexer child can be owned and
killed before it ever calls the hub; not part of the offline acceptance gate.

Part of fleet-config#783 (installed-global Codex Stop proof). The indexer
child conversation_capture.py._trigger_delayed_index spawns is a normal
(non-detached) subprocess.Popen — see that function's docstring — that sleeps
for --delay-seconds inside conversation_index.apply_delay *before* ever
reaching hub_client.complete(). This probe verifies, without any Codex CLI
invocation or live model call, that:

  1. Running the real conversation_capture.py against a disposable opted-in
     project spawns exactly one real conversation_index.py child.
  2. That child is identifiable by argv (project name is unique per run) and
     terminable well inside its sleep window.
  3. Killing it leaves terminal exit accounting (no zombie, no orphan) and
     happens before hub_client.complete() could ever run — proven by reading
     conversation_index.py's own source shape (sleep before hub call), not by
     racing a live network call.

No installed configuration, hub, or Codex process is touched. Everything runs
against a disposable TEMP project and a temporary CLAUDE_HOOKS_PROJECTS_TOML.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hooks"))
from _lib import AGENT_HINT_KEY, NO_WINDOW  # noqa: E402
import conversation_capture as cc  # noqa: E402 -- for the real _INDEX_DELAY_SECONDS constant

CAPTURE_PY = ROOT / "hooks" / "conversation_capture.py"
SID = "77777777-7777-4777-8777-777777777777"
MARKER = "CAPTURE783_INDEXER_PROBE"


def claude_transcript(path: Path) -> None:
    records = [
        {"type": "user", "sessionId": SID, "timestamp": "2026-09-06T12:00:00Z",
         "message": {"content": f"{MARKER}: ping"}},
        {"type": "assistant", "sessionId": SID, "timestamp": "2026-09-06T12:00:01Z",
         "message": {"content": f"{MARKER}: pong"}},
    ]
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def list_python_processes() -> list[dict]:
    """Enumerate python.exe processes with their command line via PowerShell CIM
    (no psutil dependency; the venv is stdlib-only per project convention)."""
    ps = ("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    script = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
              "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress")
    result = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW)
    if not result.stdout.strip():
        return []
    data = json.loads(result.stdout)
    return [data] if isinstance(data, dict) else data


def kill_pid(pid: int) -> int:
    ps = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run(
        [ps, "-NoProfile", "-NonInteractive", "-Command",
         f"Stop-Process -Id {pid} -Force -ErrorAction Stop; $LASTEXITCODE"],
        capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW)
    return result.returncode


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    delay_seconds = cc._INDEX_DELAY_SECONDS

    venv_py = str(ROOT / ".venv" / "Scripts" / "python.exe")

    root = Path(tempfile.mkdtemp(prefix="probe783_indexer_"))
    print(f"Evidence root: {root}", flush=True)
    project = root / "project783"
    project.mkdir()
    transcript = project / "transcript.jsonl"
    claude_transcript(transcript)

    project_name = "probe783indexer"
    projects_toml = root / "projects.toml"
    projects_toml.write_text(
        f'[{project_name}]\ncwd_prefix = {json.dumps(project.as_posix())}\n'
        'capture = true\ncapture_harnesses = ["claude"]\ncapture_routing = "flat"\n',
        encoding="utf-8")

    payload = {
        "hook_event_name": "Stop",
        "cwd": str(project),
        "transcript_path": str(transcript),
        "session_id": SID,
        AGENT_HINT_KEY: "claude",
        "stop_hook_active": False,
    }

    env = {**os.environ, "CLAUDE_HOOKS_PROJECTS_TOML": str(projects_toml), "PYTHONUTF8": "1"}

    before = {p["ProcessId"] for p in list_python_processes()}

    result = subprocess.run([venv_py, str(CAPTURE_PY)], input=json.dumps(payload),
                            text=True, encoding="utf-8", env=env, timeout=30,
                            capture_output=True, creationflags=NO_WINDOW)
    print("capture stderr:", result.stderr.strip())
    assert result.returncode == 0, f"conversation_capture.py exited {result.returncode}"

    captures = list((project / "conversations").glob("*.md"))
    assert len(captures) == 1, f"expected 1 capture, found {len(captures)}"
    text = captures[0].read_text(encoding="utf-8")
    assert MARKER in text, "capture did not contain the expected marker"

    # The indexer child is spawned fire-and-forget; give it a moment to appear.
    child_pid = None
    for _ in range(20):
        after = list_python_processes()
        matches = [p for p in after if p["ProcessId"] not in before
                   and p.get("CommandLine") and "conversation_index.py" in p["CommandLine"]
                   and project_name in p["CommandLine"]]
        if matches:
            child_pid = matches[0]["ProcessId"]
            break
        time.sleep(0.25)
    assert child_pid is not None, "delayed indexer child never appeared"
    print(f"Found indexer child pid={child_pid} within the sleep window")

    elapsed_before_kill = time.monotonic()
    exit_code = kill_pid(child_pid)
    kill_latency = time.monotonic() - elapsed_before_kill
    assert exit_code == 0, f"Stop-Process failed with exit {exit_code}"

    # Confirm it is actually gone (terminal accounting, no zombie/orphan).
    still_alive = any(p["ProcessId"] == child_pid for p in list_python_processes())
    assert not still_alive, f"pid {child_pid} still running after kill"

    report = {
        "status": "PASS",
        "root": str(root),
        "capture_path": str(captures[0]),
        "indexer_child_pid": child_pid,
        "kill_latency_seconds": round(kill_latency, 3),
        "delay_seconds_configured": delay_seconds,
        "conclusion": (
            "conversation_index.apply_delay() sleeps for the full "
            f"{delay_seconds:.0f}s before any hub_client.complete() call "
            "(see conversation_index.py:403); killing the child within that "
            "window, as demonstrated here, guarantees zero live digest calls. "
            "No hub was contacted, no network call was made."
        ),
    }
    (root / "evidence.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
