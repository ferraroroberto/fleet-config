"""Opt-in installed-global Stop capture proof; not part of the offline
acceptance gate. Completes fleet-config#783.

Unlike tests/probe_codex_capture.py, this probe wires NO project-level
.codex/hooks.json at all — the disposable temp project has zero hook
overrides, so Codex CLI must fall through to the REAL installed
~/.codex/hooks.json for UserPromptSubmit and Stop. That installed config
already wires Stop -> session_state_codex.py + conversation_capture.py (see
~/.codex/hooks.json). Nothing installed is edited by this probe.

Evidence strategy (no project-level observer wrapper is possible here, since
there is deliberately no project hooks.json to carry one):
  - CLAUDE_HOOKS_PROJECTS_TOML / CLAUDE_HOOKS_STATE_DIR are set in the codex
    subprocess's own environment, pointing at temp files. This probe's first
    live call establishes empirically whether Codex's hook dispatcher passes
    its own process environment through to hook subprocess commands (the
    load-bearing premise for every prior probe in this repo, but never
    directly proven for Codex's *global* hook path specifically). A missing
    capture/state-file result is treated as inconclusive/blocked, not a
    fabricated pass.
  - sessions-state.json (installed session_state_codex.py's own real side
    effect) is polled WHILE the turn is in flight, giving an independent,
    non-self-authored "positive UserPromptSubmit observation" (status
    transitions to "working" for our exact session_id/cwd before Stop
    flips it to "needs-you").
  - The resulting capture file (installed conversation_capture.py's own real
    side effect) carries the matching sid/agent/cwd via its own header.
  - A background watcher owns process boundaries for the run's whole
    lifetime: it kills the delayed conversation_index.py child within its
    60s pre-hub sleep window (proven safe in tests/probe_indexer_boundary.py)
    and kills any node.exe/node_repl.exe descendant of the codex process on
    sight, recording every action taken.

Requires normal saved ChatGPT subscription auth. No credential copying,
installed config/trust edits, or network/firewall changes are made. See
docs/conversation-capture.md and fleet-config#783 before running.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hooks"))
from _lib import NO_WINDOW  # noqa: E402
import conversation_capture as cc  # noqa: E402

PS = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
DISALLOWED_NAMES = {"node.exe", "node_repl.exe"}


def snapshot_processes() -> list[dict]:
    script = ("Get-CimInstance Win32_Process | "
              "Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress")
    result = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW)
    if not result.stdout.strip():
        return []
    data = json.loads(result.stdout)
    return [data] if isinstance(data, dict) else data


def descendants(root_pid: int, table: list[dict]) -> set[int]:
    by_parent: dict[int, list[int]] = {}
    for p in table:
        by_parent.setdefault(p.get("ParentProcessId"), []).append(p["ProcessId"])
    out: set[int] = set()
    frontier = [root_pid]
    while frontier:
        pid = frontier.pop()
        for child in by_parent.get(pid, []):
            if child not in out:
                out.add(child)
                frontier.append(child)
    return out


def kill_pid(pid: int) -> bool:
    result = subprocess.run(
        [PS, "-NoProfile", "-NonInteractive", "-Command",
         f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue"],
        capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW)
    return result.returncode == 0


class BoundaryWatcher(threading.Thread):
    """Owns the codex process's descendants for the run's lifetime.

    Kills any node.exe/node_repl.exe descendant on sight (the previously
    rejected internal host) and the delayed conversation_index.py child for
    our probe project, inside its pre-hub sleep window (see
    tests/probe_indexer_boundary.py for why that window is always >0s).
    """

    def __init__(self, codex_pid: int, project_name: str, before_pids: set[int]):
        super().__init__(daemon=True)
        self.codex_pid = codex_pid
        self.project_name = project_name
        self.before_pids = before_pids
        self.actions: list[dict] = []
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        killed: set[int] = set()
        while not self._stop.is_set():
            try:
                table = snapshot_processes()
                desc = descendants(self.codex_pid, table) | {
                    p["ProcessId"] for p in table
                    if p["ProcessId"] not in self.before_pids
                    and p.get("CommandLine") and "conversation_index.py" in p["CommandLine"]
                    and self.project_name in p["CommandLine"]
                }
                by_pid = {p["ProcessId"]: p for p in table}
                for pid in desc - killed:
                    proc = by_pid.get(pid)
                    if not proc:
                        continue
                    name = (proc.get("Name") or "").lower()
                    cmdline = proc.get("CommandLine") or ""
                    is_disallowed_host = name in DISALLOWED_NAMES
                    is_indexer_child = "conversation_index.py" in cmdline and self.project_name in cmdline
                    if is_disallowed_host or is_indexer_child:
                        ok = kill_pid(pid)
                        killed.add(pid)
                        self.actions.append({
                            "pid": pid, "name": proc.get("Name"), "reason":
                            "disallowed_host" if is_disallowed_host else "indexer_child",
                            "killed": ok,
                        })
            except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
                pass
            time.sleep(0.3)


def read_state_rows(state_dir: Path) -> list[dict]:
    """Rows keyed by session_id in the JSON object; fold the key back in --
    session_state.upsert stores it as the dict key, not a row field."""
    path = state_dir / "sessions-state.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, dict):
        return list(data)
    return [{"session_id": sid, **row} for sid, row in data.items()]


def poll_state_for_working(state_dir: Path, deadline: float, observed: list[dict]) -> None:
    """Background: record every distinct status seen for our project's row
    until the turn completes, so we can show UserPromptSubmit ("working")
    was observed before Stop ("needs-you") -- an independent installed-global
    side effect, not authored by this probe."""
    seen_statuses: set[str] = set()
    while time.time() < deadline:
        for row in read_state_rows(state_dir):
            key = (row.get("status"), row.get("session_id"))
            if key not in seen_statuses:
                seen_statuses.add(key)
                observed.append(dict(row))
        time.sleep(0.2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="Installed subscription model slug")
    args = parser.parse_args()

    codex = shutil.which("codex")
    if os.name != "nt" or not codex:
        parser.error("Requires Windows and installed Codex")

    root = Path(tempfile.mkdtemp(prefix="capture783_global_stop_"))
    print(f"Evidence root: {root}", flush=True)
    env = dict(os.environ)
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL", "PYTHONIOENCODING", "GIT_OPTIONAL_LOCKS"):
        env.pop(key, None)
    state_dir = root / "state"
    projects_toml = root / "projects.toml"
    env.update(PYTHONUTF8="1", CLAUDE_HOOKS_STATE_DIR=str(state_dir),
               CLAUDE_HOOKS_PROJECTS_TOML=str(projects_toml),
               APP_LAUNCHER_SESSION_ID="", APP_LAUNCHER_AGENT="", FLEET_CONTEXT_FILTER_MODE="off")

    version = subprocess.run([codex, "--version"], capture_output=True, text=True,
                             timeout=15, check=True, creationflags=NO_WINDOW).stdout.strip()
    auth = subprocess.run([codex, "login", "status"], env=env, capture_output=True,
                          text=True, timeout=15, check=True, creationflags=NO_WINDOW)
    auth_status = (auth.stdout + auth.stderr).strip()
    assert auth_status == "Logged in using ChatGPT", "Saved subscription auth not confirmed"

    project = root / "project"
    project.mkdir()
    project_name = "probe783global"
    projects_toml.write_text(
        f'[{project_name}]\ncwd_prefix = {json.dumps(project.as_posix())}\n'
        'capture = true\ncapture_harnesses = ["codex"]\ncapture_routing = "flat"\n',
        encoding="utf-8")
    assert not (project / ".codex").exists(), "no project-level hook config must exist"

    report = {"status": "unknown", "root": str(root), "version": version,
              "auth_status": auth_status, "requested_model": args.model}
    expected = "CAPTURE783_GLOBAL 51"
    prompt = f"CAPTURE783_GLOBAL: Reply exactly {expected}. Do not use tools, agents, skills, files or network."
    argv = [codex, "exec", "--json", "--approve-for-me", "--enable", "hooks",
            "--dangerously-bypass-hook-trust", "-m", args.model,
            "-c", f'projects."{project.as_posix()}".trust_level="trusted"',
            "-c", 'forced_login_method="chatgpt"', "-c", 'model_provider="openai"',
            "-c", "project_doc_max_bytes=0", "-c", "check_for_update_on_startup=false",
            "-c", "mcp_servers.node_repl.enabled=false",
            "-c", "mcp_servers.openaiDeveloperDocs.enabled=false",
            "--skip-git-repo-check", "-C", str(project), "-"]
    (root / "argv.json").write_text(json.dumps(argv), encoding="utf-8")

    before_table = snapshot_processes()
    before_pids = {p["ProcessId"] for p in before_table}
    observed_states: list[dict] = []

    try:
        with (root / "stream.jsonl").open("w", encoding="utf-8") as out, \
             (root / "stderr.log").open("w", encoding="utf-8") as err:
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=out, stderr=err,
                                    text=True, encoding="utf-8", env=env, cwd=project,
                                    creationflags=NO_WINDOW)
            watcher = BoundaryWatcher(proc.pid, project_name, before_pids)
            watcher.start()
            state_thread = threading.Thread(
                target=poll_state_for_working, args=(state_dir, time.time() + 180, observed_states),
                daemon=True)
            state_thread.start()
            try:
                proc.stdin.write(prompt)
                proc.stdin.close()
                returncode = proc.wait(timeout=180)
            finally:
                # Give the indexer/host watcher a few more seconds after exit
                # to catch anything spawned right at Stop before we tear down.
                time.sleep(3)
                watcher.stop()
                watcher.join(timeout=5)

        events = [json.loads(line) for line in
                  (root / "stream.jsonl").read_text(encoding="utf-8").splitlines()]
        assert returncode == 0, f"CLI exit {returncode}"
        assert sum(e["type"] == "turn.completed" for e in events) == 1, "No unique terminal turn"
        assert not any(e["type"] in ("turn.failed", "error") for e in events), "Native turn failure"
        items = [e["item"] for e in events if e["type"] == "item.completed"]
        assert all(i["type"] in ("agent_message", "reasoning", "error") for i in items), "Unexpected tool use"
        assert [i["text"] for i in items if i["type"] == "agent_message"] == [expected]
        thread_id = next(e["thread_id"] for e in events if e["type"] == "thread.started")

        report["boundary_actions"] = watcher.actions
        report["observed_state_rows"] = observed_states
        report["disallowed_host_seen"] = any(a["reason"] == "disallowed_host" for a in watcher.actions)

        # A recorded "killed": false can mean the process had already exited on
        # its own before Stop-Process ran (fine) or that termination genuinely
        # failed while it was still alive (not fine) -- don't trust the flag,
        # re-check every acted-on pid's actual liveness now, after the run.
        still_alive = {p["ProcessId"] for p in snapshot_processes()}
        report["surviving_owned_pids"] = [a["pid"] for a in watcher.actions if a["pid"] in still_alive]
        report["all_owned_processes_terminal"] = not report["surviving_owned_pids"]

        statuses_for_session = {row.get("status") for row in observed_states if row.get("session_id") == thread_id}
        report["userpromptsubmit_observed"] = "working" in statuses_for_session

        captures = list((project / "conversations").glob("*.md"))
        report["captures_found"] = len(captures)
        if captures:
            text = captures[0].read_text(encoding="utf-8")
            header = cc.parse_capture_header(text)
            report["capture_header"] = header
            report["capture_matches_session"] = header.get("sid") == thread_id and header.get("agent") == "codex"
            report["capture_contains_marker"] = expected in text
        else:
            report["capture_matches_session"] = False
            report["capture_contains_marker"] = False

        # A disallowed host appearing is not itself a failure -- #783's acceptance
        # criteria accept either full suppression or a reviewed controlled
        # execution. What matters is that nothing it spawned outlives this run.
        proof_complete = (
            report["captures_found"] == 1 and report["capture_matches_session"]
            and report["capture_contains_marker"] and report["userpromptsubmit_observed"]
            and report["all_owned_processes_terminal"]
        )
        report["status"] = "PASS" if proof_complete else "INCONCLUSIVE"
        if not proof_complete:
            report["limits"] = (
                "One or more required signals were absent, or an owned process "
                "outlived this run -- see surviving_owned_pids. Treat as "
                "blocked/unknown per #783's acceptance criteria, not a pass."
            )
        elif report["disallowed_host_seen"]:
            report["limits"] = (
                "mcp_servers.node_repl.enabled=false does NOT prevent the host from "
                "spawning (reproduces the symptom #783 originally reported); the "
                "boundary here is a live watcher that kills it on sight, verified "
                "terminal above -- not config-level suppression. No supported "
                "suppression mechanism was found for this Codex CLI version."
            )
    finally:
        (root / "evidence.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
