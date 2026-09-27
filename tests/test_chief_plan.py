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


# ---- structured questions (fleet-config#1049) ---------------------------------

def _question(**extra) -> dict:
    row = {"id": "q1", "text": "Merge the two capture copies?", "repo": "life-os",
           "ref": "life-os#171", "question": "Merge the two capture copies?",
           "detail": "Two copies of one session diverged.", "recommendation": "Merge.",
           "options": [{"label": "Merge", "description": "keep one", "recommended": True},
                       {"label": "Leave"}]}
    row.update(extra)
    doc = _valid()
    doc["waiting_on_roberto"] = [row]
    return doc


def _errors(doc: dict, needle: str) -> bool:
    return any(needle in e for e in cp.validate(doc))


check(cp.validate(_question()) == [], "a fully structured question validates")
check(cp.validate(_question(multi=True, options=[{"label": "A", "recommended": True},
                                                 {"label": "B", "recommended": True}])) == [],
      "multi allows several recommended options")
old_v1 = _valid()
old_v1["waiting_on_roberto"] = [{"text": "legacy", "ref": "app-launcher#1131"}, {"text": "bare"}]
check(cp.validate(old_v1) == [], "an old v1 file (text/ref only, no id) is still valid")
check(_errors(_question(id="1abc"), "waiting_on_roberto[0].id: must be a lowercase letter"),
      "an id must start with a letter (so unwait can tell it from an index)")
dup = _question()
dup["waiting_on_roberto"].append({"id": "q1", "text": "again"})
check(_errors(dup, "waiting_on_roberto[1].id: q1 is already used"), "a duplicate id is rejected")
check(_errors(_question(repo="app-launcher"), "repo: app-launcher does not match ref life-os#171"),
      "a repo that contradicts the ref is rejected")
check(_errors(_question(repo="bad repo"), "waiting_on_roberto[0].repo: must be a repo name"),
      "a malformed repo is rejected")
check(_errors(_question(detail=""), "waiting_on_roberto[0].detail: must not be empty"),
      "an empty detail is rejected")
check(_errors(_question(question="two\nlines"), "waiting_on_roberto[0].question: must be one line"),
      "a multi-line question is rejected")
check(cp.validate(_question(detail="x" * cp.MAX_PROSE)) == []
      and _errors(_question(detail="x" * (cp.MAX_PROSE + 1)), "detail: longer than"),
      "detail takes the longer prose cap, and no more")
check(_errors(_question(options=[{"label": str(n)} for n in range(5)]), "at most 4 options, got 5"),
      "more than four options are rejected")
check(_errors(_question(options="Merge"), "waiting_on_roberto[0].options: must be a list"),
      "options must be a list")
check(_errors(_question(options=[{"description": "no label"}]), "options[0]: missing field(s) label"),
      "an option needs a label")
check(_errors(_question(options=[{"label": "A", "why": "x"}]), "options[0]: unknown field(s) why"),
      "an option refuses unknown fields")
check(_errors(_question(options=[{"label": "A"}, {"label": "A"}]), "options[1].label: 'A' is already"),
      "duplicate option labels are rejected")
check(_errors(_question(options=[{"label": "A", "recommended": "yes"}]), "recommended: must be true or false"),
      "recommended must be a bool")
check(_errors(_question(options=[{"label": "A", "recommended": True}, {"label": "B", "recommended": True}]),
              "2 options are recommended; at most one unless multi"),
      "two recommended options without multi are rejected")
check(_errors(_question(multi="yes"), "waiting_on_roberto[0].multi: must be true or false"),
      "multi must be a bool")
bare = _question()
del bare["waiting_on_roberto"][0]["options"]
bare["waiting_on_roberto"][0]["multi"] = True
check(_errors(bare, "multi: needs options"), "multi without options is rejected")

doc = cp.empty_plan()
doc["updated_at"] = "2026-09-26T14:40:00Z"
first = cp.add_question(doc, "  Merge   the capture copies?  ", ref="life-os#171",
                        detail="Two copies diverged.", recommendation="Merge them.",
                        options=["Merge::keep one", "Leave"], recommended=["Merge"])
row = doc["waiting_on_roberto"][0]
check(first == "q1" and row["repo"] == "life-os" and row["text"] == "Merge the capture copies?"
      and row["options"] == [{"label": "Merge", "description": "keep one", "recommended": True},
                             {"label": "Leave"}] and cp.validate(doc) == [],
      "add_question auto-ids, derives repo from ref, trims text and parses options")
plain = cp.add_waiting(doc, "Tab persistence?")
check(plain == "q2", "a plain wait gets the next auto id too")
cp.clear_waiting(doc, "q1")
check(cp.add_question(doc, "Next?", repo="fleet-config") == "q3",
      "a cleared id is not reissued while a later one is waiting")
long_q = "why " * 80
cp.add_question(doc, long_q, item_id="long-one")
check(len(doc["waiting_on_roberto"][-1]["text"]) == cp.MAX_TEXT and cp.validate(doc) == [],
      "a long question's card text is cut to MAX_TEXT")
check("not one of the --option labels" in _raises(cp.add_question, doc, "Q?", options=["A"],
                                                  recommended=["B"]),
      "--recommended must name an --option label")
check("needs a label" in _raises(cp.add_question, doc, "Q?", options=["::desc only"]),
      "an --option with no label refuses")
cp.clear_waiting(doc, "long-one")
check([r["id"] for r in doc["waiting_on_roberto"]] == ["q2", "q3"], "clear_waiting matches by id")


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

    r = plan("ask", "Merge the capture copies?", "--ref", "life-os#171",
             "--detail", "Two copies diverged.", "--recommend", "Merge them.",
             "--option", "Merge::keep one", "--option", "Leave", "--recommended", "Merge")
    check(r.returncode == 0 and "waiting=2" in r.stdout and r.stdout.strip().endswith("id=q2"),
          f"CLI ask writes and reports the new id ({r.stdout.strip()[-40:]!r}, {r.stderr.strip()[:120]})")
    r = plan("show")
    shown = json.loads(r.stdout) if r.returncode == 0 else {}
    asked = (shown.get("waiting_on_roberto") or [{}])[-1]
    check(asked.get("repo") == "life-os" and asked.get("recommendation") == "Merge them."
          and asked.get("options", [{}])[0].get("recommended") is True,
          "CLI ask round-trips through show")
    asked_line = [ln for ln in r.stdout.splitlines() if '"question"' in ln]
    check(r.stdout.count("\n") <= 14 and len(asked_line) == 1
          and json.loads(asked_line[0].strip().rstrip(",")) == asked,
          "CLI show prints one row per line, a question's options on its own line")
    before = (cli_dir / "chief-plan.json").read_bytes()
    r = plan("ask", "Pick?", "--option", "A", "--option", "B", "--recommended", "A", "--recommended", "B")
    check(r.returncode == 2 and "at most one unless multi" in r.stderr
          and (cli_dir / "chief-plan.json").read_bytes() == before,
          "CLI ask refuses two recommendations without --multi and writes nothing")
    r = plan("ask", "Pick some?", "--id", "pick", "--option", "A", "--option", "B",
             "--recommended", "A", "--recommended", "B", "--multi", "--text", "Pick")
    check(r.returncode == 0 and r.stdout.strip().endswith("id=pick"), "CLI ask takes --id, --multi and --text")
    r = plan("ask", "Again?", "--id", "pick")
    check(r.returncode == 2 and "pick is already used" in r.stderr, "CLI ask refuses a duplicate --id")
    r = plan("unwait", "q2")
    check(r.returncode == 0 and "waiting=2" in r.stdout, "CLI unwait removes an item by id")
    r = plan("clear")
    check(r.returncode == 0 and "queue=0" in r.stdout, "CLI clear resets to an empty plan")
finally:
    shutil.rmtree(tmp, ignore_errors=True)


_h.report_and_exit("test_chief_plan")
