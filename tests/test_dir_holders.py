"""Unit tests for skills/_lib/dir_holders.py (fleet-config#571).

The pure matching/exclusion logic is exercised against synthetic process
tables, then the real probe is driven end-to-end against a genuinely live
holder spawned from a throwaway directory -- the shape that matters, since the
whole point is a probe that works in a repo with no `tests/e2e/`, no Playwright
and no venv of its own.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_dir_holders.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import dir_holders as dh  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


# ---- normalize / names_path: Windows path comparison ----

check(dh.normalize("E:/automation/Alpha-wt-1/") == "e:\\automation\\alpha-wt-1",
      "normalize: separators folded, case folded, trailing slash dropped")
check(dh.normalize("E:\\automation\\alpha-wt-1") == dh.normalize("e:/AUTOMATION/Alpha-wt-1"),
      "normalize: the two spellings of one path compare equal")

_key = dh.normalize("E:/automation/alpha-wt-1")
check(dh.names_path('"E:\\automation\\alpha-wt-1\\.venv\\Scripts\\python.exe" -m src.tts_server', _key),
      "names_path: a path *inside* the directory counts as naming it")
check(dh.names_path("python E:/automation/alpha-wt-1/run.py", _key),
      "names_path: forward-slash spelling in a command line still matches")
check(not dh.names_path("python E:\\automation\\alpha\\run.py", _key),
      "names_path: a sibling path that merely shares a prefix does not match")
check(not dh.names_path(None, _key), "names_path: a missing command line is not a match")
check(not dh.names_path("", _key), "names_path: an empty command line is not a match")


# ---- ancestors: the probing tree never counts as a holder ----

_tree = [
    {"ProcessId": 1, "ParentProcessId": 0},
    {"ProcessId": 10, "ParentProcessId": 1},
    {"ProcessId": 20, "ParentProcessId": 10},
    {"ProcessId": 30, "ParentProcessId": 20},
    {"ProcessId": 99, "ParentProcessId": 1},
]
check(dh.ancestors(30, _tree) == {30, 20, 10, 1}, "ancestors: walks the whole chain up from a pid")
check(99 not in dh.ancestors(30, _tree), "ancestors: an unrelated branch is not an ancestor")
check(dh.ancestors(12345, _tree) == {12345}, "ancestors: an unknown pid is still excluded as itself")
# A malformed table must not spin forever.
check(dh.ancestors(7, [{"ProcessId": 7, "ParentProcessId": 8}, {"ProcessId": 8, "ParentProcessId": 7}]) == {7, 8},
      "ancestors: a parent cycle terminates instead of hanging")


# ---- holders_for: what counts, and what is excluded ----

_procs = [
    {"ProcessId": 100, "ParentProcessId": 1, "Name": "python.exe",
     "ExecutablePath": "E:\\automation\\alpha-wt-1\\.venv\\Scripts\\python.exe",
     "CommandLine": "python.exe -m src.tts_server"},
    {"ProcessId": 200, "ParentProcessId": 1, "Name": "node.exe",
     "ExecutablePath": "C:\\Program Files\\nodejs\\node.exe",
     "CommandLine": "node E:/automation/alpha-wt-1/tools/watch.js"},
    {"ProcessId": 300, "ParentProcessId": 1, "Name": "python.exe",
     "ExecutablePath": "C:\\Python\\python.exe", "CommandLine": "python -m http.server"},
    {"ProcessId": 400, "ParentProcessId": 1, "Name": "powershell.exe",
     "ExecutablePath": "", "CommandLine": "powershell -Command probe 'E:\\automation\\alpha-wt-1'"},
]
_found = dh.holders_for("E:/automation/alpha-wt-1", _procs)
check([h["pid"] for h in _found] == [100, 200, 400], "holders_for: matches on executable path AND command line")
check(all(h["pid"] != 300 for h in _found), "holders_for: an unrelated process is not a holder")

_found = dh.holders_for("E:/automation/alpha-wt-1", _procs, exclude={400})
check([h["pid"] for h in _found] == [100, 200], "holders_for: excluded pids (the probe's own tree) are dropped")
check(dh.holders_for("E:/automation/beta-wt-2", _procs) == [],
      "holders_for: a directory nobody names has no holders")
check(dh.holders_for("E:/automation/alpha-wt-1", [{"Name": "broken.exe"}]) == [],
      "holders_for: a row with no ProcessId is skipped, not a crash")
check(dh.holders_for("E:/automation/alpha-wt-1", []) == [],
      "holders_for: an empty process table yields no holders")


# ---- the real probe, end to end ----

_tmp = Path(tempfile.mkdtemp(prefix="dir_holders_"))
_holder = None
try:
    # Inert first: nothing alive names this directory.
    _r = dh.probe(str(_tmp))
    check(_r.status == "CLEAR" and _r.holders == [],
          "probe: an inert directory is CLEAR — this is what stops an empty shell halting a run")
    check(_r.reason is None, "probe: a CLEAR verdict carries no failure reason")

    # The probe must not report *itself*: this test's own command line and the
    # PowerShell child's both name the path by construction.
    check(all(h["pid"] != os.getpid() for h in _r.holders),
          "probe: the probing process tree never counts as a holder")

    # Now a genuinely live holder, launched from a script inside the directory
    # — the local-llm-hub#475 shape (a worktree's own process still running).
    _script = _tmp / "holder.py"
    _script.write_text("import time\ntime.sleep(120)\n", encoding="utf-8")
    _holder = subprocess.Popen(
        [sys.executable, str(_script)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=NO_WINDOW,
    )
    # Win32_Process lists a process as soon as it exists; give it a beat anyway.
    for _ in range(20):
        _r = dh.probe(str(_tmp))
        if _r.status == "LIVE":
            break
        time.sleep(0.25)

    check(_r.status == "LIVE", "probe: a live process naming the directory is LIVE, not CLEAR")
    check(any(h["pid"] == _holder.pid for h in _r.holders),
          "probe: the live holder is reported by pid")
    check(any(str(_tmp).lower().replace("/", "\\") in dh.normalize(h["cmdline"]) for h in _r.holders),
          "probe: the holder's command line is reported so a human can identify it")

    # Once it exits, the same directory reads CLEAR again — an *exited* process
    # is not a holder, which is the whole zombie-shell point (fleet-config#534).
    _holder.terminate()
    _holder.wait(timeout=30)
    for _ in range(20):
        _r = dh.probe(str(_tmp))
        if _r.status == "CLEAR":
            break
        time.sleep(0.25)
    check(_r.status == "CLEAR", "probe: after the holder exits the directory is CLEAR again")

    # The CLI contract the teardown brief actually invokes.
    _out = subprocess.run(
        [sys.executable, str(REPO / "skills" / "_lib" / "dir_holders.py"), "check", str(_tmp)],
        capture_output=True, text=True, creationflags=NO_WINDOW,
    )
    check(_out.returncode == 0, "check CLI: always exits 0 — it reports, it never blocks")
    check("STATUS=CLEAR" in _out.stdout and "LIVE=0" in _out.stdout,
          "check CLI: prints STATUS and a live count")
finally:
    if _holder is not None and _holder.poll() is None:
        _holder.kill()
    shutil.rmtree(_tmp, ignore_errors=True)


# ---- the teardown brief and SKILL.md must not require a repo-local tool ----
# Condition 4 was satisfiable in 4 of 14 fleet repos; in the other ten any
# leftover directory was guaranteed RESIDUE and guaranteed to halt the run.

_wf = (REPO / ".claude" / "workflows" / "cleanup-fleet-all.js").read_text(encoding="utf-8")
_skill = (REPO / ".claude" / "skills" / "cleanup-fleet-all" / "SKILL.md").read_text(encoding="utf-8")
for _label, _text in (("teardown prompt", _wf), ("SKILL.md", _skill)):
    check("dir_holders.py" in _text,
          f"{_label}: condition 4 uses the repo-agnostic live-holder probe")
    check("_browser_sweep.py --dry-run" not in _text,
          f"{_label}: a repo-local Playwright sweeper is no longer the required proof")
    check("STATUS=CLEAR" in _text and "STATUS=LIVE" in _text,
          f"{_label}: the probe's verdicts are named, so LIVE still means residue")
    check("fleet-config#571" in _text,
          f"{_label}: records why the repo-local requirement was dropped")

_h.report_and_exit("test_dir_holders")
