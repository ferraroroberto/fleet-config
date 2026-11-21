"""tray_lifecycle.ps1 owns a process whatever spelling its command line uses (fleet-config#2187).

A fleet root is reachable as ``C:/automation/<repo>`` and ``E:/automation/<repo>`` (a
junction), either slash style, and a live process keeps the spelling it started from.
The caller's ``-VenvDir`` is only its own ``%~dp0`` spelling, so an ordinal substring
test missed the other spelling: detect started a second tray and reclaim left the old
port holder serving. Three layers here, all on synthetic command lines and throwaway
listeners on free high ports (never a real fleet port):

* the ownership matcher, lifted out of the script by its AST, through a table: both
  drive spellings, both slash styles, case, a session-host with no ``.venv``, and the
  near misses that must stay foreign (a ``-wt-N`` sibling, another repo, a longer
  parent path, a ``.venv`` prefix, this repo without the session-host role);
* ``detect`` and ``reclaim`` against real processes whose command lines carry the other
  spelling, and a holder that survives its stop failing the reclaim;
* the session-host guard: a ``launch -Restart`` whose ``-Ports`` leaves the session-host's
  port out (app-launcher's tray.bat names only its webapp port) restarts the webapp and
  leaves the session-host alive; only a caller naming that port reclaims it.

Windows PowerShell 5.1 only; elsewhere the suite records a skip.
"""
from __future__ import annotations

import json
import os
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
NW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Defines every function of the real script (not its param block or action switch),
# then runs one scenario chosen by the environment. ASCII only: PowerShell 5.1 runs it.
HARNESS = r'''
$ErrorActionPreference = 'Stop'
$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:TL_SCRIPT, [ref] $null, [ref] $null)
foreach ($fn in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $false)) {
    . ([scriptblock]::Create($fn.Extent.Text))
}
$VenvDir = $env:TL_VENV
$ScriptDir = $env:TL_SCRIPTDIR
$Ports = $env:TL_PORTS
if ($env:TL_NO_STOP) { function Stop-Process { } }
if ($env:TL_CASES) {
    $out = @()
    foreach ($c in (Get-Content -Raw $env:TL_CASES | ConvertFrom-Json)) { $out += (Test-OwnsProcess $c) }
    Write-Output ($out -join ',')
} else {
    Write-Output ('SURVIVORS=' + (Invoke-ReclaimPorts))
}
'''

REPO = r"\automation\some-repo"
# (command line, owned?) for a caller whose repo is <drive>:\automation\some-repo
OWNERSHIP = [
    (r"C:\automation\some-repo\.venv\Scripts\pythonw.exe launcher.py tray", True),
    (r"E:\automation\some-repo\.venv\Scripts\pythonw.exe launcher.py tray", True),
    ("C:/automation/some-repo/.venv/Scripts/pythonw.exe launcher.py tray", True),
    ('"E:/automation/some-repo/.venv/Scripts/python.exe" -m app', True),
    (r"c:\AUTOMATION\Some-Repo\.venv\Scripts\python.exe -m app", True),
    (r"python.exe --venv=E:\automation\some-repo\.venv", True),
    # a session-host re-execed from the base interpreter: no .venv, either spelling
    (r'"C:\Python314\pythonw.exe" E:\automation\some-repo\launcher.py session-host --port 9', True),
    ('python.exe "C:/automation/some-repo/launcher.py" session-host', True),
    # the mixed form: an E: venv launching a C: launcher path
    (r"E:\automation\some-repo\.venv\Scripts\pythonw.exe C:\automation\some-repo\launcher.py session-host", True),
    # near misses that are not this repo
    (r"C:\automation\some-repo-wt-3\.venv\Scripts\pythonw.exe launcher.py tray", False),
    ("E:/automation/some-repo-wt-3/.venv/Scripts/pythonw.exe launcher.py tray", False),
    (r"E:\automation\other-repo\.venv\Scripts\pythonw.exe launcher.py tray", False),
    (r"C:\other\automation\some-repo\.venv\Scripts\python.exe -m app", False),
    (r"C:\automation\some-repo\.venvx\Scripts\python.exe -m app", False),
    (r"C:\Python314\pythonw.exe C:\automation\some-repo\launcher.py tray", False),
    (r"C:\Python314\pythonw.exe E:\automation\some-repo-wt-3\launcher.py session-host", False),
    (r"C:\Python314\pythonw.exe E:\automation\other-repo\launcher.py session-host", False),
    ("", False),
]
# The same table must hold whichever spelling the caller passes.
CALLERS = [
    ("C:" + REPO + r"\.venv", ""),
    ("E:" + REPO.replace("\\", "/") + "/.venv", ""),
    ("E:" + REPO + r"\.venv", "C:" + REPO),
]

HOLDER = textwrap.dedent('''
    import os, socket, sys, time
    s = socket.socket()
    s.bind(("127.0.0.1", int(sys.argv[1])))
    s.listen(1)
    print(os.getpid(), flush=True)
    time.sleep(180)
''')

# The session-host stand-in: `<repo>/launcher.py session-host --port N`, no .venv.
SESSION_HOST = textwrap.dedent('''
    import os, socket, sys, time
    s = socket.socket()
    s.bind(("127.0.0.1", int(sys.argv[sys.argv.index("--port") + 1])))
    s.listen(1)
    print(os.getpid(), flush=True)
    time.sleep(180)
''')

STUB = textwrap.dedent('''
    import json, os, sys
    from http.server import BaseHTTPRequestHandler, HTTPServer

    port, head = int(sys.argv[1]), sys.argv[2]

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            data = json.dumps({"git_sha": head, "pid": os.getpid()}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    class Exclusive(HTTPServer):
        allow_reuse_address = False

    Exclusive(("127.0.0.1", port), H).serve_forever()
''')


def run_ps(args: list[str], env: dict | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    full = None
    if env is not None:
        full = {k: v for k, v in os.environ.items() if k.upper() != "PSEXECUTIONPOLICYPREFERENCE"}
        full.update(env)
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", *args],
        capture_output=True, text=True, timeout=timeout, env=full, creationflags=NW,
    )


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def other_drive(path: Path) -> str:
    """``path`` spelled under another drive letter, forward slashes: the junction spelling."""
    drive, rest = os.path.splitdrive(str(path))
    other = "Z:" if drive.upper() != "Z:" else "Y:"
    return other + rest.replace("\\", "/")


def spawn(script: Path, *args: str) -> tuple[subprocess.Popen, int]:
    proc = subprocess.Popen([sys.executable, str(script), *args], stdout=subprocess.PIPE, text=True,
                            creationflags=NW)
    assert proc.stdout is not None
    return proc, int(proc.stdout.readline())


def alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
                         timeout=30, creationflags=NW).stdout
    return str(pid) in out


def wait_dead(pid: int, seconds: float = 10) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if not alive(pid):
            return True
        time.sleep(0.2)
    return not alive(pid)


def script_action(action: str, venv: Path, *extra: str) -> subprocess.CompletedProcess:
    return run_ps(["-File", str(SCRIPT), action, "-VenvDir", str(venv), *extra])


if sys.platform != "win32" or not Path(POWERSHELL).exists():
    _h.skip("tray_lifecycle spelling-agnostic ownership: needs Windows PowerShell 5.1")
    _h.report_and_exit("test_tray_lifecycle_spelling")

tmp = Path(tempfile.mkdtemp(prefix="trayspelling_"))
spawned: list[subprocess.Popen] = []
try:
    harness = tmp / "harness.ps1"
    harness.write_text(HARNESS, encoding="ascii")
    cases = tmp / "cases.json"
    cases.write_text(json.dumps([line for line, _ in OWNERSHIP]), encoding="utf-8")

    # ---- the matcher, through a table, for every caller spelling --------------
    for venv, script_dir in CALLERS:
        res = run_ps(["-File", str(harness)], env={
            "TL_SCRIPT": str(SCRIPT), "TL_VENV": venv, "TL_SCRIPTDIR": script_dir, "TL_CASES": str(cases)})
        got = [p.strip() == "True" for p in res.stdout.strip().split(",")] if res.returncode == 0 else []
        check(len(got) == len(OWNERSHIP),
              f"ownership table ran for caller {venv!r} (rc={res.returncode} err={res.stderr[:200]!r})")
        for (line, want), have in zip(OWNERSHIP, got):
            check(have == want, f"caller {venv!r}: {'owns' if want else 'ignores'} {line!r}")

    base = tmp / "app"
    repo = base / "repo"
    venv = base / ".venv"
    repo.mkdir(parents=True)
    outside = tmp / "outside"
    outside.mkdir()
    holder = outside / "holder.py"
    holder.write_text(HOLDER, encoding="ascii")

    # ---- detect: a tray started from the other spelling is found ---------------
    port = free_port()
    proc, pid = spawn(holder, str(port), other_drive(venv) + "/Scripts/pythonw.exe", "launcher.py", "tray")
    spawned.append(proc)
    res = script_action("detect", venv, "-TrayMatch", r"launcher\.py\s+tray")
    check(str(pid) in res.stdout.split(),
          f"detect finds a tray spelled under the other drive and slash style (out={res.stdout[-200:]!r} "
          f"err={res.stderr[-200:]!r})")

    # ---- reclaim: the other spelling's port holder is stopped ------------------
    res = script_action("reclaim", venv, "-Ports", str(port))
    check(res.returncode == 0 and f"Reclaiming :{port} from PID {pid}" in res.stdout and wait_dead(pid),
          f"reclaim stops a holder spelled under the other drive and slash style (rc={res.returncode} "
          f"out={res.stdout[-200:]!r} err={res.stderr[-200:]!r})")

    # ---- reclaim leaves a sibling checkout's and another repo's holder alone ---
    for label, tag in (("a -wt-N sibling checkout", str(base) + "-wt-3\\.venv\\Scripts\\pythonw.exe"),
                       ("another repo", str(tmp / "other-repo" / ".venv" / "Scripts" / "pythonw.exe"))):
        port = free_port()
        proc, pid = spawn(holder, str(port), tag)
        spawned.append(proc)
        res = script_action("reclaim", venv, "-Ports", str(port))
        check(res.returncode == 0 and "not this repo's process" in res.stdout and alive(pid),
              f"reclaim leaves {label} alive (rc={res.returncode} out={res.stdout[-200:]!r})")
        proc.kill()

    # ---- a holder that survives its stop fails the reclaim ---------------------
    port = free_port()
    proc, pid = spawn(holder, str(port), str(venv / "Scripts" / "pythonw.exe"))
    spawned.append(proc)
    res = run_ps(["-File", str(harness)], env={
        "TL_SCRIPT": str(SCRIPT), "TL_VENV": str(venv), "TL_SCRIPTDIR": str(repo), "TL_PORTS": str(port),
        "TL_NO_STOP": "1"})
    check("SURVIVORS=1" in res.stdout and f"FAILED: old holder PID {pid} is still running on :{port}" in res.stdout
          and alive(pid),
          f"an owned holder that survives the stop is counted as a failed reclaim (out={res.stdout[-300:]!r} "
          f"err={res.stderr[-200:]!r})")
    proc.kill()

    # ---- session-host guard: the webapp restart never reaches it ---------------
    # A real repo + venv so `launch -Restart` can start a tray and verify HEAD.
    git = ["git", "-C", str(repo)]
    subprocess.run([*git, "init", "-q"], check=True, creationflags=NW)
    subprocess.run([*git, "-c", "core.hooksPath=/dev/null", "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "--allow-empty", "-m", "x"], check=True, creationflags=NW)
    head = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
                          creationflags=NW).stdout.strip()
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True, creationflags=NW)
    (venv / "stub_server.py").write_text(STUB, encoding="ascii")
    (outside / "stub_server.py").write_text(STUB, encoding="ascii")
    (repo / "launcher.py").write_text(SESSION_HOST, encoding="ascii")

    host_port, web_port = free_port(), free_port()
    host, host_pid = spawn(repo / "launcher.py", "session-host", "--port", str(host_port))
    spawned.append(host)
    # the old webapp, spelled under the other drive: the restart must still replace it
    old_web = subprocess.Popen([sys.executable, str(outside / "stub_server.py"), str(web_port), head,
                                other_drive(venv) + "/Scripts/pythonw.exe"], creationflags=NW)
    spawned.append(old_web)
    time.sleep(1.5)
    res = run_ps([
        "-File", str(SCRIPT), "launch", "-AppName", "stub", "-ScriptDir", str(repo), "-VenvDir", str(venv),
        "-TrayMatch", r"launcher\.py\s+tray", "-Ports", str(web_port),
        "-TrayLaunch", f"{venv / 'stub_server.py'} {web_port} {head}",
        "-VersionUrl", f"http://127.0.0.1:{web_port}/api/version", "-VerifyTimeoutSeconds", "30", "-Restart",
    ])
    check(res.returncode == 0 and f"Reclaiming :{web_port}" in res.stdout and "from a new process" in res.stdout,
          f"a restart from the other spelling replaces the webapp (rc={res.returncode} out={res.stdout[-300:]!r} "
          f"err={res.stderr[-300:]!r})")
    check(wait_dead(old_web.pid), "the old webapp process is gone after the restart")
    check(alive(host_pid) and f":{host_port}" not in res.stdout,
          "a restart whose -Ports leaves the session-host's port out leaves the session-host alive")
    # positive control: the same session-host IS this repo's, so naming its port reclaims it
    res = script_action("reclaim", venv, "-ScriptDir", str(repo), "-Ports", str(host_port))
    check(res.returncode == 0 and f"Reclaiming :{host_port} from PID {host_pid}" in res.stdout and wait_dead(host_pid),
          f"only a caller naming the session-host's port reclaims it, with no .venv in its command line "
          f"(rc={res.returncode} out={res.stdout[-200:]!r})")
finally:
    for p in spawned:
        p.kill()
    run_ps(["-Command",
            f"Get-CimInstance Win32_Process | Where-Object {{ $_.ProcessId -ne $PID -and $_.CommandLine -and "
            f"$_.CommandLine.IndexOf('{tmp}', [System.StringComparison]::OrdinalIgnoreCase) -ge 0 }} | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"])
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_tray_lifecycle_spelling")
