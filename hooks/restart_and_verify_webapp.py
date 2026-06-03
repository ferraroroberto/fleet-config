"""Project-aware: kill the webapp PID, bring the webapp back, verify the new build.

Looks up the project from the current working directory (matched against
`projects.toml`), kills only the PID listening on that project's
`webapp_port` (sister hubs on `never_kill_ports` are never touched), brings
the webapp back, then polls `<api_version_path>` until `git_sha` matches
`git rev-parse HEAD`. Reports the new `asset_hash`.

Two restart strategies, chosen per project:

* **`restart_cmd` (tray-owned apps — app-launcher / photo-ocr / voice-transcriber).**
  Their webapp runs as a uvicorn *child* of a long-lived `launcher.py tray`.
  An idempotent `tray.bat` no-ops while that tray lives, so it never respawns
  the webapp — the old "kill PID + run tray_cmd" sequence left the webapp dead
  (issue #51). When `restart_cmd` is set we run it instead: it respawns a fresh
  uvicorn through the app's `WebappManager`, which the tray adopts. It never
  touches the `:8446` session-host (unlike `tray.bat --restart`, which cascades
  a `/T` kill onto live PTY sessions). A just-killed port can linger in
  `TIME_WAIT` and make the respawn race-fail to bind, so the respawn is retried
  with a settle between attempts.

* **`tray_cmd` (everything else, e.g. local-llm-hub).** Relaunch the tray and
  wait for the port. Unchanged default.

Replaces the manual ritual from `feedback_restart_webapp_after_changes.md`.

Usage (called by the /restart-webapp slash command, but runnable directly):

    py C:/Users/rober/.claude/hooks/restart_and_verify_webapp.py [--cwd <path>]

Exit codes:
    0 on success (and prints the version line)
    1 if the project isn't in projects.toml or has no webapp_port
    2 on kill / restart / verify failure
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402


KILL_TIMEOUT_S       = 8
START_TIMEOUT_S      = 25
VERIFY_TIMEOUT_S     = 25
POLL_INTERVAL_S      = 0.5

# A force-killed listening socket lingers in TIME_WAIT; a fresh uvicorn can lose
# the bind race and exit before binding. Settle, and retry the respawn.
TIME_WAIT_SETTLE_S   = 3
RESTART_ATTEMPTS     = 3
RESTART_CMD_TIMEOUT_S = 30
PER_ATTEMPT_WAIT_S   = 4


def _run_ps(
    command: str,
    *,
    cwd: Optional[Path] = None,
    timeout: Optional[int] = 15,
    env: Optional[dict] = None,
) -> subprocess.CompletedProcess:
    """Run a PowerShell one-liner via the absolute Windows PowerShell 5.1 path."""
    ps = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    return subprocess.run(
        [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(cwd) if cwd else None,
        env=env,
    )


def _first_line(text: Optional[str]) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _pid_on_port(port: int) -> Optional[int]:
    res = _run_ps(
        "$c = Get-NetTCPConnection -LocalPort " + str(port) + " -State Listen -ErrorAction SilentlyContinue | "
        "Select-Object -First 1; if ($c) { $c.OwningProcess }"
    )
    out = (res.stdout or "").strip()
    if not out:
        return None
    try:
        return int(out)
    except ValueError:
        return None


def _kill_pid(pid: int) -> None:
    _run_ps("Stop-Process -Id " + str(pid) + " -Force")


def _wait_port_free(port: int, deadline: float) -> bool:
    while time.time() < deadline:
        if _pid_on_port(port) is None:
            return True
        time.sleep(POLL_INTERVAL_S)
    return False


def _wait_port_listening(port: int, deadline: float) -> bool:
    while time.time() < deadline:
        if _pid_on_port(port) is not None:
            return True
        time.sleep(POLL_INTERVAL_S)
    return False


def _git_head(cwd: Path) -> Optional[str]:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if res.returncode != 0:
        return None
    return (res.stdout or "").strip() or None


_INSECURE_CTX = ssl._create_unverified_context()  # self-signed certs are normal in our fleet


def _fetch_version(port: int, path: str, timeout: float = 2.0) -> Optional[dict]:
    # Most fleet apps serve HTTPS with a self-signed cert on the same port,
    # but a few (e.g. local-llm-hub) serve plain HTTP. Try HTTPS first, then HTTP.
    for scheme in ("https", "http"):
        url = scheme + "://127.0.0.1:" + str(port) + path
        try:
            ctx = _INSECURE_CTX if scheme == "https" else None
            with urllib.request.urlopen(url, timeout=timeout, context=ctx) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, ConnectionError, OSError):
            continue
    return None


def _start_tray(tray_cmd: str, cwd: Path) -> None:
    # Use cmd /c start so the tray runs detached; we don't want to block on it.
    # Quote the tray_cmd for safety; assume it's a relative bat path.
    subprocess.Popen(
        ["cmd", "/c", "start", "", tray_cmd],
        cwd=str(cwd),
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )


def _restart_via_cmd(restart_cmd: str, cwd: Path, port: int) -> bool:
    """Run the project's `restart_cmd` (a WebappManager respawn) until the port
    is listening. The respawn spawns a detached uvicorn that survives this call
    and that the tray adopts. Retried because a just-killed port can linger in
    TIME_WAIT and make the first bind race-fail. Returns True once listening.
    """
    # Force UTF-8 so a manager traceback (its messages carry emoji) can't itself
    # die with UnicodeEncodeError under captured stdout (the Windows gotcha).
    env = {**os.environ, "PYTHONUTF8": "1"}
    for attempt in range(1, RESTART_ATTEMPTS + 1):
        print(f"[restart-webapp] respawn attempt {attempt}/{RESTART_ATTEMPTS}: {restart_cmd} (cwd={cwd})")
        try:
            res = _run_ps(restart_cmd, cwd=cwd, timeout=RESTART_CMD_TIMEOUT_S, env=env)
            if res.returncode != 0:
                reason = _first_line(res.stderr) or _first_line(res.stdout) or f"exit {res.returncode}"
                print(f"[restart-webapp]   respawn reported: {reason}")
        except subprocess.TimeoutExpired:
            print(f"[restart-webapp]   respawn timed out after {RESTART_CMD_TIMEOUT_S}s")

        if _wait_port_listening(port, time.time() + PER_ATTEMPT_WAIT_S):
            return True
        if attempt < RESTART_ATTEMPTS:
            print(f"[restart-webapp]   :{port} still down — settling {TIME_WAIT_SETTLE_S}s for TIME_WAIT, then retrying")
            time.sleep(TIME_WAIT_SETTLE_S)
    return False


def recovery_hint(name: str, port: int, root: Path, restart_cmd: Optional[str], tray_cmd: Optional[str]) -> str:
    """Actionable manual-recovery text shown when the webapp can't be revived —
    so the user isn't left with a bare `nothing listening on :<port>`."""
    lines = [
        f"[restart-webapp] Could not bring {name} back on :{port} automatically.",
        f"[restart-webapp] Manual recovery (run in {root}):",
    ]
    n = 1
    if restart_cmd:
        lines.append(f"[restart-webapp]   {n}) Re-spawn through the manager (tray adopts it; spares :8446):")
        lines.append(f"[restart-webapp]        {restart_cmd}")
        n += 1
    if tray_cmd:
        lines.append(f"[restart-webapp]   {n}) Full clean restart (destroys :8446 PTY sessions — last resort):")
        lines.append(f"[restart-webapp]        {tray_cmd} --restart")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Restart and verify a project's webapp.")
    parser.add_argument("--cwd", help="Override the working directory (defaults to $PWD).")
    args = parser.parse_args()

    cwd = Path(args.cwd) if args.cwd else Path(os.getcwd())

    project = _lib.detect_project(cwd)
    if project is None:
        print(
            "No project matches cwd=" + str(cwd) + " in projects.toml. "
            "Add a [project] table with cwd_prefix to register it.",
            file=sys.stderr,
        )
        return 1
    if project.webapp_port is None:
        print(
            "Project " + project.name + " has no webapp_port in projects.toml.",
            file=sys.stderr,
        )
        return 1
    if not project.tray_cmd:
        print(
            "Project " + project.name + " has no tray_cmd in projects.toml.",
            file=sys.stderr,
        )
        return 1

    port      = project.webapp_port
    api_path  = project.api_version_path or "/api/version"
    project_root = project.cwd_prefix
    restart_cmd = project.restart_cmd

    print(f"[restart-webapp] project={project.name} port={port} root={project_root}")

    # 1) Kill the running PID, if any. We only ever target this project's own
    #    webapp_port — the tray and the :8446 session-host are never touched.
    pid = _pid_on_port(port)
    if pid is None:
        print(f"[restart-webapp] no PID listening on :{port} — will just (re)start it")
    else:
        print(f"[restart-webapp] killing PID {pid} on :{port}")
        _kill_pid(pid)
        if not _wait_port_free(port, time.time() + KILL_TIMEOUT_S):
            print(f"[restart-webapp] ERROR: port :{port} still occupied after {KILL_TIMEOUT_S}s", file=sys.stderr)
            return 2
        # The closed socket lingers in TIME_WAIT; give the OS a beat to release
        # it so the respawn below can rebind instead of losing the race.
        time.sleep(TIME_WAIT_SETTLE_S)

    # 2) Bring the webapp back — respawn-through-manager for tray-owned apps,
    #    else relaunch the tray.
    if restart_cmd:
        came_up = _restart_via_cmd(restart_cmd, project_root, port)
    else:
        print(f"[restart-webapp] launching {project.tray_cmd} (cwd={project_root})")
        try:
            _start_tray(project.tray_cmd, project_root)
        except OSError as exc:
            print(f"[restart-webapp] ERROR: failed to launch tray: {exc}", file=sys.stderr)
            return 2
        came_up = _wait_port_listening(port, time.time() + START_TIMEOUT_S)

    # 3) Confirm something is listening; otherwise hand back an actionable hint.
    if not came_up:
        print(f"[restart-webapp] ERROR: nothing listening on :{port}", file=sys.stderr)
        print(recovery_hint(project.name, port, project_root, restart_cmd, project.tray_cmd), file=sys.stderr)
        return 2

    # 4) Verify the new build via /api/version
    expected_sha = _git_head(project_root)
    deadline = time.time() + VERIFY_TIMEOUT_S
    last_payload: Optional[dict] = None
    while time.time() < deadline:
        payload = _fetch_version(port, api_path)
        if payload is not None:
            last_payload = payload
            got_sha = str(payload.get("git_sha") or "")
            if expected_sha is None or got_sha.startswith(expected_sha[:7]) or expected_sha.startswith(got_sha[:7]):
                print(
                    "[restart-webapp] OK  git_sha=" + got_sha[:7]
                    + (" (matches HEAD)" if expected_sha else "")
                    + "  asset_hash=" + str(payload.get("asset_hash") or "?")
                )
                return 0
        time.sleep(POLL_INTERVAL_S)

    print(
        "[restart-webapp] ERROR: " + api_path + " did not converge to HEAD within "
        + str(VERIFY_TIMEOUT_S) + "s. last payload: " + json.dumps(last_payload),
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
