"""Unit tests for skills/_lib/retro/digest.py, the counts-and-paths reduction `/retro` reads.

Builds synthetic transcripts in a temp dir; no real session is read. The privacy
checks are the point: no prompt text, tool output or absolute path may reach the
digest, and a private repo's paths become `<private>`.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/audit/test_retro_digest.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
from retro import digest as rd  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

ROOT = Path(tempfile.gettempdir()) / "retro-fixture-root"
CWD = str(ROOT / "demo-repo")
SECRET = "owner typed this sentence"


def user(content, meta=False):
    return {"type": "user", "cwd": CWD, "isMeta": meta, "message": {"role": "user", "content": content}}


def call(tid, name, **args):
    return {"type": "assistant", "cwd": CWD,
            "message": {"role": "assistant", "content": [{"type": "tool_use", "id": tid, "name": name, "input": args}]}}


def result(tid, text, error=False):
    return user([{"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": error}])


def write(records, name="s.jsonl"):
    folder = Path(tempfile.mkdtemp())
    path = folder / name
    path.write_text("\n".join(json.dumps(r) for r in records) + "\nnot json\n", encoding="utf-8")
    return path


target = str(ROOT / "demo-repo" / "app" / "main.py")
records = [
    user(f"<command-message>issue-yolo</command-message>\n<command-name>/issue-yolo</command-name>"),
    user([{"type": "text", "text": "skill body"}], meta=True),
    user(SECRET),
    *[r for i in range(3) for r in (call(f"r{i}", "Read", file_path=target), result(f"r{i}", "body " + SECRET))],
    call("g1", "Grep", pattern="x"), result("g1", "x" * rd.LARGE_RESULT_CHARS),
    call("e1", "Edit", file_path=target, old_string="a", new_string="b"),
    result("e1", "<tool_use_error>String to replace not found in file.</tool_use_error>", error=True),
    call("e2", "Edit", file_path=target, old_string="a", new_string="b"),
    result("e2", "PreToolUse:Edit hook error: [shim -File run-hook.ps1 -Hook branch_guard]: refused " + SECRET, error=True),
    call("b1", "Bash", command="pytest"), result("b1", "Exit code 143 Command timed out after 10m", error=True),
    call("b2", "Bash", command="ls nope"), result("b2", "Exit code 2\n" + SECRET, error=True),
    call("q1", "AskUserQuestion", questions=[]),
    result("q1", "The user doesn't want to proceed with this tool use.", error=True),
    user([{"type": "text", "text": "[Request interrupted by user for tool use]"}]),
    call("x1", "Read", file_path=str(ROOT / "life-os" / "identity" / "notes.md")), result("x1", "ok"),
    {"type": "system", "subtype": "turn_duration", "durationMs": 61500},
]
out = rd.build(write(records), rd.DEFAULT_PRIVATE)
text = json.dumps(out)

check(SECRET not in text and "pytest" not in text, "no prompt text, command or tool output reaches the digest")
check(str(ROOT) not in text and str(ROOT).replace("\\", "/") not in text, "no absolute path reaches the digest")
check(out["owner_prompts"] == 1, f"one owner prompt; skill bodies, commands and results excluded (got {out['owner_prompts']})")
check(out["skills"] == {"issue-yolo": 1}, f"skill invocations counted by name (got {out['skills']})")
check(out["tool_calls"]["Read"] == 4 and out["tool_calls"]["Edit"] == 2, "tool calls counted per tool")
check(out["tool_errors"]["Edit"] == {"edit_mismatch": 1, "hook_refusal": 1}, f"edit errors classed (got {out['tool_errors'].get('Edit')})")
check(out["tool_errors"]["Bash"] == {"timeout": 1, "exit_nonzero": 1}, f"bash errors classed (got {out['tool_errors'].get('Bash')})")
check(out["tool_errors"]["AskUserQuestion"] == {"user_rejected": 1}, "a rejected tool use is its own class")
check(out["hook_refusals"] == {"PreToolUse:Edit:branch_guard": 1}, f"hook refusal keyed by event, tool and hook (got {out['hook_refusals']})")
check(out["interrupts"] == {"count": 1, "after_tool": {"AskUserQuestion": 1}}, f"interrupt and the tool before it (got {out['interrupts']})")
check(out["repeated_identical_calls"] == 3, f"identical repeated calls counted (got {out['repeated_identical_calls']})")
check(out["lookups_before_first_edit"] == 4, f"lookups before the first edit (got {out['lookups_before_first_edit']})")
check(out["repeat_reads"] == [{"path": "demo-repo/app/main.py", "reads": 3}], f"repeat reads as repo-relative paths (got {out['repeat_reads']})")
check(out["large_results"] == [{"tool": "Grep", "chars": rd.LARGE_RESULT_CHARS, "path": ""}], f"large results by tool and size (got {out['large_results']})")
check(out["turn_seconds"] == 62, f"turn time summed (got {out['turn_seconds']})")
check("life-os" not in text and "identity" not in text, "a private repo's path never appears")
check(not out["private_session"], "a non-private cwd is not a private session")

# ---- path masking ----
mask = rd.PathMask(CWD, ["life-os"])
check(mask(str(ROOT / "other-repo" / "x.py")) == "other-repo/x.py", "a sibling repo path is repo-relative")
check(mask(str(ROOT / "LIFE-OS" / "a.md")) == "<private>", "private repo match ignores case")
check(mask(str(Path(tempfile.gettempdir()).anchor) + "elsewhere/secret.txt").startswith("<outside>/"), "a path outside the fleet root keeps only its name")
check(mask("docs/a.md") == "demo-repo/docs/a.md", "a relative path is read against the session repo")
check(rd.PathMask("", [])("C:/x/y.md") == "<outside>/y.md", "no cwd keeps only the name")

# ---- a private session redacts every path ----
priv_cwd = str(ROOT / "life-os")
priv = [dict(r, cwd=priv_cwd) for r in records]
pout = rd.build(write(priv), rd.DEFAULT_PRIVATE)
check(pout["private_session"] and pout["repeat_reads"] == [], "a private session lists no read paths")

# ---- classify_error ----
check(rd.classify_error("File has not been read yet. Read it first") == "file_state", "file-state class")
check(rd.classify_error("Permission denied") == "permission", "permission class")
check(rd.classify_error("something odd") == "other", "unknown text is other")

# ---- not confirmed, never an empty digest ----
for path, why in ((write([{"type": "summary"}]), "no messages"), (Path(tempfile.gettempdir()) / "retro-missing.jsonl", "missing")):
    try:
        rd.build(path, [])
        check(False, f"{why}: raises NotConfirmed")
    except rd.NotConfirmed:
        check(True, f"{why}: raises NotConfirmed")

home = Path(tempfile.mkdtemp())
(home / ".claude" / "projects" / "p").mkdir(parents=True)
(home / ".claude" / "projects" / "p" / "sid1.jsonl").write_text("{}", encoding="utf-8")
check(rd.current_transcript(home, "sid1").name == "sid1.jsonl", "current session found by id")
for sid in (None, "nope"):
    try:
        rd.current_transcript(home, sid)
        check(False, f"session id {sid!r}: raises NotConfirmed")
    except rd.NotConfirmed:
        check(True, f"session id {sid!r}: raises NotConfirmed")

check(rd.main(["--current", str(write(records))]) == 2, "both --current and a path is refused")
check(rd.main([str(write(records))]) == 0, "a readable transcript exits 0")

_h.report_and_exit("test_retro_digest")
