"""Prepare or verify the interactive Codex lifecycle probe for issue #799.

The tool deliberately does not launch Codex: ``PermissionRequest`` requires a
real interactive TTY. ``prepare`` prints the command and prompts for a human
terminal; ``verify`` proves the captured native event separation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
from _lib import run_git  # noqa: E402

EVENTS = ("UserPromptSubmit", "PermissionRequest", "Stop", "SessionEnd")


def prepare(root: Path) -> int:
    """Create the capture-only workspace and print interactive instructions."""
    if root.exists():
        print("PROBE=not_confirmed reason=workspace already exists")
        return 1
    root.mkdir(parents=True)
    if run_git(["init", "-q", "-b", "main", str(root)], timeout=15).returncode:
        print("PROBE=not_confirmed reason=git init failed")
        return 1
    hook_dir = root / ".codex" / "hooks"
    hook_dir.mkdir(parents=True)
    evidence = root / "hook-events.jsonl"
    capture = hook_dir / "capture.py"
    capture.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "raw = json.load(sys.stdin)\n"
        f"out = Path({str(evidence)!r})\n"
        "record = {key: raw.get(key) for key in "
        "('hook_event_name', 'session_id', 'turn_id', 'last_assistant_message')}\n"
        "with out.open('a', encoding='utf-8') as handle:\n"
        "    handle.write(json.dumps(record) + '\\n')\n",
        encoding="utf-8",
    )
    command = subprocess.list2cmdline([sys.executable, str(capture)])
    handler = {"matcher": "", "hooks": [{"type": "command", "command": command, "timeout": 15}]}
    (root / ".codex" / "hooks.json").write_text(
        json.dumps({"hooks": {event: [handler] for event in EVENTS}}, indent=2),
        encoding="utf-8",
    )
    root_posix = root.as_posix()
    print("Run in a real terminal, approve the marker write, then enter the two remaining prompts:")
    print(f"codex --no-alt-screen --dangerously-bypass-hook-trust -a on-request -s read-only "
          f"-c 'projects.\"{root_posix}\".trust_level=\"trusted\"' -C {root_posix} "
          "'Create marker.txt containing exactly approved.'")
    print("Ask exactly one concise question and take no action.")
    print("Reply exactly: clean completion. Take no action.")
    return 0


def _events_by_turn(events: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    turns: list[list[dict[str, Any]]] = []
    for event in events:
        turn_id = event.get("turn_id")
        if not turns or turns[-1][0].get("turn_id") != turn_id:
            turns.append([])
        turns[-1].append(event)
    return turns


def _names(turn: list[dict[str, Any]]) -> list[object]:
    return [event.get("hook_event_name") for event in turn]


def _has_stable_identity(turn: list[dict[str, Any]]) -> bool:
    sessions = {event.get("session_id") for event in turn}
    turns = {event.get("turn_id") for event in turn}
    return (len(sessions) == 1 and all(isinstance(value, str) and value for value in sessions)
            and len(turns) == 1 and all(isinstance(value, str) and value for value in turns))


def evaluate(events: list[dict[str, Any]]) -> dict[str, object]:
    """Return a strict report for the three required native event sequences."""
    relevant = [turn for turn in _events_by_turn(events)
                if any(event.get("hook_event_name") == "Stop" for event in turn)]
    approval = next((turn for turn in relevant
                     if any(event.get("hook_event_name") == "PermissionRequest" for event in turn)), [])
    question = next((turn for turn in relevant
                     if any(str(event.get("last_assistant_message") or "") ==
                            "What would you like me to do next?" for event in turn)), [])
    completion = next((turn for turn in relevant
                       if any(str(event.get("last_assistant_message") or "").lower() ==
                              "clean completion." for event in turn)), [])
    passed = bool(
        _names(approval) == ["UserPromptSubmit", "PermissionRequest", "Stop"]
        and _has_stable_identity(approval)
        and _names(question) == ["UserPromptSubmit", "Stop"]
        and _has_stable_identity(question)
        and _names(completion) == ["UserPromptSubmit", "Stop"]
        and _has_stable_identity(completion)
        and len({approval[0]["session_id"], question[0]["session_id"],
                 completion[0]["session_id"]}) == 1
        and len({approval[0]["turn_id"], question[0]["turn_id"],
                 completion[0]["turn_id"]}) == 3
    )
    return {
        "probe": "pass" if passed else "not_confirmed",
        "approval_request": _names(approval),
        "prose_question": _names(question),
        "clean_completion": _names(completion),
    }


def verify(root: Path) -> int:
    """Verify approval, prose-question, and clean-completion event sequences."""
    try:
        events = [json.loads(line) for line in (root / "hook-events.jsonl").read_text(
            encoding="utf-8").splitlines()]
    except (OSError, ValueError, UnicodeDecodeError):
        print("PROBE=not_confirmed reason=evidence missing or malformed")
        return 1
    report = evaluate(events)
    print(json.dumps(report, indent=2))
    return 0 if report["probe"] == "pass" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "verify"))
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    root = args.workspace.absolute()
    return prepare(root) if args.mode == "prepare" else verify(root)


if __name__ == "__main__":
    sys.exit(main())
