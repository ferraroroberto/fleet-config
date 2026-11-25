"""Chief timings read from the launcher's chief settings, each with a tested fallback to today's default.

No live launcher: `launcher._request` is replaced for every read. Covers the resolver per value (in range, out of
range, wrong type, missing, unreachable), the `inbox` re-offer window, the `timings` verb, the Stop hook's hold
window and the scheduled runner's stall limit (fleet-config#2238).

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/chief_ops/test_chief_timings.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import urllib.error

from _suite import REPO, _h, check, co  # noqa: E402
from chief import timings  # noqa: E402

sys.path.insert(0, str(REPO / "hooks"))
import chief_waiter_stop as hook  # noqa: E402
import scheduled_runner as sr  # noqa: E402

_DEFAULTS = {"chief_rering_seconds": 60.0, "chief_reoffer_minutes": 900.0,
             "chief_hold_minutes": 600.0, "chief_stall_minutes": 2700.0}


def _seconds(resolved):
    return {key: item.seconds for key, item in resolved.items()}


@contextlib.contextmanager
def _launcher(reply):
    """Every launcher read answers `reply` (an exception is raised); yields the paths asked."""
    asked = []

    def fake(base, path, **_kw):
        asked.append(path)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    old = co.launcher._request
    co.launcher._request = fake
    try:
        yield asked
    finally:
        co.launcher._request = old


# ---- the resolver ----
check(_seconds(timings.resolve({})) == _DEFAULTS, "resolve: an empty block is today's defaults")
check(all(item.source == "default" and item.reason == "setting missing" for item in timings.resolve({}).values()),
      "resolve: a missing setting is a default with its reason, never 'settings'")
got = timings.resolve({"chief_rering_seconds": 90, "chief_reoffer_minutes": 20, "chief_hold_minutes": 5,
                       "chief_stall_minutes": 240})
check(_seconds(got) == {"chief_rering_seconds": 90.0, "chief_reoffer_minutes": 1200.0,
                        "chief_hold_minutes": 300.0, "chief_stall_minutes": 14400.0}
      and all(item.source == "settings" for item in got.values()),
      "resolve: in-range values are used, minutes converted, range edges included")
for bad, why in ((True, "not a whole number"), (15.5, "not a whole number"), ("15", "not a whole number"),
                 (9, "outside 10-120"), (121, "outside 10-120"), (0, "outside 10-120"), (None, "setting missing")):
    item = timings.resolve({"chief_reoffer_minutes": bad})["chief_reoffer_minutes"]
    check(item.value == 15 and item.seconds == 900.0 and item.source == "default" and why in item.reason,
          f"resolve: {bad!r} is not trusted -> default 15m ({item.reason})")
none = timings.resolve(None, "launcher unreachable")
check(_seconds(none) == _DEFAULTS and {i.reason for i in none.values()} == {"launcher unreachable"},
      "resolve: an unread block is every default with the reason")

# ---- the fetch ----
with _launcher({"settings": {"chief_stall_minutes": 60}}) as asked:
    got = timings.fetch("http://127.0.0.1:8445")
check(asked == ["/api/board/chief/settings"] and got["chief_stall_minutes"].seconds == 3600.0
      and got["chief_hold_minutes"].source == "default", "fetch: reads the chief settings and resolves each value alone")
for reply, why in ((urllib.error.URLError("refused"), "launcher unreachable"),
                   (urllib.error.HTTPError("u", 404, "nf", None, None), "HTTP 404"),
                   ({"model": "x"}, "no settings block"), ({"settings": "x"}, "no settings block"),
                   (ValueError("bad json"), "launcher unreachable")):
    with _launcher(reply):
        got = timings.fetch("http://127.0.0.1:8445")
    check(_seconds(got) == _DEFAULTS and all(why in i.reason for i in got.values()),
          f"fetch: {type(reply).__name__ if isinstance(reply, BaseException) else reply!r} -> defaults, reason names it")

# ---- format ----
check(timings.format_line(timings.resolve({"chief_hold_minutes": 7})).startswith(
    "TIMINGS rering=60s(default: setting missing) reoffer=15m(default: setting missing) hold=7m(settings) "),
      "format_line: value, unit and source per timing")


# ---- the timings verb ----
def run_verb(reply):
    out = io.StringIO()
    with _launcher(reply), contextlib.redirect_stdout(out):
        code = co.main(["timings"])
    return code, out.getvalue().strip()


code, out = run_verb({"settings": {"chief_rering_seconds": 60, "chief_reoffer_minutes": 15,
                                   "chief_hold_minutes": 10, "chief_stall_minutes": 45}})
check(code == 0 and out == "TIMINGS rering=60s(settings) reoffer=15m(settings) hold=10m(settings) stall=45m(settings)",
      "timings verb: all four from settings -> exit 0")
code, out = run_verb(urllib.error.URLError("refused"))
check(code == 1 and "default: launcher unreachable" in out, "timings verb: a default in use exits 1, never a quiet 0")

# ---- inbox re-offer window ----
os.environ["CLAUDE_HOOKS_STATE_DIR"] = tempfile.mkdtemp(prefix="chief_timings_")
args = co.cli.build_parser("x").parse_args(["inbox", "--count"])
check(args.stale_after is None, "inbox: --stale-after has no baked-in default, so the setting can supply it")
with _launcher({"settings": {"chief_reoffer_minutes": 30}}):
    check(timings.stale_after_seconds(args.stale_after) == 1800.0, "inbox: the re-offer window comes from chief_reoffer_minutes")
with _launcher(urllib.error.URLError("refused")):
    check(timings.stale_after_seconds(args.stale_after) == 900.0, "inbox: an unreadable setting keeps today's 900 s")
args = co.cli.build_parser("x").parse_args(["inbox", "--count", "--stale-after", "60"])
with _launcher({"settings": {"chief_reoffer_minutes": 30}}) as asked:
    check(timings.stale_after_seconds(args.stale_after) == 60.0 and not asked, "inbox: an explicit --stale-after wins and asks nothing")

# ---- the Stop hook's hold window ----
_real_ops = hook._chief_ops
try:
    hook._chief_ops = lambda *a: "TIMINGS rering=60s(settings) reoffer=15m(settings) hold=7m(settings) stall=45m(settings)"
    check(hook.hold_window_s() == 420.0, "hook: the hold window is chief_hold_minutes, in seconds")
    hook._chief_ops = lambda *a: ("TIMINGS rering=60s(default: x) reoffer=15m(default: x) "
                                  "hold=10m(default: x) stall=45m(default: x)")
    check(hook.hold_window_s() == 600.0, "hook: a default reported by the verb is still 10 minutes")
    for answer in (None, "", "WAKE=waiter doorbell=off"):
        hook._chief_ops = lambda *a, _a=answer: _a
        check(hook.hold_window_s() == 600.0, f"hook: a verb that gave no TIMINGS line ({answer!r}) keeps 600 s")
finally:
    hook._chief_ops = _real_ops


# ---- the scheduled runner's stall limit ----
class _Sink:
    def __init__(self):
        self.lines = []

    def emit(self, message):
        self.lines.append(message)


os.environ.pop("CLAUDE_PROGRESS_STALL_TIMEOUT", None)
sink = _Sink()
with _launcher({"settings": {"chief_stall_minutes": 90}}):
    check(sr.resolve_stall_with_launcher(None, sink) == 5400.0 and "launcher Settings" in sink.lines[0],
          "stall: the launcher's chief_stall_minutes sets the watchdog, and the log says where it came from")
sink = _Sink()
with _launcher(urllib.error.URLError("refused")):
    check(sr.resolve_stall_with_launcher(None, sink) == sr.DEFAULT_STALL_TIMEOUT_SECONDS == 2700.0
          and "default: launcher unreachable" in sink.lines[0],
          "stall: an unreadable setting keeps 2700 s and the log says it is the default")
sink = _Sink()
with _launcher({"settings": {"chief_stall_minutes": 90}}) as asked:
    check(sr.resolve_stall_with_launcher(600.0, sink) == 600.0 and not asked and not sink.lines,
          "stall: --stall-timeout wins and the launcher is not asked")
    os.environ["CLAUDE_PROGRESS_STALL_TIMEOUT"] = "120"
    check(sr.resolve_stall_with_launcher(None, sink) == 120.0 and not asked,
          "stall: the env override wins and the launcher is not asked")
    os.environ["CLAUDE_PROGRESS_STALL_TIMEOUT"] = "0"
    check(sr.resolve_stall_with_launcher(None, sink) == 0.0, "stall: an env 0 still disables the watchdog")
del os.environ["CLAUDE_PROGRESS_STALL_TIMEOUT"]

_h.report_and_exit("test_chief_timings")
