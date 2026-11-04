"""Unit tests for skills/_lib/grokbot/switch.py and the `ensure` / digest / attention readers that use it.

The launcher response is always a fake, so the owner's live switch is never read or changed. The relay
reader is covered against a loopback fake launcher in test_relay.py.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/grokbot/test_switch.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
from grokbot import attention  # noqa: E402
from grokbot import status as gb  # noqa: E402
from grokbot import switch as sw  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

tmp = Path(tempfile.mkdtemp(prefix="grokbot-switch-test-"))
URL = "http://127.0.0.1:1"
REAL_READ = sw.read_switch


def unreachable(base_url, path, **kw):
    raise OSError("connection refused")


def responding(body):
    return lambda base_url, path, **kw: body


# label, launcher answer, expected switch state
CASES = (
    ("on", responding({"settings": {"grokbot_enabled": True}}), sw.ON),
    ("off", responding({"settings": {"grokbot_enabled": False}}), sw.OFF),
    ("missing field", responding({"settings": {"worker_cap": 3}}), sw.UNKNOWN),
    ("no settings object", responding({}), sw.UNKNOWN),
    ("non-boolean string", responding({"settings": {"grokbot_enabled": "true"}}), sw.UNKNOWN),
    ("non-boolean number", responding({"settings": {"grokbot_enabled": 1}}), sw.UNKNOWN),
    ("null", responding({"settings": {"grokbot_enabled": None}}), sw.UNKNOWN),
    ("unreachable", unreachable, sw.UNKNOWN),
)


def reader(request):
    return lambda *a, **k: REAL_READ(URL, request=request)


try:
    for label, request, expected in CASES:
        state, reason = REAL_READ(URL, request=request)
        check(state == expected, f"read_switch: {label} -> {expected}")
        check((reason == "") == (expected != sw.UNKNOWN), f"read_switch: {label} carries a reason only when unknown")

    # ---- ensure: only a confirmed on may start or ping ---------------------------------
    outage = tmp / "outage.json"
    for label, request, expected in CASES:
        launched, pings = [], []
        res = gb.ensure(
            exe=tmp / "Grok Bot.exe", status_file=tmp / "none.json", outage_file=outage,
            launch=launched.append, notify=lambda t: pings.append(t) or True,
            settle_seconds=0.0, sleep=lambda s: None, switch=reader(request),
        )
        if expected == sw.ON:
            # The exe is absent in this fixture, so the app reads unknown: on lets the normal check run.
            check(res.action == "refused", f"ensure: {label} runs the normal check")
        else:
            want = "disabled" if expected == sw.OFF else "switch-unknown"
            check(res.action == want and not launched and not pings and not outage.exists(),
                  f"ensure: {label} -> {want}, no start, no ping, no outage record")

    # main(): exit 0 and a distinct GROKBOT= line, so the Job never alarms
    for label, state, line in (("off", sw.OFF, "GROKBOT=disabled"), ("unknown", sw.UNKNOWN, "GROKBOT=unknown")):
        out = io.StringIO()
        with mock.patch.object(sw, "read_switch", return_value=(state, "fixture")), contextlib.redirect_stdout(out):
            code = gb.main(["ensure"])
        check(code == 0 and line in out.getvalue(), f"ensure CLI: switch {label} prints {line} and exits 0")

    # ---- digest line ---------------------------------------------------------------------
    for label, request, expected in CASES:
        line = gb.digest_line(switch=reader(request), exe=tmp / "nope.exe", status_file=tmp / "none.json")
        if expected == sw.OFF:
            check(line == "grokbot=disabled", f"digest: {label} reads disabled")
        elif expected == sw.UNKNOWN:
            check(line.startswith("grokbot=unknown (") and "not installed" not in line,
                  f"digest: {label} reads unknown from the switch")
        else:
            check("not installed" in line, f"digest: {label} falls through to the app reading")

    # ---- attention -----------------------------------------------------------------------
    for label, request, expected in CASES:
        with mock.patch.object(sw, "read_switch", reader(request)), \
                mock.patch.object(attention.grok_status, "read_status", return_value=gb.Status(gb.DOWN, "x")):
            items, unknown = attention._grokbot_lines()
        if expected == sw.OFF:
            check(items == [] and unknown == [], f"attention: {label} is quiet, off is the owner's choice")
        elif expected == sw.UNKNOWN:
            check(items == [] and unknown and unknown[0].startswith("UNKNOWN grokbot"),
                  f"attention: {label} is unknown, never folded into quiet")
        else:
            check(items and not unknown, f"attention: {label} still reports a down app")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_grokbot_switch")
