"""tray_lifecycle.ps1 never takes another checkout's running copy for this one (fleet-config#2288).

Two checkouts of a dummy app, ``a/app`` and ``b/app``, each with its own ``.venv``,
the shape of a migration to a fresh clone beside the old one. A restart from ``b``:

* refuses before stopping anything while ``a``'s process holds the owned port on a
  loopback or wildcard address, naming ``a`` and its PID, and leaves ``b``'s tray running;
* fails when, after nothing served before the restart, the copy that comes up serving
  HEAD is ``a``'s (its watchdog revived it), instead of reading it as a fresh start;
* ignores ``a``'s listener bound only to another specific address on the same port
  number (Tailscale serving the tailnet next to the webapp's own bind), both in the
  pure address filter and live when this host has a non-loopback IPv4 address.

Single-checkout restarts are pinned by ``test_tray_lifecycle_restart.py``.
Windows PowerShell 5.1 only; elsewhere the suite records a skip.
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from tray_fixture import (  # noqa: E402
    NW,
    POWERSHELL,
    SCRIPT,
    STUB,
    answers,
    free_port,
    init_repo,
    kill_under,
    run_ps,
    wait_answering,
)

_h = CheckHarness()
check = _h.check

# Stands in for a tray: it only has to be alive and match -TrayMatch.
TRAY = "import time\nwhile True:\n    time.sleep(1)\n"

# b's tray launch that brings up a's server instead: the other checkout's watchdog
# reviving its own webapp while this checkout starts.
REVIVE = textwrap.dedent('''
    import subprocess, sys
    subprocess.Popen(sys.argv[1:], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
''')

FILTER_HARNESS = r'''
param([string] $Script)
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Script, [ref] $null, [ref] $null)
$fn = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Test-ProbeAddress' }, $true)
if (-not $fn) { 'missing'; exit 0 }
Invoke-Expression $fn.Extent.Text
(@('0.0.0.0', '::', '127.0.0.1', '::1', '100.107.242.100', 'fd7a:115c:a1e0::1', '192.168.1.5') |
    ForEach-Object { [string] (Test-ProbeAddress $_) }) -join ','
'''


def flat(text: str) -> str:
    # PowerShell wraps a thrown message at the console width, splitting a path mid-word.
    return "".join(text.split())


def listener_pid(port: int) -> str:
    # The venv launcher re-execs the base interpreter, so the listener is not the Popen pid.
    res = run_ps(["-Command", f"(Get-NetTCPConnection -LocalPort {port} -State Listen | Select-Object -First 1).OwningProcess"])
    return res.stdout.strip()


def lan_ipv4() -> str | None:
    # A UDP connect sends nothing; it only picks the outbound interface's address.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            ip = s.getsockname()[0]
    except OSError:
        return None
    return None if ip.startswith("127.") or ip == "0.0.0.0" else ip


def make_checkout(root: Path) -> tuple[Path, str]:
    """A git repo with a venv and the stub scripts under .venv; returns (venv python, HEAD)."""
    head = init_repo(root)
    venv = root / ".venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True, creationflags=NW)
    (venv / "stub_server.py").write_text(STUB, encoding="utf-8")
    (venv / "stub_tray.py").write_text(TRAY, encoding="utf-8")
    (venv / "revive.py").write_text(REVIVE, encoding="utf-8")
    return venv / "Scripts" / "python.exe", head


def launch(root: Path, port: int, tray_launch: str, verify_seconds: int) -> subprocess.CompletedProcess:
    return run_ps([
        "-File", str(SCRIPT), "launch", "-AppName", "stub", "-ScriptDir", str(root),
        "-VenvDir", str(root / ".venv"), "-TrayMatch", r"stub_(tray|server)\.py", "-Ports", str(port),
        "-TrayLaunch", tray_launch, "-VersionUrl", f"http://127.0.0.1:{port}/api/version",
        "-VerifyTimeoutSeconds", str(verify_seconds), "-Restart",
    ])


def serve(python: Path, root: Path, host: str, port: int, head: str) -> subprocess.Popen:
    return subprocess.Popen([str(python), str(root / ".venv" / "stub_server.py"), str(port), head, "--host", host],
                            cwd=root, creationflags=NW)


if sys.platform != "win32" or not Path(POWERSHELL).exists():
    _h.skip("tray_lifecycle two checkouts: needs Windows PowerShell 5.1")
    _h.report_and_exit("test_tray_lifecycle_checkouts")

tmp = Path(tempfile.mkdtemp(prefix="traycheckouts_"))
try:
    # ---- the address filter, lifted out of the script by its AST --------------
    (tmp / "filter.ps1").write_text(FILTER_HARNESS, encoding="utf-8")
    res = run_ps(["-File", str(tmp / "filter.ps1"), "-Script", str(SCRIPT)])
    got = res.stdout.strip()
    check(got == "True,True,True,True,False,False,False",
          f"only loopback and wildcard listeners count as holding a port (got {got!r} err={res.stderr[:200]!r})")

    a_root, b_root = tmp / "a" / "app", tmp / "b" / "app"
    a_python, head = make_checkout(a_root)
    b_python, _ = make_checkout(b_root)
    # Both checkouts at the same commit, as a fresh clone of the same repo is: the served
    # sha alone then cannot tell them apart.
    subprocess.run(["git", "-C", str(b_root), "fetch", "-q", str(a_root), "HEAD"], check=True, creationflags=NW)
    subprocess.run(["git", "-C", str(b_root), "reset", "-q", "--hard", "FETCH_HEAD"], check=True, creationflags=NW)
    b_server = f"{b_root / '.venv' / 'stub_server.py'}"

    # ---- another checkout holds the port: refused, nothing stopped -----------
    port = free_port()
    a_srv = serve(a_python, a_root, "127.0.0.1", port, head)
    b_tray = subprocess.Popen([str(b_python), str(b_root / ".venv" / "stub_tray.py")], cwd=b_root, creationflags=NW)
    check(wait_answering(port, True), "a's server answers before b's restart")
    time.sleep(1)
    holder = listener_pid(port)
    res = launch(b_root, port, f"{b_server} {port} {head}", 6)
    out = res.stdout + res.stderr
    check(res.returncode != 0 and "restartrefused" in flat(out) and holder and f"PID{holder}" in flat(out) and flat(str(a_root)) in flat(out),
          f"a restart from b while a holds the port is refused, naming a and its PID "
          f"(rc={res.returncode} out={out[-400:]!r})")
    check(b_tray.poll() is None, "the refused restart left b's own tray running (nothing stopped)")
    check(answers(port), "a's server still serves after the refusal")
    kill_under(tmp)
    a_srv.wait(timeout=10)
    b_tray.wait(timeout=10)
    check(wait_answering(port, False), "refusal case cleaned up")

    # ---- a's watchdog revives a's server while b starts: not b's restart ------
    port = free_port()
    revive = f"{b_root / '.venv' / 'revive.py'} {a_python} {a_root / '.venv' / 'stub_server.py'} {port} {head}"
    res = launch(b_root, port, revive, 10)
    out = res.stdout + res.stderr
    check(res.returncode != 0 and "anothercheckout" in flat(out) and flat(str(a_root)) in flat(out),
          f"a restart from b that comes up served by a's process fails, naming a "
          f"(rc={res.returncode} out={out[-400:]!r})")
    kill_under(tmp)
    check(wait_answering(port, False), "revive case cleaned up")

    # ---- a listens only on another specific address: ignored -----------------
    ip = lan_ipv4()
    if ip is None:
        check(True, "no non-loopback IPv4 on this host: the live other-address case is covered by the filter table only")
    else:
        port = free_port()
        a_srv = serve(a_python, a_root, ip, port, head)
        time.sleep(2)
        check(a_srv.poll() is None, f"a's server listens on {ip} only")
        res = launch(b_root, port, f"{b_server} {port} {head}", 30)
        out = res.stdout + res.stderr
        check(res.returncode == 0 and "from a new process" in out,
              f"a's listener on another specific address neither blocks b's restart nor serves it "
              f"(rc={res.returncode} out={out[-400:]!r})")
        check(a_srv.poll() is None, "a's other-address listener was left running")
        kill_under(tmp)
        a_srv.wait(timeout=10)
finally:
    kill_under(tmp)
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_tray_lifecycle_checkouts")
