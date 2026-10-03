"""Unit tests for skills/_lib/e2e_value.py, /e2e-audit's time and failure layer (fleet-config#1018).

Synthetic progress logs only, in the shape app-launcher's tests/_progress_log.py
writes (#534, #943, #1231). No gate is started and no GitHub call is made.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_e2e_value.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "_lib"))
import e2e_value as v  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


def _log(day: str, start: str, lines: list) -> str:
    return f"verify-before-ship run started {day} {start}\n" + "".join(l + "\n" for l in lines)


# Run 1: serial, both projections, a WebKit red that stopped at line 40 of its test.
RUN1 = _log("2026-09-20", "10:00:00", [
    "[10:00:00 +    0,0s] ==> phase: pytest (non-e2e)...",
    "[10:00:01 +    0.1s] START tests/test_api.py::test_ok",
    "[10:00:01 +    0.2s] DONE  tests/test_api.py::test_ok (0.1s)",
    "[10:01:00 +    1,0s] ==> phase: pytest e2e (tests/e2e)...",
    "[10:01:00 +    0.1s] START tests/e2e/test_board.py::test_load[chromium]",
    "[10:01:02 +    2.0s] DONE  tests/e2e/test_board.py::test_load[chromium] (2.0s)",
    "[10:01:02 +    2.0s] START tests/e2e/test_board.py::test_load[webkit]",
    "[10:01:06 +    6.0s] DONE  tests/e2e/test_board.py::test_load[webkit] (4.0s)",
    "[10:01:06 +    6.0s] START tests/e2e/test_chat.py::test_link_opens[webkit-iphone]",
    "[10:01:18 +   18.0s] FAILED (call) tests/e2e/test_chat.py::test_link_opens[webkit-iphone]",
    "    | tests/e2e/test_chat.py:40: in test_link_opens",
    "    |     expect(row).to_be_visible()",
    "    | E   AssertionError: timed out",
    "[10:01:18 +   18.0s] DONE  tests/e2e/test_chat.py::test_link_opens[webkit-iphone] (12.0s)",
    "[10:01:18 +   18.0s] START tests/e2e/test_redact.py::test_mask[{'a': 1} b]",
    "[10:01:18 +   18.5s] DONE  tests/e2e/test_redact.py::test_mask[{'a': 1} b] (0.5s)",
    "[10:01:18 +   18.5s] pytest session finished (exit status 1)",
])
# Run 2: the same test red again at a different step, on two xdist workers.
RUN2 = _log("2026-09-21", "23:59:50", [
    "[23:59:50 +    0,0s] ==> phase: pytest e2e (tests/e2e)...",
    "[23:59:50 +    0.1s] START tests/e2e/test_chat.py::test_link_opens[webkit-iphone]",
    "[00:00:05 +   15.0s] FAILED (call) tests/e2e/test_chat.py::test_link_opens[webkit-iphone] [gw1]",
    "    | tests\\e2e\\test_chat.py:52: in test_link_opens",
    "    | E   AssertionError: wrong conversation",
    "[00:00:05 +   15.0s] DONE  tests/e2e/test_chat.py::test_link_opens[webkit-iphone] (15.0s) [gw1]",
    "[00:00:05 +   15.0s] pytest session finished (exit status 1)",
])
# An unfinished run: a START with no DONE. Never "the last completed run".
RUN3 = _log("2026-09-22", "09:00:00", [
    "[09:00:00 +    0,0s] ==> phase: pytest e2e (tests/e2e)...",
    "[09:00:00 +    0.1s] START tests/e2e/test_board.py::test_load[chromium]",
])

# ---- parsing ------------------------------------------------------------------

r1 = v.parse_run(RUN1)
check(r1["complete"] and r1["exit_status"] == 1, "a run with every START done and a session end is complete")
check(r1["nodes"]["tests/e2e/test_redact.py::test_mask[{'a': 1} b]"] == 0.5, "a node id holding spaces parses whole")
check([p["name"] for p in r1["phases"]] == ["pytest (non-e2e)", "pytest e2e (tests/e2e)"]
      and r1["phases"][0]["wall_s"] == 60.0 and r1["phases"][1]["nodes"] == 4, f"phases with wall time and node counts -- {r1['phases']}")
check(r1["failures"] == [{"outcome": "FAILED", "when": "call", "nodeid": "tests/e2e/test_chat.py::test_link_opens[webkit-iphone]",
                          "step": "tests/e2e/test_chat.py:40"}], f"a FAILED line keeps the step its excerpt stopped at -- {r1['failures']}")
r2 = v.parse_run(RUN2)
check(r2["nodes"]["tests/e2e/test_chat.py::test_link_opens[webkit-iphone]"] == 15.0 and r2["failures"][0]["step"] == "tests/e2e/test_chat.py:52",
      "an xdist [gwN] tag is dropped and a backslash path normalised")
check(v._wall_s(r2) == 15.0, "a run crossing midnight keeps a positive wall time")
check(not v.parse_run(RUN3)["complete"], "a START without a DONE is not complete")
check(v.projection_of("t.py::a[webkit-900]") == "webkit" and v.projection_of("t.py::a[chromium]") == "chromium"
      and v.projection_of("t.py::a[x-1]") == "default" and v.projection_of("t.py::a") == "default", "projection from the param id")
check(v.test_of("t.py::a[webkit-900]") == "t.py::a", "test id without its param id")

# ---- measurements -------------------------------------------------------------------------

e2e = {n: s for n, s in r1["nodes"].items() if v.is_e2e(n, ["tests/e2e/"])}
check(len(e2e) == 4 and "tests/test_api.py::test_ok" not in e2e, "only nodes under the test dirs are e2e")
proj = v.projections(e2e)
check(proj["webkit"] == {"nodes": 2, "seconds": 16.0, "mean_s": 8.0} and proj["chromium"]["seconds"] == 2.0,
      f"per-projection nodes, seconds and mean -- {proj}")
check([b["nodes"] for b in v.buckets(e2e)] == [1, 1, 1, 0, 1], "the #1220 duration buckets")
t = v.tail(e2e, slowest_n=1)
check(t["slowest_share"] == round(12.0 / 18.5, 3) and t["top5pct_n"] == 1 and t["max_s"] == 12.0, f"tail share -- {t}")
check(v.tail({})["slowest_share"] is None, "an empty run has no tail share, not 0")
check(v.cost_drivers("page.goto(u)\npage.reload()\nspawn a PTY\n@pytest.mark.real_agent\n")
      == {"page_loads": 2, "shots": 0, "pty_refs": 1, "real_agent": 1}, "static cost drivers")

events = v.failure_events([(Path("a.log"), [r1, r2])])
check(len(events) == 2 and events[0]["date"] == "2026-09-20" and events[0]["projection"] == "webkit", "failure events with date and projection")
rc = v.race_candidates(events)
check(rc == [{"test": "tests/e2e/test_chat.py::test_link_opens", "projections": ["webkit"],
              "steps": ["tests/e2e/test_chat.py:40", "tests/e2e/test_chat.py:52"], "events": 2}],
      f"a test red at two different steps is a race candidate (app-launcher#1222) -- {rc}")
check(v.race_candidates(events[:1]) == [], "one red is not a race candidate")

m = v.text_mentions("- The first had 1 red, `test_compose_bar.py::test_send[chromium]`, which passed alone.\n- test_other passed.")
check([x["nodeid"] for x in m] == ["test_compose_bar", "test_send[chromium]"] and m[1]["projection"] == "chromium",
      f"tests named on a line about a red, with projection; a line with no red word is skipped -- {m}")

# ---- load: overlapping runs in other checkouts ------------------------------------------------

ld = v.load_state(r1, [(Path("wt.log"), [v.parse_run(_log("2026-09-20", "10:00:30", [
    "[10:00:30 +    0,0s] START tests/e2e/x.py::t", "[10:02:00 +    1,0s] DONE  tests/e2e/x.py::t (90.0s)",
    "[10:02:00 +    1,0s] pytest session finished (exit status 0)"]))])])
check(ld["state"] == "loaded" and len(ld["overlaps"]) == 1, "a run overlapping another checkout's run is loaded")
check(v.load_state(r1, [])["state"] == "quiet", "no overlapping run -> quiet (scope: this repo's checkouts)")
check(v.load_state({"started": None, "finished": None}, [])["state"] == "unknown", "no run window -> unknown, never quiet")

# ---- the subcommands against a repo on disk ---------------------------------------------------

tmp = Path(tempfile.mkdtemp(prefix="e2e-value-"))
(tmp / "tests" / "e2e").mkdir(parents=True)
(tmp / "tests" / "e2e" / "test_board.py").write_text("def test_load(page):\n    page.goto('/')\n", encoding="utf-8")
none = v.timing(tmp, ["tests/e2e"])
check(none["status"] == "unknown" and "no timing source" in none["reason"], "no log declared -> unknown (no timing source)")
(tmp / ".fleet.toml").write_text('[e2e]\nprogress_log = "gate.log"\n', encoding="utf-8")
missing = v.timing(tmp, ["tests/e2e"])
check(missing["status"] == "unknown" and "does not exist" in missing["reason"], "a declared log that is absent -> unknown")
(tmp / "gate.log").write_text(RUN3, encoding="utf-8")
check(v.timing(tmp, ["tests/e2e"])["reason"] == "no completed gate run in any checkout's log", "only an unfinished run -> unknown")
(tmp / "gate.log").write_text(RUN1 + RUN2, encoding="utf-8")
(tmp / "CLAUDE.md").write_text("- The full gate takes ~10 min.\n- Lunch takes 30 min.\n", encoding="utf-8")
tm = v.timing(tmp, ["tests/e2e"])
check(tm["status"] == "ok" and tm["run"]["started"] == "2026-09-21T23:59:50" and tm["run"]["e2e_nodes"] == 1,
      f"timing reads the latest completed run -- {tm['run']}")
check(tm["modules"][0]["module"] == "tests/e2e/test_chat.py", "modules heaviest first")
dr = tm["runtime_drift"]
check(dr["status"] == "candidates" and len(dr["claims"]) == 1 and dr["claims"][0]["claimed_min"] == 10.0 and dr["claims"][0]["candidate"],
      f"a gate runtime far from every measured span is a drift candidate; a line not about the gate is ignored -- {dr}")
fl = v.failures(tmp, use_gh=False)
check(fl["logs"]["status"] == "ok" and len(fl["log_events"]) == 2 and fl["race_candidates"][0]["events"] == 2
      and fl["gh"] == {"prs": "skipped", "bug_issues": "skipped"}, "failures reads every run in the log")
(tmp / "junit.xml").write_text('<testsuite><testcase classname="tests.e2e.test_board" name="test_load[webkit]" time="3.5">'
                               '<failure>tests/e2e/test_board.py:2: in test_load</failure></testcase></testsuite>', encoding="utf-8")
ju = v.timing(tmp, ["tests/e2e"], Path("junit.xml"))
check(ju["status"] == "ok" and ju["projections"]["webkit"]["seconds"] == 3.5 and ju["load"]["state"] == "unknown"
      and ju["runtime_drift"]["status"] == "unknown", "a JUnit XML gives timings, but no window: load and drift unknown")

# ---- routing report (step 2): the repo's own classifier, imported ------------------------------

FAKE_CLASSIFIER = '''
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path


class Category(IntEnum):
    NONE = 0
    STATIC = 1
    FULL = 2


@dataclass
class Rule:
    tier: Category
    prefix: object = None
    path: object = None
    extensions: object = None
    label: str = "rule"

    def matches(self, path, ext):
        if self.path is not None:
            return path == self.path
        if self.prefix is not None and not path.startswith(self.prefix):
            return False
        if self.extensions is not None and ext not in self.extensions:
            return False
        return self.prefix is not None or self.extensions is not None


@dataclass
class Routing:
    tier: str
    surface: str = ""


@dataclass
class Config:
    rules: list
    source: str = "declared"


def load_config(path):
    static = Rule(Category.FULL, prefix="static/", label="static-code")
    docs = Rule(Category.NONE, extensions=("md",), label="docs")
    tests = Rule(Category.NONE, prefix="tests/", label="tests")
    e2e = Rule(Category.FULL, prefix="tests/e2e/", label="e2e-test")
    order = [docs, static] if "docs_first" in Path(path).read_text() else [static, docs]
    return Config([e2e, tests] + order)


def _classify_one(path, rules):
    ext = path.rsplit(".", 1)[-1] if "." in path else ""
    for r in rules:
        if r.matches(path, ext):
            return r.tier, r.label
    return Category.FULL, "unclassified"


def classify(paths, config):
    top = max((_classify_one(p, config.rules)[0] for p in paths), default=Category.FULL)
    return Routing({Category.FULL: "full", Category.STATIC: "static", Category.NONE: "skip"}[top])
'''

rt = Path(tempfile.mkdtemp(prefix="e2e-value-routing-"))
check(v.routing_report(rt, pr_list=[])["status"] == "unknown", "no scripts/classify_e2e.py -> unknown, never an empty report")
(rt / "scripts").mkdir()
(rt / "scripts" / "classify_e2e.py").write_text(FAKE_CLASSIFIER, encoding="utf-8")
(rt / ".fleet.toml").write_text("[e2e]\n", encoding="utf-8")
(rt / "proposed.toml").write_text("docs_first = true\n", encoding="utf-8")
prs = [
    {"number": 1, "files": ["static/styles.css", "tests/e2e/test_a.py"]},
    {"number": 2, "files": ["static/_vendored/nav/README.md"]},
    {"number": 3, "files": ["docs/guide.md"]},
    {"number": 4, "files": ["tools/build.sh"]},
    {"number": 5, "files": ["tests/e2e/test_b.py", "tests/test_unit.py"]},
]
rr = v.routing_report(rt, pr_list=prs, proposed_path=rt / "proposed.toml")
check(rr["status"] == "ok" and rr["tiers"] == {"skip": 1, "static": 0, "surface": 0, "full": 4} and rr["browser_relevant"] == 4,
      f"tier distribution through the imported classifier -- {rr.get('tiers')}")
check(rr["full_classes"] == {"static-code": 2, "e2e-test": 2, "unclassified": 1}
      and rr["single_cause"] == {"static-code": 1, "unclassified": 1, "e2e-test": 1},
      f"forcing classes: every PR containing one, and single-cause PRs -- {rr['full_classes']} / {rr['single_cause']}")
check(rr["unclassified"] == [{"path": "tools/build.sh", "prs": 1, "check": "python scripts/classify_e2e.py tools/build.sh"}],
      "unclassified paths listed for a table rule, with the command that checks one")
check([s["path"] for s in rr["shadowed"]] == ["static/_vendored/nav/README.md"] and rr["shadowed"][0]["shadowed"] == ["docs"],
      f"a README a broad prefix rule took over the later *.md rule is shadowed; tests/ after tests/e2e/ is not -- {rr['shadowed']}")
check(rr["shadowed"][0]["next_rule"] == "docs" and rr["shadowed"][0]["drop_safe"] is True
      and rr["shadowed"][0]["check"] == "python scripts/classify_e2e.py static/_vendored/nav/README.md",
      f"the report names the next-matching rule and the check command -- {rr['shadowed'][0]}")
check(rr["gate"] == {"verdict": "not-consumed", "reason": rr["gate"]["reason"], "readers": []},
      f"the report says whether the gate runs this routing at all -- {rr['gate']}")
check(rr["counterfactual"]["changed"] == [{"pr": 2, "from": "full", "to": "skip", "surface": ""}],
      f"--proposed lists every PR whose tier changes -- {rr['counterfactual']}")

# Import holes (task-os#287): files the suite imports that the table routes to `none`.
for rel, body in {
    "tests/e2e/test_a.py": "import os\nfrom tests.fixtures.fake import Fake\nfrom tests.conftest import write_config\nimport static.helper\n",
    "tests/e2e/test_b.py": "from tests.fixtures import fake\nfrom tests.conftest import write_config\n",
    "tests/fixtures/__init__.py": "",
    "tests/fixtures/fake.py": "",
    "tests/fixtures/unused.py": "",
    "tests/conftest.py": "",
    "static/helper.py": "",
}.items():
    (rt / rel).parent.mkdir(parents=True, exist_ok=True)
    (rt / rel).write_text(body, encoding="utf-8")
ih = v.routing_report(rt, pr_list=[])["import_holes"]
check([(h["path"], h["kind"], h["imported_by"], h["rule"]) for h in ih]
      == [("tests/conftest.py", "loaded", 2, "tests"), ("tests/fixtures/fake.py", "imported", 2, "tests"),
         ("tests/fixtures/__init__.py", "imported", 1, "tests")],
      f"a fixture, its package and a conftest the e2e modules import, routed `none`, are holes; a full-routed "
      f"import, an unimported fixture and the stdlib are not -- {ih}")
check(ih[0]["check"] == "python scripts/classify_e2e.py tests/conftest.py", "a hole carries the command that routes it")

# Runtime-read data files (fleet-config#1165, facilitation-suite#165): `tests/conftest.py` read
# `config/config.sample.json` for every e2e instance and the table routed `config/` to `none`; the scan only followed imports.
for rel, body in {
    "tests/e2e/test_c.py": ("from pathlib import Path\nROOT = Path(__file__).parents[2]\n"
                            "SAMPLE = ROOT / 'docs' / 'sample.md'\nSEED = open('tests/data/seed.yaml')\n"
                            "GONE = 'tests/data/missing.yaml'\nBUILT = 'static/app.json'\nNOTE = 'two words.md'\n"),
    "tests/e2e/test_d.py": "SEED = 'tests/data/seed.yaml'\n",
    "tests/data/seed.yaml": "a: 1\n",
    "docs/sample.md": "# sample\n",
    "static/app.json": "{}\n",
}.items():
    (rt / rel).parent.mkdir(parents=True, exist_ok=True)
    (rt / rel).write_text(body, encoding="utf-8")
ih = v.routing_report(rt, pr_list=[])["import_holes"]
reads = [(h["path"], h["kind"], h["imported_by"], h["rule"]) for h in ih if h["kind"] == "read"]
check(reads == [("tests/data/seed.yaml", "read", 2, "tests"), ("docs/sample.md", "read", 1, "docs")],
      f"a data file the suite reads by repo-relative literal or `/` chain, routed `none`, is a hole; a missing file, a full-routed one "
      f"and a non-path string are not -- {reads}")
check(all(h["kind"] != "read" for h in ih if h["path"].endswith(".py")), "a Python file is an import, never a read")
# An absolute path to a real file outside the repo (a system font a story loads) replaced the repo root and crashed `routing` on
# facilitation-suite (`C:/Windows/Fonts/segoepr.ttf` is not in the subpath of the repo): it is not a repo file, so it is skipped.
outside = Path(tempfile.mkdtemp(prefix="e2e-value-outside-")) / "font.ttf"
outside.write_text("x", encoding="utf-8")
(rt / "tests" / "e2e" / "test_f.py").write_text(f"FONT = {outside.as_posix()!r}\nWIN = {str(outside)!r}\n", encoding="utf-8")
ih2 = v.routing_report(rt, pr_list=[])["import_holes"]
check([h["path"] for h in ih2 if h["kind"] == "read"] == [h["path"] for h in ih if h["kind"] == "read"],
      f"an absolute path outside the repo is no read and does not stop the report -- {ih2}")

# --proposed re-checks the table's other findings against the candidate (fleet-config#1165, facilitation-suite#165): the
# counterfactual only listed PR tier changes, so the fixer swapped the file in and classified paths by hand to prove a hole closed.
(rt / "static" / "_vendored" / "nav").mkdir(parents=True, exist_ok=True)
(rt / "static" / "_vendored" / "nav" / "README.md").write_text("# nav\n", encoding="utf-8")
(rt / "tests" / "e2e" / "test_e.py").write_text("README = 'static/_vendored/nav/README.md'\n", encoding="utf-8")
rp = v.routing_report(rt, pr_list=prs, proposed_path=rt / "proposed.toml")
cf = rp["counterfactual"]
check([s_["path"] for s_ in rp["shadowed"]] == ["static/_vendored/nav/README.md"] and cf["shadowed"] == [],
      f"the candidate table's shadowed paths are re-evaluated: the README the declared table shadows is not shadowed under it -- {cf['shadowed']}")
check([u["path"] for u in cf["unclassified"]] == ["tools/build.sh"],
      f"the candidate table's unclassified paths are re-evaluated -- {cf['unclassified']}")
check(cf["holes_opened"] == ["static/_vendored/nav/README.md"] and cf["holes_closed"] == []
      and "static/_vendored/nav/README.md" in [h["path"] for h in cf["import_holes"]]
      and "static/_vendored/nav/README.md" not in [h["path"] for h in rp["import_holes"]],
      f"a README the suite reads that the candidate routes `none` is a hole it opens; one it newly routes `full` is closed -- "
      f"{cf['holes_opened']} / {cf['holes_closed']}")
check(v.routing_report(rt, pr_list=prs)["counterfactual"] is None, "no --proposed table, no counterfactual")

# A gitignored local file the suite reads (facilitation-suite's `sessions.local.yaml`) can never be in a PR diff: not a hole.
# A tree with no git answer keeps every existing file, never a guess.
import git_run  # noqa: E402
rg = Path(tempfile.mkdtemp(prefix="e2e-value-tracked-"))
(rg / "scripts").mkdir()
(rg / "scripts" / "classify_e2e.py").write_text(FAKE_CLASSIFIER, encoding="utf-8")
(rg / ".fleet.toml").write_text("[e2e]\n", encoding="utf-8")
(rg / "tests" / "e2e").mkdir(parents=True)
(rg / "tests" / "data").mkdir()
(rg / "tests" / "e2e" / "test_g.py").write_text("A = 'tests/data/tracked.yaml'\nB = 'tests/data/local.yaml'\n", encoding="utf-8")
(rg / "tests" / "data" / "tracked.yaml").write_text("a: 1\n", encoding="utf-8")
(rg / "tests" / "data" / "local.yaml").write_text("a: 2\n", encoding="utf-8")
(rg / ".gitignore").write_text("local.yaml\n", encoding="utf-8")
check([h["path"] for h in v.routing_report(rg, pr_list=[])["import_holes"] if h["kind"] == "read"]
      == ["tests/data/local.yaml", "tests/data/tracked.yaml"], "outside a git tree every existing file is kept")
git_run.run_git(["-C", str(rg), "init", "-q"], check=True)
git_run.run_git(["-C", str(rg), "add", "tests", ".gitignore"], check=True)
check([h["path"] for h in v.routing_report(rg, pr_list=[])["import_holes"] if h["kind"] == "read"] == ["tests/data/tracked.yaml"],
      "a gitignored file the suite reads is not a hole; a tracked one is")

# Routing sources (fleet-config#1165, facilitation-suite#165): a diff that edits only `.fleet.toml` (the table itself) was routed
# `skip` by the `*.toml` rule, so a routing change never ran the browser suite it reroutes.
check(not any(h["kind"] == "routing-source" for h in v.routing_report(rt, pr_list=[])["import_holes"]),
      "a table that routes `.fleet.toml` and the classifier `full` has no routing-source hole")
rs = Path(tempfile.mkdtemp(prefix="e2e-value-routing-source-"))
(rs / "scripts").mkdir()
(rs / "scripts" / "classify_e2e.py").write_text(FAKE_CLASSIFIER.replace('extensions=("md",)', 'extensions=("md", "toml", "py")'), encoding="utf-8")
(rs / ".fleet.toml").write_text("[e2e]\n", encoding="utf-8")
rsh = [(h["path"], h["kind"], h["imported_by"], h["rule"]) for h in v.routing_report(rs, pr_list=[])["import_holes"]]
check(rsh == [(".fleet.toml", "routing-source", 0, "docs"), ("scripts/classify_e2e.py", "routing-source", 0, "docs")],
      f"the table and the classifier that reads it are holes when the table routes them `none` -- {rsh}")

# ---- stylesheet-aware routing (fleet-config#1033) ------------------------------------------------------
# A sheet-aware classifier (project-scaffolding#289's shape, reduced): `changed_selectors` diffs two
# texts line by line, any line holding `UNSAFE` poisons the sheet, and `classify` narrows a diff to the
# one surface whose selector prefixes own every changed rule. Real git history supplies the blobs.

SHEET_CLASSIFIER = '''
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Surface:
    name: str
    prefixes: tuple

    def owns_selector(self, sel):
        return any(sel.startswith(p) for p in self.prefixes)


@dataclass
class Config:
    surfaces: list
    shared_stylesheets: tuple = ()
    source: str = "declared"
    rules: list = field(default_factory=list)


@dataclass
class Routing:
    tier: str
    surface: str = ""


def load_config(path):
    text = Path(path).read_text()
    sheets = ("static/styles.css",) if "sheets" in text else ()
    board = (".board-",) if "selectors" in text else ()
    return Config([Surface("board", board), Surface("chat", (".chat-",) if board else ())], sheets)


def _classify_one(path, rules):
    return Category.FULL, "static"


class Category:
    FULL = type("C", (), {"name": "FULL"})()


def changed_selectors(old, new):
    if old is None or new is None:
        return None
    a, b = set(old.splitlines()), set(new.splitlines())
    diff = (a ^ b) - {""}
    if any("UNSAFE" in line for line in diff):
        return None
    return {line.split("{")[0].strip() for line in diff}


def classify(paths, config, sheet_changes=None):
    for sheet, sels in (sheet_changes or {}).items():
        if sheet not in config.shared_stylesheets or not sels:
            return Routing("full")
        owners = {s.name for sel in sels for s in config.surfaces if s.owns_selector(sel)}
        if len(owners) == 1 and all(any(s.owns_selector(sel) for s in config.surfaces) for sel in sels):
            return Routing("surface", owners.pop())
    return Routing("full")
'''

import subprocess  # noqa: E402

from git_fixtures import init_repo  # noqa: E402

sr = init_repo(empty_commit=False)


def _git(*args):
    return subprocess.run(["git", "-C", str(sr), *args], capture_output=True, text=True, check=True).stdout.strip()


(sr / "scripts").mkdir()
(sr / "static").mkdir()
(sr / "scripts" / "classify_e2e.py").write_text(SHEET_CLASSIFIER, encoding="utf-8")
(sr / ".fleet.toml").write_text("sheets\n", encoding="utf-8")
(sr / "proposed.toml").write_text("sheets selectors\n", encoding="utf-8")
css = sr / "static" / "styles.css"
css.write_text(".board-card { color: red; }\n.chat-row { color: blue; }\n", encoding="utf-8")
_git("add", "-A")
_git("commit", "-q", "-m", "base")
css.write_text(".board-card { color: green; }\n.chat-row { color: blue; }\n", encoding="utf-8")
_git("commit", "-qam", "board rule")
board_sha = _git("rev-parse", "HEAD")
css.write_text(".board-card { color: green; }\n.chat-row { color: blue; }\n.misc { margin: 0; }\n", encoding="utf-8")
_git("commit", "-qam", "unmapped rule")
misc_sha = _git("rev-parse", "HEAD")
css.write_text(".board-card { color: green; }\n.chat-row { color: blue; }\n.misc { margin: 0; }\n/* UNSAFE */\n",
               encoding="utf-8")
_git("commit", "-qam", "unsafe")
unsafe_sha = _git("rev-parse", "HEAD")
sheet_prs = [
    {"number": 11, "files": ["static/styles.css"], "mergeCommit": board_sha},
    {"number": 12, "files": ["static/styles.css"], "mergeCommit": misc_sha},
    {"number": 13, "files": ["static/styles.css"], "mergeCommit": unsafe_sha},
    {"number": 14, "files": ["static/styles.css"], "mergeCommit": ""},
    {"number": 15, "files": ["static/styles.css"], "mergeCommit": "0" * 40},
]
sx = v.routing_report(sr, pr_list=sheet_prs, proposed_path=sr / "proposed.toml")
check(sx["status"] == "ok" and sx["counterfactual"]["changed"] == [{"pr": 11, "from": "full", "to": "surface", "surface": "board"}],
      f"--proposed shows a sheet PR whose changed rules one surface owns narrowing -- {sx.get('counterfactual')}")
check(sx["sheet_routing"] == {"sheets": ["static/styles.css"], "reasons": {"static/styles.css": {
          "owned by one surface": 1, "unmapped selector": 1, "unsafe": 1, "unreadable": 2}}},
      f"per-sheet reason buckets; no merge commit and an object missing from the clone are unreadable, not unsafe -- {sx.get('sheet_routing')}")
changes, unreadable = v.pr_sheet_changes(v.load_classifier(sr), sr, board_sha, ["static/styles.css"])
check(changes == {"static/styles.css": {".board-card"}} and unreadable == [],
      f"pr_sheet_changes: merge commit vs its first parent -- {changes} / {unreadable}")
(sr / "scripts" / "classify_e2e.py").write_text(
    SHEET_CLASSIFIER.replace("def changed_selectors", "def _no_changed_selectors")
                    .replace("def classify(paths, config, sheet_changes=None):",
                             "def classify(paths, config, *, sheet_changes=None):"),
    encoding="utf-8")
sx_old = v.routing_report(sr, pr_list=sheet_prs, proposed_path=sr / "proposed.toml")
check(sx_old["status"] == "ok" and sx_old["sheet_routing"] == "n/a: classifier routes file lists only"
      and sx_old["counterfactual"]["changed"] == [],
      f"a classifier without changed_selectors keeps file-list routing -- {sx_old.get('sheet_routing')}")
check(rr["sheet_routing"] == "n/a: no shared_stylesheets declared", "no declared sheet -> sheet routing n/a")

# ---- parallelisability (step 2) ---------------------------------------------------------------------

check(v.lpt([5, 4, 3, 3], 2) == 8 and v.lpt([5, 4, 3, 3], 4) == 5 and v.lpt([], 3) == 0, "LPT makespans")
pj = v.projection({"a.py::t1": 10.0, "a.py::t2": 10.0, "b.py::t1": 4.0})
check(pj["serial_s"] == 24.0 and pj["table"][0] == {"workers": 2, "load_s": [16.1, 18.2, 21.0], "loadscope_s": [23.0, 26.0, 30.0]}
      and pj["floor_loadscope"] == {"module": "a.py", "seconds": 20.0} and pj["floor_load"]["seconds"] == 10.0,
      f"projection: per-test and per-module LPT with inflation, and both floors -- {pj.get('table')}")
check(v.projection({})["status"] == "unknown", "no durations -> unknown")

pt = Path(tempfile.mkdtemp(prefix="e2e-value-parallel-"))
(pt / "tests" / "e2e").mkdir(parents=True)
(pt / "tests" / "e2e" / "conftest.py").write_text(
    "import socket, pytest\n\n\ndef _free_port():\n    s = socket.socket()\n    s.bind(('127.0.0.1', 0))\n    return s\n\n"
    "LOG = 'e2e-autoboot-webapp.log'\n\n\n@pytest.fixture(scope=\"session\")\ndef server(tmp_path_factory):\n    pass\n", encoding="utf-8")
(pt / "tests" / "_progress.py").write_text("def w(p):\n    open(p, 'a').write('x')\n", encoding="utf-8")
(pt / "tests" / "e2e" / "test_agent.py").write_text("import pytest\n\npytestmark = pytest.mark.real_agent\n", encoding="utf-8")
(pt / "tests" / "test_unit_elsewhere.py").write_text("LOG = 'other.log'\n", encoding="utf-8")
pb = v.parallel_blockers(pt, ["tests/e2e"])
check(pb["blockers"] == 4 and pb["free_port_race"][0]["state"] == "blocker"
      and pb["fixed_log_names"][0]["names"] == ["e2e-autoboot-webapp.log"] and pb["shared_append"][0]["file"] == "tests/_progress.py"
      and pb["load_sensitive_ungrouped"][0]["state"] == "blocker", f"the four app-launcher#1220 (c) blockers, pre-#1231 shape -- {pb}")
check(all(e["file"] != "tests/test_unit_elsewhere.py" for e in pb["fixed_log_names"]), "non-e2e test modules outside the plugins are not scanned")
check(pb["session_fixtures"] == [{"file": "tests/e2e/conftest.py", "fixture": "server", "state": "info", "per_worker_tmp": True}],
      "session fixtures listed with per-worker tmp isolation")
check(pb["xdist"] == {"installed": "unknown (no .venv)", "declared": False}, "no venv -> xdist installed is unknown, not False")
(pt / "tests" / "e2e" / "conftest.py").write_text(
    "import os, socket\nW = os.environ.get('PYTEST_XDIST_WORKER', '')\n\n\ndef boot():\n    for attempt in range(3):\n"
    "        port = _free_port()\n\n\ndef _free_port():\n    s = socket.socket()\n    s.bind(('127.0.0.1', 0))\n    return s\n\n"
    "LOG = f'e2e-autoboot-{W}.log' if W else 'e2e-autoboot.log'\n", encoding="utf-8")
(pt / "tests" / "_progress.py").write_text("import os\n\n\ndef w(p):\n    if os.environ.get('PYTEST_XDIST_WORKER'):\n        return\n    open(p, 'a').write('x')\n", encoding="utf-8")
(pt / "tests" / "e2e" / "test_agent.py").write_text("import pytest\n\npytestmark = [pytest.mark.real_agent, pytest.mark.serial]\n", encoding="utf-8")
pb2 = v.parallel_blockers(pt, ["tests/e2e"])
check(pb2["blockers"] == 0 and {e["state"] for k in ("free_port_race", "fixed_log_names", "shared_append", "load_sensitive_ungrouped")
                                 for e in pb2[k]} == {"mitigated"}, f"after the #1231 fixes, every blocker reads mitigated -- {pb2}")

serial_run = v.parse_run(_log("2026-09-23", "10:00:00", [
    "[10:00:00 +    0,0s] START tests/e2e/test_jobs.py::test_list[chromium]",
    "[10:00:02 +    2.0s] DONE  tests/e2e/test_jobs.py::test_list[chromium] (2.0s)",
    "[10:00:02 +    2.0s] pytest session finished (exit status 0)"]))
par_run = v.parse_run(_log("2026-09-24", "10:00:00", [
    "[10:00:00 +    0,0s] START tests/e2e/test_jobs.py::test_list[chromium]",
    "[10:00:03 +    3.0s] FAILED (call) tests/e2e/test_jobs.py::test_list[chromium] [gw2]",
    "[10:00:03 +    3.0s] DONE  tests/e2e/test_jobs.py::test_list[chromium] (3.0s) [gw2]",
    "[10:00:03 +    3.0s] pytest session finished (exit status 1)"]))
check(par_run["parallel"] and not serial_run["parallel"], "a run with [gwN] DONE lines is parallel")
check(v.shared_state_evidence([(Path("x.log"), [serial_run, par_run])])
      == [{"nodeid": "tests/e2e/test_jobs.py::test_list[chromium]", "parallel_reds": 1, "green_serially": True}],
      "red under workers, green serially -> shared state (app-launcher#1231), never a flake")

# ---- time budget, growth baseline and the audit trigger (step 3) ----------------------------------

check(v.time_budget_limit('[e2e]\ntime_budget_s = 900\n') == (900, "[e2e] time_budget_s"), "time_budget_s read")
for bad in ("true", "0", "-5", '"900"', "1.5"):
    check(v.time_budget_limit(f"[e2e]\ntime_budget_s = {bad}\n")[0] is None, f"invalid time_budget_s {bad} ignored, never a raised bar")
check(v.time_budget_limit(None) == (None, "no [e2e] time_budget_s declared"), "undeclared time budget")

ROUTED = _log("2026-09-24", "09:00:00", [
    "[09:00:00 +    0,0s] ==> phase: pytest (non-e2e)...",
    "[09:00:00 +    0.1s] START tests/test_api.py::test_ok",
    "[09:00:01 +    1.0s] DONE  tests/test_api.py::test_ok (1.0s)",
    "[09:03:00 +    0,0s] ==> phase: e2e routing: full (e2e-test: tests/e2e/test_board.py)",
    "[09:03:00 +    0,0s] ==> phase: pytest e2e parallel...",
    "[09:03:00 +    0.1s] START tests/e2e/test_board.py::test_load[chromium]",
    "[09:13:00 +  600.0s] DONE  tests/e2e/test_board.py::test_load[chromium] (600.0s) [gw0]",
    "[09:13:00 +  600.0s] ==> phase: pytest e2e serial...",
    "[09:13:00 +    0.1s] START tests/e2e/test_agent.py::test_reconnect[chromium]",
    "[09:15:00 +  120.0s] DONE  tests/e2e/test_agent.py::test_reconnect[chromium] (120.0s)",
    "[09:15:00 +  120.0s] pytest session finished (exit status 0)",
])
rr_run = v.parse_run(ROUTED)
check(rr_run["routed_tier"] == "full" and rr_run["parallel"], "the routed tier is read from the gate's routing line")
check(v.browser_leg_s(rr_run, ["tests/e2e"]) == 720.0, "the browser leg sums every phase that ran e2e nodes (parallel + serial pass)")

tb = Path(tempfile.mkdtemp(prefix="e2e-value-budget-"))
(tb / ".fleet.toml").write_text('[e2e]\nprogress_log = "gate.log"\ntime_budget_s = 600\n', encoding="utf-8")
check(v.time_budget(tb, ["tests/e2e"])["verdict"] == "unknown", "a declared budget with no log is unknown, never within")
(tb / "gate.log").write_text(ROUTED, encoding="utf-8")
tv = v.time_budget(tb, ["tests/e2e"])
check(tv["verdict"] == "over" and tv["seconds"] == 720.0 and tv["limit"] == 600, f"720 s browser leg over a 600 s budget -- {tv}")
(tb / ".fleet.toml").write_text('[e2e]\nprogress_log = "gate.log"\ntime_budget_s = 900\n', encoding="utf-8")
check(v.time_budget(tb, ["tests/e2e"])["verdict"] == "within", "within a 900 s budget")
(tb / "gate.log").write_text(ROUTED.replace("e2e routing: full", "e2e routing: static"), encoding="utf-8")
ts = v.time_budget(tb, ["tests/e2e"])
check(ts["verdict"] == "unknown" and "full-tier" in ts["reason"], f"a run routed below full is not the suite -> unknown -- {ts}")
(tb / "gate.log").write_text(ROUTED, encoding="utf-8")
(tb / ".fleet.toml").write_text('[e2e]\nprogress_log = "gate.log"\n', encoding="utf-8")
check(v.time_budget(tb, ["tests/e2e"])["verdict"] == "undeclared", "no time_budget_s -> undeclared, not a trigger")

import os  # noqa: E402
os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(tb / "state")
check(v.read_audit_record(tb) is None, "no record before the first audit")
rec_path = v.write_audit_record(tb, 40, 120)
check(rec_path.parent == tb / "state" / "e2e-audit" and v.read_audit_record(tb)["raw_tests"] == 40, "record lands in hooks state and reads back")
check(v.growth(49, v.read_audit_record(tb))["trigger"] is False and v.growth(50, v.read_audit_record(tb))["trigger"] is True,
      "the growth trigger fires at +10 test functions")
check(v.growth(12, None) == {"state": "none-recorded", "delta": None, "since": None, "trigger": False}, "no baseline -> none-recorded, no trigger")
g10 = v.growth(50, v.read_audit_record(tb))
check(v.audit_trigger("within", "within", g10)[0] == "yes" and "+10 test functions" in v.audit_trigger("within", "within", g10)[1],
      "growth alone triggers the audit")
check(v.audit_trigger("over", "undeclared", v.growth(40, None)) == ("yes", "over the node budget"), "nodes over triggers")
check(v.audit_trigger("within", "over", v.growth(40, None))[0] == "yes", "time over triggers")
check(v.audit_trigger("within", "unknown", v.growth(41, v.read_audit_record(tb)))[0] == "unknown",
      "an unmeasured leg with nothing else firing is unknown, never no")
check(v.audit_trigger("within", "undeclared", v.growth(38, v.read_audit_record(tb))) ==
      ("no", f"within every declared budget; -2 test functions since {v.read_audit_record(tb)['date']}"), "nothing fired -> no, with the delta")

# ---- second-projection fit (fleet-config#1026) --------------------------------------------------------

pf_root = Path(tempfile.mkdtemp(prefix="e2e-value-projfit-"))
(pf_root / "tests" / "e2e").mkdir(parents=True)
(pf_root / "tests" / "e2e" / "test_mixed.py").write_text(
    "def _open_board(page):\n    return page.locator('#row').bounding_box()\n\n\n"
    "def test_row_fits(page):\n    assert _open_board(page)['width'] <= 390\n\n\n"
    "def test_poll_payload(page):\n    assert page.request.get('/api/board').ok\n\n\n"
    "class TestNav:\n    def test_tab_switch(self, page):\n        page.locator('[role=tablist] button').first.click()\n", encoding="utf-8")
(pf_root / "tests" / "e2e" / "test_phone_only.py").write_text(
    "import pytest\n\npytestmark = pytest.mark.usefixtures('iphone_viewport')\n\n\ndef test_menu(page):\n    page.click('#menu')\n", encoding="utf-8")
pf_nodes = {
    "tests/e2e/test_mixed.py::test_row_fits[webkit]": 3.0, "tests/e2e/test_mixed.py::test_row_fits[chromium]": 2.0,
    "tests/e2e/test_mixed.py::test_poll_payload[webkit]": 4.0, "tests/e2e/test_mixed.py::test_poll_payload[chromium]": 2.5,
    "tests/e2e/test_mixed.py::TestNav::test_tab_switch[webkit-390]": 1.5,
    "tests/e2e/test_phone_only.py::test_menu[webkit]": 2.0,
    "tests/e2e/test_gone.py::test_x[webkit]": 1.0,
}
pf = v.projection_fit(pf_nodes, pf_root)
check(pf["candidates"] == [{"module": "tests/e2e/test_mixed.py", "tests": 1, "nodes": 1, "seconds": 4.0}] and pf["candidate_seconds"] == 4.0,
      f"a behavioural test with no engine or viewport signal is a Chromium-only candidate; Chromium nodes are not counted -- {pf['candidates']}")
check(pf["kept_nodes"] == 4 and {r["module"] for r in pf["kept"]} == {"tests/e2e/test_mixed.py", "tests/e2e/test_phone_only.py", "tests/e2e/test_gone.py"},
      "kept: a signal in a called helper, a nav tablist in a class test, a module-level viewport fixture, and an unreadable module")
check(v.projection_fit({"tests/e2e/test_mixed.py::test_poll_payload[chromium]": 2.5}, pf_root)["candidates"] == [],
      "a Chromium-only suite has nothing to move")

# ---- learnings from two slimming lanes (fleet-config#1134) ---------------------------------------------

# A skipped node pays no browser time: home-automation's progress log writes `SKIPPED (setup)` and then
# `DONE`, and voice-transcriber's "30 -> 22 nodes" counted 9 skipped WebKit nodes as savings.
SKIPPY = _log("2026-10-01", "10:00:00", [
    "[10:00:00 +    0,0s] ==> phase: pytest e2e (tests/e2e)...",
    "[10:00:00 +    0.1s] START tests/e2e/test_board.py::test_load[chromium]",
    "[10:00:03 +    3.0s] DONE  tests/e2e/test_board.py::test_load[chromium] (3.0s)",
    "[10:00:03 +    3.0s] START tests/e2e/test_board.py::test_load[webkit]",
    "[10:00:03 +    3.0s] SKIPPED (setup) tests/e2e/test_board.py::test_load[webkit]",
    "[10:00:03 +    3.0s] DONE  tests/e2e/test_board.py::test_load[webkit] (0.0s)",
    "[10:00:03 +    3.0s] START tests/e2e/test_board.py::test_poll[chromium]",
    "[10:00:04 +    4.0s] DONE  tests/e2e/test_board.py::test_poll[chromium] (1.0s)",
    "[10:00:04 +    4.0s] pytest session finished (exit status 0)",
])
sk = v.parse_run(SKIPPY)
check(sk["skipped"] == ["tests/e2e/test_board.py::test_load[webkit]"] and sk["complete"],
      f"a SKIPPED line marks its node skipped, the run stays complete -- {sk.get('skipped')}")
check(v.executed_e2e(sk, ["tests/e2e"]) == {"tests/e2e/test_board.py::test_load[chromium]": 3.0,
                                            "tests/e2e/test_board.py::test_poll[chromium]": 1.0},
      "executed e2e nodes leave the skipped one out")

# A backend-only gate run, or a narrow surface slice, finishing later must not displace the full run.
SLICE = _log("2026-10-01", "12:00:00", [
    "[12:00:00 +    0,0s] ==> phase: e2e routing: tier=surface target=tests/e2e/test_board.py",
    "[12:00:00 +    0,0s] ==> phase: pytest e2e (surface)...",
    "[12:00:00 +    0.1s] START tests/e2e/test_board.py::test_poll[chromium]",
    "[12:00:01 +    1.0s] DONE  tests/e2e/test_board.py::test_poll[chromium] (1.0s)",
    "[12:00:01 +    1.0s] pytest session finished (exit status 0)",
])
BACKEND = _log("2026-10-01", "13:00:00", [
    "[13:00:00 +    0,0s] ==> phase: pytest (non-e2e)...",
    "[13:00:00 +    0.1s] START tests/test_api.py::test_ok",
    "[13:00:01 +    1.0s] DONE  tests/test_api.py::test_ok (1.0s)",
    "[13:00:01 +    1.0s] pytest session finished (exit status 0)",
])
tt = Path(tempfile.mkdtemp(prefix="e2e-value-tier-"))
(tt / ".fleet.toml").write_text('[e2e]\nprogress_log = "gate.log"\n', encoding="utf-8")
(tt / "gate.log").write_text(SKIPPY + SLICE + BACKEND, encoding="utf-8")
ttm = v.timing(tt, ["tests/e2e"])
check(ttm["status"] == "ok" and ttm["run"]["started"] == "2026-10-01T10:00:00" and ttm["run"]["slice"] is None,
      f"timing takes the latest full-tier run with e2e nodes, not a later slice or backend-only run -- {ttm.get('run')}")
check(ttm["run"]["e2e_nodes"] == 2 and ttm["run"]["e2e_skipped"] == 1 and ttm["projections"]["chromium"]["nodes"] == 2
      and "webkit" not in ttm["projections"], f"executed nodes only; skipped nodes reported apart -- {ttm['run']}")
check(ttm["first_node"]["nodeid"] == "tests/e2e/test_board.py::test_load[chromium]" and ttm["first_node"]["seconds"] == 3.0,
      f"the first executed node is named: it carries the session boot -- {ttm.get('first_node')}")

# Staleness (fleet-config#1175, app-launcher#1375): the only log on disk was a 612-node run from before the suite was
# 485 nodes, and timing reported `ok` over it, doubling every paid figure. The run covers 3 e2e nodes (2 executed, 1 skipped).
check(ttm["freshness"]["status"] == "unchecked" and "not measured" in ttm["freshness"]["reason"],
      f"no suite node count given: the freshness is its own `unchecked` state, never folded into fresh -- {ttm['freshness']}")
tfresh = v.timing(tt, ["tests/e2e"], suite_nodes=3)
check(tfresh["status"] == "ok" and tfresh["freshness"] == {"status": "fresh", "run_nodes": 3, "suite_nodes": 3, "reason": None},
      f"a run covering the suite's node count is fresh, and names both counts it compared -- {tfresh['freshness']}")
tstale = v.timing(tt, ["tests/e2e"], suite_nodes=5)
check(tstale["status"] == "stale" and tstale["freshness"]["status"] == "stale" and "fresh baseline" in tstale["reason"]
      and "3" in tstale["reason"] and "5" in tstale["reason"] and tstale["run"]["e2e_nodes"] == 2,
      f"a run covering 3 nodes of a 5-node suite is stale: status `stale`, the numbers kept for reading, a fresh baseline asked for -- "
      f"{tstale['status']} {tstale.get('reason')}")
check(v.stale_nodes(100, 109)["status"] == "fresh" and v.stale_nodes(100, 112)["status"] == "stale"
      and v.stale_nodes(112, 100)["status"] == "stale" and v.stale_nodes(100, 95)["status"] == "fresh"
      and v.stale_nodes(100, 90)["status"] == "stale",
      "the tolerance is 10% of the suite's collected nodes, in either direction")
check(v.stale_nodes(612, 485)["status"] == "stale", "app-launcher's 612-node log against its 485-node suite is stale")
check(v.stale_nodes(5, 0)["status"] == "unchecked" and v.stale_nodes(5, None)["status"] == "unchecked",
      "an empty or unmeasured suite count is unchecked, never fresh")
(tt / "gate.log").write_text(SLICE + BACKEND, encoding="utf-8")
tsl = v.timing(tt, ["tests/e2e"])
check(tsl["status"] == "ok" and tsl["run"]["routed_tier"] == "surface" and "surface" in str(tsl["run"]["slice"]),
      f"with only a slice on record, timing measures it and says it is a slice, not the suite -- {tsl.get('run')}")
check(v.timing(tt, ["tests/e2e"], suite_nodes=50)["status"] == "ok" and v.timing(tt, ["tests/e2e"], suite_nodes=50)["freshness"]["status"] == "slice",
      "a slice covers fewer nodes than the suite by design: it is never called stale")

# page_loads: a module booting through a helper or a fixture paid page loads the old count missed.
BOOT_MOD = (
    "def boot_home(page):\n    page.goto('/')\n\n\n"
    "def test_a(page):\n    boot_home(page)\n\n\n"
    "def test_b(page):\n    boot_home(page)\n    page.reload()\n\n\n"
    "def test_c(authed_page):\n    assert authed_page.title()\n"
)
check(v.cost_drivers(BOOT_MOD, frozenset({"authed_page"}))["page_loads"] == 4,
      "page loads count calls to a loading helper and tests taking a loading fixture, not the helper's own goto")
check(v.loaders("import pytest\n\n@pytest.fixture\ndef authed_page(page):\n    page.goto('/login')\n    return page\n\n"
                "def _nothing():\n    return 1\n") == {"authed_page"}, "a fixture or helper holding a goto is a loader")

# shots (task-os#278): screenshot calls were a third of a suite's wall time and nothing counted them. A call
# to a helper that screenshots counts once per call site; the helper's own `screenshot` is not counted again.
SHOT_MOD = (
    "def shot(page, name):\n    page.screenshot(path=name)\n\n\n"
    "def test_a(page):\n    shot(page, 'a')\n    shot(page, 'b')\n    page.screenshot(path='c')\n"
)
check(v.cost_drivers(SHOT_MOD)["shots"] == 3, "shots count helper calls and direct screenshots, not the helper's own call")
check(v.cost_drivers("def test_x(page):\n    shot(page, 'a')\n    shot(page, 'b')\n", frozenset(), frozenset({"shot"}))["shots"] == 2,
      "a shot helper defined in a shared conftest counts at the call sites in a module")
sh = Path(tempfile.mkdtemp(prefix="e2e-value-shots-"))
(sh / "tests" / "e2e").mkdir(parents=True)
(sh / "tests" / "e2e" / "conftest.py").write_text("def shot(page, name):\n    page.screenshot(path=name)\n", encoding="utf-8")
(sh / "tests" / "e2e" / "test_a.py").write_text("def test_a(page):\n    shot(page, 'a')\n    shot(page, 'b')\n", encoding="utf-8")
mods = v.modules({"tests/e2e/test_a.py::test_a[chromium]": 4.0}, sh, ["tests/e2e"])
check(mods[0]["shots"] == 2, f"modules() reports shots through the conftest's shot helper -- {mods}")

# runtime_drift: a threshold, a history figure and an unrelated README line are not runtime claims.
claims = v.runtime_claims(
    "- Local runtime contract: 270 executions in ~3.5 min (3m30s, pytest). Investigate if a full run exceeds "
    "**~7 min** (the previous ~10 min was 2x an older measurement).\n"
    "Renew iCloud browser trust: open the Presence card, type the code (`expired` after 10 min / `failed`).\n"
    "A background telemetry sampler runs on a gentle cadence (default 5 min), so its reading gate defaults off.\n",
    "CLAUDE.md")
check([c["claimed_min"] for c in claims] == [3.5],
      f"only the measured runtime is a claim: thresholds, history and figures far from a gate word are not -- {claims}")

# waits: fixed sleeps, page timers, real poll constants and long timeouts, with what each one is paid.
wr = Path(tempfile.mkdtemp(prefix="e2e-value-waits-"))
(wr / "tests" / "e2e").mkdir(parents=True)
(wr / "tests" / "e2e" / "test_feedback.py").write_text(
    "POLL_MS = 15_000\n"
    "SLOW_INIT = '''\n"
    "window.fetch = function(u) {\n"
    "  return new Promise(function(res) { setTimeout(function() { res(orig(u)); }, 750); });\n"
    "};\n'''\n\n\n"
    "def _install(page):\n    page.add_init_script(SLOW_INIT)\n\n\n"
    "def test_loading(page):\n    _install(page)\n    page.goto('/')\n\n\n"
    "def test_settle(page):\n    page.goto('/')\n    page.wait_for_timeout(500)\n\n\n"
    "def test_retry(page):\n    page.goto('/')\n    expect(page.locator('#s')).to_have_text('Online', timeout=20_000)\n", encoding="utf-8")
(wr / "tests" / "e2e" / "conftest.py").write_text(
    "import time\n\n\ndef _wait_up(url):\n    time.sleep(0.4)\n", encoding="utf-8")
sites = v.wait_sites(wr, ["tests/e2e"])
kinds = {(s["file"].rsplit("/", 1)[-1], s["kind"], s["ms"], s["scope"]) for s in sites}
check(kinds == {("test_feedback.py", "poll-constant", 15000, "POLL_MS"), ("test_feedback.py", "page-timer", 750, "SLOW_INIT"),
                ("test_feedback.py", "sleep", 500, "test_settle"), ("test_feedback.py", "long-timeout", 20000, "test_retry"),
                ("conftest.py", "sleep", 400, "_wait_up")},
      f"every wait shape with its milliseconds and enclosing scope -- {sorted(kinds)}")
wnodes = {"tests/e2e/test_feedback.py::test_loading[chromium]": 1.9, "tests/e2e/test_feedback.py::test_loading[webkit]": 2.1,
          "tests/e2e/test_feedback.py::test_settle[chromium]": 0.9, "tests/e2e/test_feedback.py::test_retry[chromium]": 15.6,
          "tests/e2e/test_feedback.py::test_retry[webkit]": 15.8}
ranked = v.rank_waits(sites, wnodes, wr)
check(ranked[-1]["scope"] == "test_retry" and ranked[-1]["measured_s"] == 31.4 and ranked[-1]["nodes"] == 2
      and ranked[-1]["ceiling"] is True and not any(r["ceiling"] for r in ranked[:-1]),
      f"a `timeout=` is a ceiling, not a wait: it ranks after every fixed wait, flagged -- {ranked}")
check([r["scope"] for r in ranked[:3]] == ["SLOW_INIT", "test_settle", "_wait_up"],
      f"fixed sleeps and page timers rank by the seconds they are paid, not by the seconds of the test around them -- "
      f"{[(r['scope'], r['paid_s']) for r in ranked]}")
slp = next(r for r in ranked if r["scope"] == "test_settle")
tmr = next(r for r in ranked if r["scope"] == "SLOW_INIT")
check(slp["paid_s"] == 0.5 and tmr["nodes"] == 2 and tmr["measured_s"] == 4.0 and tmr["paid_s"] == 1.5,
      f"a fixed sleep or page timer is paid on every executed node that reaches it -- {slp} / {tmr}")
check(next(r for r in ranked if r["scope"] == "_wait_up")["nodes"] is None,
      "a wait in a shared conftest is not attributed to tests")

# poll loops (fleet-config#1165, facilitation-suite#165): a sleep inside a condition loop ends when the condition holds,
# so it costs its interval at most. 6 of 11 listed sleeps were that; the "paid" figure overstated the saving 1.75x.
pr_ = Path(tempfile.mkdtemp(prefix="e2e-value-pollsleep-"))
(pr_ / "tests" / "e2e").mkdir(parents=True)
(pr_ / "tests" / "e2e" / "test_poll.py").write_text(
    "import time\n\n\n"
    "def test_deadline(page):\n    deadline = time.monotonic() + 5\n    while time.monotonic() < deadline:\n"
    "        if ready():\n            break\n        time.sleep(0.2)\n\n\n"
    "def test_cond(page):\n    while not ready():\n        page.wait_for_timeout(300)\n\n\n"
    "def test_forever(page):\n    while True:\n        time.sleep(0.4)\n\n\n"
    "def test_taps(page):\n    for _ in range(5):\n        time.sleep(0.15)\n\n\n"
    "def _wait_for_calls(page, calls, n):\n    for _ in range(40):\n        if len(calls) >= n:\n            return\n"
    "        page.wait_for_timeout(250)\n\n\n"
    "def test_for_break(page):\n    for _ in range(10):\n        if ready():\n            break\n        time.sleep(0.1)\n\n\n"
    "def test_fixed(page):\n    time.sleep(0.6)\n", encoding="utf-8")
psites = {s["scope"]: s for s in v.wait_sites(pr_, ["tests/e2e"])}
check({k: psites[k]["kind"] for k in psites} == {"test_deadline": "poll-sleep", "test_cond": "poll-sleep", "test_forever": "sleep",
                                                  "test_taps": "sleep", "_wait_for_calls": "poll-sleep", "test_for_break": "poll-sleep",
                                                  "test_fixed": "sleep"},
      f"a sleep in a loop that can end on its condition is a poll, not a fixed wait; a `for` retry loop that returns or breaks "
      f"on a condition is one too (app-launcher#1375: `for _ in range(40)` priced 2.5 s for a 250 ms interval); "
      f"a for-loop over taps with no exit and an endless loop sleep still are fixed -- "
      f"{ {k: s['kind'] for k, s in psites.items()} }")
pn = {f"tests/e2e/test_poll.py::{t}[chromium]": 1.0 for t in ("test_deadline", "test_cond", "test_forever", "test_taps", "test_fixed")}
prk = {r["scope"]: r for r in v.rank_waits(list(psites.values()), pn, pr_)}
check(prk["test_deadline"]["paid_s"] is None and prk["test_deadline"]["ceiling"] is True and prk["test_fixed"]["paid_s"] == 0.6
      and {r["scope"] for r in v.rank_waits(list(psites.values()), pn, pr_)[-4:]} == {"test_deadline", "test_cond", "_wait_for_calls", "test_for_break"},
      f"a poll sleep is never priced as paid seconds and ranks after every fixed wait -- {prk['test_deadline']}")

# slow_fixtures (fleet-config#1170, local-llm-hub#644): 40 s of a 76 s run was a session fixture's `httpx.get(..., timeout=_WARMUP_TIMEOUT)`
# with `_WARMUP_TIMEOUT = 90.0` (seconds). `waits` read `timeout=` as integer milliseconds, so a float-seconds timeout never matched
# and the audit ranked 4 s of sleeps first.
sf = Path(tempfile.mkdtemp(prefix="e2e-value-slowfix-"))
(sf / "tests" / "e2e").mkdir(parents=True)
(sf / "tests" / "e2e" / "test_tab.py").write_text(
    "import httpx\nimport pytest\n\n_WARMUP_TIMEOUT = 90.0\n_QUICK = 5.0\n\n\n"
    "@pytest.fixture(scope='session', autouse=True)\ndef _warm(admin_url):\n"
    "    httpx.get(admin_url, params={}, timeout=_WARMUP_TIMEOUT)\n\n\n"
    "@pytest.fixture\ndef _short(admin_url):\n    httpx.get(admin_url, timeout=_QUICK)\n\n\n"
    "def _fetch(url):\n    return httpx.get(url, timeout=45)\n\n\n"
    "def _click(page):\n    page.click('a', timeout=45000)\n    page.wait_for_selector('b', timeout=60.0 * 1000)\n\n\n"
    "def test_x(page):\n    httpx.get('u', timeout=120.0)\n    page.wait_for_selector('#x', timeout=60000)\n", encoding="utf-8")
slow = v.slow_fixtures(sf, ["tests/e2e"])
check([(r["name"], r["fixture_scope"], r["autouse"], r["timeout_s"]) for r in slow] == [("_warm", "session", True, 90.0), ("_fetch", None, False, 45.0)],
      f"a fixture or helper (not a test) with a seconds-valued timeout of 30 s or more is listed, longest first; a short one, a test, "
      f"and a Playwright millisecond timeout are not -- {slow}")
check(slow[0]["file"] == "tests/e2e/test_tab.py" and slow[0]["line"] == 9 and slow[0]["is_fixture"] is True and slow[1]["is_fixture"] is False,
      f"each entry names its file, line and whether it is a pytest fixture -- {slow[0]}")
check(v.slow_fixtures(Path(tempfile.mkdtemp(prefix="e2e-value-slowfix-none-")), ["tests/e2e"]) == [], "no test tree, no slow fixtures")

# slow_nodes + app_timers (fleet-config#1157, photo-ocr#127): a 1 s poll in app source (`poll.js`) was waited out twice
# by one test, invisible to a scan of the test tree. The junit showed it: 2.6 s against a 0.2 s median.
sn = v.slow_nodes({"tests/e2e/test_a.py::test_boot[chromium]": 7.1, "tests/e2e/test_a.py::test_fast[chromium]": 0.2,
                   "tests/e2e/test_a.py::test_fast2[chromium]": 0.2, "tests/e2e/test_a.py::test_fast3[chromium]": 0.2, "tests/e2e/test_a.py::test_fast4[chromium]": 0.2,
                   "tests/e2e/test_a.py::test_fast5[chromium]": 0.3, "tests/e2e/test_a.py::test_fast6[chromium]": 0.2,
                   "tests/e2e/test_a.py::test_extract[chromium]": 2.6, "tests/e2e/test_a.py::test_mid[chromium]": 0.9})
check([r["nodeid"].rsplit("::", 1)[1] for r in sn] == ["test_extract[chromium]"],
      f"a node 10x the median and over 1 s is a find-the-wait candidate; the boot node and a sub-second one are not -- {sn}")
check(v.slow_nodes({}) == [] and v.slow_nodes({"tests/e2e/test_a.py::test_x[chromium]": 9.0}) == [],
      "no nodes, or one node (nothing to be an outlier against), yields no candidates")
ap = Path(tempfile.mkdtemp(prefix="e2e-value-apptimers-"))
(ap / "tests" / "e2e").mkdir(parents=True)
(ap / "app" / "static").mkdir(parents=True)
(ap / "app" / "static" / "_vendored").mkdir()
(ap / "node_modules" / "x").mkdir(parents=True)
(ap / "app" / "static" / "poll.js").write_text(
    "export async function poll(id) {\n  for (;;) {\n    await sleep(1000);\n    const r = await fetch(id);\n  }\n}\n"
    "setInterval(tick, 30_000);\nsetTimeout(() => hide(), 250);\nsetTimeout(function () { save(); }, 4000);\n", encoding="utf-8")
(ap / "app" / "static" / "_vendored" / "lib.js").write_text("setTimeout(f, 9000);\n", encoding="utf-8")
(ap / "node_modules" / "x" / "i.js").write_text("setTimeout(f, 9000);\n", encoding="utf-8")
(ap / "tests" / "e2e" / "helper.js").write_text("setTimeout(f, 9000);\n", encoding="utf-8")
(ap / "app" / "static" / "app.min.js").write_text("setTimeout(f,9000);\n", encoding="utf-8")
at = v.app_timers(ap, ["tests/e2e"])
check([(t["file"], t["line"], t["kind"], t["ms"]) for t in at] ==
      [("app/static/poll.js", 7, "interval", 30000), ("app/static/poll.js", 9, "timeout", 4000), ("app/static/poll.js", 3, "sleep", 1000)],
      f"app-source timers of 1 s or more, longest first; sub-second, vendored, node_modules, test and minified files are left out -- {at}")
check(v.app_timers(Path(tempfile.mkdtemp(prefix="e2e-value-empty-")), ["tests/e2e"]) == [], "no app JS, no timers")

# first_node.boot (fleet-config#1157, photo-ocr#127): a "session boot 6.9 s" finding was one cold run in a fresh
# worktree; warm, the same node took under 0.3 s. A boot is a finding only once a second run in the same
# checkout repeats it.
def _boot_run(day: str, start: str, first_s: float) -> str:
    h, m, sec = (int(x) for x in start.split(":"))
    lines = [f"[{start} +    0,0s] ==> phase: pytest e2e (tests/e2e)..."]
    for i, secs in enumerate([first_s, 0.5, 0.5, 0.5]):
        nid = f"tests/e2e/test_board.py::test_n{i}[chromium]"
        lines += [f"[{start} +    0.1s] START {nid}", f"[{start} +    1.0s] DONE  {nid} ({secs}s)"]
    lines.append(f"[{start} +    9.0s] pytest session finished (exit status 0)")
    return _log(day, start, lines)


bt = Path(tempfile.mkdtemp(prefix="e2e-value-boot-"))
(bt / ".fleet.toml").write_text('[e2e]\nprogress_log = "gate.log"\n', encoding="utf-8")
(bt / "gate.log").write_text(_boot_run("2026-10-01", "10:00:00", 7.1), encoding="utf-8")
check(v.timing(bt, ["tests/e2e"])["first_node"]["boot"] == "single-run",
      "one run on record: the first node is a cold single run, unconfirmed")
(bt / "gate.log").write_text(_boot_run("2026-10-01", "10:00:00", 7.1) + _boot_run("2026-10-01", "11:00:00", 6.0), encoding="utf-8")
check(v.timing(bt, ["tests/e2e"])["first_node"]["boot"] == "repeated",
      "the two newest runs of one log both carry the boot: a repeated cost")
(bt / "gate.log").write_text(_boot_run("2026-10-01", "10:00:00", 7.1) + _boot_run("2026-10-01", "11:00:00", 0.6), encoding="utf-8")
fn = v.timing(bt, ["tests/e2e"])["first_node"]
check(fn["boot"] == "not-repeated" and fn["seconds"] == 0.6,
      f"a warm re-run without the boot: the cold run was a one-off, not a finding -- {fn}")

# checkout + skip reasons (fleet-config#1157, photo-ocr#127): the baseline ran from a linked worktree with no
# certificates, so the cert test skipped as "not HTTPS" and the suite booted over plain HTTP. A run in a linked
# worktree can differ from the primary in skips and boot; say so, and say why each node skipped.
ck = Path(tempfile.mkdtemp(prefix="e2e-value-checkout-"))
(ck / "primary" / ".git").mkdir(parents=True)
(ck / "linked").mkdir()
(ck / "linked" / ".git").write_text("gitdir: ../primary/.git/worktrees/linked", encoding="utf-8")
(ck / "primary" / "webapp").mkdir()
(ck / "primary" / "webapp" / "junit.xml").write_text("<x/>", encoding="utf-8")
(ck / "bare").mkdir()
check(v.checkout_of(ck / "primary" / "webapp" / "junit.xml")["linked_worktree"] is False
      and v.checkout_of(ck / "primary" / "webapp" / "junit.xml")["root"] == str((ck / "primary").resolve()),
      "a file inside a normal checkout: the root is the checkout, not a linked worktree")
check(v.checkout_of(ck / "linked" / "x.xml")["linked_worktree"] is True, "a `.git` file marks a linked worktree")
check(v.checkout_of(ck / "bare" / "x.xml") == {"root": None, "linked_worktree": None},
      "outside any checkout the answer is unknown, never a guess")
sj = Path(tempfile.mkdtemp(prefix="e2e-value-skipreasons-"))
(sj / ".git").write_text("gitdir: ../elsewhere/.git/worktrees/sj", encoding="utf-8")
(sj / "junit.xml").write_text(
    '<testsuite><testcase classname="tests.e2e.test_cert" name="test_cert_lifetime[chromium]" time="0.0"><skipped message="not HTTPS" type="pytest.skip">x</skipped></testcase>'
    '<testcase classname="tests.e2e.test_cert" name="test_cert_lifetime[webkit]" time="0.0"><skipped message="not HTTPS" type="pytest.skip">x</skipped></testcase>'
    '<testcase classname="tests.e2e.test_board" name="test_load[chromium]" time="1.0"/></testsuite>', encoding="utf-8")
sjt = v.timing(sj, ["tests/e2e"], Path("junit.xml"))
check(sjt["run"]["skip_reasons"] == [{"reason": "not HTTPS", "nodes": 2}] and sjt["run"]["checkout"]["linked_worktree"] is True,
      f"a JUnit run in a linked worktree names the checkout and why each node skipped -- {sjt['run']}")
check(v.timing(tt, ["tests/e2e"])["run"]["skip_reasons"] is None,
      "a progress log carries no skip reason: unknown, not an empty list")

# routing: the rule that wins once the first one stops matching, and the command that checks it.
class _R:  # noqa: E302
    def __init__(self, tier, label, prefix=None, path=None, extensions=None):
        self.tier, self.label, self.prefix, self.path, self.extensions = tier, label, prefix, path, extensions

    def matches(self, path, ext):
        if self.path is not None:
            return path == self.path
        if self.prefix is not None and not path.startswith(self.prefix):
            return False
        if self.extensions is not None and ext not in self.extensions:
            return False
        return self.prefix is not None or self.extensions is not None


HA_RULES = [_R(1, "vendored-static", prefix="app/webapp/static/_vendored/", extensions=("html", "md")),
            _R(2, "webapp", prefix="app/webapp/"), _R(0, "docs", extensions=("md",))]
she = v.shadow_entry("app/webapp/static/_vendored/nav/README.md", HA_RULES)
check(she is not None and she["shadowed"] == ["docs"] and she["next_rule"] == "webapp" and she["next_tier"] == 2
      and she["drop_safe"] is False,
      f"home-automation#778: dropping md from the static rule hands the README to the full webapp rule, not docs -- {she}")
safe = v.shadow_entry("static/_vendored/nav/README.md", [_R(2, "static-code", prefix="static/"), _R(0, "docs", extensions=("md",))])
check(safe is not None and safe["next_rule"] == "docs" and safe["drop_safe"] is True, f"no rule in between: dropping is safe -- {safe}")
check(v.shadow_entry("tests/e2e/test_a.py", [_R(2, "e2e", prefix="tests/e2e/"), _R(0, "tests", prefix="tests/")]) is None,
      "a general rule after a specific one is the intended order")
check(v.classify_cmd(["tray.bat", ".gitignore"]) == "python scripts/classify_e2e.py tray.bat .gitignore",
      "the exact classifier command that checks a proposed rule's paths")

_h.report_and_exit("test_e2e_value")
