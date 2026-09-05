"""Opt-in native Stop capture proof; not part of the offline acceptance gate.

Uses normal saved ChatGPT auth and reviewed disposable project hooks. Does not
edit installed configuration. See docs/conversation-capture.md before running.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hooks"))
from _lib import NO_WINDOW, run_git


def wire_project(project: Path) -> int:
    """Wire an observer around the real entry point in a disposable project."""
    hook_dir = project / ".codex" / "hooks"
    hook_dir.mkdir(parents=True)
    run_git(["init", "-q", str(project)], check=True, timeout=15)
    (project / ".gitignore").write_text("conversations/\n", encoding="utf-8")
    (project / ".codex" / "config.toml").write_text("", encoding="utf-8")
    wrapper = hook_dir / "capture_observer.py"
    wrapper.write_text('''import io, json, os, sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, SOURCE)
import conversation_capture as cc
import transcript_readers as tr
raw = json.load(sys.stdin)
project = Path(__file__).resolve().parents[2]
# Only this reviewed wrapper opts in. Installed global capture stays inert,
# including when the probe is repeated after this branch ships.
os.environ["CLAUDE_HOOKS_PROJECTS_TOML"] = str(project.parent / "projects.toml")
observations = project / "observations.jsonl"
row = {key: raw.get(key) for key in (
    "hook_event_name", "session_id", "transcript_path", "cwd", "model",
    "turn_id", "permission_mode", "stop_hook_active", "last_assistant_message")}
if raw["hook_event_name"] == "Stop":
    # Establish positive PROJECT hook loading before interpreting Stop.
    prior = [json.loads(line) for line in observations.read_text(encoding="utf-8").splitlines()]
    assert any(p["hook_event_name"] == "UserPromptSubmit" and
               p["session_id"] == raw["session_id"] and p["turn_id"] == raw["turn_id"] for p in prior)
    source = tr.read_transcript(Path(raw["transcript_path"]), harness="codex", session_id=raw["session_id"])
    assert source.status == "ok" and source.messages, source.detail
    assert all("CAPTURE753" in text and len(text) < 500 for _, text in source.messages)
    row.update(source_status=source.status, messages=source.messages,
               parent_session_id=source.parent_session_id)
    # Only detached digest scheduling is replaced: no model calls or orphan children.
    with patch.object(cc, "_trigger_delayed_index") as digest:
        with patch.object(sys, "stdin", io.StringIO(json.dumps(raw))):
            row["native_capture_exit"] = cc.main()
        row["digest_trigger_count"] = digest.call_count
        files = list((project / "conversations").glob("*.md"))
        snapshot = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files}
        # This second invocation is explicitly REPLAY, following native capture.
        with patch.object(sys, "stdin", io.StringIO(json.dumps(raw))):
            row["repeat_capture_exit"] = cc.main()
        row["repeat_idempotent"] = (digest.call_count == row["digest_trigger_count"] and
            snapshot == {p: (p.read_bytes(), p.stat().st_mtime_ns)
                         for p in (project / "conversations").glob("*.md")})
    row["captures"] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        header = cc.parse_capture_header(text)
        if header.get("sid") == raw["session_id"]:
            assert header["agent"] == "codex"
            assert header.get("parent_sid", "") == source.parent_session_id
            offset = 0
            for role, message in source.messages:
                rendered = ("**You**: " if role == "user" else "**Codex**: ") + message
                offset = text.index(rendered, offset) + len(rendered)
            row["captures"].append({"path": str(path), "header": header})
with observations.open("a", encoding="utf-8") as log:
    log.write(json.dumps(row) + "\\n")
'''.replace("SOURCE", repr(str(ROOT / "hooks"))), encoding="utf-8")
    handler = {"hooks": [{"type": "command", "timeout": 15,
                           "command": subprocess.list2cmdline([sys.executable, str(wrapper)])}]}
    (project / ".codex" / "hooks.json").write_text(json.dumps({
        "hooks": {event: [handler] for event in ("UserPromptSubmit", "Stop")},
    }), encoding="utf-8")
    return 0


def run_turn(codex: str, project: Path, env: dict[str, str], model: str,
             label: str, expected: str, mode: str = "", sid: str = "") -> dict:
    """Collect one native process and require terminal/model/hook evidence."""
    argv = [codex, "exec", "--json", "--approve-for-me", "--enable", "hooks",
            "--dangerously-bypass-hook-trust", "-m", model,
            "-c", f'projects."{project.as_posix()}".trust_level="trusted"',
            "-c", 'forced_login_method="chatgpt"', "-c", 'model_provider="openai"',
            "-c", "project_doc_max_bytes=0", "-c", "mcp_servers.node_repl.enabled=false",
            "-c", "mcp_servers.openaiDeveloperDocs.enabled=false", "-C", str(project)]
    if mode:
        argv.extend([mode, sid])
    argv.append("-")
    (project / f"{label}-argv.json").write_text(json.dumps(argv), encoding="utf-8")
    prompt = f"{expected.split()[0]}: Reply exactly {expected}. Do not use tools, agents, skills, files or network."
    with (project / f"{label}-stream.jsonl").open("w", encoding="utf-8") as out, (
            project / f"{label}-stderr.log").open("w", encoding="utf-8") as err:
        result = subprocess.run(argv, input=prompt, text=True, encoding="utf-8", env=env,
                                cwd=project, stdout=out, stderr=err, timeout=180,
                                creationflags=NO_WINDOW)
    events = [json.loads(line) for line in
              (project / f"{label}-stream.jsonl").read_text(encoding="utf-8").splitlines()]
    assert result.returncode == 0, f"{label}: CLI exit {result.returncode}"
    assert sum(e["type"] == "turn.completed" for e in events) == 1, "No unique terminal turn"
    assert not any(e["type"] in ("turn.failed", "error") for e in events), "Native turn failure"
    items = [e["item"] for e in events if e["type"] == "item.completed"]
    assert all(i["type"] in ("agent_message", "reasoning", "error") for i in items), "Unexpected tool use"
    assert all("hook failed" not in i.get("message", "").lower() for i in items), "Hook failure"
    assert [i["text"] for i in items if i["type"] == "agent_message"] == [expected]
    thread = next(e["thread_id"] for e in events if e["type"] == "thread.started")
    rows = [json.loads(line) for line in
            (project / "observations.jsonl").read_text(encoding="utf-8").splitlines()]
    submit, stop = rows[-2:]
    assert [submit["hook_event_name"], stop["hook_event_name"]] == ["UserPromptSubmit", "Stop"]
    assert submit["session_id"] == stop["session_id"] == thread
    assert submit["turn_id"] == stop["turn_id"]
    assert Path(stop["cwd"]).resolve() == project.resolve()
    assert stop["model"] == model and stop["last_assistant_message"] == expected
    assert stop["source_status"] == "ok" and stop["messages"][-2:] == [["user", prompt], ["assistant", expected]]
    assert stop["native_capture_exit"] == stop["repeat_capture_exit"] == 0
    assert stop["repeat_idempotent"]
    return {"label": label, "exit_code": result.returncode, "terminal_turn": True, **stop}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="Installed subscription model slug")
    args = parser.parse_args()
    codex = shutil.which("codex")
    if os.name != "nt" or not codex:
        parser.error("Requires Windows and installed Codex")
    root = Path(tempfile.mkdtemp(prefix="capture753_native_stop_"))
    print(f"Evidence root: {root}", flush=True)
    env = dict(os.environ)
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL", "PYTHONIOENCODING", "GIT_OPTIONAL_LOCKS"):
        env.pop(key, None)
    env.update(PYTHONUTF8="1", CLAUDE_HOOKS_STATE_DIR=str(root / "state"),
               CLAUDE_HOOKS_PROJECTS_TOML=str(root / "disabled-projects.toml"),
               APP_LAUNCHER_SESSION_ID="", APP_LAUNCHER_AGENT="", FLEET_CONTEXT_FILTER_MODE="off")
    version = subprocess.run([codex, "--version"], capture_output=True, text=True,
                             timeout=15, check=True, creationflags=NO_WINDOW).stdout.strip()
    auth = subprocess.run([codex, "login", "status"], env=env, capture_output=True,
                          text=True, timeout=15, check=True, creationflags=NO_WINDOW)
    auth_status = (auth.stdout + auth.stderr).strip()
    assert auth_status == "Logged in using ChatGPT", "Saved subscription auth not confirmed"
    project, unrelated = root / "project", root / "unrelated"
    wire_project(project)
    wire_project(unrelated)
    (root / "disabled-projects.toml").write_text("", encoding="utf-8")
    (root / "projects.toml").write_text(
        '[probe]\ncwd_prefix = ' + json.dumps(project.as_posix()) +
        '\ncapture = true\ncapture_harnesses = ["codex"]\n', encoding="utf-8")
    report = {"status": "unknown", "root": str(root), "version": version,
              "auth_status": auth_status, "requested_model": args.model, "runs": []}
    try:
        runs = report["runs"]
        runs.append(run_turn(codex, project, env, args.model, "new", "CAPTURE753_ALPHA 42"))
        sid = runs[0]["session_id"]
        assert len(runs[0]["messages"]) == 2 and not runs[0]["parent_session_id"]
        runs.append(run_turn(codex, project, env, args.model, "resume", "CAPTURE753_BETA 63", "resume", sid))
        assert runs[1]["session_id"] == sid and len(runs[1]["messages"]) == 4
        assert runs[1]["messages"][:2] == runs[0]["messages"]
        runs.append(run_turn(codex, project, env, args.model, "fork", "CAPTURE753_FORK 84", "fork", sid))
        assert runs[2]["session_id"] != sid and runs[2]["parent_session_id"] == sid
        assert len(runs[2]["messages"]) == 2
        for run in runs:
            assert len(run["captures"]) == 1 and run["digest_trigger_count"] == 1
        assert runs[0]["captures"][0]["path"] == runs[1]["captures"][0]["path"]
        snapshots = {p: p.read_bytes() for p in (project / "conversations").glob("*.md")}
        assert len(snapshots) == 2
        runs.append(run_turn(codex, unrelated, env, args.model, "excluded", "CAPTURE753_ALPHA 42"))
        assert not runs[3]["captures"] and runs[3]["digest_trigger_count"] == 0
        assert not (unrelated / "conversations").exists()
        command = [sys.executable, str(ROOT / "hooks" / "conversation_search.py"), "--project", "probe"]
        search_env = {**env, "CLAUDE_HOOKS_PROJECTS_TOML": str(root / "projects.toml")}
        for flags in (["--rebuild"], ["--query", "CAPTURE753", "--json"]):
            search = subprocess.run([*command, *flags], env=search_env, capture_output=True, text=True,
                                    encoding="utf-8", timeout=30, check=True, creationflags=NO_WINDOW)
        hits = json.loads(search.stdout)
        assert len(hits) == 2
        assert {hit["resume"] for hit in hits} == {f'codex resume {sid}', f'codex resume {runs[2]["session_id"]}'}
        assert snapshots == {p: p.read_bytes() for p in (project / "conversations").glob("*.md")}
        for path in snapshots:
            run_git(["-C", str(project), "check-ignore", str(path)], check=True, timeout=15)
        (root / "search.json").write_text(search.stdout, encoding="utf-8")
        report.update(status="PASS", search="PASS", originals_preserved=True, ignored=True,
                      unrelated_excluded=True, all_commands_collected=True,
                      limits="Native Stop invokes real capture; repeat is replay. Digest scheduling suppressed. "
                             "Invocation-only hook trust; no persisted trust/install or live digest proof.")
    finally:
        (root / "evidence.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "runs"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
