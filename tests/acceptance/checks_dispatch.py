"""`hook_dispatch.py` answers exactly as its hooks' own processes do (fleet-config#1274).

Two halves:

1. **Differential.** Every hook-matrix case for a shell `PreToolUse` hook,
   Claude- and Grok-shaped, is replayed through `hook_dispatch.py <hook>` and
   must match the hook's own process byte for byte: exit code, stdout and
   stderr.
2. **Folding.** The full Bash list run in one process: a refusal wins and drops
   the rewrite, refusals combine, a nudge rides along with a rewrite, a lone
   rewrite is byte-identical to `context_filter_hook`'s own, the order of names
   changes nothing, a missing module fails open for itself alone, and non-ASCII
   survives both stdin and stderr as UTF-8.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any, Dict, List, Tuple

from acceptance.hook_cases.inproc import run_in_process
from acceptance.hook_matrix import FAKE_GHP, matrix_cases
from acceptance.shared import HOOKS, PYTHON, REPO, _Checker, hook_env

# The live Bash wiring's hooks, guards first (settings.template.json order),
# and the PowerShell subset.
BASH_HOOKS = [
    "pre_commit_no_ai_trailer", "gh_body_file_guard", "bash_cmdexe_syntax_guard",
    "bash_windows_path_guard", "secret_scan_guard", "safe_kill_guard",
    "venv_discipline", "context_filter_hook",
]
POWERSHELL_HOOKS = [
    "pre_commit_no_ai_trailer", "secret_scan_guard", "safe_kill_guard", "venv_discipline",
    "context_filter_hook",
]

Result = Tuple[int, str, str]


def standalone(hook: str, payload: Dict[str, Any], mode: str = "off") -> Result:
    """The hook's own answer, as its process would give it, run in-process."""
    return run_in_process(hook, payload, {"FLEET_CONTEXT_FILTER_MODE": mode})


def dispatch(hooks: List[str], payload: Dict[str, Any], mode: str = "off") -> Result:
    """`hook_dispatch.py <hooks>`'s answer, run in-process."""
    return run_in_process("hook_dispatch", payload, {"FLEET_CONTEXT_FILTER_MODE": mode}, args=hooks)


def dispatch_spawn(hooks: List[str], payload: Dict[str, Any]) -> Tuple[int, bytes, bytes]:
    """`hook_dispatch.py <hooks>` as a real process, answering in raw bytes: the spawn
    that proves its wiring (UTF-8 stdin in, UTF-8 stderr out, over real pipes). The
    secret-read suite, which is not in-process, calls it too."""
    res = subprocess.run(
        [PYTHON, str(HOOKS / "hook_dispatch.py"), *hooks],
        input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        capture_output=True, timeout=30, env=hook_env({"FLEET_CONTEXT_FILTER_MODE": "off"}),
    )
    return res.returncode, res.stdout, res.stderr


def bash(command: str, **extra: Any) -> Dict[str, Any]:
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(REPO),
            "session_id": "dispatch-acceptance", "tool_input": {"command": command}, **extra}


def _hook_dispatch_checks() -> Tuple[int, int]:
    check = _Checker()

    cases, tmp = matrix_cases()
    try:
        for label, hook, payload, _expected in cases:
            if hook not in BASH_HOOKS:
                continue
            want, got = standalone(hook, payload), dispatch([hook], payload)
            check(f"hook_dispatch: matches its own process: {label}", got == want,
                  f"standalone={want!r} dispatch={got!r}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # A refusal wins, and every guard saw the original command: the wrappable
    # pipe is refused, and no rewritten command reaches stdout.
    code, out, err = dispatch(BASH_HOOKS, bash("cmd.exe /c dir | head -5"), "rewrite")
    check("hook_dispatch: a refusal drops context_filter_hook's rewrite",
          code == 2 and out == "" and "cmd.exe /c" in err, f"{code} {out!r} {err!r}")

    code, out, err = dispatch(BASH_HOOKS, bash("cmd.exe /c dir && git push --force origin main"))
    check("hook_dispatch: two refusals both reach stderr, in order",
          code == 2 and err.find("cmd.exe /c") < err.find("git push --force") and err.count("Blocked:") == 2,
          f"{code} {err!r}")

    payload = bash("git log --oneline | head -5")
    code, out, err = dispatch(BASH_HOOKS, payload, "rewrite")
    want = standalone("context_filter_hook", payload, "rewrite")
    check("hook_dispatch: a lone rewrite is byte-identical to context_filter_hook's own",
          (code, out) == want[:2] and "updatedInput" in out, f"{code} {out!r} want {want!r}")

    payload = bash("git log %USERPROFILE% | head -5")
    code, out, _err = dispatch(BASH_HOOKS, payload, "rewrite")
    nudge = json.loads(standalone("bash_cmdexe_syntax_guard", payload)[1])
    rewrite = json.loads(standalone("context_filter_hook", payload, "rewrite")[1])
    merged = json.loads(out) if code == 0 and out else {}
    check("hook_dispatch: a nudge rides in the rewrite envelope's systemMessage",
          merged == {**rewrite, "systemMessage": nudge["systemMessage"]}, f"{code} {out!r}")

    for label, command in (("refusal + rewrite", "cmd.exe /c dir | head -5"),
                           ("nudge + rewrite", "git log %USERPROFILE% | head -5")):
        forward = dispatch(BASH_HOOKS, bash(command), "rewrite")
        backward = dispatch(BASH_HOOKS[::-1], bash(command), "rewrite")
        check(f"hook_dispatch: hook order changes nothing ({label})", forward == backward,
              f"{forward!r} vs {backward!r}")

    payload = bash(r"ls E:\automation\café")
    want, got = standalone("bash_windows_path_guard", payload), dispatch(BASH_HOOKS, payload)
    # The one real process: UTF-8 has to survive actual pipes, which a call inside
    # this interpreter cannot show.
    spawned = dispatch_spawn(BASH_HOOKS, payload)
    check("hook_dispatch: non-ASCII survives stdin and the refusal's UTF-8 stderr (real process)",
          spawned[0] == 2 and want[2].strip().encode("utf-8") in spawned[2]
          and "café".encode("utf-8") in spawned[2],
          f"{spawned!r}")
    check("hook_dispatch: non-ASCII survives stdin and the refusal's stderr, in-process",
          got[0] == 2 and want[2] in got[2] and "café" in got[2], f"{got!r}")

    code, out, err = dispatch(BASH_HOOKS, bash("git status"))
    check("hook_dispatch: an innocuous command is a silent allow", (code, out, err) == (0, "", ""),
          f"{code} {out!r} {err!r}")

    code, out, err = dispatch(POWERSHELL_HOOKS, {**bash("Stop-Process -Name python -Force"),
                                                 "tool_name": "PowerShell"})
    check("hook_dispatch: the PowerShell list refuses a blanket python kill", code == 2, f"{code} {err!r}")

    trailer = "Claude-Session: https://claude.ai/code/session_x"
    for tool, hooks in (("Bash", BASH_HOOKS), ("PowerShell", POWERSHELL_HOOKS)):
        code, out, err = dispatch(hooks, {**bash(f'git commit -m "fix: x\n\n{trailer}"'), "tool_name": tool})
        check(f"hook_dispatch: the {tool} list refuses a session-link trailer (fleet-config#1288)",
              code == 2 and "AI attribution" in err, f"{code} {err!r}")

    code, out, err = dispatch(["no_such_hook"], bash("git status"))
    check("hook_dispatch: a missing module alone is a non-blocking error (exit 1)",
          code == 1 and "no_such_hook" in err, f"{code} {err!r}")
    code, _out, _err = dispatch(["no_such_hook", "safe_kill_guard"], bash("git push --force origin main"))
    check("hook_dispatch: a missing module never disarms the guards beside it", code == 2, str(code))

    grok = {"hookEventName": "pre_tool_use", "toolName": "run_terminal_command",
            "toolInput": {"command": f'gh issue create --title t --body "key {FAKE_GHP}"'},
            "cwd": str(REPO), "sessionId": "grok-acceptance"}
    code, out, _err = dispatch(BASH_HOOKS, grok)
    check("hook_dispatch: a Grok refusal keeps its stdout deny decision",
          code == 2 and json.loads(out or "{}").get("decision") == "deny", f"{code} {out!r}")

    return check.failures, check.total
