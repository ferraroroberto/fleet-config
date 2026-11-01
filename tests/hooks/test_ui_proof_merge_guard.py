"""Tests for hooks/ui_proof_merge_guard.py: `gh pr merge` on a UX diff needs its proof page.

Pure logic (which commands are merges, which repo and PR they name, the
directory they run in), the gate-answer mapping (only an explicit pass
passes; a refusal quotes `NEED=`; a crash, timeout, garbled answer or unknown
state refuses with its own message), then the real hook process driven with
sample `PreToolUse` payloads, directly and through `hook_dispatch.py`: a
non-merge or non-UX merge passes, an unknown state refuses, and the owner's
bypass lets the merge through and is logged. The gate's own verdicts are
covered by tests/web_review/test_ui_proof_gate.py.

Run: E:/automation/fleet-config/.venv/Scripts/python.exe tests/hooks/test_ui_proof_merge_guard.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent.parent
HOOKS = REPO / "hooks"
sys.path.insert(0, str(HOOKS))
import ui_proof_merge_guard as guard  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import init_repo  # noqa: E402

_h = CheckHarness()
check = _h.check
PYTHON = sys.executable


def words(cmd: str):
    return guard._lib.shell_tokens(cmd, unquote=True)


# ---- parse_merge: which commands are merges, and what they name ----
_PARSE = [
    ("gh pr merge 2030 --squash --delete-branch", ("2030", None)),
    ("gh pr merge --squash -R acme/widget 12", ("12", "acme/widget")),
    ("gh -R acme/widget pr merge 12 --auto", ("12", "acme/widget")),
    ('gh pr merge --repo=acme/widget --body "ship it now" 5', ("5", "acme/widget")),
    ("gh pr merge -t subject -d https://github.com/acme/widget/pull/9", ("https://github.com/acme/widget/pull/9", None)),
    ("gh pr merge", (None, None)),
    ("GH_DEBUG=1 gh.exe pr merge 4 -s", ("4", None)),
    ("gh pr view 12", None),
    ("gh issue merge 12", None),
    ("echo gh pr merge 1", None),
    ("git log --grep gh-pr-merge", None),
]
for cmd, want in _PARSE:
    got = guard.parse_merge(words(cmd))
    check(got == want, f"parse_merge({cmd!r}) == {want} — got {got}")

# ---- merge_calls: the directory a merge runs in ----
tmp = Path(tempfile.mkdtemp(prefix="merge_guard_"))
try:
    sub = tmp / "sub"
    sub.mkdir()
    calls = guard.merge_calls(f'cd "{sub}" && gh pr merge 3 --squash', tmp)
    check(len(calls) == 1 and calls[0].cwd == sub and calls[0].selector == "3",
          f"cd before the merge moves its directory — got {calls}")
    calls = guard.merge_calls(f"Set-Location '{sub}'; gh pr merge 3", tmp)
    check(len(calls) == 1 and calls[0].cwd == sub, f"PowerShell Set-Location is followed — got {calls}")
    calls = guard.merge_calls("cd $REPO && gh pr merge 3", tmp)
    check(len(calls) == 1 and calls[0].cwd is None, f"an unreadable cd makes the directory unknown — got {calls}")
    calls = guard.merge_calls("gh pr merge 1 --squash && gh pr merge 2 -R acme/other", tmp)
    check([(c.selector, c.slug, c.cwd) for c in calls] == [("1", None, tmp), ("2", "acme/other", tmp)],
          f"every merge in a chain is gated — got {calls}")
    check(guard.merge_calls("git status && npm test", tmp) == [], "no merge, no call")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ---- refusal: only an explicit pass passes ----
call = guard.MergeCall(Path("."), "7", None, "gh pr merge 7 --squash")
check(guard.refusal(call, 0, {"GATE": "pass"}, "") is None, "GATE=pass exit 0 passes")
text = guard.refusal(call, 1, {"GATE": "refuse", "PR": "acme/widget#7",
                               "NEED": "no .proof/7/index.html; then `ui_proof build . --pr 7`",
                               "DETAIL": "touched: app/x.css"}, "")
check(text is not None and "without its visual proof" in text and "`ui_proof build . --pr 7`" in text
      and "ux-conformance-gate.md" in text and "app/x.css" in text,
      f"a refusal quotes the gate's NEED and touched files, and points at the steps — got {text!r}")
for code, fields, err, label in [
        (3, {"GATE": "unknown", "REASON": "gh pr view failed"}, "", "GATE=unknown"),
        (None, {}, "the gate did not answer within 50 s", "a timeout"),
        (2, {}, "can't open file", "a gate that cannot start"),
        (0, {}, "", "exit 0 with no GATE line"),
        (1, {"GATE": "pass"}, "", "a mismatched exit and GATE line")]:
    text = guard.refusal(call, code, fields, err)
    check(text is not None and "proof state unknown" in text and guard.BYPASS_ENV in text,
          f"{label} refuses as unknown, naming the bypass — got {text!r}")


# ---- the hook process, driven with sample payloads ----
def env_for(state: Path, bypass: Optional[str] = None) -> Dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if k not in (guard.BYPASS_ENV, "APP_LAUNCHER_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT")}
    env.update({"CLAUDE_HOOKS_STATE_DIR": str(state), "PYTHONUTF8": "1"})
    if bypass is not None:
        env[guard.BYPASS_ENV] = bypass
    return env


def drive(command: str, cwd: Path, env: Dict[str, str], tool: str = "Bash",
          dispatch: bool = False) -> Tuple[int, str, str]:
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": str(cwd),
               "session_id": "test-session", "tool_input": {"command": command}}
    script = [str(HOOKS / "hook_dispatch.py"), "ui_proof_merge_guard"] if dispatch \
        else [str(HOOKS / "ui_proof_merge_guard.py")]
    res = subprocess.run([PYTHON, *script], input=json.dumps(payload), capture_output=True, text=True,
                         encoding="utf-8", timeout=90, env=env)
    return res.returncode, res.stdout, res.stderr


def make_repo(claude_md: str, origin: str) -> Path:
    repo = init_repo()
    (repo / "CLAUDE.md").write_text(claude_md, encoding="utf-8")
    for args in (["add", "-A"], ["commit", "-q", "-m", "base"], ["remote", "add", "origin", origin]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    return repo


UX_MD = "# P\n\n## UX surface\n- design spec applies: yes\n- paths:\n  - app/**/*.css\n"
state = Path(tempfile.mkdtemp(prefix="merge_guard_state_"))
plain = make_repo("# P\n", "https://github.com/acme/widget.git")
ux_no_gh = make_repo(UX_MD, "file:///not-github")
try:
    env = env_for(state)
    code, _o, err = drive("git status --short", plain, env)
    check(code == 0, f"a non-merge command passes — got {code} {err[:200]}")
    code, _o, err = drive("gh pr merge 12 --squash", plain, env, tool="Edit")
    check(code == 0, "a non-shell tool passes")
    code, _o, err = drive("gh pr merge 12 --squash --delete-branch", plain, env)
    check(code == 0, f"a merge in a repo with no UX surface passes (no gh call) — got {code} {err[:300]}")
    code, _o, err = drive("gh pr merge 12 --squash", plain, env, tool="PowerShell")
    check(code == 0, f"the same from PowerShell passes — got {code} {err[:300]}")

    code, _o, err = drive("gh pr merge 12 --squash", ux_no_gh, env)
    check(code == 2 and "proof state unknown" in err and "GitHub origin" in err,
          f"a UX repo whose PR cannot be looked up refuses as unknown — got {code} {err[:300]}")
    code, _o, err = drive("gh pr merge 12 --squash", ux_no_gh, env, dispatch=True)
    check(code == 2 and "proof state unknown" in err,
          f"the refusal holds through hook_dispatch — got {code} {err[:300]}")
    code, _o, err = drive("cd $SOMEWHERE && gh pr merge 12", plain, env)
    check(code == 2 and "cannot read" in err, f"an unknown merge directory refuses — got {code} {err[:300]}")

    code, out, err = drive("gh pr merge 12 --squash", ux_no_gh, env_for(state, "gate broken, owner merging"))
    log = state / guard.BYPASS_LOG
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.is_file() else []
    check(code == 0 and guard.BYPASS_ENV in (out + err),
          f"the owner's bypass lets the merge through and says so — got {code} {(out + err)[:300]}")
    check(len(rows) == 1 and rows[0]["reason"] == "gate broken, owner merging"
          and rows[0]["merges"] == ["gh pr merge 12 --squash"] and rows[0]["session_id"] == "test-session",
          f"every bypass is logged with its reason — got {rows}")
    code, _o, err = drive(f"{guard.BYPASS_ENV}=1 gh pr merge 12", ux_no_gh, env)
    check(code == 2, f"a bypass typed into the command itself does not count — got {code}")
finally:
    for path in (state, plain, ux_no_gh):
        shutil.rmtree(path, ignore_errors=True)


# ---- in process: a gate refusal blocks, and a non-merge never spawns the gate ----
def run_main(command: str, gate_answer) -> Tuple[int, str, list]:
    seen = []

    def fake_gate(call):
        seen.append(call)
        return gate_answer

    saved_gate, saved_stdin = guard.run_gate, sys.stdin
    guard.run_gate = fake_gate
    sys.stdin = io.TextIOWrapper(io.BytesIO(json.dumps(
        {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(REPO),
         "tool_input": {"command": command}}).encode("utf-8")), encoding="utf-8")
    err = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            guard.main()
    except SystemExit as exc:
        code = exc.code or 0
    finally:
        guard.run_gate, sys.stdin = saved_gate, saved_stdin
    return code, err.getvalue(), seen


saved_bypass = os.environ.pop(guard.BYPASS_ENV, None)
try:
    code, err, seen = run_main("gh pr merge 7 --squash",
                               (1, {"GATE": "refuse", "PR": "acme/widget#7", "NEED": "no .proof/7/index.html"}, ""))
    check(code == 2 and "no .proof/7/index.html" in err, f"a gate refusal blocks the merge — got {code} {err[:200]}")
    code, err, seen = run_main("gh pr merge 7 --squash", (0, {"GATE": "pass"}, ""))
    check(code == 0 and len(seen) == 1, f"a gate pass allows the merge — got {code} {err[:200]}")
    code, err, seen = run_main("git push -u origin HEAD && gh pr create --fill", (1, {"GATE": "refuse"}, ""))
    check(code == 0 and seen == [], "a command with no merge never spawns the gate")
finally:
    if saved_bypass is not None:
        os.environ[guard.BYPASS_ENV] = saved_bypass


_h.report_and_exit("ui_proof_merge_guard")
