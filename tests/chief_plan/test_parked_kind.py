"""chief_plan parked kinds: `parked` needs a kind, `owner` needs the `needs-owner` label,
a question carries the date it was asked, and the owner list sorts newest first.

Every part runs against a throwaway plan file or in-memory document; the real
chief-plan.json is never touched and no `gh` call is made (the label lookup is
replaced).

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/chief_plan/test_parked_kind.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import chief_plan as cp  # noqa: E402
from owner_check import github  # noqa: E402

sys.path.insert(0, str(REPO / "skills" / "_lib" / "chief"))
import migrate_parked as mp  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


def _raises(fn, *args, **kwargs) -> str:
    try:
        fn(*args, **kwargs)
    except ValueError as exc:
        return str(exc)
    return ""


def _doc() -> dict:
    doc = cp.empty_plan()
    doc["updated_at"] = "2026-11-04T05:00:00Z"
    cp.add_item(doc, "life-os#10", "a build", status="building")
    return doc


LABELLED: dict = {}
real_state = cp.owner_label_state
cp.owner_label_state = lambda ref: LABELLED.get(ref)

# ---- the refusal ---------------------------------------------------------------

doc = _doc()
msg = _raises(cp.set_item, doc, "life-os#10", status="parked")
check("owner" in msg and "umbrella" in msg and "blocked-by" in msg, f"parked without a kind names the kinds ({msg[:60]})")
check(doc["queue"][0]["status"] == "building", "a refused parked leaves the row untouched")
check("kind" in _raises(cp.add_item, doc, "life-os#11", "t", status="parked"), "add --status parked without a kind is refused")

# ---- umbrella and blocked-by ---------------------------------------------------

cp.set_item(doc, "life-os#10", status="parked", kind="umbrella")
check(doc["queue"][0]["kind"] == "umbrella" and cp.validate(doc) == [], "umbrella is stored and validates")
cp.set_item(doc, "life-os#10", note="still open")
check(doc["queue"][0]["kind"] == "umbrella", "a note edit keeps the kind")
cp.set_item(doc, "life-os#10", status="parked", kind="blocked-by app-launcher#5")
row = doc["queue"][0]
check(row["kind"] == "blocked-by" and row["blocked_by"] == "app-launcher#5" and cp.validate(doc) == [],
      "blocked-by stores the blocker ref")
check("blocking issue" in _raises(cp.parse_kind, "blocked-by"), "blocked-by without an issue is refused")
check("repo" in _raises(cp.parse_kind, "blocked-by #5"), "blocked-by with a bare #N is refused")
check("no argument" in _raises(cp.parse_kind, "umbrella extra"), "umbrella takes no argument")
check("unknown kind" in _raises(cp.parse_kind, "later"), "an unknown kind is refused")
cp.set_item(doc, "life-os#10", kind="umbrella")
check("blocked_by" not in doc["queue"][0], "switching kind drops the stale blocker")
cp.set_item(doc, "life-os#10", status="queued")
check("kind" not in doc["queue"][0], "leaving parked drops the kind")
check("only applies" in _raises(cp.set_item, doc, "life-os#10", kind="umbrella"), "a kind on a non-parked row is refused")

# ---- owner needs the label -----------------------------------------------------

doc = _doc()
LABELLED["life-os#10"] = False
msg = _raises(cp.set_item, doc, "life-os#10", status="parked", kind="owner")
check("needs-owner" in msg and "owner_check mark" in msg, f"owner without the label names owner_check mark ({msg[:60]})")
check("kind" not in doc["queue"][0], "a refused owner kind writes nothing")
LABELLED["life-os#10"] = None
check("could not confirm" in _raises(cp.set_item, doc, "life-os#10", status="parked", kind="owner"),
      "an unreadable label is its own refusal, not a pass")
LABELLED["life-os#10"] = True
cp.set_item(doc, "life-os#10", status="parked", kind="owner")
check(doc["queue"][0]["kind"] == "owner" and cp.validate(doc) == [], "owner with the label is stored")

# ---- pre-kind rows still load and can be edited ----------------------------------

legacy = _doc()
legacy["queue"][0]["status"] = "parked"
check(cp.validate(legacy) == [], "a parked row without a kind still validates")
cp.set_item(legacy, "life-os#10", note="edited before migration")
check(legacy["queue"][0]["note"] == "edited before migration" and "kind" not in legacy["queue"][0],
      "a legacy parked row can be edited without a kind")

bad = _doc()
bad["queue"][0]["kind"] = "someday"
check(any("queue[0].kind" in e for e in cp.validate(bad)), "an unknown kind in a file is rejected")
bad["queue"][0].update(kind="blocked-by")
check(any("blocked_by" in e for e in cp.validate(bad)), "blocked-by in a file needs its blocker")
bad["queue"][0].update(kind="umbrella", blocked_by="life-os#2")
check(any("only allowed" in e for e in cp.validate(bad)), "a blocker on another kind is rejected")

# ---- a question carries its date -------------------------------------------------

doc = _doc()
qid = cp.add_question(doc, "Merge?")
asked = next(r for r in doc["waiting_on_roberto"] if r["id"] == qid).get("asked_at", "")
check(len(asked) == 10 and cp.validate(doc) == [], f"ask stamps asked_at as a date ({asked})")
doc["waiting_on_roberto"][0]["asked_at"] = "yesterday"
check(any("asked_at" in e for e in cp.validate(doc)), "a malformed asked_at is rejected")

# ---- owner list newest first -----------------------------------------------------

items = [
    {"repo": "b", "number": 2, "since": "2026-10-30"},
    {"repo": "a", "number": 9, "since": ""},
    {"repo": "a", "number": 1, "since": "2026-11-03"},
    {"repo": "a", "number": 5, "since": "2026-10-30"},
]
github.newest_first(items)
check([(i["repo"], i["number"]) for i in items] == [("a", 1), ("a", 5), ("b", 2), ("a", 9)],
      f"owner items sort newest first, undated last, ties by repo then number ({items})")

# ---- CLI -----------------------------------------------------------------------

cp.owner_label_state = real_state
tmp = Path(tempfile.mkdtemp(prefix="plan-kind-"))
try:
    env = {**os.environ, "CLAUDE_HOOKS_STATE_DIR": str(tmp), "PYTHONUTF8": "1"}

    def plan(*argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(REPO / "skills" / "_lib" / "chief_ops.py"), "plan", *argv],
            capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=60)

    r = plan("add", "life-os#1", "x", "--status", "parked")
    check(r.returncode == 2 and "umbrella" in r.stderr, f"CLI add --status parked without --kind exits 2 ({r.stderr.strip()[:80]})")
    r = plan("add", "life-os#1", "x", "--status", "parked", "--kind", "umbrella")
    check(r.returncode == 0, f"CLI add --kind umbrella writes ({r.stderr.strip()[:80]})")
    r = plan("add", "life-os#2", "y")
    r = plan("set", "life-os#2", "--status", "parked")
    check(r.returncode == 2 and "kind" in r.stderr, "CLI set --status parked without --kind exits 2")
    r = plan("set", "life-os#2", "--status", "parked", "--kind", "blocked-by life-os#1")
    shown = json.loads(plan("show").stdout)["queue"]
    check(r.returncode == 0 and shown[1].get("blocked_by") == "life-os#1", "CLI --kind 'blocked-by <ref>' stores the blocker")
finally:
    shutil.rmtree(tmp, ignore_errors=True)


# ---- the one-time migration -----------------------------------------------------

def _row(ref: str, note: str, title: str = "t") -> dict:
    repo, local = cp.split_ref(ref)
    return {"repo": repo, "ref": local, "title": title, "status": "parked", "note": note}


check(mp.guess(_row("a#1", "umbrella; steps #2-#4"))[0] == "umbrella", "migration: umbrella note")
check(mp.guess(_row("a#1", "waits #5 (Roberto's dry run)"))[0] == "blocked-by a#5", "migration: blocker beats the owner word")
check(mp.guess(_row("a#1", "after al#9 + rollout"))[0] == "blocked-by app-launcher#9", "migration: short repo names expand")
check(mp.guess(_row("a#1", "needs Roberto's phone check"))[0] == "owner", "migration: Roberto's step is owner")
check(mp.guess(_row("a#1", "owner revised: measure first"))[0] is None, "migration: a bare 'owner' stays unclassified")
check(mp.guess(_row("a#1", "closed not planned (Roberto)"))[0] is None, "migration: closed-not-planned is not given a kind")

tmp = Path(tempfile.mkdtemp(prefix="plan-migrate-"))
try:
    path = tmp / "chief-plan.json"
    doc = cp.empty_plan()
    for ref, note in [("a#1", "umbrella"), ("a#2", "Roberto's phone check"), ("a#3", "Roberto's probe"), ("a#4", "nothing")]:
        row = _row(ref, note)
        doc["queue"].append(row)
    cp.update(path, lambda d: d.update(queue=doc["queue"]))
    mp.owner_labels = lambda repos: ({"a#2"}, set())
    before = path.read_text(encoding="utf-8")
    plan = mp.classify_rows([r for r in cp.load(path)["queue"]], {})
    check({i["ref"]: i["label"] for i in plan} == {"a#1": "", "a#2": "present", "a#3": "missing", "a#4": ""},
          "migration: owner rows report their needs-owner state")
    check(path.read_text(encoding="utf-8") == before, "migration: classifying writes nothing")
    count, left = mp.apply_plan(path, plan)
    kinds = {r["ref"]: r.get("kind") for r in cp.load(path)["queue"]}
    check(count == 2 and kinds == {"#1": "umbrella", "#2": "owner", "#3": None, "#4": None},
          f"migration: apply writes only placeable kinds ({kinds})")
    check(any("NEEDS_MARK a#3" in line for line in left) and any("UNCLASSIFIED a#4" in line for line in left),
          "migration: an unlabelled owner row and an unclassified row are left for the chief")
finally:
    shutil.rmtree(tmp, ignore_errors=True)


_h.report_and_exit("test_parked_kind")
