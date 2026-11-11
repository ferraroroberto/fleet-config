"""Is the Grok Bot desktop app running and signed in, and bring it back when it is not.

Grok Bot is the owner's phone front door to the chief: its
iPhone app drives commands that run on this PC through the desktop app, so the
desktop app being down means the front door is shut. Two commands:

    status.py status    one read-only reading; never starts or changes anything
    status.py ensure    start the app minimized when it is down, then record the check

`status` prints `GROKBOT=<state>` and exits 0 for `up`, 1 for `down` and
`signed-out`, 3 for `unknown` (not confirmed, never a pass). The states:

- `up`: the pid in the app's own status file is alive, its executable is the
  installed `Grok Bot.exe`, and the file says the user is signed in.
- `signed-out`: the same process is running but the file says nobody is signed
  in. Only the owner can fix that, so `ensure` leaves it alone.
- `down`: no process runs the installed executable.
- `unknown`: the evidence did not settle it (no install, a status file that does
  not describe the running process, an unreadable process table). `unknown` is
  never counted as `up`, and `ensure` refuses to start anything on it, because a
  second launch over a half-started app is worse than waiting a cycle.

`ensure` never kills a process and never touches the app's settings or startup
entries; those are the owner's. It records each finished check in an outage
episode: the first check that ends `down` or `signed-out` starts one, a second
consecutive one sends a single attention ping, and an `up` reading closes it. An
`unknown` check leaves the episode as it was.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# A direct run starts without `skills/_lib` on the path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import active_issue  # noqa: E402
from fleet_core import hooks_state  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402

from grokbot import switch as grok_switch  # noqa: E402

logger = logging.getLogger("fleet_chief")

REPO = Path(__file__).resolve().parents[3]
UP, DOWN, SIGNED_OUT, UNKNOWN = "up", "down", "signed-out", "unknown"
DISABLED = "disabled"
EXE_NAME = "Grok Bot.exe"
OUTAGE_STATE_NAME = "grokbot-outage.json"
# Two consecutive failed checks before the ping: one blip (an update restart) stays quiet.
PING_AFTER_BAD_CHECKS = 2
# The app writes its status file a moment after the process starts; wait for it before judging.
SETTLE_SECONDS = 30.0
SETTLE_POLL_SECONDS = 2.0
# Win32 OpenProcess limited-query right.
_QUERY_LIMITED = 0x1000
_SW_SHOWMINNOACTIVE = 7


@dataclass(frozen=True)
class Status:
    state: str
    reason: str = ""
    pid: Optional[int] = None


def default_exe() -> Path:
    override = os.environ.get("GROKBOT_EXE")
    if override:
        return Path(override)
    return Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Grok Bot" / EXE_NAME


def default_status_file() -> Path:
    override = os.environ.get("GROKBOT_STATUS_FILE")
    if override:
        return Path(override)
    return Path(os.environ.get("APPDATA", "")) / "Grok Bot" / "desktop-status.json"


def _same_path(a: str, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(str(b)))


def image_path(pid: int) -> Optional[str]:
    """The executable path of a live process, None when no such process runs.

    Liveness comes from the fleet's one pid probe; only the path lookup lives here.
    Raises OSError when the answer cannot be had (probe unsure, access denied, not Windows).
    """
    if sys.platform != "win32":
        raise OSError("process image lookup is Windows-only")
    alive = active_issue.pid_state(pid)
    if alive is False:
        return None
    if alive is None:
        raise OSError(f"cannot tell whether pid {pid} is running")
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    handle = k32.OpenProcess(_QUERY_LIMITED, False, pid)
    if not handle:
        raise OSError(ctypes.get_last_error(), "OpenProcess failed")
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        if not k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            raise OSError(ctypes.get_last_error(), "QueryFullProcessImageNameW failed")
        return buf.value
    finally:
        k32.CloseHandle(handle)


def named_pids(exe_name: str) -> List[int]:
    """Pids of every process whose image name is `exe_name` (path not yet confirmed).

    Raises OSError when the process table cannot be read.
    """
    # `tasklist` prints localized text under the OEM code page, so the CSV is decoded as OEM.
    proc = subprocess.run(
        ["tasklist", "/FO", "CSV", "/NH", "/FI", f"IMAGENAME eq {exe_name}"],
        capture_output=True, text=True, encoding="oem", errors="replace",
        stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, timeout=30, check=False,
    )
    if proc.returncode != 0:
        raise OSError(f"tasklist exited {proc.returncode}: {proc.stderr.strip()[:200]}")
    pids: List[int] = []
    for row in csv.reader(proc.stdout.splitlines()):
        if len(row) >= 2 and row[0].lower() == exe_name.lower() and row[1].isdigit():
            pids.append(int(row[1]))
    return pids


def _read_status_file(path: Path) -> Optional[Dict[str, Any]]:
    """The app's status record, None when absent or not a usable record."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("pid"), int):
        return None
    return data


def read_status(
    *,
    exe: Optional[Path] = None,
    status_file: Optional[Path] = None,
    image_of: Callable[[int], Optional[str]] = image_path,
    pids_named: Callable[[str], List[int]] = named_pids,
) -> Status:
    """One reading of the app. Never raises, never changes anything."""
    exe = exe or default_exe()
    status_file = status_file or default_status_file()
    if not exe.is_file():
        return Status(UNKNOWN, f"Grok Bot is not installed at {exe}")
    record = _read_status_file(status_file)
    try:
        if record is not None:
            image = image_of(record["pid"])
            if image is not None and _same_path(image, exe):
                signed_in = record.get("signedIn")
                if signed_in is True:
                    return Status(UP, pid=record["pid"])
                if signed_in is False:
                    return Status(SIGNED_OUT, "running, nobody signed in", pid=record["pid"])
                return Status(UNKNOWN, "the status file does not say whether anyone is signed in",
                              pid=record["pid"])
        running = [pid for pid in pids_named(exe.name)
                   if (image := image_of(pid)) is not None and _same_path(image, exe)]
    except OSError as exc:
        return Status(UNKNOWN, f"could not read the process table: {exc}")
    if running:
        return Status(UNKNOWN, f"Grok Bot is running (pid {running[0]}) but its status file "
                               "does not describe it; it may still be starting", pid=running[0])
    return Status(DOWN, "no process runs the installed Grok Bot")


def digest_line(*, switch: Optional[Callable[[], Tuple[str, str]]] = None, **kwargs: Any) -> str:
    """The `grokbot=` token for the chief board digest; a crash reads as unknown, never as up.

    The owner's switch comes first: off reads `disabled`, an unreadable switch reads `unknown`.
    """
    state, reason = (switch or grok_switch.read_switch)()
    if state == grok_switch.OFF:
        return f"grokbot={DISABLED}"
    if state != grok_switch.ON:
        return f"grokbot=unknown ({reason})"
    try:
        status = read_status(**kwargs)
    except Exception as exc:  # noqa: BLE001 - the digest must print whatever happens here
        return f"grokbot=unknown (probe failed: {exc})"
    if status.state == UNKNOWN:
        return f"grokbot=unknown ({status.reason})"
    return f"grokbot={status.state}"


def launch_minimized(exe: Path) -> None:
    """Start the app minimized and detached from this process; raises OSError when it cannot."""
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = _SW_SHOWMINNOACTIVE
    subprocess.Popen(
        [str(exe)], startupinfo=startup, cwd=str(exe.parent), close_fds=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=NO_WINDOW,
    )


def send_attention(text: str) -> bool:
    """Send `text` to the attention chat through the fleet's one notifier."""
    proc = subprocess.run(
        [sys.executable, str(REPO / "hooks" / "notify_send.py"), "--category", "attention", "--text", text],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, cwd=str(REPO), timeout=120, check=False,
    )
    if proc.returncode != 0:
        logger.warning("⚠️ grokbot attention ping failed: %s", (proc.stderr or proc.stdout).strip()[:200])
    return proc.returncode == 0


def _outage_text(state: str, bad_checks: int) -> str:
    # ASCII only: a Windows command line is not a UTF-8-safe channel to the notifier.
    if state == SIGNED_OUT:
        return (f"Grok Bot is running on the tower but signed out ({bad_checks} checks in a row). "
                "Sign in on the desktop app to bring the phone front door back.")
    return (f"Grok Bot is not running on the tower ({bad_checks} checks in a row, and the "
            "automatic start did not bring it back). Start it from the Start menu.")


def _load_outage(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"bad_checks": 0, "pinged": False}
    if not isinstance(data, dict) or not isinstance(data.get("bad_checks"), int):
        return {"bad_checks": 0, "pinged": False}
    return {"bad_checks": data["bad_checks"], "pinged": data.get("pinged") is True}


@dataclass(frozen=True)
class Ensured:
    action: str  # none | started | start-failed | refused | disabled | switch-unknown
    status: Status
    bad_checks: int
    pinged: bool
    state_error: str = ""


def ensure(
    *,
    exe: Optional[Path] = None,
    status_file: Optional[Path] = None,
    outage_file: Optional[Path] = None,
    image_of: Callable[[int], Optional[str]] = image_path,
    pids_named: Callable[[str], List[int]] = named_pids,
    launch: Callable[[Path], None] = launch_minimized,
    notify: Callable[[str], bool] = send_attention,
    settle_seconds: float = SETTLE_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    switch: Optional[Callable[[], Tuple[str, str]]] = None,
) -> Ensured:
    """Start the app when it is down, then record the outcome in the outage episode.

    The owner's switch is read first: off or unreadable does nothing, so no start, no ping and no
    outage bookkeeping happen while the owner has Grok Bot turned off.
    """
    switch_state, switch_reason = (switch or grok_switch.read_switch)()
    if switch_state == grok_switch.OFF:
        return Ensured("disabled", Status(DISABLED, "turned off in the app-launcher settings"), 0, False)
    if switch_state != grok_switch.ON:
        return Ensured("switch-unknown", Status(UNKNOWN, switch_reason), 0, False)
    exe = exe or default_exe()
    # The hooks state dir exists wherever hooks run; a missing one surfaces as an unwritable state, not silence.
    outage_file = outage_file or hooks_state.state_dir() / OUTAGE_STATE_NAME

    def reading() -> Status:
        return read_status(exe=exe, status_file=status_file, image_of=image_of, pids_named=pids_named)

    status = reading()
    action = "none"
    if status.state == UNKNOWN:
        action = "refused"
    elif status.state == DOWN:
        try:
            launch(exe)
            action = "started"
        except OSError as exc:
            action = "start-failed"
            status = Status(DOWN, f"could not start Grok Bot: {exc}")
        if action == "started":
            waited = 0.0
            while waited <= settle_seconds:
                status = reading()
                if status.state in (UP, SIGNED_OUT):
                    break
                sleep(SETTLE_POLL_SECONDS)
                waited += SETTLE_POLL_SECONDS
    logger.info("ℹ️ grokbot ensure: action=%s state=%s %s", action, status.state, status.reason)
    return _record(status, action, outage_file, notify)


def _record(status: Status, action: str, outage_file: Path, notify: Callable[[str], bool]) -> Ensured:
    episode = _load_outage(outage_file)
    if status.state == UNKNOWN:
        return Ensured(action, status, episode["bad_checks"], episode["pinged"])
    if status.state == UP:
        episode = {"bad_checks": 0, "pinged": False}
    else:
        episode["bad_checks"] += 1
        if episode["bad_checks"] >= PING_AFTER_BAD_CHECKS and not episode["pinged"]:
            # Marked pinged only on a delivered ping, so a failed send is retried next check.
            episode["pinged"] = bool(notify(_outage_text(status.state, episode["bad_checks"])))
    state_error = ""
    try:
        hooks_state.atomic_write_text(outage_file, json.dumps(episode))
    except OSError as exc:
        state_error = str(exc)
        logger.warning("⚠️ grokbot outage state not saved: %s", exc)
    return Ensured(action, status, episode["bad_checks"], episode["pinged"], state_error)


def _print_status(status: Status) -> int:
    reason = f" reason={status.reason}" if status.reason else ""
    print(f"GROKBOT={status.state}{reason}")
    return {UP: 0, DOWN: 1, SIGNED_OUT: 1}.get(status.state, 3)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("status", "ensure"))
    args = parser.parse_args(argv)
    if args.command == "status":
        return _print_status(read_status())
    result = ensure()
    print(f"ENSURE={result.action}")
    code = _print_status(result.status)
    if result.action in ("disabled", "switch-unknown"):
        return 0  # the Job must not alarm over the owner's switch
    print(f"OUTAGE bad_checks={result.bad_checks} pinged={'yes' if result.pinged else 'no'}")
    if result.state_error:
        print(f"OUTAGE_STATE=unwritable reason={result.state_error}")
        return code or 1
    return code


if __name__ == "__main__":
    sys.exit(main())
