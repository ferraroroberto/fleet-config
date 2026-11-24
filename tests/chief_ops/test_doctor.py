"""chief_ops doctor: the read-only setup check against a temp Claude home, a temp state dir,
a fake verb runner and a stubbed launcher (fleet-config#2226).

Covers a missing hook target, a backslashed command, a broken hooks link, an unreadable
mailbox (unknown, never pass), corrupt waiter heartbeats, log grouping, the verdict line,
redaction and that a run writes nothing.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/chief_ops/test_doctor.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _suite import REPO, _h, check, co  # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="chief_doctor_"))
_STATE = _TMP / "state"
_STATE.mkdir()
os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(_STATE)

from chief import doctor, doctor_logs, mailbox  # noqa: E402


def _link(link: Path, target: Path) -> None:
    """A directory junction on Windows (no privilege needed), a symlink elsewhere."""
    if sys.platform == "win32":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def _checkout() -> Path:
    root = _TMP / "fleet" / "fleet-config"
    (root / "hooks").mkdir(parents=True, exist_ok=True)
    (root / "skills").mkdir(exist_ok=True)
    (root / "hooks" / "run-hook.ps1").write_text("", encoding="utf-8")
    (root / "hooks" / "hook_dispatch.py").write_text("", encoding="utf-8")
    for name in ("session_state", "secret_scan"):
        (root / "hooks" / f"{name}.py").write_text("", encoding="utf-8")
    return root


def _home(checkout: Path, commands: list) -> Path:
    home = _TMP / f"home{len(list(_TMP.glob('home*')))}"
    home.mkdir()
    _link(home / "hooks", checkout / "hooks")
    _link(home / "skills", checkout / "skills")
    hooks = {"Stop": [{"hooks": [{"type": "command", "command": c} for c in commands]}]}
    (home / "settings.json").write_text(json.dumps({"hooks": hooks}), encoding="utf-8")
    return home


CHECKOUT = _checkout()
HOOKS = (CHECKOUT / "hooks").as_posix()
GOOD = [
    f"powershell.exe -File {HOOKS}/run-hook.ps1 -Hook session_state",
    f"python.exe {HOOKS}/hook_dispatch.py secret_scan",
]


def _by_name(checks):
    return {f"{c.area}.{c.name}": c for c in checks}


# ---- install: settings targets and slashes -----------------------------------------------

d = doctor.Doctor(CHECKOUT, _home(CHECKOUT, GOOD))
got = _by_name(d.check_settings())
check(got["install.settings.targets"].status == doctor.PASS and got["install.settings.slashes"].status == doctor.PASS,
      "every -Hook module and hook_dispatch name that exists passes, forward slashes pass")

d = doctor.Doctor(CHECKOUT, _home(CHECKOUT, GOOD + [f"powershell.exe -File {HOOKS}/run-hook.ps1 -Hook gone_hook"]))
got = _by_name(d.check_settings())
check(got["install.settings.targets"].status == doctor.FAIL
      and "gone_hook.py" in got["install.settings.targets"].detail["missing"],
      "a -Hook name with no module is a missing hook target")

d = doctor.Doctor(CHECKOUT, _home(CHECKOUT, GOOD + [f"python.exe {HOOKS}/hook_dispatch.py secret_scan no_such"]))
check("no_such.py" in _by_name(d.check_settings())["install.settings.targets"].detail.get("missing", ""),
      "a hook_dispatch name with no module is a missing hook target")

back = GOOD + ["powershell.exe -File " + str(CHECKOUT / "hooks" / "run-hook.ps1").replace("/", "\\") + " -Hook session_state"]
check(_by_name(doctor.Doctor(CHECKOUT, _home(CHECKOUT, back)).check_settings())["install.settings.slashes"].status
      == doctor.FAIL, "a backslashed command fails the slashes check")

bad_json = _home(CHECKOUT, GOOD)
(bad_json / "settings.json").write_text("{not json", encoding="utf-8")
check(all(c.status == doctor.UNKNOWN for c in doctor.Doctor(CHECKOUT, bad_json).check_settings()),
      "an unparseable settings.json is unknown, never pass")

# ---- install: links ------------------------------------------------------------------------

home = _home(CHECKOUT, GOOD)
d = doctor.Doctor(CHECKOUT, home)
check(d.check_link("hooks").status == doctor.PASS, "a hooks link into this checkout passes")

other = _TMP / "other-checkout" / "hooks"
other.mkdir(parents=True)
home2 = _TMP / "home-other"
home2.mkdir()
_link(home2 / "hooks", other)
check(doctor.Doctor(CHECKOUT, home2).check_link("hooks").status == doctor.FAIL,
      "a hooks link into another checkout fails")

dangling = _TMP / "dangling-target"
dangling.mkdir()
home3 = _TMP / "home-broken"
home3.mkdir()
_link(home3 / "hooks", dangling)
dangling.rmdir()
broken = doctor.Doctor(CHECKOUT, home3).check_link("hooks")
check(broken.status == doctor.FAIL and broken.detail.get("reason") == "broken link", "a broken hooks link fails")
check(doctor.Doctor(CHECKOUT, home3).check_link("skills").status == doctor.FAIL, "a missing skills link fails")

# ---- verbs ---------------------------------------------------------------------------------

OUTPUTS = {
    "wake": "WAKE=doorbell doorbell=on fallback=waiter seconds=1800\n",
    "inbox": "PENDING=0\n", "board": "{}", "sessions": "{}", "chief-sid": f"CHIEF_SID={'a' * 32}\n",
    "plan": "{}", "slots-idle": "SLOTS_IDLE=0\n", "sweep": "SUMMARY finished=0\n",
    "owner-checks": "OWNER_ITEMS=0 UNKNOWN=0\n",
}


def fake_runner(outputs):
    def run(argv, timeout):
        return 0, outputs[argv[2]]
    return run


verbs = _by_name(doctor.Doctor(CHECKOUT, home, runner=fake_runner(OUTPUTS)).check_verbs())
check(all(c.status == doctor.PASS for c in verbs.values()) and len(verbs) == 9, "nine healthy read verbs pass")

worse = dict(OUTPUTS, **{"owner-checks": "OWNER_ITEMS=0 UNKNOWN=2\n", "slots-idle": "nothing\n",
                         "inbox": "UNREACHABLE=refused\n"})
verbs = _by_name(doctor.Doctor(CHECKOUT, home, runner=fake_runner(worse)).check_verbs())
check(verbs["verb.owner-checks"].status == doctor.UNKNOWN, "owner-checks with unknown repos is unknown")
check(verbs["verb.slots-idle"].status == doctor.FAIL, "a verb without its expected line fails")
check(verbs["verb.inbox-count"].status == doctor.UNKNOWN, "an unreachable launcher is unknown, never pass")

# ---- mailbox, doorbell, waiters ------------------------------------------------------------

inbox = mailbox.chief_inbox_dir()
inbox.mkdir(parents=True)
(inbox / f"{mailbox._now_ms() - 3_600_000}-old.json").write_text("{}", encoding="utf-8")
check(d.check_mailbox().status == doctor.FAIL, "an event pending past the fallback waiter fails")
(inbox / next(inbox.glob("*.json")).name).unlink()
check(d.check_mailbox().status == doctor.PASS, "an empty mailbox passes")

real_event_files = mailbox._event_files


def _refuse(directory):
    raise PermissionError("denied")


mailbox._event_files = _refuse
check(d.check_mailbox().status == doctor.UNKNOWN, "an unreadable mailbox is unknown, never pass")
mailbox._event_files = real_event_files

real_request = doctor.launcher._request
doctor.launcher._request = lambda base, path: {"chief_doorbell": False}
d.verb_lines["wake"] = OUTPUTS["wake"].strip()
check(_by_name(d.check_doorbell())["mailbox.doorbell"].status == doctor.FAIL, "a doorbell setting wake disagrees with fails")


def _down(base, path):
    raise urllib.error.URLError("refused")


doctor.launcher._request = _down
got = _by_name(d.check_doorbell())
check(got["launcher.reachable"].status == doctor.FAIL and got["mailbox.doorbell"].status == doctor.UNKNOWN,
      "an unreachable launcher fails reachability and leaves the doorbell unknown")
doctor.launcher._request = real_request

waiters = _STATE / "chief-waiters"
waiters.mkdir()
now = datetime(2026, 1, 1, tzinfo=timezone.utc)
for name, end in (("a", now + timedelta(minutes=5)), ("b", now - timedelta(minutes=5))):
    (waiters / f"{name}.json").write_text(json.dumps({"expected_end": end.strftime("%Y-%m-%dT%H:%M:%SZ")}), encoding="utf-8")
(waiters / "c.json").write_text(" " * 20, encoding="utf-8")
w = d.check_waiters(now)
check(w.status == doctor.PASS and (w.detail["armed"], w.detail["expired"], w.detail["corrupt"]) == ("1", "1", "1"),
      "waiter heartbeats count armed, expired and corrupt files separately")

# ---- logs ----------------------------------------------------------------------------------

logs = _TMP / "launcher" / "webapp"
logs.mkdir(parents=True)
stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
(logs / "webapp-warnings.log").write_text(
    f"{stamp},101 [ERROR] x: failed 12 times\n{stamp},102 [ERROR] x: failed 13 times\n"
    f"{stamp},103 [INFO] fine\nTraceback (most recent call last):\n  File a\nTimeoutError: SSL\n", encoding="utf-8")
(logs / "e2e-run.log").write_text(f"{stamp} [ERROR] gate noise\n", encoding="utf-8")
groups = doctor_logs.scan_logs(logs, "launcher", datetime.now() + timedelta(seconds=5))
check(sorted((g.kind, g.count) for g in groups) == [("error", 2), ("traceback", 1)],
      "repeated errors group by masked message, tracebacks count, e2e logs are skipped")
check(doctor_logs.scan_logs(_TMP / "no-such", "x", datetime.now()) == [], "a missing log folder has no groups")
check(all(g.severity == doctor_logs.FAIL for g in groups), "an ordinary error and its traceback are failures")

(logs / "session-host.log").write_text(
    f"{stamp},1 [ERROR] asyncio: Unhandled exception in client_connected_cb\ntransport: <x>\n"
    "Traceback (most recent call last):\n  File a\nTimeoutError: SSL shutdown timed out\n"
    f"{stamp},2 [INFO] next\nTraceback (most recent call last):\n  File b\nKeyError: 'k'\n", encoding="utf-8")
host = {(g.kind, g.message): g.severity for g in doctor_logs.scan_logs(logs, "launcher", datetime.now() + timedelta(seconds=5))
        if g.source.endswith("session-host.log")}
check(host.get(("error", "asyncio: Unhandled exception in client_connected_cb")) == doctor_logs.NOTE
      and host.get(("traceback", "TimeoutError: SSL shutdown timed out")) == doctor_logs.NOTE,
      "a client that went away, and the traceback under it, are notes")
check(host.get(("traceback", "KeyError: 'k'")) == doctor_logs.FAIL, "a traceback after a later line is a failure again")
(logs / "session-host.log").unlink()

dl = doctor.Doctor(CHECKOUT, home, launcher_dir=None, launcher_reason="not registered")
log_checks = _by_name(dl.check_logs()[0])
check(log_checks["logs.launcher"].status == doctor.UNKNOWN, "an unknown launcher checkout is unknown, never pass")

# ---- verdict, redaction, wiring ------------------------------------------------------------

C = doctor.Check
check(doctor.verdict([C("a", "b", doctor.PASS)]) == "DOCTOR=pass fails=0 unknowns=0", "all pass is pass")
check(doctor.verdict([C("a", "b", doctor.PASS), C("a", "c", doctor.UNKNOWN)]) == "DOCTOR=unknown fails=0 unknowns=1",
      "an unknown without a fail is unknown")
check(doctor.verdict([C("a", "b", doctor.FAIL), C("a", "c", doctor.UNKNOWN)]).startswith("DOCTOR=fail "),
      "a fail wins")

red = doctor.Redactor([(r"C:\Users\jdoe\fleet\fleet-config", "<checkout>"), (r"C:\Users\jdoe", "<home>")],
                      "jdoe", "BOX-7")
text = red(r"at C:\Users\jdoe\fleet\fleet-config\hooks, c:/users/jdoe/x, user jdoe on BOX-7, D:\data\secret\file.log "
           f"sid {'0123456789abcdef' * 2}")
check("jdoe" not in text.lower() and "box-7" not in text.lower() and "D:" not in text and "0123456789abcdef" not in text,
      "home, user, host, unnamed absolute paths and session ids are all masked")
check("<checkout>\\hooks" in text and "<home>/x" in text and "<path>/file.log" in text and "<sid>" in text,
      "masks keep the readable tail")

lines = doctor.render([C("install", "venv", doctor.PASS, {"python": r"C:\Users\jdoe\x.exe"})], groups, red)
check(lines[-1] == "DOCTOR=pass fails=0 unknowns=0" and not any("jdoe" in ln for ln in lines),
      "rendered output ends with the verdict and carries no user name")

before = sorted(p.relative_to(_TMP).as_posix() for p in _TMP.rglob("*"))
doctor.Doctor(CHECKOUT, home, runner=fake_runner(OUTPUTS), base_url="http://127.0.0.1:9").run_all()
after = sorted(p.relative_to(_TMP).as_posix() for p in _TMP.rglob("*"))
check(before == after, "a full run writes nothing")

check(co.cli.build_parser("x").parse_args(["doctor"]).func is doctor.cmd_doctor, "`chief_ops.py doctor` is wired")
check(doctor.DEFAULT_CHECKOUT == REPO, "the default checkout is the one the code sits in")

shutil.rmtree(_TMP, ignore_errors=True)
_h.report_and_exit("test_doctor")
