"""Unit tests for /retro-fleet's miner and report builder (skills/_lib/retro/miner.py, proposals.py, fleet.py).

Builds synthetic Claude Code, Codex and Pi logs under a temp home; no real session
is read. The points: the watermark makes a second run with no new logs read
nothing, a signature counts as recurring only at three sessions in thirty days,
an unrecognised log is counted unmeasured rather than clean, and nothing but
counts and signatures reaches the page.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/audit/test_retro_fleet.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
from retro import fleet, miner, proposals  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

SECRET = "owner typed this sentence"
HOME = Path(tempfile.mkdtemp())
FLEET = Path(tempfile.mkdtemp())
DAY = 86400
NOW = datetime.now(timezone.utc)


def claude_log(name, repo, *results, age_days=1):
    cwd = str(FLEET / repo)
    rows = [{"type": "user", "cwd": cwd, "message": {"role": "user", "content": SECRET}}]
    for i, (text, error) in enumerate(results):
        rows.append({"type": "assistant", "cwd": cwd, "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": "Edit", "input": {"file_path": cwd + "/a.py"}}]}})
        rows.append({"type": "user", "cwd": cwd, "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": text, "is_error": error}]}})
    return _write(HOME / ".claude" / "projects" / repo / f"{name}.jsonl", rows, age_days)


def _write(path, rows, age_days):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    stamp = time.time() - age_days * DAY
    os.utime(path, (stamp, stamp))
    return path


MISMATCH = ("<tool_use_error>String to replace not found in file.\nString: " + SECRET + "</tool_use_error>", True)
REFUSAL = ("PreToolUse:Edit hook error: [shim -File run-hook.ps1 -Hook branch_guard]: refused " + SECRET, True)

for i in range(3):
    claude_log(f"a{i}", "alpha", MISMATCH, REFUSAL)
claude_log("b0", "beta", MISMATCH)
claude_log("old", "beta", MISMATCH, age_days=60)
_write(HOME / ".codex" / "sessions" / "2026" / "rollout-1.jsonl", [
    {"type": "session_meta", "payload": {"cwd": str(FLEET / "gamma")}},
    {"type": "response_item", "payload": {"type": "function_call", "name": "shell", "call_id": "c1",
                                          "arguments": json.dumps({"command": ["ls"]})}},
    {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "c1",
                                          "output": "Exit code: 2\n" + SECRET}}], 2)
_write(HOME / ".pi" / "agent" / "sessions" / "x" / "p1.jsonl", [{"type": "session", "cwd": "x"}], 2)
_write(HOME / ".pi" / "agent" / "sessions" / "x" / "p2.jsonl", [{"type": "novel", "message": {"x": 1}}], 2)

roots = miner.default_roots(HOME)
first = miner.scan(roots, {}, workers=1)
check(dict(first.logs) == {"claude": 5, "codex": 1, "pi": 2}, f"every agent's logs are listed (got {dict(first.logs)})")
check(first.digested == 8 and first.unchanged == 0, "the first run is a full backfill")
check(miner.unmeasured(first.cache) == {"unmeasured": 1}, f"an unrecognised log is unmeasured, not clean (got {miner.unmeasured(first.cache)})")
check(miner.empty(first.cache) == 1, "a log of metadata records only is empty, neither clean nor unmeasured")

second = miner.scan(roots, json.loads(json.dumps(first.cache)), workers=1)
check(second.digested == 0 and second.unchanged == 8, "a second run with no new logs reads nothing (watermark)")
claude_log("b1", "beta", MISMATCH)
third = miner.scan(roots, second.cache, workers=1)
check(third.digested == 1, "a new log is the only one read")
(HOME / ".claude" / "projects" / "beta" / "b1.jsonl").unlink()
fourth = miner.scan(roots, third.cache, workers=1)
check(fourth.gone == 1 and any(r.get("gone") for r in fourth.cache["files"].values()),
      "a deleted log keeps its history, marked gone")

stale = dict(json.loads(json.dumps(fourth.cache)), version=miner.CACHE_VERSION - 1)
fifth = miner.scan(roots, stale, workers=1)
check(fifth.digested == 8 and fifth.unchanged == 0, f"a cache of older signatures is re-read (got {fifth.digested})")
check(any(r.get("gone") for r in fifth.cache["files"].values()), "a deleted log's row survives the re-read")

rows = {r["signature"]: r for r in miner.aggregate(fourth.cache, NOW, "someone")}
mismatch = rows.get("Edit|edit_mismatch|<tool_use_error>String to replace not found in file.")
check(mismatch is not None, f"the edit mismatch has one signature across sessions (got {sorted(rows)})")
check(mismatch["sessions"] == 6 and mismatch["sessions_window"] == 5 and mismatch["flagged"],
      f"sessions counted all time and in the window (got {mismatch['sessions']}/{mismatch['sessions_window']})")
check(mismatch["repos"] == {"alpha": 3, "beta": 3} and mismatch["kind"] == "candidate", "repos counted per session")
refusal = rows["Edit|hook_refusal|branch_guard"]
check(refusal["kind"] == "enforced" and refusal["flagged"], "a recurring hook refusal is enforced, reported only")
check(miner.repo_label("SOMEONE", "someone") == "<home>" and miner.repo_label("alpha", "someone") == "alpha",
      "a session in the home folder never shows the account name")
many = dict(mismatch, repos={f"r{i}": 1 for i in range(8)})
check("r4 1 and 3 more" in proposals.evidence(many) and "r5" not in proposals.evidence(many),
      "evidence names the top repos and counts the rest")
check(not rows["shell|exit_nonzero|ls"]["flagged"], "one session is not recurring")

doc = miner.candidates_doc(list(rows.values()), fourth, NOW, top=10)
check([c["key"] for c in doc["candidates"]] == [mismatch["key"]], "only recurring candidates reach the classifier")
check(doc["enforced"] == [refusal], "enforced ones are listed apart")
check(SECRET not in json.dumps(doc) and str(FLEET) not in json.dumps(doc), "no transcript text or path in the candidates")

verdicts = [
    {"key": mismatch["key"], "verdict": "unenforced", "title": "Re-read before edit", "summary": "Check it.",
     "home": "hooks/", "impact": 4, "effort": 2},
    {"key": refusal["key"], "verdict": "enforced"},
]
report, reported = proposals.build(doc, verdicts)
item = report["suggestions"][0]
check(item["repo"] == "project-scaffolding", "a signature in two repos is proposed fleet-wide")
check(item["id"] == mismatch["key"] and "5 sessions" in item["evidence"], f"evidence comes from counts (got {item['evidence']})")
check(reported == [refusal["key"]] and len(report["suggestions"]) == 1, "an enforced verdict is reported only")
_, reported = proposals.build(doc, verdicts[:1])
check(reported == [refusal["key"]], "an enforced signature with no verdict is still reported only")
proposed_refusal = dict(verdicts[0], key=refusal["key"])
_, reported = proposals.build(doc, [proposed_refusal])
check(reported == [], "an enforced signature a verdict proposes for is not also reported only")
for bad, why in (([{"key": "sig-0000000000", "verdict": "unenforced"}], "an unknown key"),
                 ([{"key": mismatch["key"], "verdict": "maybe"}], "an unknown verdict"),
                 ([dict(verdicts[0], summary="x" * 401)], "an over-long text")):
    try:
        proposals.build(doc, bad)
        check(False, f"{why} is refused")
    except ValueError:
        check(True, f"{why} is refused")

out_dir = Path(tempfile.mkdtemp())
cand_path, verdict_path = out_dir / "c.json", out_dir / "v.json"
cand_path.write_text(json.dumps(doc), encoding="utf-8")
verdict_path.write_text(json.dumps(verdicts), encoding="utf-8")
buf = io.StringIO()
with redirect_stdout(buf):
    code = fleet.main(["report", str(cand_path), str(verdict_path), "--out-dir", str(out_dir)])
page = (out_dir / "page.html").read_text(encoding="utf-8")
check(code == 0 and "PAGE=" in buf.getvalue() and "quick_wins=1" in buf.getvalue(), f"report renders (got {buf.getvalue()!r})")
check(SECRET not in page and str(FLEET) not in page, "no transcript text or path on the page")

os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(Path(tempfile.mkdtemp()))
buf = io.StringIO()
with redirect_stdout(buf):
    code = fleet.main(["scan", "--home", str(HOME), "--workers", "1", "--out-dir", str(out_dir)])
check(code == 0 and "LOGS=claude:5 codex:1 pi:2" in buf.getvalue() and "UNMEASURED=1" in buf.getvalue()
      and "EMPTY=1" in buf.getvalue(),
      f"scan prints the manifest (got {buf.getvalue()!r})")

_h.report_and_exit("test_retro_fleet")
