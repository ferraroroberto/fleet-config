"""Opt-in actual Codex patch/syntax probe; only edits a new disposable repository."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
from _lib import NO_WINDOW, run_git

PATCH = """*** Begin Patch
*** Add File: broken_a.py
+def broken_a(:
*** Add File: broken_b.py
+def broken_b(:
*** Add File: good.py
+VALUE = 1
*** Add File: notes.txt
+harmless
*** Update File: old.py
*** Move to: renamed.py
@@
-VALUE = 0
+VALUE = 2
*** Delete File: deleted.py
*** End Patch"""
FAILED_PATCH = """*** Begin Patch
*** Update File: missing.py
@@
-absent
+def never_written(:
*** End Patch"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--expect-bug", action="store_true", help="Require the pre-fix silent early return")
    args = parser.parse_args()
    root = args.workspace.absolute()
    codex = shutil.which("codex")
    if os.name != "nt" or root.exists() or not codex:
        parser.error("Requires Windows, installed Codex and a new workspace directory")
    hook_dir = root / ".codex" / "hooks"
    hook_dir.mkdir(parents=True)
    if run_git(["init", str(root)], timeout=15).returncode:
        parser.error("disposable git init failed")
    for name in ("old.py", "deleted.py"):
        (root / name).write_text("VALUE = 0\n", encoding="utf-8")
    source = Path(__file__).resolve().parents[1] / "hooks"
    hook = hook_dir / "observe.py"
    hook.write_text('''import io, json, sys
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
sys.path.insert(0, SOURCE)
import _lib, py_syntax_check
raw = json.load(sys.stdin)
payload = _lib.normalize_payload(raw)
_lib._ACTIVE_EVENT = payload.get("hook_event_name")
_lib.read_stdin_json = lambda: payload
compiled = []
original_run = py_syntax_check.subprocess.run
def observe_run(argv, **kwargs):
    if "py_compile" in argv:
        compiled.extend(argv[argv.index("py_compile") + 1:])
    return original_run(argv, **kwargs)
py_syntax_check.subprocess.run = observe_run
out, err = io.StringIO(), io.StringIO()
code = 0
with redirect_stdout(out), redirect_stderr(err):
    try:
        if payload.get("hook_event_name") != "PreToolUse":
            py_syntax_check.main()
    except SystemExit as exc:
        code = exc.code
with (Path(__file__).parents[2] / "observations.jsonl").open("a", encoding="utf-8") as log:
    log.write(json.dumps({"event": payload.get("hook_event_name"),
        "tool_name": payload.get("tool_name"), "tool_input": payload.get("tool_input"),
        "tool_response": payload.get("tool_response"), "agent": _lib.payload_agent(payload),
        "compiled": compiled, "exit_code": code, "stdout": out.getvalue(), "stderr": err.getvalue()}) + "\\n")
sys.stdout.write(out.getvalue())
sys.stderr.write(err.getvalue())
sys.exit(code)
'''.replace("SOURCE", repr(str(source))), encoding="utf-8")
    command = subprocess.list2cmdline([sys.executable, str(hook)])
    (root / ".codex" / "hooks.json").write_text(json.dumps({"hooks": {
        event: [{"matcher": "Edit|Write|MultiEdit", "hooks": [{"type": "command", "command": command, "timeout": 15}]}]
        for event in ("PreToolUse", "PostToolUse", "PostToolUseFailure")}}), encoding="utf-8")
    (root / ".codex" / "config.toml").write_text("model = " + json.dumps(args.model) + "\n", encoding="utf-8")
    prompt = f"""Authorized disposable hook conformance test. Make exactly two apply_patch tool calls, in order, with the exact patches below. Deliberate invalid Python is required for this test. The second patch must fail because missing.py is absent. Do not use any other tools, read files, repair errors, retry, delegate, or edit outside this disposable repository. After both attempts, report tool results and all syntax hook feedback verbatim.
First patch:
{PATCH}
Second patch:
{FAILED_PATCH}
"""
    env = {**os.environ, "PYTHONUTF8": "1", "CLAUDE_HOOKS_STATE_DIR": str(root / "state"), "FLEET_CONTEXT_FILTER_MODE": "off"}
    version = subprocess.run([codex, "--version"], capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW).stdout.strip()
    with (root / "run.jsonl").open("w", encoding="utf-8") as out, (root / "stderr.txt").open("w", encoding="utf-8") as err:
        try:
            proc = subprocess.run([codex, "exec", "--ephemeral", "--json", "--approve-for-me",
                "--dangerously-bypass-hook-trust", "-c", f'projects."{root.as_posix()}".trust_level="trusted"',
                "-m", args.model, "-C", str(root), "-"], input=prompt, text=True, encoding="utf-8",
                stdout=out, stderr=err, env=env, timeout=180, creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            print("CONFORMANCE=unknown reason=Codex timed out after 180 seconds")
            return 1
    observations = root / "observations.jsonl"
    if proc.returncode or not observations.exists():
        print("CONFORMANCE=unknown reason=CLI failed or hook invocation not observed")
        return 1
    rows = [json.loads(line) for line in observations.read_text(encoding="utf-8").splitlines()]
    post = [r for r in rows if r["event"] == "PostToolUse"]
    if not post:
        print("CONFORMANCE=unknown reason=PostToolUse invocation not observed")
        return 1
    compiled = [Path(p).name for r in post for p in r["compiled"]]
    transcript = (root / "run.jsonl").read_text(encoding="utf-8")
    effects = ((root / "broken_a.py").exists() and (root / "broken_b.py").exists()
               and (root / "renamed.py").is_file()
               and (root / "renamed.py").read_text(encoding="utf-8").strip() == "VALUE = 2"
               and not any((root / p).exists() for p in ("old.py", "deleted.py", "missing.py")))
    syntax_ok = (compiled == [] and all(r["exit_code"] == 0 for r in post)) if args.expect_bug else (
        sorted(compiled) == ["broken_a.py", "broken_b.py", "good.py", "renamed.py"]
        and "py_compile:" in transcript and "broken_a.py" in transcript and "broken_b.py" in transcript)
    pre = [r for r in rows if r["event"] == "PreToolUse"]
    attempts_ok = (len(pre) == 2 and [r["tool_input"]["command"] for r in pre] == [PATCH, FAILED_PATCH]
                   and len(post) == 1 and post[0]["tool_input"]["command"] == PATCH)
    failure_seen = "apply_patch verification failed:" in transcript and not (root / "missing.py").exists()
    if not args.expect_bug:
        feedback = json.loads(post[0]["stdout"] or "{}") if len(post) == 1 else {}
        context = feedback.get("hookSpecificOutput", {})
        syntax_ok = (syntax_ok and post[0]["exit_code"] == 0 and not post[0]["stderr"]
                     and context.get("hookEventName") == "PostToolUse"
                     and context.get("additionalContext", "").count("SyntaxError") == 2)
    passed = effects and syntax_ok and attempts_ok and failure_seen and all(r["agent"] == "codex" for r in rows)
    print(json.dumps({"version": version, "conformance": "pass" if passed else "fail", "expect_bug": args.expect_bug,
                      "compiled": compiled, "events": [r["event"] for r in rows], "evidence": str(root)}))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
