"""Tests for the fleet structure census (skills/_lib/structure_census/).

Three layers:
  1. `measure`, pure -- which files count, line counting, Python and JS function spans (a JS
     function with no closer is skipped, not guessed), flat folders and `src`->`app` imports.
  2. `repo_read.census_repo` on throwaway git repos -- the numbers come from `origin/main` while
     the working tree holds something else, `as_of` re-reads an earlier commit, the vendored
     share and the enabled ratchets are read from the ref's `.fleet.toml`, and a missing
     checkout is `unknown` rather than zero.
  3. `census.compare`, pure -- totals over the repos measured in both runs, the active subset,
     and shapes taken from the later run.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/structure_census/test_structure_census.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import make_upstream_and_clone, run_git  # noqa: E402
from structure_census import census, measure, repo_read  # noqa: E402
from throwaway_dir import throwaway_dir  # noqa: E402

_h = CheckHarness()
check = _h.check


# ---- 1. measure ----

check(measure.is_code("src/app.py") and measure.is_code("app/webapp/static/styles.css"),
      "is_code: python and css count")
for _path in ("app/static/_vendored/nav/nav.js", "node_modules/x/index.js", "tests/fixtures/big.py",
              "static/app.min.js", "package-lock.json", "README.md", "docs/a.html.txt"):
    check(not measure.is_code(_path), f"is_code: {_path} is not counted")
check(measure.count_lines("a\nb\n") == 2 and measure.count_lines("a\nb") == 2 and measure.count_lines("") == 0,
      "count_lines: a missing final newline still counts its line, an empty file is zero")
check(measure.kind("tests/test_a.py") == "test" and measure.kind("src/a.py") == "py",
      "kind: test files are their own kind")

_py = "def outer():\n    def inner():\n        return 1\n    return inner\n"
_spans = sorted(measure.python_functions(_py, "m.py"))
check(_spans == [(2, "m.py:2 inner"), (4, "m.py:1 outer")],
      f"python_functions: nested functions count on their own and inside the outer one, got {_spans!r}")
check(measure.python_functions("def (:\n", "bad.py") is None, "python_functions: an unparseable file is None, not []")

_body = "\n".join(f"  x{i}();" for i in range(25))
_js = f"function mountA() {{\n{_body}\n}}\nfunction broken() {{\n{_body}\nfunction short() {{\n  y();\n}}\n"
_js_spans = measure.js_functions(_js, "v.js")
check(_js_spans == [(27, "v.js:1 mountA")],
      f"js_functions: a closed function is spanned, an unclosed one skipped, a short one unrecorded, got {_js_spans!r}")

check(measure.layer_imports("src/x.py", "import app.webapp\nfrom app import tray\nimport apples\n") == ["app", "app.webapp"],
      "layer_imports: app and app.* from src/, never a module that only starts with 'app'")
check(measure.layer_imports("app/x.py", "import app\n") == [], "layer_imports: only src/ files are checked")
_dirs = measure.flat_dirs([f"src/m{i}.py" for i in range(41)] + ["src/__init__.py", "lib/a.py"])
check(_dirs == [("src", 41)], f"flat_dirs: more than 40 direct modules, __init__ not counted, got {_dirs!r}")


# ---- 2. one repo at a ref ----

def _commit(repo: Path, files: dict[str, str], when: str, message: str) -> None:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    run_git(repo, "add", "-A", check=check)
    env = {**os.environ, "GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when}
    proc = subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", message], capture_output=True, text=True, env=env)
    check(proc.returncode == 0, f"commit {message}: {proc.stderr}")


_BIG = "".join(f"x{i} = {i}\n" for i in range(1200))
_NEAR = "".join(f"y{i} = {i}\n" for i in range(950))
_TOML = (
    'shape = "pipeline-cli"\n\n[structure_ratchet]\nenabled = true\n\n[comment_ratchet]\nenabled = true\n\n'
    '[vendored]\nstructure_ratchet = { src = "scripts/structure_ratchet.py", sha = "abc", dest = "scripts/vendored_tool.py" }\n'
)
_tmp = throwaway_dir("structure_census_")
_up, _work = make_upstream_and_clone(_tmp, check)
_commit(_up, {"src/big.py": _BIG}, "2026-01-01T00:00:00Z", "big file")
_commit(_up, {"src/big.py": "x = 1\n", ".fleet.toml": _TOML, "scripts/vendored_tool.py": _NEAR, "src/near.py": _NEAR,
              ".structure-ratchet-baseline.json": json.dumps({"version": 1, "entries": []}),
              ".comment-ratchet-baseline.json": "{}"},
        "2026-06-01T00:00:00Z", "split and adopt")
run_git(_work, "fetch", "-q", "origin", check=check)
(_work / "src" / "local_only.py").parent.mkdir(exist_ok=True)
(_work / "src" / "local_only.py").write_text(_BIG, encoding="utf-8")

_now = repo_read.census_repo("demo", _work, None)
check(_now["status"] == "ok" and _now["ref"] == "origin/main", f"census_repo: reads origin/main, got {_now!r}")
check(_now["over_1k"] == [] and _now["loc"] == 1 + 950 + 950,
      f"census_repo: the working tree's own file is never read, got loc {_now.get('loc')} and {_now.get('over_1k')}")
check(_now["loc_vendored"] == 950, f"census_repo: the [vendored] dest is the vendored share, got {_now.get('loc_vendored')}")
check(_now["files_near_limit"] == 1, f"census_repo: only the repo's own file counts as near the limit, got {_now.get('files_near_limit')}")
check(_now["shape"] == "pipeline-cli" and _now["shape_declared"], "census_repo: the declared shape wins")
check(_now["ratchets"]["hits"] == {"structure_ratchet": 0, "comment_ratchet": None}
      and _now["ratchets"]["structure"] == {"files": 0, "fns": 0, "dirs": 0},
      f"census_repo: an empty structure baseline is a real zero, got {_now['ratchets']!r}")
check(_now["ratchets"]["unknown"] == ["comment_ratchet"],
      "census_repo: a baseline that cannot be counted is unknown on its own, never zero and never sinking the others")

_then = repo_read.census_repo("demo", _work, "2026-03-01T00:00:00Z")
check(_then["status"] == "ok" and _then["over_1k"] == [["src/big.py", 1200, "py"]],
      f"census_repo: as_of re-reads the commit current then, got {_then.get('over_1k')}")
check(_then["ratchets"]["state"] == "not_adopted" and not _then["shape_declared"],
      "census_repo: before adoption there is no ratchet and the shape is guessed")
check(repo_read.census_repo("demo", _work, "2025-01-01T00:00:00Z")["status"] == "unknown",
      "census_repo: no commit before as_of is unknown, not empty")
check(repo_read.census_repo("ghost", _tmp / "missing", None) == {"repo": "ghost", "status": "unknown", "reason": "no checkout"},
      "census_repo: a repo with no checkout is unknown")


# ---- 3. compare ----

def _doc(repos: list[dict]) -> dict:
    return {"version": census.CENSUS_VERSION, "as_of": None, "measured_at": "t", "repos": repos}


_then["dormant"] = _now["dormant"] = False
_quiet = {**_then, "repo": "quiet", "dormant": True}
_lost = {"repo": "lost", "status": "unknown", "reason": "no checkout"}
_result = census.compare(_doc([_then, _quiet]), _doc([_now, {**_quiet, "shape": "streamlit"}, _lost]))
check(_result["repos_compared"] == ["demo", "quiet"] and _result["dormant"] == ["quiet"] and _result["unknown"] == ["lost"],
      f"compare: repos in both runs, dormant and unknown listed, got {_result['repos_compared']}, {_result['dormant']}, {_result['unknown']}")
check(_result["headline_all"]["files_over_1000"] == [2, 1, -1] and _result["headline_active"]["files_over_1000"] == [1, 0, -1],
      f"compare: all and active totals with the change, got {_result['headline_all']['files_over_1000']} / {_result['headline_active']['files_over_1000']}")
check(sorted(_result["by_shape"]) == ["Pipeline / CLI", "Streamlit"],
      f"compare: shapes come from the later run, got {sorted(_result['by_shape'])}")
check("| files_over_1000 | 2 | 1 | -1 |" in census.render(_result), "render: the Markdown table carries before, after and change")

_h.report_and_exit("test_structure_census")
