"""Shared helpers for the tray_lifecycle.ps1 suites: the script path, a PowerShell 5.1 runner,
a stub version server and the port and cleanup probes they all drive it with."""
from __future__ import annotations

import os
import socket
import subprocess
import textwrap
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "tray" / "tray_lifecycle.ps1"
POWERSHELL = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
NW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# argv: <port> <git_sha> [nopid] [--host <address>]. It reports its own pid unless told
# `nopid`, and binds 127.0.0.1 unless given --host. Any other trailing argument is ignored,
# so a caller can plant a path in the command line.
STUB = textwrap.dedent('''
    import json, os, sys
    from http.server import BaseHTTPRequestHandler, HTTPServer

    port, head, rest = int(sys.argv[1]), sys.argv[2], sys.argv[3:]
    with_pid = "nopid" not in rest
    host = rest[rest.index("--host") + 1] if "--host" in rest else "127.0.0.1"

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

    Exclusive((host, port), H).serve_forever()
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


def init_repo(root: Path) -> str:
    """A git repo at ``root`` with one empty commit; returns its HEAD."""
    root.mkdir(parents=True, exist_ok=True)
    git = ["git", "-C", str(root)]
    subprocess.run([*git, "init", "-q"], check=True, creationflags=NW)
    subprocess.run([*git, "-c", "core.hooksPath=/dev/null", "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "--allow-empty", "-m", "x"], check=True, creationflags=NW)
    return subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
                          creationflags=NW).stdout.strip()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def answers(port: int) -> bool:
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
    """Stop every process whose command line names ``base``."""
    run_ps(["-Command",
            f"Get-CimInstance Win32_Process | Where-Object {{ $_.ProcessId -ne $PID -and $_.CommandLine -and "
            f"$_.CommandLine.IndexOf('{base}', [System.StringComparison]::OrdinalIgnoreCase) -ge 0 }} | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"])
