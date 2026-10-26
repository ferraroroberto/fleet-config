import tempfile
from pathlib import Path

from _suite import check, v


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

# A merged PR whose file list is empty (GitHub answers 422 when it cannot generate the diff) is unreadable, never `full`
# (fleet-config#1138): `classify([])` fails safe to `full`, which put 46 of 60 PRs above a 15-PR browser-relevant count.
ru = v.routing_report(rt, pr_list=prs + [{"number": 6, "files": []}, {"number": 7, "files": []}],
                      proposed_path=rt / "proposed.toml")
check(rr["prs_unreadable"] == 0 and ru["prs_unreadable"] == 2 and ru["prs"] == 7,
      f"PRs with no file list are counted in prs_unreadable, beside the total -- {ru.get('prs')} / {ru.get('prs_unreadable')}")
check(ru["tiers"] == rr["tiers"] and ru["browser_relevant_full"] <= ru["browser_relevant"],
      f"an unreadable PR is in no tier, so browser_relevant_full cannot exceed browser_relevant -- {ru['tiers']}")
check(sum(ru["tiers"].values()) == ru["prs"] - ru["prs_unreadable"]
      and [r["pr"] for r in ru["per_pr"]] == [r["pr"] for r in rr["per_pr"]],
      "the tiers sum to the readable PRs; per_pr lists no unreadable PR")
check(ru["counterfactual"]["changed"] == rr["counterfactual"]["changed"],
      "an unreadable PR is left out of the --proposed counterfactual")

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
    "tests/e2e/test_c.py": ("from pathlib import Path\nROOT = Path(__file__).parent.parents[2]\n"
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

# Helper imports (fleet-config#1175, parking-manager#58): a conftest fixture called `mockup_seed.seed_live`, which imports
# `tests.burst_fixture` inside the function. The scan listed `mockup_seed.py` (one level) and missed `burst_fixture.py` (two).
# A test-support file (under a `tests/` directory) is followed; an app module's own imports are not.
for rel, body in {
    "tests/e2e/test_h.py": "from tests.mockup_seed import seed_live\n",
    "tests/mockup_seed.py": "def seed_live(path):\n    from tests.burst_fixture import write_burst\n    return write_burst(path)\n",
    "tests/burst_fixture.py": "from tests.deep_helper import deep\n",
    "tests/deep_helper.py": "",
    "tests/only_via_app.py": "",
    "static/helper.py": "from tests.only_via_app import x\n",
}.items():
    (rt / rel).parent.mkdir(parents=True, exist_ok=True)
    (rt / rel).write_text(body, encoding="utf-8")
hh = {h["path"]: (h["kind"], h["imported_by"]) for h in v.routing_report(rt, pr_list=[])["import_holes"]}
check(hh.get("tests/mockup_seed.py") == ("imported", 1) and hh.get("tests/burst_fixture.py") == ("imported", 1)
      and hh.get("tests/deep_helper.py") == ("imported", 1),
      f"imports made by a test-support helper, in a function body or at the top, are followed through the chain -- {hh}")
check("tests/only_via_app.py" not in hh,
      "a module imported only by app source is not followed: the scan walks test support, never the app's import graph")
for rel in ("tests/e2e/test_h.py", "tests/mockup_seed.py", "tests/burst_fixture.py", "tests/deep_helper.py", "tests/only_via_app.py"):
    (rt / rel).unlink()
(rt / "static" / "helper.py").write_text("", encoding="utf-8")

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
