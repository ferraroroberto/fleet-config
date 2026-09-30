"""Unit tests for skills/_lib/start_beacon.py (fleet-config#1114).

The rendered `.pth` line is run for real: a child interpreter started with
`-S` calls `site.addsitedir()` on a folder holding it, the same path `site`
takes at a normal start. Calling it twice matches Windows 3.14, where a venv's
site-packages is processed twice (the #1114 probe). The host must print its own
output and exit 0 whether the ledger is writable or not.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_start_beacon.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import start_beacon as sb  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

tmp = Path(tempfile.mkdtemp(prefix="start_beacon_"))
TEMP = tmp / "temp"
TEMP.mkdir()
ENV = {**os.environ, "TEMP": str(TEMP), "TMP": str(TEMP), "CLAUDE_HOOKS_STATE_DIR": str(tmp / "state")}


def host(site_dir: Path, *args: str, prelude: str = "") -> subprocess.CompletedProcess:
    """A host script that loads `site_dir` twice, then prints its own output."""
    code = f"import site, sys\n{prelude}site.addsitedir({str(site_dir)!r})\nsite.addsitedir({str(site_dir)!r})\nprint('host-ok')\n"
    script = tmp / "host_script.py"
    script.write_text(code, encoding="utf-8")
    return subprocess.run([sys.executable, "-S", str(script), *args], capture_output=True, text=True, env=ENV,
                          timeout=60, creationflags=NO_WINDOW)


def ledger_lines(path: Path) -> list:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


try:
    line = sb.render_pth(tmp / "ledger" / "starts.tsv", "fixture")
    check(line.startswith("import sys; exec(") and line.count("\n") == 1 and line.endswith("\n"),
          "the .pth is one `import` line, which `site` executes")

    # ---- a working ledger: one record per process, whatever site does ----
    good = tmp / "good"
    good.mkdir()
    (tmp / "ledger").mkdir()
    (good / sb.PTH_NAME).write_text(line, encoding="utf-8")
    res = host(good, "--flag")
    rows = [r.split("\t") for r in ledger_lines(tmp / "ledger" / "starts.tsv")]
    check(res.returncode == 0 and res.stdout.strip() == "host-ok" and res.stderr == "",
          f"the host runs normally with the beacon active -- rc={res.returncode} out={res.stdout!r} err={res.stderr[-300:]!r}")
    check(len(rows) == 1 and rows[0][1] == "fixture" and Path(rows[0][2]) == tmp / "host_script.py"
          and len(rows[0][0]) == 20 and rows[0][0].endswith("Z"),
          f"one UTC record per process, with the script's absolute path, even with site-packages loaded twice -- {rows}")
    res = host(good, prelude="sys.argv = ['-m']; sys.orig_argv = [sys.executable, '-X', 'utf8', '-m', 'pkg.tool', '--x']\n")
    check(res.returncode == 0 and ledger_lines(tmp / "ledger" / "starts.tsv")[-1].endswith("\t-m pkg.tool"),
          f"a `-m` start records the module name from orig_argv -- {ledger_lines(tmp / 'ledger' / 'starts.tsv')[-1:]}")
    headers, hits, malformed = sb.read_ledger(tmp / "ledger" / "starts.tsv")
    check(headers == [] and set(hits) == {str(tmp / "host_script.py"), "-m pkg.tool"} and malformed == 0,
          f"read_ledger returns the last hit per target -- {hits}")

    # ---- an unwritable ledger never breaks the host, and is logged once ----
    bad = tmp / "bad"
    bad.mkdir()
    blocker = tmp / "not_a_dir"
    blocker.write_text("a file where the ledger folder should be", encoding="utf-8")
    (bad / sb.PTH_NAME).write_text(sb.render_pth(blocker / "starts.tsv", "fixture"), encoding="utf-8")
    runs = [host(bad) for _ in range(2)]
    check(all(r.returncode == 0 and r.stdout.strip() == "host-ok" and r.stderr == "" for r in runs),
          f"a failing beacon leaves the host's exit code, stdout and stderr untouched -- {[(r.returncode, r.stdout, r.stderr[-300:]) for r in runs]}")
    err = TEMP / "fleet_start_beacon.fixture.error"
    check(err.exists() and len(ledger_lines(err)) == 1 and "not_a_dir" in err.read_text(encoding="utf-8"),
          f"the failure is logged once to the temp error file, never to the host's output -- {ledger_lines(err)}")

    # ---- install / status / uninstall on a venv folder ----
    target = tmp / "target"
    venv = target / ".venv"
    (venv / "Lib" / "site-packages").mkdir(parents=True)
    os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(tmp / "state")
    first = sb.install(target, venv)
    again = sb.install(target, venv)
    pth = venv / "Lib" / "site-packages" / sb.PTH_NAME
    ledger = sb.ledger_path(target)
    check(first["first_install"] and not again["first_install"] and not again["written"] and pth.exists()
          and ledger == tmp / "state" / "dead_code" / "target" / "starts.tsv"
          and [k for k, _ in sb.read_ledger(ledger)[0]] == ["installed"],
          f"install adds one .pth and one install header; a re-install is a no-op -- {first} {again}")
    check(sb.status(target, venv)["installed"] is True and sb.status(target, venv)["current"] is True,
          "status reports the .pth as installed and current")
    check(sorted(p.name for p in pth.parent.iterdir()) == [sb.PTH_NAME], "the atomic write leaves no temp file behind")
    gone = sb.uninstall(target, venv)
    check(gone["removed"] and not pth.exists() and [k for k, _ in sb.read_ledger(ledger)[0]] == ["installed", "uninstalled"]
          and venv.is_dir(), "uninstall deletes only the .pth, records the header and leaves the venv in place")
    try:
        sb.install(tmp / "no_venv_here", tmp / "no_venv_here" / ".venv")
        refused = False
    except SystemExit:
        refused = True
    check(refused and not (tmp / "no_venv_here").exists(), "install refuses a missing venv and never creates one")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_start_beacon")
