"""Unit tests for skills/_lib/entry_inventory.py (fleet-config#961).

A tiny synthetic git repo carries one of every shape the verdict must tell
apart: a job-launched script (live), a job-launched .bat and its script
(cold), a manual launcher, an unlaunched `__main__` script, a module reached
by import, one nobody imports, one only tests import, a dynamic-dispatch
file, a name-mentioned module, a doc-only script and a seasonal keep. The
verdict is a pure function of that tree plus a hand-written evidence snapshot.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_entry_inventory.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import ast
import json
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import entry_inventory as ei  # noqa: E402
from git_run import run_git  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

MAIN = 'if __name__ == "__main__":\n    main()\n'
FIXTURE = {
    "jobs/live_main.py": "from lib import used\n\ndef main():\n    used.go()\n\n" + MAIN,
    "jobs/run_cold.bat": '@echo off\nREM runs cold_script.py nightly\ncd /d "%~dp0"\n"%VENV%\\Scripts\\python.exe" cold_script.py %*\n',
    "jobs/cold_script.py": "def main():\n    pass\n\n" + MAIN,
    "jobs/nightly/run-scan.bat": ('@echo off\nset "SCRIPT_DIR=%~dp0"\nset "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"\n'
                                  '"%PYTHON%" "%SCRIPT_DIR%\\scan_cli.py" scan\n'),
    "jobs/nightly/scan_cli.py": "def main():\n    pass\n\n" + MAIN,
    "tools/manual.bat": "@echo off\npython manual_tool.py\n",
    "tools/manual_tool.py": "def main():\n    pass\n\n" + MAIN,
    "tools/orphan_main.py": "def main():\n    return 1\n\n" + MAIN,
    "tools/doc_only.py": "def main():\n    pass\n\n" + MAIN,
    "tools/seasonal.py": "def main():\n    pass\n\n" + MAIN,
    "lib/__init__.py": "",
    "lib/used.py": "def go():\n    return 1\n",
    "lib/dead.py": "def never():\n    return 1\n\ndef also_never():\n    return 2\n",
    "lib/testonly.py": "def helper():\n    return 1\n",
    "lib/mentioned.py": "def x():\n    return 1\n",
    "tools/dyn.py": "import importlib\n\ndef main(name):\n    importlib.import_module(name)\n\n" + MAIN,
    "tests/test_x.py": "from lib import testonly\n",
    "config.json": '{"plugin": "mentioned"}\n',
    "README.md": "Run `tools/doc_only.py` by hand.\n",
}
EVIDENCE = {
    "schema": 1, "repo": "fixture", "collected_utc": "2026-09-30T08:00:00Z", "jobs_error": None, "tasks_error": None,
    "jobs": [{"id": "live-job", "target": "jobs/live_main.py", "paused": False, "last_run_utc": "2026-09-29T05:00:00Z"},
             {"id": "cold-job", "target": "jobs/run_cold.bat", "paused": True, "last_run_utc": "2026-03-01T05:00:00Z"},
             {"id": "scan-job", "target": "jobs/nightly/run-scan.bat", "paused": False, "last_run_utc": "2026-09-30T03:15:58Z"}],
    "tasks": [],
}
KEEPS = [{"path": "tools/seasonal.py", "reason": "tax season", "expires": "2027-05-01"},
         {"path": "tools/gone.py", "reason": "stale keep", "expires": "2027-01-01"}]


def git(repo: Path, *args: str) -> str:
    # `core.hooksPath` -> an empty dir: this machine carries a global commit hook
    # that rejects non-allowlisted author emails (same as test_worktree_residue).
    return run_git(["-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                    "-c", f"core.hooksPath={NO_HOOKS}", *args], check=True).stdout


tmp = Path(tempfile.mkdtemp(prefix="entry_inventory_"))
NO_HOOKS = tmp / "no-hooks"
NO_HOOKS.mkdir()
try:
    repo = tmp / "fixture"
    for rel, text in FIXTURE.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "fixture")

    doc = ei.verdict(repo, EVIDENCE, date(2026, 9, 30), 90, KEEPS)
    rows = {r["path"]: r for r in doc["files"]}

    def state(path: str) -> tuple:
        row = rows.get(path, {})
        return row.get("state"), row.get("reason")

    check(state("jobs/live_main.py")[0] == "live" and rows["jobs/live_main.py"]["last_seen"] == "2026-09-29T05:00:00Z"
          and rows["jobs/live_main.py"]["last_seen_source"] == "job:live-job",
          f"a job-launched script run inside the window is live, with its source -- {rows.get('jobs/live_main.py')}")
    check(state("lib/used.py") == ("live", "imported"), f"a module a live entry imports inherits live -- {state('lib/used.py')}")
    check(state("jobs/run_cold.bat")[0] == "cold" and state("jobs/cold_script.py")[0] == "cold"
          and "via jobs/run_cold.bat" in rows["jobs/cold_script.py"]["last_seen_source"],
          f"a paused job's .bat and the script it runs are cold, evidence propagated down the launcher -- {rows.get('jobs/cold_script.py')}")
    check(state("jobs/nightly/scan_cli.py")[0] == "live",
          f"a script a launcher runs through a %VAR% folder prefix is found and inherits the job's run -- {rows.get('jobs/nightly/scan_cli.py')}")
    check(state("tools/manual.bat") ==("unknown", "manual-launcher") and state("tools/manual_tool.py") == ("unknown", "manual-launcher"),
          "a launcher with no run record, and its script, are unknown: manual-launcher -- never cold")
    check(state("tools/orphan_main.py") == ("unknown", "no-launcher"), f"an unlaunched __main__ script is unknown: no-launcher -- {state('tools/orphan_main.py')}")
    check(state("tools/doc_only.py") == ("unknown", "doc-mention-only"), f"a README mention alone is not a launcher -- {state('tools/doc_only.py')}")
    check(state("tools/dyn.py") == ("unknown", "dynamic-dispatch") and rows["tools/dyn.py"]["dynamic"] == ["importlib"],
          f"a dynamic-dispatch file is forced to unknown -- {rows.get('tools/dyn.py')}")
    check(state("lib/dead.py") == ("unreachable", "no-importer"), f"a module nothing imports is unreachable -- {state('lib/dead.py')}")
    check(state("lib/testonly.py") == ("unreachable", "test-only"), f"tests are not roots: a test-only import is unreachable -- {state('lib/testonly.py')}")
    check(state("lib/mentioned.py") == ("unknown", "name-mentioned"), f"a module named in config text is unknown, never dead -- {state('lib/mentioned.py')}")
    check(rows["tools/seasonal.py"].get("keep", {}).get("reason") == "tax season"
          and "tools/seasonal.py" not in [c["path"] for c in doc["candidates"]] and doc["keeps_unmatched"] == ["tools/gone.py"],
          "an unexpired seasonal keep is annotated and off the candidate list; a keep matching no file is reported")
    check([c["path"] for c in doc["candidates"]] == ["lib/dead.py", "lib/testonly.py", "tools/orphan_main.py"]
          and doc["candidate_lines"] == 5 + 2 + 5,
          f"candidates are the unreachable modules and unlaunched scripts, with line counts -- {doc['candidates']}")
    check(doc["unknown_reasons"] == {"doc-mention-only": 1, "dynamic-dispatch": 1, "manual-launcher": 2, "name-mentioned": 1, "no-launcher": 2}
          and "tests/test_x.py" not in rows,
          f"unknown is counted per reason, separately from the other states -- {doc['unknown_reasons']}")

    expired = ei.verdict(repo, EVIDENCE, date(2027, 6, 1), 90, KEEPS)
    check("tools/seasonal.py" in [c["path"] for c in expired["candidates"]]
          and {r["path"]: r for r in expired["files"]}["tools/seasonal.py"]["keep"]["expired"] is True,
          "an expired keep no longer protects its path")
    down = ei.verdict(repo, {**EVIDENCE, "jobs": [], "jobs_error": "URLError: refused"}, date(2026, 9, 30))
    check({r["path"]: r for r in down["files"]}["jobs/live_main.py"]["state"] == "unknown"
          and down["unknown_reasons"].get("evidence-unavailable", 0) >= 3 and down["counts"].get("cold", 0) == 0,
          f"an unreadable evidence source makes entries unknown, never cold -- {down['counts']} {down['unknown_reasons']}")

    # ---- determinism and report-only ----
    out1, out2 = tmp / "o1", tmp / "o2"
    for out in (out1, out2):
        ei._write_output(out, "verdict.json", ei.verdict(repo, EVIDENCE, date(2026, 9, 30), 90, KEEPS))
    check((out1 / "verdict.json").read_bytes() == (out2 / "verdict.json").read_bytes(), "two runs over the same inputs are byte-identical")
    evidence_file = tmp / "evidence.json"
    evidence_file.write_text(json.dumps(EVIDENCE), encoding="utf-8")
    rc = ei.main(["verdict", str(repo), "--evidence", str(evidence_file), "--out-dir", str(tmp / "cli")])
    check(rc == 0 and json.loads((tmp / "cli" / "verdict.json").read_text(encoding="utf-8"))["as_of"] == "2026-09-30",
          "the CLI writes verdict.json, taking as-of from the evidence's collection date")
    check(git(repo, "status", "--porcelain") == "", "the target repo's git status is clean after a run")
    collected = ei.collect(repo, jobs_url="https://127.0.0.1:1/api/jobs", tasks=False)
    check(collected["jobs"] == [] and collected["jobs_error"], f"an unreachable jobs API is recorded as an error, not as no jobs -- {collected.get('jobs_error')}")

    source = (REPO / "skills" / "_lib" / "entry_inventory.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    writes = sorted({f.name for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)
                     for node in ast.walk(f) if isinstance(node, ast.Attribute) and node.attr in ("write_text", "write_bytes", "mkdir")})
    forbidden = sorted({f"{node.value.id}.{node.attr}" for node in ast.walk(tree) if isinstance(node, ast.Attribute)
                        and isinstance(node.value, ast.Name) and node.value.id in ("os", "shutil")
                        and node.attr in ("remove", "unlink", "rename", "replace", "rmdir", "rmtree", "move")}
                       | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute) and node.attr in ("unlink", "rmtree")})
    check(writes == ["_write_output"] and not forbidden and "anthropic" not in source.lower() and "127.0.0.1:8000" not in source,
          f"report-only by design: one write site, into machine-local state; no delete/move; no LLM call -- writes={writes} forbidden={forbidden}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_entry_inventory")
