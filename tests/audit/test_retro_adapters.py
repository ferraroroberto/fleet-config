"""Unit tests for the /retro digest's Codex and Pi adapters and its error signatures.

Every log here is synthetic, built in a temp dir in each harness's record shape; no
real session is read. The checks: the three harnesses produce the same digest shape
with `agent` set, two errors that differ only by path or number share a signature,
no prompt or tool-output text reaches the digest, a ratchet's name and rule id are
captured from gate output, and an unrecognised log is unmeasured, never empty.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/audit/test_retro_adapters.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
from retro import digest as rd  # noqa: E402
from retro import signature as sig  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

ROOT = Path(tempfile.gettempdir()) / "retro-adapter-root"
CWD = str(ROOT / "demo-repo-wt-17")
TARGET = str(ROOT / "demo-repo-wt-17" / "app" / "main.py")
PROSE = "owner typed this sentence"
GATE_FAILURE = "\n".join((
    "Exit code 1",
    f"collected output {PROSE}",
    "conventions ratchet: 1 hit(s) beyond the baseline, in 1 file(s):",
    "  skills/_lib/x.py: [no-window] 1 hit(s), baseline 0:",
    f"    skills/_lib/x.py:12: [no-window] {PROSE}",
    "static ratchet: PASS (0 baselined)",
))


def write(lines, name="s.jsonl"):
    path = Path(tempfile.mkdtemp()) / name
    path.write_text("\n".join(json.dumps(r) for r in lines) + "\nnot json\n", encoding="utf-8")
    return path


def claude_log():
    def call(tid, name, **args):
        return {"type": "assistant", "cwd": CWD, "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": tid, "name": name, "input": args}]}}

    def result(tid, text, error):
        return {"type": "user", "cwd": CWD, "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": error}]}}

    return [
        {"type": "user", "cwd": CWD, "message": {"role": "user", "content": PROSE}},
        call("1", "Read", file_path=TARGET), result("1", f"body {PROSE}", False),
        call("2", "Bash", command=f"echo {PROSE}"), result("2", GATE_FAILURE, True),
        call("3", "Edit", file_path=TARGET, old_string=PROSE, new_string="b"),
        result("3", "<tool_use_error>String to replace not found in file.\nString: " + PROSE, True),
        {"type": "user", "cwd": CWD, "message": {"role": "user", "content": [
            {"type": "text", "text": "[Request interrupted by user]"}]}},
        {"type": "system", "subtype": "turn_duration", "durationMs": 4000},
    ]


def codex_log():
    def item(payload):
        return {"timestamp": "t", "type": "response_item", "payload": payload}

    return [
        {"type": "session_meta", "payload": {"id": "s", "cwd": CWD}},
        item({"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": f"# AGENTS.md instructions for {CWD}\n{PROSE}"}]}),
        item({"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "<environment_context>x</environment_context>"}]}),
        item({"type": "message", "role": "user", "content": [{"type": "input_text", "text": PROSE}]}),
        item({"type": "function_call", "name": "shell", "call_id": "c1",
              "arguments": json.dumps({"command": ["bash", "-lc", f"echo {PROSE}"]})}),
        item({"type": "function_call_output", "call_id": "c1",
              "output": json.dumps({"output": GATE_FAILURE.split("\n", 1)[1],
                                    "metadata": {"exit_code": 1, "duration_seconds": 0.5}})}),
        item({"type": "custom_tool_call", "name": "apply_patch", "call_id": "c2", "input": PROSE}),
        item({"type": "custom_tool_call_output", "call_id": "c2",
              "output": f"apply_patch verification failed: Failed to find expected lines in {TARGET}"}),
        item({"type": "function_call", "name": "shell", "call_id": "c3", "arguments": "{\"command\": [\"ls\"]}"}),
        item({"type": "function_call_output", "call_id": "c3", "output": f"Exit code: 0\nOutput:\n{PROSE}"}),
        {"type": "event_msg", "payload": {"type": "turn_aborted", "reason": "interrupted"}},
        {"type": "event_msg", "payload": {"type": "task_complete", "duration_ms": 4000}},
    ]


def pi_log():
    def msg(role, **fields):
        return {"type": "message", "id": "x", "message": {"role": role, **fields}}

    def text(value):
        return [{"type": "text", "text": value}]

    return [
        {"type": "session", "version": 3, "id": "s", "cwd": CWD},
        msg("user", content=text("/skill:retro")),
        msg("user", content=text(PROSE)),
        msg("assistant", content=[{"type": "toolCall", "id": "p1", "name": "read", "arguments": {"path": TARGET}}],
            stopReason="toolUse"),
        msg("toolResult", toolCallId="p1", toolName="read", content=text(PROSE), isError=False),
        msg("assistant", content=[{"type": "toolCall", "id": "p2", "name": "bash",
                                   "arguments": {"command": f"echo {PROSE}"}}], stopReason="toolUse"),
        msg("toolResult", toolCallId="p2", toolName="bash",
            content=text(GATE_FAILURE.split("\n", 1)[1] + "\n\nCommand exited with code 1"), isError=True),
        msg("assistant", content=[{"type": "toolCall", "id": "p3", "name": "edit",
                                   "arguments": {"path": TARGET, "oldText": PROSE, "newText": "b"}}],
            stopReason="toolUse"),
        msg("toolResult", toolCallId="p3", toolName="edit", isError=True, content=text(
            f"Could not find the exact text in {TARGET}. The old text must match exactly.")),
        {"type": "custom", "customType": "claude-agent-sdk-tool-watch",
         "data": {"toolCallId": "p3", "toolName": "edit", "content": PROSE, "isError": True}},
        msg("assistant", content=[], stopReason="aborted"),
    ]


digests = {name: rd.build(write(make()), rd.DEFAULT_PRIVATE)
           for name, make in (("claude", claude_log), ("codex", codex_log), ("pi", pi_log))}

# ---- one shape, agent set ----
shape = set(digests["claude"])
for agent, out in digests.items():
    check(set(out) == shape, f"{agent}: same top-level fields as the Claude Code digest "
          f"(diff {sorted(set(out) ^ shape)})")
    check(out["agent"] == agent, f"{agent}: agent field set (got {out['agent']})")
    check(out["repo"] == "demo-repo", f"{agent}: repo from the cwd, worktree suffix folded (got {out['repo']})")
    check(out["unparsed_lines"] == 1, f"{agent}: a non-JSON line is counted, not dropped (got {out['unparsed_lines']})")
    check(out["owner_prompts"] == 1, f"{agent}: one owner prompt, injected context excluded (got {out['owner_prompts']})")
    check(out["interrupts"]["count"] == 1, f"{agent}: the interrupt counted (got {out['interrupts']})")
    check(out["ratchet_rules"] == {"conventions_ratchet:no-window": 1},
          f"{agent}: ratchet name and rule id captured from gate output (got {out['ratchet_rules']})")
    check(out["lookups_before_first_edit"] is not None, f"{agent}: the edit attempt is seen as an edit")
    text = json.dumps(out)
    check(PROSE not in text and "echo" not in text, f"{agent}: no prompt, command or tool output in the digest")
    check(str(ROOT) not in text and str(ROOT).replace("\\", "/") not in text, f"{agent}: no absolute path")

check(digests["codex"]["tool_errors"] == {"shell": {"exit_nonzero": 1}, "apply_patch": {"edit_mismatch": 1}},
      f"codex: a nonzero exit and a failed patch are errors, a zero exit is not (got {digests['codex']['tool_errors']})")
check(digests["pi"]["tool_errors"] == {"bash": {"exit_nonzero": 1}, "edit": {"edit_mismatch": 1}},
      f"pi: exit line at the end and the edit miss classed (got {digests['pi']['tool_errors']})")
check(digests["pi"]["tool_calls"] == {"read": 1, "bash": 1, "edit": 1},
      f"pi: the SDK tool-watch echo is not a second call (got {digests['pi']['tool_calls']})")
check(digests["pi"]["skills"] == {"retro": 1}, f"pi: a /skill: prompt is a skill (got {digests['pi']['skills']})")
check(digests["pi"]["turn_seconds"] is None, "pi: no recorded turn time reads as unmeasured, not zero")
check(digests["codex"]["turn_seconds"] == 4 and digests["claude"]["turn_seconds"] == 4, "turn time summed")
check(digests["pi"]["error_signatures"].get("edit|edit_mismatch|Could not find the exact text in <path>. "
                                             "The old text must match exactly.") == 1,
      f"pi: the edit error's own wording templated (got {digests['pi']['error_signatures']})")
check("bash|exit_nonzero|conventions_ratchet:no-window" in digests["pi"]["error_signatures"],
      "a gate failure's signature names its ratchet rule")

# ---- signatures: same mistake, different path or number ----
def one_signature(text, tool="Bash"):
    cls, span = rd.classify(text)
    return sig.signature(tool, cls, text, span)


pairs = (
    ("<tool_use_error>File does not exist: C:/a/b.py</tool_use_error>",
     "<tool_use_error>File does not exist: /x/y/z.md</tool_use_error>", "path"),
    ("Exit code 2\nboom", "Exit code 127\nother", "exit number"),
    (f"{PROSE}\nCommand exited with code 1", "different\nCommand exited with code 3", "trailing exit line"),
    ("Found 3 occurrences of the text in a/b.py. The text must be unique.",
     "Found 12 occurrences of the text in c/d/e.py. The text must be unique.", "count and path"),
    ("<tool_use_error>Id 01a2b3c4d5e6 failed</tool_use_error>",
     "<tool_use_error>Id 9f8e7d6c5b4a3 failed</tool_use_error>", "hex id"),
)
for a, b, why in pairs:
    check(one_signature(a) == one_signature(b), f"{why}: same signature ({one_signature(a)!r} vs {one_signature(b)!r})")
check(one_signature("Exit code 2") != one_signature("Exit code 2", tool="Edit"), "the tool is part of the signature")
check(one_signature(f"{PROSE} No such file or directory") == "Bash|missing_path|No such file or directory",
      "free text before a class match never enters the template")
check(one_signature(f"{PROSE} something odd") == "Bash|other|-", "free text with no own line templates to -")
check(sig.mask("open 'a b' and \"c\" at E:/x/y.py line 42") == "open <q> and <q> at <path> line <n>",
      "quoted strings, paths and numbers masked")
check(sig.signature("Edit", "hook_refusal", "x", "hook error", ["branch_guard"]) == "Edit|hook_refusal|branch_guard",
      "a hook refusal's template is the refusing hook")

# ---- ratchet rules ----
gate = "\n".join((
    "structure ratchet: 1 finding(s) above the baseline:",
    "  [file length] skills/_lib/y.py has 1200 lines; was 0",
    "docs ratchet: NOT CONFIRMED: README.md unreadable",
    "static ratchet: PASS (0 baselined)",
    "  [hook:branch_guard] not a ratchet rule",
))
got = sig.ratchet_rules(gate)
check(got == ["structure_ratchet:file-length", "docs_ratchet:-"],
      f"each failing ratchet recorded, a passing one and hook markers not (got {got})")

# ---- unmeasured, never an empty digest ----
for lines, why in (([{"type": "summary"}], "no message records"),
                   ([{"type": "session", "cwd": CWD}], "a Pi header alone")):
    try:
        rd.build(write(lines), [])
        check(False, f"{why}: raises NotConfirmed")
    except rd.NotConfirmed as exc:
        check(str(exc).startswith("unmeasured:"), f"{why}: reported as unmeasured (got {exc})")

private = [dict(r, cwd=str(ROOT / "life-os")) if r.get("cwd") else r for r in pi_log()]
pout = rd.build(write(private), rd.DEFAULT_PRIVATE)
check(pout["repo"] == "<private>" and "life-os" not in json.dumps(pout), "a private repo's name is withheld")

_h.report_and_exit("test_retro_adapters")
