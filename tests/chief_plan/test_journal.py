"""The chief's journal: `chief_ops.py note`, the plan transitions that log themselves, dedupe and the bounds.

Every part runs against a throwaway state dir; the real chief-journal.jsonl is never touched.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/chief_plan/test_journal.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
from chief import journal  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

CHIEF_OPS = REPO / "skills" / "_lib" / "chief_ops.py"
PYTHON = sys.executable
NOW = datetime(2026, 11, 14, 9, 0, 0, tzinfo=timezone.utc)

tmp = Path(tempfile.mkdtemp(prefix="chief-journal-"))
try:
    env = {**os.environ, "CLAUDE_HOOKS_STATE_DIR": str(tmp), "PYTHONUTF8": "1"}
    plan_file = tmp / "chief-plan.json"
    journal_path = tmp / journal.JOURNAL_FILENAME

    def ops(*argv: str) -> subprocess.CompletedProcess:
        return subprocess.run([PYTHON, str(CHIEF_OPS), *argv], capture_output=True, text=True,
                              env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def rows() -> list:
        return [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]

    # ---- note: append, dedupe ------------------------------------------------
    first = ops("note", "perf sweep moved to Saturday", "--kind", "note", "--ref", "fleet-config#1")
    check(first.returncode == 0 and first.stdout.strip() == "NOTE=appended kind=note ref=fleet-config#1",
          f"note appends and says so ({first.stdout!r} {first.stderr!r})")
    entry = rows()[0]
    check(set(entry) == {"ts", "kind", "ref", "text"} and entry["kind"] == "note"
          and entry["ref"] == "fleet-config#1" and entry["ts"].endswith("Z"),
          "an entry is {ts, kind, ref, text}")
    again = ops("note", "perf sweep moved to Saturday", "--ref", "fleet-config#1")
    check(again.returncode == 0 and again.stdout.strip() == "NOTE=skipped reason=duplicate" and len(rows()) == 1,
          "the same kind + ref + text as the newest entry is skipped")
    ops("note", "different text", "--ref", "fleet-config#1")
    ops("note", "perf sweep moved to Saturday", "--ref", "fleet-config#2")
    check(len(rows()) == 3, "a new text, or the same text on another ref, is kept")
    ops("note", "perf sweep moved to Saturday", "--ref", "fleet-config#1")
    check(len(rows()) == 4, "a repeat of an older text (not the newest for that ref) is kept")
    ops("note", "no ref line")
    ops("note", "no ref line")
    check(len(rows()) == 5 and rows()[-1]["ref"] == "", "an entry with no ref dedupes against the newest ref-less one")

    for argv, why in ((("note", "x", "--kind", "bogus"), "an unknown kind"),
                      (("note", "x", "--ref", "no-hash"), "a malformed ref"),
                      (("note", "   "), "an empty text"),
                      (("note", "x" * 201), "a text over 200 characters")):
        bad = ops(*argv)
        check(bad.returncode == 2 and "ERROR" in bad.stderr, f"{why} is refused with exit 2")
    check(len(rows()) == 5, "a refused note writes nothing")

    # ---- the six plan transitions each append exactly one entry ---------------
    journal_path.unlink()

    def plan(*argv: str) -> subprocess.CompletedProcess:
        out = ops("plan", *argv)
        check(out.returncode == 0, f"plan {' '.join(argv[:2])} succeeds ({out.stderr.strip()!r})")
        return out

    def newest(kind: str) -> dict:
        return [row for row in rows() if row["kind"] == kind][-1]

    plan("add", "demo#7", "Fix the thing", "--status", "queued")
    check([r["kind"] for r in rows()] == ["filed"] and newest("filed")["ref"] == "demo#7"
          and "Fix the thing" in newest("filed")["text"], "plan add -> one `filed` entry")

    plan("lane", "demo", "building", "--item", "#7")
    check([r["kind"] for r in rows()] == ["filed", "dispatched"] and newest("dispatched")["ref"] == "demo#7",
          "plan lane on a new item -> one `dispatched` entry")
    plan("lane", "demo", "gate", "--item", "#7")
    check(len(rows()) == 2, "a status change on the same lane item is not a second dispatch")

    plan("set", "demo#7", "--status", "gate")
    check(len(rows()) == 2, "a status change that is not merged/parked logs nothing")
    plan("set", "demo#7", "--status", "merged", "--note", "abc1234")
    check(newest("merged")["ref"] == "demo#7" and "abc1234" in newest("merged")["text"] and len(rows()) == 3,
          "plan set --status merged -> one `merged` entry")
    plan("set", "demo#7", "--note", "later")
    check(len(rows()) == 3, "editing a merged row's note is not a second merge")

    plan("add", "demo#8", "Wait for it", "--status", "parked", "--kind", "umbrella")
    plan("set", "demo#8", "--status", "queued")
    plan("set", "demo#8", "--status", "parked", "--kind", "umbrella")
    parked = [r for r in rows() if r["kind"] == "parked"]
    check(len(parked) == 1 and parked[0]["ref"] == "demo#8" and "umbrella" in parked[0]["text"],
          "plan set --status parked -> one `parked` entry (the same text as the newest is not repeated)")

    asked = plan("ask", "Merge it?", "--ref", "demo#7", "--option", "Yes::do it", "--option", "No")
    item_id = asked.stdout.split("id=")[-1].strip()
    check(newest("asked")["ref"] == "demo#7" and "Merge it?" in newest("asked")["text"],
          "plan ask -> one `asked` entry")
    before = len(rows())
    plan("unwait", item_id)
    check(len(rows()) == before + 1 and newest("answered")["ref"] == "demo#7"
          and "Merge it?" in newest("answered")["text"], "plan unwait -> one `answered` entry")
    plan("wait", "Remember the tab?", "--ref", "demo#9")
    check(newest("asked")["ref"] == "demo#9", "plan wait (the short ask) -> an `asked` entry")

    refused = ops("plan", "set", "demo#7", "--status", "waiting-roberto")
    n = len(rows())
    check(refused.returncode == 2 and len(rows()) == n, "a refused plan command journals nothing")
    plan("prune")
    check(len(rows()) == n, "plan prune is not a journalled transition")

    # ---- a journal failure never fails a plan command --------------------------
    broken = tmp / "broken"
    broken.mkdir()
    (broken / journal.JOURNAL_FILENAME).mkdir()  # a directory where the journal file belongs
    out = subprocess.run([PYTHON, str(CHIEF_OPS), "plan", "--plan-file", str(broken / "chief-plan.json"),
                          "add", "demo#1", "Still works"],
                         capture_output=True, text=True, env=env,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    check(out.returncode == 0 and out.stdout.startswith("PLAN=written") and "journal write skipped" in out.stderr,
          f"an unwritable journal logs and leaves the plan command's exit 0 ({out.returncode} {out.stderr!r})")
    check((broken / "chief-plan.json").exists(), "and the plan itself was still written")
    note_fail = subprocess.run([PYTHON, str(CHIEF_OPS), "note", "x", "--journal-file",
                                str(broken / journal.JOURNAL_FILENAME)],
                               capture_output=True, text=True, env=env,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    check(note_fail.returncode == 1 and note_fail.stdout.startswith("NOTE=failed"),
          "an explicit `note` into an unwritable journal reports NOTE=failed with exit 1, never a success")

    # ---- the bounds ---------------------------------------------------------------
    bounded = tmp / "bounded.jsonl"
    for i in range(journal.MAX_ENTRIES + 25):
        journal.append(bounded, "note", f"line {i}", now=NOW + timedelta(seconds=i))
    kept = journal.read_entries(bounded)
    check(len(kept) == journal.MAX_ENTRIES and kept[0]["text"] == "line 25" and kept[-1]["text"] == f"line {journal.MAX_ENTRIES + 24}",
          f"the file keeps the newest {journal.MAX_ENTRIES} entries")

    aged = tmp / "aged.jsonl"
    journal.append(aged, "note", "old", now=NOW - timedelta(days=journal.MAX_AGE_DAYS + 1))
    journal.append(aged, "note", "edge", now=NOW - timedelta(days=journal.MAX_AGE_DAYS - 1))
    journal.append(aged, "note", "new", now=NOW)
    check([r["text"] for r in journal.read_entries(aged)] == ["edge", "new"],
          f"entries older than {journal.MAX_AGE_DAYS} days are dropped on the next write")

    damaged = tmp / "damaged.jsonl"
    damaged.write_text('not json\n{"ts": "bad", "kind": "note", "text": "x"}\n'
                       + json.dumps({"ts": "2026-11-14T08:00:00Z", "kind": "note", "ref": "", "text": "ok"}) + "\n",
                       encoding="utf-8")
    journal.append(damaged, "note", "after", now=NOW)
    check([r["text"] for r in journal.read_entries(damaged)] == ["ok", "after"],
          "a damaged line is dropped, never fatal")

    long_title = journal.fit("t" * 500)
    check(len(long_title) == journal.MAX_TEXT and long_title.endswith("…"),
          "transition text built from a long title is cut to one line of at most 200 characters")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_journal")
