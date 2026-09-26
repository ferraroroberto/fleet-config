"""Unit tests for skills/_lib/chief_plan.py, the chief's plan-file writer
(fleet-config#1034).

Exercises validation, every mutation, the atomic no-write on a rejected
mutation, the missing/corrupt-file paths and the `chief_ops.py plan` CLI legs
against a throwaway state dir -- no real `~/.claude/hooks/state/chief-plan.json`
touched.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_chief_plan.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import chief_plan as cp  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

NOW = datetime(2026, 9, 26, 14, 40, 0, tzinfo=timezone.utc)


def _raises(fn, *args, **kwargs) -> str:
    try:
        fn(*args, **kwargs)
    except ValueError as exc:
        return str(exc)
    return ""


def _valid() -> dict:
    doc = cp.empty_plan()
    doc["updated_at"] = "2026-09-26T14:40:00Z"
    cp.add_item(doc, "app-launcher#1273", "Code tab: Chat by default", status="gate")
    cp.add_item(doc, "automation#135", "parking burst trial", note="before Thu 16:00")
    cp.set_lane(doc, "app-launcher", "gate", item="#1273", session="sid-1")
    cp.add_waiting(doc, "Remember the last tab?", ref="app-launcher#1131")
    return doc


# ---- validation --------------------------------------------------------------

check(cp.validate(_valid()) == [], "the brief's example plan validates")
check(cp.validate([]) != [], "a non-object is rejected")

bad = _valid()
bad["version"] = 2
check(any("version" in e for e in cp.validate(bad)), "an unsupported version is rejected")

bad = _valid()
bad["updated_at"] = "yesterday"
check(any("updated_at" in e for e in cp.validate(bad)), "a non-UTC stamp is rejected")

bad = _valid()
bad["extra"] = 1
check(any("unknown field" in e for e in cp.validate(bad)), "the writer refuses unknown top-level fields")

bad = _valid()
bad["queue"][0]["status"] = "done"
check(any("queue[0].status" in e for e in cp.validate(bad)), "an unknown queue status is rejected")

bad = _valid()
bad["lanes"][0]["status"] = "gating"
check(any("lanes[0].status" in e for e in cp.validate(bad)), "an unknown lane status is rejected")

bad = _valid()
bad["queue"][0]["ref"] = "app-launcher#1273"
check(any("queue[0].ref" in e for e in cp.validate(bad)), "a queue ref must be local #N")

bad = _valid()
bad["waiting_on_roberto"][0]["ref"] = "#1131"
check(any("waiting_on_roberto[0].ref" in e for e in cp.validate(bad)), "a waiting ref must be repo#N")

bad = _valid()
bad["queue"][1]["note"] = "two\nlines"
check(any("one line" in e for e in cp.validate(bad)), "a multi-line note is rejected")

bad = _valid()
bad["queue"][0]["title"] = "x" * (cp.MAX_TEXT + 1)
check(any("longer than" in e for e in cp.validate(bad)), "an over-long title is rejected")

bad = _valid()
bad["queue"].append(dict(bad["queue"][0]))
check(any("already queued" in e for e in cp.validate(bad)), "a duplicate queue item is rejected")

bad = _valid()
bad["lanes"].append({"repo": "app-launcher", "status": "idle"})
check(any("second lane" in e for e in cp.validate(bad)), "two lanes for one repo are rejected")

ok = _valid()
ok["waiting_on_roberto"].append({"text": "no ref needed"})
ok["lanes"].append({"repo": "fleet-config", "status": "idle"})
check(cp.validate(ok) == [], "waiting ref, lane item and lane session are optional")


# ---- mutations ----------------------------------------------------------------

doc = _valid()
cp.set_lane(doc, "app-launcher", "building", item="#1274")
check(len(doc["lanes"]) == 1 and doc["lanes"][0] == {"repo": "app-launcher", "status": "building", "item": "#1274"},
      "set_lane upserts one lane per repo, replacing the old row")
cp.drop_lane(doc, "app-launcher")
check(doc["lanes"] == [], "drop_lane removes the repo's lane")
check("no lane" in _raises(cp.drop_lane, doc, "app-launcher"), "drop_lane on a missing lane refuses")

doc = _valid()
cp.add_item(doc, "life-os#169", "privacy", at=1)
check([r["ref"] for r in doc["queue"]] == ["#169", "#1273", "#135"], "add_item --at 1 inserts at the top")
check("position" in _raises(cp.add_item, doc, "life-os#170", "t", at=9), "add_item refuses an out-of-range position")
check("<repo>#<number>" in _raises(cp.add_item, doc, "#170", "t"), "add_item needs a qualified ref")

cp.move_item(doc, "life-os#169", 3)
check([r["ref"] for r in doc["queue"]] == ["#1273", "#135", "#169"], "move_item moves to a 1-based position")
check("not in the queue" in _raises(cp.move_item, doc, "life-os#999", 1), "move_item on an unknown ref refuses")

cp.set_item(doc, "automation#135", status="merged", note="")
row = doc["queue"][1]
check(row["status"] == "merged" and "note" not in row, "set_item sets status and an empty note clears it")
cp.set_item(doc, "automation#135", note="shipped 4190488", title="parking burst")
check(doc["queue"][1]["note"] == "shipped 4190488" and doc["queue"][1]["title"] == "parking burst",
      "set_item sets note and title")
check("nothing to set" in _raises(cp.set_item, doc, "automation#135"), "set_item with no change refuses")

cp.remove_item(doc, "life-os#169")
check([r["ref"] for r in doc["queue"]] == ["#1273", "#135"], "remove_item drops the item")

cp.add_waiting(doc, "Tab persistence?")
cp.add_waiting(doc, "Inset rows?", ref="app-launcher#1128")
cp.clear_waiting(doc, "app-launcher#1128")
cp.clear_waiting(doc, "Tab persistence?")
cp.clear_waiting(doc, "1")
check(doc["waiting_on_roberto"] == [], "clear_waiting matches by ref, by exact text and by 1-based index")
check("nothing waiting" in _raises(cp.clear_waiting, doc, "gone"), "clear_waiting on no match refuses")


# ---- load / update against a real file ---------------------------------------

tmp = Path(tempfile.mkdtemp(prefix="chief_plan_test_"))
try:
    path = tmp / cp.STATE_FILENAME
    check(cp.load(path) == cp.empty_plan(), "a missing file loads as the empty plan")

    written = cp.update(path, lambda d: cp.add_item(d, "fleet-config#1034", "plan file"), now=NOW)
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    check(on_disk == written and on_disk["updated_at"] == "2026-09-26T14:40:00Z",
          "update writes the validated document with a fresh updated_at")
    check(not any(p.name.endswith(".tmp") for p in tmp.iterdir()), "update leaves no temp file behind")

    before = path.read_bytes()
    err = _raises(cp.update, path, lambda d: cp.add_item(d, "fleet-config#1034", "dup"))
    check("already queued" in err and path.read_bytes() == before,
          "a rejected mutation raises and leaves the file byte-identical")
    err = _raises(cp.update, path, lambda d: cp.add_waiting(d, "bad\x07bell"))
    check("control characters" in err and path.read_bytes() == before,
          "a control character is rejected before anything is written")

    path.write_text("{not json", encoding="utf-8")
    check("plan clear" in _raises(cp.load, path), "a corrupt file is refused, pointing at `plan clear`")
    check("plan clear" in _raises(cp.update, path, lambda d: None),
          "a mutation never builds on (or overwrites) a corrupt file")
    check(path.read_text(encoding="utf-8") == "{not json", "the corrupt file is left for inspection")
    reset = cp.update(path, lambda d: None, now=NOW, reset=True)
    check(reset["queue"] == [] and cp.validate(json.loads(path.read_text(encoding="utf-8"))) == [],
          "clear (reset=True) replaces a corrupt file with a valid empty plan")

    path.write_text(json.dumps({"version": 2}), encoding="utf-8")
    check("not a valid v1 plan" in _raises(cp.load, path), "a future-version file is refused, not rewritten")

    old = os.environ.get("CLAUDE_HOOKS_STATE_DIR")
    os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(tmp)
    try:
        check(cp.plan_file() == tmp / "chief-plan.json", "plan_file honours CLAUDE_HOOKS_STATE_DIR")
    finally:
        if old is None:
            os.environ.pop("CLAUDE_HOOKS_STATE_DIR", None)
        else:
            os.environ["CLAUDE_HOOKS_STATE_DIR"] = old

    # ---- CLI legs through chief_ops.py -----------------------------------------
    cli_dir = tmp / "cli"
    env = {**os.environ, "CLAUDE_HOOKS_STATE_DIR": str(cli_dir), "PYTHONUTF8": "1"}

    def plan(*argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(REPO / "skills" / "_lib" / "chief_ops.py"), "plan", *argv],
            capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=60)

    r = plan("add", "app-launcher#1273", "Code tab: Chat by default")
    check(r.returncode == 0 and "PLAN=written" in r.stdout and "queue=1" in r.stdout,
          f"CLI add writes and reports one line (rc={r.returncode}, {r.stderr.strip()[:120]})")
    r = plan("set", "app-launcher#1273", "--status", "gate")
    check(r.returncode == 0, "CLI set changes status")
    r = plan("lane", "app-launcher", "gate", "--item", "#1273")
    check(r.returncode == 0 and "lanes=1" in r.stdout, "CLI lane upserts a lane")
    r = plan("wait", "Remember the last tab?", "--ref", "app-launcher#1131")
    check(r.returncode == 0 and "waiting=1" in r.stdout, "CLI wait adds a waiting item")
    before = (cli_dir / "chief-plan.json").read_bytes()
    r = plan("add", "app-launcher#1273", "again")
    check(r.returncode == 2 and "ERROR:" in r.stderr and (cli_dir / "chief-plan.json").read_bytes() == before,
          "CLI refusal exits 2 and writes nothing")
    r = plan("show")
    shown = json.loads(r.stdout) if r.returncode == 0 else {}
    check(shown.get("queue", [{}])[0].get("status") == "gate" and shown.get("lanes", [{}])[0].get("item") == "#1273",
          "CLI show prints the plan as written")
    r = plan("clear")
    check(r.returncode == 0 and "queue=0" in r.stdout, "CLI clear resets to an empty plan")
finally:
    shutil.rmtree(tmp, ignore_errors=True)


_h.report_and_exit("test_chief_plan")
