"""Unit tests for skills/_lib/design_review/inventory.py, the cross-app inventory comparator (fleet-config#1500).

Pure-logic, no browser, no `gh`, invented data only. Three invented apps
(`tests/fixtures/design_review/inventory/`) produce exactly the expected
INV-01..INV-04 findings with their counts and scopes. Re-running gives
byte-identical output, whatever the input order. An `unknown` component (no
catalog) is never compared as bespoke. A long whole-row label is never
matched against the action vocabulary. Single-app findings land as app-issue
lines and cross-app ones as fleet-config digest lines, through `filing.py` on
a dry run with a fake filing layer that sends nothing. No filing line carries a
label, a control key or a screenshot path. With no inventory anywhere the
digest, the bodies and the per-app scores are what they were before.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_design_inventory.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
sys.path.insert(0, str(REPO / "tests" / "_lib"))
sys.path.insert(0, str(REPO / "tests"))

from check_harness import CheckHarness  # noqa: E402
from throwaway_dir import throwaway_dir  # noqa: E402
from acceptance.shared import SKIP_EXIT  # noqa: E402

STATE = Path(throwaway_dir("design-inventory-test-state-"))
os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(STATE)
WORK = Path(throwaway_dir("design-inventory-test-work-"))

from design_review import capture, evaluate as ev, filing, fleet, inventory as inv, rubric as rb  # noqa: E402

_h = CheckHarness()
check = _h.check

FIX = REPO / "tests" / "fixtures" / "design_review"
INV_FIX = FIX / "inventory"
APPS = ("app-north", "app-south", "app-east")
CATALOG = ["action-row", "button", "icon-button", "range-tab", "row-menu", "segmented", "select-native", "switch"]
rubric = rb.load_rubric(REPO / "design.rubric.toml")
specs = rb.load_specs(FIX / "spec_inventory.md", FIX / "spec_inventory.md")
REF = inv.type_reference(specs["light"])
# Masked labels and control keys the fixtures carry: none may reach a filing line.
CAPTURED = ("Delete", "Save", "Cancel", "Frobnicate", "More actions", "Remove", "Notifications", "Sort by", "Morning backup",
            "Range", "Model", "Alpha", "Golf", "button.", "select.", "div.toggle", ".png")


def _metrics(target: str) -> dict:
    return json.loads((INV_FIX / f"{target}.json").read_text(encoding="utf-8"))


def _apps() -> list:
    return [inv.app_inventory(t, _metrics(t), "20261018T000000Z", f"fixture:{t}") for t in APPS]


# ---- input: measured screens only, design.md's type roles ---------------------------------------

north = inv.app_inventory("app-north", _metrics("app-north"))
check([s["id"] for s in north["screens"]] == ["desktop-light-home", "iphone-dark-home"],
      f"an errored or absent inventory is dropped, an uncatalogued one kept: {[s['id'] for s in north['screens']]}")
check(inv.app_inventory("x", {"screens": [{"id": "a", "metrics": {}}]}) is None, "no measured inventory -> None, never an empty app")
check(REF == {"heading-1": "system-ui 32px/700", "heading-2": "system-ui 24px/700", "heading-3": "system-ui 20px/700",
              "row-title": "system-ui 16px/600", "secondary": "system-ui 14px/400", "button-label": "system-ui 14px/600",
              "caption": "system-ui 12px/600", "overline": "system-ui 12px/600", "body": "system-ui 16px/400"},
      f"type roles resolve from design.md tokens (rem at a 16px root; row-title takes action-row's titleWeight): {REF}")
check(inv.type_reference({}) == {}, "no design.md tokens -> no reference, never a guessed one")

# newest_inventory walks back past a run with no inventory to the newest that has one
root = STATE / "design-review" / "app-north"
for stamp, doc in (("20261016T000000Z", _metrics("app-north")), ("20261017T000000Z", {"screens": []}), ("20261015T000000Z", _metrics("app-north"))):
    (root / stamp).mkdir(parents=True)
    (root / stamp / "metrics.json").write_text(json.dumps(doc), encoding="utf-8")
(root / "ledger.json").write_text("{}", encoding="utf-8")
newest = inv.newest_inventory("app-north")
check(newest and newest["run"] == "20261016T000000Z" and newest["source"].endswith("metrics.json"),
      f"the newest run that carries an inventory wins; an unmeasured newer run is skipped: {newest and newest['run']}")
check(inv.newest_inventory("app-nowhere") is None, "a target with no runs has no inventory")

# ---- the comparison: exactly the expected findings ------------------------------------------------

sec = inv.compare(_apps(), REF, CATALOG, None, targets=list(APPS) + ["app-west"])
got = {f["id"]: (f["scope"], f["divergent"]) for f in sec["findings"]}
want = {
    "INV-01-caption": ("app", ["app-east"]),
    "INV-01-heading-4": ("fleet", ["app-north", "app-south"]),
    "INV-01-secondary": ("fleet", ["app-east", "app-south"]),
    "INV-02-commit": ("fleet", ["app-east", "app-south"]),
    "INV-02-destructive": ("app", ["app-east"]),
    "INV-03-button": ("fleet", ["app-east", "app-south"]),
    "INV-03-switch": ("app", ["app-north"]),
    "INV-04-other-6-plus": ("app", ["app-south"]),
    "INV-04-time-range-2-5": ("fleet", ["app-east", "app-south"]),
}
check(got == want, f"the fixtures produce exactly the expected findings, scopes and divergent apps: {got}")
check(sec["counts"] == {"INV-01": 3, "INV-02": 2, "INV-03": 2, "INV-04": 2, "app": 4, "fleet": 5},
      f"counts by class and scope: {sec['counts']}")
by_id = {f["id"]: f for f in sec["findings"]}
check(by_id["INV-01-secondary"]["renderings"] == [
          {"rendering": "system-ui 13px/400", "apps": ["app-east", "app-south"], "count": 2},
          {"rendering": "system-ui 14px/400", "apps": ["app-north"], "count": 1}]
      and by_id["INV-01-secondary"]["reference_source"] == "design.md",
      "INV-01: off design.md's value in two apps, counted per rendering (secondary: 13px in 2 apps, 14px in 1)")
check(by_id["INV-01-heading-4"]["reference"] is None and by_id["INV-01-heading-4"]["scope"] == "fleet",
      "INV-01: a role design.md has no value for, rendered two ways in a tie, has no reference and every app diverges")
check(by_id["INV-02-destructive"]["reference"] == "vendored:button 14px/600"
      and by_id["INV-02-destructive"]["reference_source"] == "most apps, vendored",
      "INV-02: the reference is the vendored rendering most apps use; the one bespoke app diverges alone")
check(len(by_id["INV-02-commit"]["renderings"]) == 3 and by_id["INV-02-commit"]["reference"] == "vendored:button 14px/600",
      "INV-02: a lone vendored rendering outranks two different bespoke ones")
check(by_id["INV-03-button"]["counts"] == {"app-east": 4, "app-south": 2} and by_id["INV-03-switch"]["counts"] == {"app-north": 1},
      f"INV-03: bespoke controls counted per app, `n` included: {by_id['INV-03-button']['counts']}")
check(by_id["INV-04-time-range-2-5"]["reference"] == "segmented" and by_id["INV-04-other-6-plus"]["reference"] == "select",
      "INV-04: design.md's control per option count (2-5 segmented, 6+ select) is the reference")
check("INV-04-sort-6-plus" not in by_id, "INV-04: a choice already on design.md's control is no finding")
check(by_id["INV-01-caption"]["examples"] == {"app-east": "desktop-dark-home"},
      "one example screen id per divergent app, the first in sorted order")
check(not any(f["id"].startswith("INV-02-destructive") and "app-north" in f["divergent"] for f in sec["findings"]),
      "an uncatalogued screen's `unknown` component is never compared, so it never makes an app diverge")
check([(u["app"], u["reason"], u["n"]) for u in sec["unclassified"]] == [
          ("app-east", inv.R_NO_OPTIONS, 2), ("app-north", inv.R_SHORT_NO_WORD, 1), ("app-south", inv.R_LONG_LABEL, 1)],
      f"what the DOM cannot decide is listed as unclassified, never guessed: {[(u['app'], u['reason']) for u in sec['unclassified']]}")
check(sec["missing"] == ["app-west"] and [a["target"] for a in sec["apps"]] == ["app-east", "app-north", "app-south"]
      and {a["target"]: a["uncatalogued_screens"] for a in sec["apps"]}["app-north"] == 1,
      "the section names the apps compared, the one with no inventory, and the uncatalogued screens")
check(inv.action_of({"label": "Delete old backups from the archive", "component": "bespoke"}) == (None, inv.R_LONG_LABEL),
      "a long whole-row label is never matched, even when it holds an action word")
check(inv.action_of({"label": "x", "component": inv.ROW_COMPONENT})[0] == "row open", "a vendored action-row tap is the row-open action")

# no catalog: INV-03 still uses the vendored components the inventories show, and says the catalog was missing
no_cat = inv.compare(_apps(), REF, None, "no scaffold checkout")
check(no_cat["catalog"] == {"components": [], "error": "no scaffold checkout"} and "INV-03-button" in {f["id"] for f in no_cat["findings"]}
      and "INV-03-switch" not in {f["id"] for f in no_cat["findings"]},
      "without the catalog only kinds seen vendored somewhere count for INV-03 (no switch is vendored in the fixtures)")

# ---- deterministic ---------------------------------------------------------------------------------

again = inv.compare(_apps(), REF, CATALOG, None, targets=list(APPS) + ["app-west"])
check(json.dumps(sec, sort_keys=False) == json.dumps(again, sort_keys=False), "two runs over the same inventories are byte-identical")
shuffled = _apps()
random.Random(7).shuffle(shuffled)
for a in shuffled:
    random.Random(3).shuffle(a["screens"])
    for s in a["screens"]:
        random.Random(5).shuffle(s["inventory"].get("controls") or [])
third = inv.compare(shuffled, REF, list(reversed(CATALOG)), None, targets=["app-west"] + list(reversed(APPS)))
check(json.dumps(sec) == json.dumps(third), "input order (apps, screens, controls, catalog) never changes the output")

# ---- filing lines: text-only, routed by scope -----------------------------------------------------

app_lines = {t: [filing.finding_line(s) for s in inv.summaries(sec, "app", t)] for t in APPS}
fleet_lines = [filing.finding_line(s) for s in inv.summaries(sec, "fleet")]
check([l.split("**")[1] for l in app_lines["app-east"]] == ["INV-01-caption", "INV-02-destructive"]
      and [l.split("**")[1] for l in app_lines["app-north"]] == ["INV-03-switch"]
      and [l.split("**")[1] for l in app_lines["app-south"]] == ["INV-04-other-6-plus"],
      f"each single-app finding goes to its one app: {app_lines}")
check(len(fleet_lines) == 5 and all("(P2, fleet)" in l for l in fleet_lines), "the cross-app findings are the fleet's lines")
all_lines = [l for ls in app_lines.values() for l in ls] + fleet_lines
leaks = [(w, l) for l in all_lines for w in CAPTURED if w in l]
check(not leaks, f"no filing line carries a label, a control key or a screenshot path: {leaks[:2]}")
check(all("/propagate-vendored" in l and " · e.g. " in l for l in all_lines), "every line names the vendored fix path and an example screen")
check("- [ ] **INV-01-secondary** (P2, fleet) — text role `secondary` renders 2 ways — system-ui 13px/400 in 2 apps (app-east, app-south); "
      "system-ui 14px/400 in 1 app (app-north) · reference system-ui 14px/400, from design.md · e.g. app-east: desktop-dark-home, "
      "app-south: desktop-light-home · fix: the role's type token in the vendored base + /propagate-vendored" in fleet_lines,
      "the cross-app type line reads like the issue's example")
check(any("the vendored `select-native`" in l for l in app_lines["app-south"]), "the INV-04 fix names the catalog component, not the bare kind")

# carry_statuses: an INV line no longer current is fixed, carried as filed elsewhere, or carried as unmeasured
old = ("intro\n\n## Findings\n\n- [x] **INV-01-secondary** (P2, app) — old\n- [ ] **INV-02-gone** (P2, app) — old\n"
       "- [ ] **TOUCH-01** (P0, app) — t\n\n## Review run log\n\n- x\n")
check(inv.carry_statuses(old, sec, "app-east") == {"INV-01-secondary": "fail", "INV-02-gone": "pass"},
      "on an app body: still divergent but filed on the digest -> fail (carried); gone -> pass (fixed)")
check(inv.carry_statuses(old, sec, "app-west") == {"INV-01-secondary": "unmeasured", "INV-02-gone": "unmeasured"},
      "an app with no inventory this run carries its lines as unmeasured, never fixed")
check(inv.carry_statuses(old, None, "app-east") == {"INV-01-secondary": "unmeasured", "INV-02-gone": "unmeasured"},
      "no inventory anywhere -> unmeasured, never `not in rubric`")
check(inv.carry_statuses("- [ ] **TOUCH-01** (P0, app) — t", sec, "app-east") == {}, "a body with no INV line adds no status")
merged = filing.merge_body(old, inv.summaries(sec, "app", "app-east"), [], [],
                           {"TOUCH-01": "pass", **inv.carry_statuses(old, sec, "app-east")}, "- log", "intro", "s", "1")
check("- [x] **INV-01-secondary** (P2, app) — old _(carried — not filed here this run)_" in merged
      and "**INV-02-gone** (P2, app) — old _(fixed — passes since s)_" in merged and "**INV-01-caption** (P2, app)" in merged,
      "the merge keeps the tick, tags a moved line carried and a gone one fixed")

# ---- run_fleet: dry run, fake filing layer, bodies beside the digest ----------------------------------

projects = WORK / "projects.toml"
projects.write_text("".join(f'[{t}]\ncwd_prefix = "{(WORK / t).as_posix()}"\nwebapp_port = {i + 1}\nbrowser_scheme = "http"\n'
                            for i, t in enumerate(APPS)), encoding="utf-8")
compliant = ev.evaluate(json.loads((FIX / "metrics_compliant.json").read_text(encoding="utf-8")), rubric,
                        rb.load_specs(FIX / "spec_compliant.md", FIX / "spec_compliant.md"))


def _review(name: str, table: dict) -> dict:
    d = json.loads(json.dumps(compliant))
    d["target"] = name
    return {"row": {"target": name, "root": None, "run_id": "r1", "run_dir": "x", "probe": "listening", "probe_detail": None,
                    "unmeasured": None, "commit": d.get("commit"), "live_build": None, "overall": dict(d["overall"]),
                    "failed": sorted(r["id"] for r in d["rules"] if r["status"] == "fail"), "unmeasured_rules": 0,
                    "diff": {"fixed": 0, "regressed": 0, "new": 0, "unchanged": 0, "unmeasured": 0}, "previous_run": None,
                    "filed": [], "accepted": [], "problems": []}, "doc": d, "root": None}


sent: list = []
fixtures = {a["target"]: a for a in _apps()}
FIXED_NOW = _dt.datetime(2026, 10, 18, 12, 0, 0, tzinfo=_dt.timezone.utc)
_real_now = capture.utc_now
capture.utc_now = lambda: FIXED_NOW  # generated_at is the only clock in the digest


def _sweep(out: Path, loader) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    return fleet.run_fleet(rubric, specs, ["desktop"], projects, file_issues=False, out_dir=out, reviewer=_review,
                           fetch=lambda r, k: (sent.append(("fetch", r)), {"number": None, "body": ""})[1],
                           upsert=lambda *a: (sent.append(("upsert",) + a[:1]), "url")[1], today="2026-10-18",
                           inventory_loader=loader, catalog={"components": {c: {} for c in CATALOG}, "error": None})


out = WORK / "20261018T120000Z"
dig = _sweep(out, fixtures.get)
first_json = (out / fleet.DIGEST_JSON).read_bytes()
check(sent == [], f"a dry run reads and sends nothing to GitHub (no remote, fake legs untouched): {sent}")
check(dig["inventory"]["counts"] == sec["counts"] and json.loads(first_json)["inventory"] == json.loads(json.dumps(sec | {"missing": []})),
      "the digest JSON carries the inventory section")
bodies = {t: (out / f"issue-{t}.md").read_text(encoding="utf-8") for t in APPS + ("fleet-config", "project-scaffolding")}
findings_of = {t: b.split("## Findings")[1].split("\n## ")[0] for t, b in bodies.items()}
check("**INV-01-caption** (P2, app)" in findings_of["app-east"] and "**INV-02-destructive** (P2, app)" in findings_of["app-east"]
      and "**INV-03-switch** (P2, app)" in findings_of["app-north"] and "**INV-04-other-6-plus** (P2, app)" in findings_of["app-south"],
      "single-app findings are lines on their app's would-be issue body")
check(all(f"**{fid}** (P2, fleet)" in findings_of["fleet-config"] for fid, (scope, _) in want.items() if scope == "fleet")
      and not any(f"**{fid}**" in findings_of["fleet-config"] for fid, (scope, _) in want.items() if scope == "app"),
      "cross-app findings are lines on fleet-config's digest body, single-app ones are not")
check(not any("INV-" in findings_of[t] for t in ("project-scaffolding",))
      and not any(f"**{fid}**" in findings_of[t] for t in APPS for fid, (scope, _) in want.items() if scope == "fleet"),
      "no cross-app finding lands on an app issue or on project-scaffolding's")
body_leaks = [(t, w) for t, b in bodies.items() for w in CAPTURED if w in b]
check(not body_leaks, f"no body carries a label, a control key or a screenshot path: {body_leaks[:3]}")
page = (out / fleet.DIGEST_HTML).read_text(encoding="utf-8")
check('id="inventory"' in page and "INV-03-button" in page and "cross-app" in page and "Frobnicate" not in page,
      "the digest page has an Inventory section, with no masked label on it")
_sweep(out, fixtures.get)
check((out / fleet.DIGEST_JSON).read_bytes() == first_json, "re-running the sweep on the same inventories gives byte-identical digest JSON")

# ---- without inventories nothing changes ---------------------------------------------------------------

plain_out = WORK / "20261018T130000Z"
plain = _sweep(plain_out, lambda t: None)
plain_json = json.loads((plain_out / fleet.DIGEST_JSON).read_text(encoding="utf-8"))
check("inventory" not in plain_json and list(plain_json) == ["schema_version", "stamp", "generated_at", "rubric_version", "judgment",
                                                               "apps", "spec", "scaffold", "promoted", "filing"],
      f"no inventory anywhere -> the digest keeps exactly its pre-#1500 keys: {list(plain_json)}")
check('id="inventory"' not in (plain_out / fleet.DIGEST_HTML).read_text(encoding="utf-8"), "and the digest page has no Inventory section")
for t in APPS:
    r = _review(t, {})
    expect = filing.file_body(r["doc"], "r1", None, "", "2026-10-18", accepted={}, problems=[], promoted=set(plain["promoted"]))["body"]
    check((plain_out / f"issue-{t}.md").read_text(encoding="utf-8") == expect, f"{t}: the app body is byte-identical to the pre-#1500 filing")
check(fleet.compare_inventories(list(APPS), specs, lambda t: None) is None, "compare_inventories with no inventory is None, never an empty section")
check([a["overall"] for a in plain["apps"]] == [a["overall"] for a in dig["apps"]] and [a["failed"] for a in plain["apps"]] == [a["failed"] for a in dig["apps"]],
      "per-app scores and failing rules are the same with and without inventories")
capture.utc_now = _real_now

# per-app rules never read the inventory: the same metrics with and without it evaluate identically
base = json.loads((FIX / "metrics_violating.json").read_text(encoding="utf-8"))
with_inv = json.loads(json.dumps(base))
for s in with_inv["screens"]:
    s.setdefault("metrics", {})["inventory"] = _metrics("app-south")["screens"][0]["metrics"]["inventory"]
vspecs = rb.load_specs(FIX / "spec_violating.md", FIX / "spec_violating.md")
a, b = ev.evaluate(base, rubric, vspecs), ev.evaluate(with_inv, rubric, vspecs)
check([(r["id"], r["status"]) for r in a["rules"]] == [(r["id"], r["status"]) for r in b["rules"]] and a["overall"] == b["overall"],
      "rule results and the overall score are identical with and without an inventory on every screen")

_h.report_and_exit("test_design_inventory", skip_code=SKIP_EXIT)
