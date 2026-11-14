"""tray_lifecycle.ps1 verifies a restart by process identity, not git_sha alone (fleet-config#1975).

A matching ``git_sha`` proves nothing: a restart that never replaced the process
leaves the old one serving, and when it already loaded HEAD the sha matches
anyway. Two layers here:

* the pure decision (``Get-RestartVerdict``) is lifted out of the script by its
  AST and driven through a table, including every "cannot read it" case, which
  must come out as ``not-confirmed`` and never as a pass;
* ``launch -Restart`` runs end to end against stub version servers: a real
  restart passes (with and without a ``pid`` in the answer), a restart whose old
  process survives fails, and nothing answering before the restart passes.

Windows PowerShell 5.1 only; elsewhere the suite records a skip.
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

SCRIPT = ROOT / "scripts" / "tray" / "tray_lifecycle.ps1"
POWERSHELL = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
HEAD = "abc1234def5678abc1234def5678abc1234def56"

VERDICT_HARNESS = r'''
param([string] $Script, [string] $Cases)
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Script, [ref] $null, [ref] $null)
foreach ($name in 'Test-GitShaMatches', 'Get-RestartVerdict') {
    $fn = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $true)
    Invoke-Expression $fn.Extent.Text
}
function ConvertTo-State($o) {
    $s = @{ Known = $true; Answered = $true; Url = 'u'; Sha = 'abc1234'; ReportedPid = $null; OwnerPid = $null; OwnerStarted = $null; Error = $null }
    foreach ($p in $o.psobject.Properties) { $s[$p.Name] = $p.Value }
    return $s
}
$out = @()
foreach ($c in (Get-Content -Raw $Cases | ConvertFrom-Json)) {
    $v = Get-RestartVerdict -Before (ConvertTo-State $c.before) -After (ConvertTo-State $c.after) -Head 'abc1234def5678'
    $out += $v.Status
}
$out -join ','
'''

# (label, before, after, expected status)
VERDICTS = [
    ("owner pid changed", {"OwnerPid": 10, "OwnerStarted": "1"}, {"OwnerPid": 11, "OwnerStarted": "2"}, "confirmed"),
    ("same owner pid and start time", {"OwnerPid": 10, "OwnerStarted": "1"}, {"OwnerPid": 10, "OwnerStarted": "1"}, "unchanged"),
    ("owner pid reused, new start time", {"OwnerPid": 10, "OwnerStarted": "1"}, {"OwnerPid": 10, "OwnerStarted": "2"}, "confirmed"),
    ("reported pid changed", {"ReportedPid": 5}, {"ReportedPid": 6}, "confirmed"),
    ("same reported pid", {"ReportedPid": 5, "OwnerPid": 5, "OwnerStarted": "1"},
     {"ReportedPid": 5, "OwnerPid": 5, "OwnerStarted": "1"}, "unchanged"),
    ("pid only after, owners differ", {"OwnerPid": 10, "OwnerStarted": "1"},
     {"ReportedPid": 6, "OwnerPid": 11, "OwnerStarted": "2"}, "confirmed"),
    ("nothing served before", {"Answered": False}, {"OwnerPid": 11, "OwnerStarted": "2"}, "confirmed"),
    ("after has no identity at all", {"OwnerPid": 10, "OwnerStarted": "1"}, {}, "not-confirmed"),
    ("before has no identity at all", {}, {"OwnerPid": 11, "OwnerStarted": "2"}, "not-confirmed"),
    ("no baseline was read", {"Known": False}, {"ReportedPid": 6}, "not-confirmed"),
    ("another sha is served", {"ReportedPid": 5}, {"ReportedPid": 6, "Sha": "ffff000"}, "stale"),
    ("nothing answers after", {"ReportedPid": 5}, {"Answered": False, "Error": "refused"}, "stale"),
]


def run_ps(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", *args],
        capture_output=True, text=True, timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


STUB = textwrap.dedent('''
    import json, os, sys
    from http.server import BaseHTTPRequestHandler, HTTPServer

    port, head, with_pid = int(sys.argv[1]), sys.argv[2], sys.argv[3] == "pid"

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = {"git_sha": head}
            if with_pid:
                body["pid"] = os.getpid()
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    class Exclusive(HTTPServer):
        # Windows lets a second process share a SO_REUSEADDR port; a real app that
        # cannot bind fails instead, which is the case this stub stands in for.
        allow_reuse_address = False

    Exclusive(("127.0.0.1", port), H).serve_forever()
''')


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def answers(port: int) -> bool:
    import urllib.request
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/version", timeout=2).read()
        return True
    except OSError:
        return False


def wait_answering(port: int, want: bool, seconds: float = 20) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if answers(port) == want:
            return True
        time.sleep(0.3)
    return False


def kill_under(base: Path) -> None:
    run_ps(["-Command",
            f"Get-CimInstance Win32_Process | Where-Object {{ $_.ProcessId -ne $PID -and $_.CommandLine -and $_.CommandLine.IndexOf('{base}', "
            "[System.StringComparison]::OrdinalIgnoreCase) -ge 0 } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"])


def launch(base: Path, port: int, with_pid: bool) -> subprocess.CompletedProcess:
    kind = "pid" if with_pid else "nopid"
    return run_ps([
        "-File", str(SCRIPT), "launch", "-AppName", "stub", "-ScriptDir", str(base / "repo"),
        "-VenvDir", str(base / ".venv"), "-TrayMatch", r"stub_server\.py", "-Ports", str(port),
        "-TrayLaunch", f"{base / '.venv' / 'stub_server.py'} {port} {HEAD} {kind}",
        "-VersionUrl", f"http://127.0.0.1:{port}/api/version", "-VerifyTimeoutSeconds", "6", "-Restart",
    ])


def start_old(script: Path, base: Path, port: int, with_pid: bool, venv_python: Path | None) -> subprocess.Popen:
    exe = str(venv_python or sys.executable)
    kind = "pid" if with_pid else "nopid"
    proc = subprocess.Popen([exe, str(script), str(port), HEAD, kind], cwd=base,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return proc


if sys.platform != "win32" or not Path(POWERSHELL).exists():
    _h.skip("tray_lifecycle restart identity: needs Windows PowerShell 5.1")
    _h.report_and_exit("test_tray_lifecycle_restart")

tmp = Path(tempfile.mkdtemp(prefix="trayrestart_"))
try:
    # ---- the pure verdict, lifted out of the script by its AST ---------------
    (tmp / "verdict.ps1").write_text(VERDICT_HARNESS, encoding="utf-8")
    (tmp / "cases.json").write_text(
        json.dumps([{"before": b, "after": a} for _, b, a, _ in VERDICTS]), encoding="utf-8")
    res = run_ps(["-File", str(tmp / "verdict.ps1"), "-Script", str(SCRIPT), "-Cases", str(tmp / "cases.json")])
    got = res.stdout.strip().split(",") if res.returncode == 0 else []
    check(len(got) == len(VERDICTS), f"verdict table ran for every case (rc={res.returncode} err={res.stderr[:200]!r})")
    for (label, _b, _a, want), have in zip(VERDICTS, got):
        check(have == want, f"Get-RestartVerdict: {label} -> {want} (got {have})")

    # ---- launch -Restart end to end against stub version servers --------------
    base = tmp / "app"
    (base / "repo").mkdir(parents=True)
    git = ["git", "-C", str(base / "repo")]
    nw = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.run([*git, "init", "-q"], check=True, creationflags=nw)
    subprocess.run([*git, "-c", "core.hooksPath=/dev/null", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"],
                   check=True, creationflags=nw)
    real_head = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
                               creationflags=nw).stdout.strip()
    HEAD = real_head
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(base / ".venv")], check=True, creationflags=nw)
    venv_python = base / ".venv" / "Scripts" / "python.exe"
    inside = base / ".venv" / "stub_server.py"
    inside.write_text(STUB, encoding="utf-8")
    outside_dir = tmp / "outside"
    outside_dir.mkdir()
    outside = outside_dir / "stub_server.py"
    outside.write_text(STUB, encoding="utf-8")

    for with_pid in (False, True):
        label = "reports a pid" if with_pid else "reports no pid (port-owner fallback)"
        port = free_port()
        old = start_old(inside, base, port, with_pid, venv_python)
        check(wait_answering(port, True), f"stub ({label}): the old process answers before the restart")
        res = launch(base, port, with_pid)
        check(res.returncode == 0 and "from a new process" in res.stdout,
              f"a real restart passes, app {label} (rc={res.returncode} out={res.stdout[-300:]!r} err={res.stderr[-300:]!r})")
        kill_under(base)
        old.wait(timeout=10)
        check(wait_answering(port, False), f"stub ({label}): cleaned up")

    # a restart that did not replace the process: the old server is outside the venv,
    # so the helper neither stops nor reclaims it; the new one cannot bind.
    for with_pid in (False, True):
        label = "reports a pid" if with_pid else "reports no pid"
        port = free_port()
        old = start_old(outside, outside_dir, port, with_pid, None)
        check(wait_answering(port, True), f"stub ({label}): the surviving process answers")
        res = launch(base, port, with_pid)
        check(res.returncode != 0 and "still serving" in (res.stderr + res.stdout),
              f"a restart that left the old process serving HEAD fails, app {label} "
              f"(rc={res.returncode} out={res.stdout[-200:]!r} err={res.stderr[-300:]!r})")
        old.kill()
        old.wait(timeout=10)
        kill_under(base)

    # nothing was serving before: any answer that serves HEAD is a new process.
    port = free_port()
    res = launch(base, port, True)
    check(res.returncode == 0 and "no process was serving" in res.stdout,
          f"nothing served before the restart: a fresh start passes (rc={res.returncode} out={res.stdout[-200:]!r} "
          f"err={res.stderr[-300:]!r})")
    kill_under(base)
finally:
    kill_under(tmp)
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_tray_lifecycle_restart")
