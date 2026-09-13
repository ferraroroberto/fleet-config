"""Unit tests for the pure logic in skills/_lib/rate_gate.py.

Exercises `decide` directly with synthetic cache dicts and a fixed `now`, plus
the `check` CLI end-to-end against a temp `--state-dir` file (no real
`~/.claude/hooks/state/rate-limits.json` is touched).

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_rate_gate.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import rate_gate as rg  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


NOW = dt.datetime(2026, 7, 4, 12, 0, 0, tzinfo=dt.timezone.utc)


def _cache(used_pct, resets_at, captured_at):
    return {
        "five_hour": {"used_percentage": used_pct, "resets_at": resets_at},
        "captured_at": captured_at,
    }


# ---- decide: no signal -> UNKNOWN ----

check(rg.decide({}, NOW).status == "UNKNOWN", "empty cache -> UNKNOWN")
check(rg.decide({"captured_at": None}, NOW).status == "UNKNOWN", "no captured_at -> UNKNOWN")
check(
    rg.decide({"five_hour": None, "captured_at": "2026-07-04T11:59:00Z"}, NOW).status == "UNKNOWN",
    "five_hour null -> UNKNOWN",
)
check(
    rg.decide(_cache(None, None, "2026-07-04T11:59:00Z"), NOW).status == "UNKNOWN",
    "used_percentage null -> UNKNOWN",
)

check(rg.decide({}, NOW).reason == "cache_missing", "empty cache names cache_missing")
check(
    rg.decide(_cache(None, None, "2026-07-04T11:59:00Z"), NOW).reason == "usage_missing",
    "used_percentage null names usage_missing",
)

# ---- decide: stale cache -> UNKNOWN regardless of usage ----
stale_cache = _cache(95.0, "2026-07-04T13:00:00Z", "2026-07-04T11:00:00Z")  # 1h old, default max_age=30min
check(rg.decide(stale_cache, NOW).status == "UNKNOWN", "stale captured_at -> UNKNOWN even if usage is high")
check(rg.decide(stale_cache, NOW).reason == "cache_stale", "stale captured_at names cache_stale")

# ---- decide: unattended + no signal -> bounded PAUSE, never proceed (fleet-config#825) ----
# The 2026-09-10 weekly chain proceeded on UNKNOWN into the 5-hour limit.
for label, no_signal, reason in (
    ("empty cache", {}, "cache_missing"),
    ("stale cache", stale_cache, "cache_stale"),
    ("null usage", _cache(None, None, "2026-07-04T11:59:00Z"), "usage_missing"),
):
    d = rg.decide(no_signal, NOW, unattended=True)
    check(d.status == "PAUSE", f"unattended + {label} -> PAUSE, not UNKNOWN")
    check(d.reason == reason, f"unattended + {label} keeps reason {reason}")
    check(d.used_pct is None and d.resets_at is None, f"unattended + {label} invents no usage or reset")
    check(d.wait_seconds == float(rg.UNKNOWN_WAIT_SECONDS), f"unattended + {label} waits UNKNOWN_WAIT_SECONDS")
import scheduled_runner  # noqa: E402
check(
    rg.UNKNOWN_WAIT_SECONDS < scheduled_runner.DEFAULT_STALL_TIMEOUT_SECONDS,
    "a silent no-signal pause wait stays under scheduled_runner's stall watchdog",
)

# ---- unattended waits stay under the launcher's stall watchdog (fleet-config#891) ----
# Probe: a silent Monitor wait emits no stream record, so --stall-timeout 120
# killed a 5-min wait at 02:10. An over-threshold wait of hours must re-check in
# cycles that each fit inside the watchdog.
check(rg.watchdog_seconds({rg.STALL_TIMEOUT_ENV: "2700.0"}) == 2700.0, "exported watchdog is read")
check(rg.watchdog_seconds({rg.STALL_TIMEOUT_ENV: "0.0"}) == 0.0, "watchdog 0 reads as off, not unknown")
for raw in (None, "", "soon", "nan", "-5"):
    env = {} if raw is None else {rg.STALL_TIMEOUT_ENV: raw}
    check(rg.watchdog_seconds(env) is None, f"watchdog {raw!r} -> unknown")
check(rg.max_silent_wait(2700.0) < 2700.0, "known watchdog caps each wait below it")
check(rg.max_silent_wait(0.0) is None, "watchdog off -> no cap")
check(rg.max_silent_wait(None) == float(rg.UNKNOWN_WAIT_SECONDS),
      "unknown watchdog is capped conservatively, never treated as off")
check(rg.max_silent_wait(None) < scheduled_runner.DEFAULT_STALL_TIMEOUT_SECONDS,
      "the unknown-watchdog cap still fits the runner's default watchdog")

over = _cache(90.0, "2026-07-04T16:30:00Z", "2026-07-04T11:55:00Z")  # reset 4.5h out
default_cap = rg.max_silent_wait(scheduled_runner.DEFAULT_STALL_TIMEOUT_SECONDS)
capped = rg.decide(over, NOW, unattended=True, max_wait_seconds=default_cap)
check(capped.status == "PAUSE" and capped.reason == "over_threshold", "capping keeps the over-threshold PAUSE")
check(capped.wait_seconds == default_cap, "a 4.5h over-threshold wait is capped under the default watchdog")
check(capped.resets_at == "2026-07-04T16:30:00Z", "capping does not rewrite the reported reset")
check(rg.decide(over, NOW).wait_seconds == 4.5 * 3600 + rg.WAIT_BUFFER_SECONDS,
      "no cap given -> interactive wait unchanged")
short = rg.decide(_cache(90.0, "2026-07-04T11:00:00Z", "2026-07-04T11:55:00Z"),
                  NOW, unattended=True, max_wait_seconds=default_cap)
check(short.wait_seconds == rg.WAIT_BUFFER_SECONDS, "a wait already under the cap is left alone")
check(rg.decide({}, NOW, unattended=True, max_wait_seconds=60.0).wait_seconds == 60.0,
      "the no-signal wait is capped too under a short watchdog")
check(rg.decide(_cache(10.0, None, "2026-07-04T11:55:00Z"), NOW, max_wait_seconds=60.0).wait_seconds is None,
      "OK never gains a wait from the cap")

# A fresh signal decides exactly as it does interactively.
check(rg.decide(_cache(42.0, None, "2026-07-04T11:55:00Z"), NOW, unattended=True).status == "OK",
      "unattended + fresh low usage -> OK")
check(rg.decide(_cache(80.0, None, "2026-07-04T11:55:00Z"), NOW, unattended=True).reason == "over_threshold",
      "unattended + fresh high usage -> over_threshold PAUSE")

check(rg.is_unattended({rg.UNATTENDED_ENV: "1"}), "FLEET_SCHEDULED_RUN=1 -> unattended")
check(not rg.is_unattended({}), "no marker -> interactive")
check(not rg.is_unattended({rg.UNATTENDED_ENV: "0"}), "FLEET_SCHEDULED_RUN=0 -> interactive")

# ---- decide: fresh + below threshold -> OK ----
fresh_low = _cache(42.0, "2026-07-04T14:00:00Z", "2026-07-04T11:55:00Z")
d = rg.decide(fresh_low, NOW)
check(d.status == "OK", "fresh + below threshold -> OK")
check(d.used_pct == 42.0, "OK carries used_pct")

# ---- decide: fresh + at/above threshold -> PAUSE, with resets_at-derived wait ----
fresh_high = _cache(70.0, "2026-07-04T14:00:00Z", "2026-07-04T11:55:00Z")
d = rg.decide(fresh_high, NOW)
check(d.status == "PAUSE", "used_pct == threshold -> PAUSE (>=)")
check(d.resets_at == "2026-07-04T14:00:00Z", "PAUSE carries resets_at")
check(d.wait_seconds == 2 * 3600 + rg.WAIT_BUFFER_SECONDS, "wait_seconds = resets_at - now + buffer")

# ---- decide: PAUSE with no resets_at -> bounded fallback wait ----
fresh_high_no_reset = _cache(88.0, None, "2026-07-04T11:55:00Z")
d = rg.decide(fresh_high_no_reset, NOW)
check(d.status == "PAUSE", "high usage, no resets_at -> still PAUSE")
check(d.wait_seconds == float(rg.DEFAULT_WAIT_SECONDS), "missing resets_at -> fallback wait")

# ---- decide: resets_at already in the past -> wait clamps to the buffer, not negative ----
fresh_high_past_reset = _cache(90.0, "2026-07-04T11:00:00Z", "2026-07-04T11:55:00Z")
d = rg.decide(fresh_high_past_reset, NOW)
check(d.wait_seconds == rg.WAIT_BUFFER_SECONDS, "past resets_at -> wait clamps to buffer, never negative")

# ---- custom threshold / max_age ----
check(
    rg.decide(_cache(60.0, None, "2026-07-04T11:55:00Z"), NOW, threshold_pct=50.0).status == "PAUSE",
    "custom threshold lowers the PAUSE bar",
)
check(
    rg.decide(_cache(10.0, None, "2026-07-04T10:00:00Z"), NOW, max_age_seconds=3600 * 3).status == "OK",
    "custom max_age tolerates an older cache",
)

# ---- load_cache: missing/corrupt file -> empty dict, no crash ----
tmp = Path(tempfile.mkdtemp(prefix="rate_gate_"))
try:
    missing = tmp / "rate-limits.json"
    check(rg.load_cache(missing) == {}, "load_cache missing file -> {}")
    missing.write_text("not json{", encoding="utf-8")
    check(rg.load_cache(missing) == {}, "load_cache corrupt file -> {} (no crash)")

    # ---- check CLI end-to-end against a temp --state-dir ----
    # captured_at must be near the CLI's real wall-clock `now` (the subprocess
    # uses datetime.now(), not the fixed NOW used for the decide() checks above).
    recent = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)).isoformat(timespec="seconds").replace("+00:00", "Z")
    good = tmp / "rate-limits.json"
    good.write_text(json.dumps(_cache(15.0, None, recent)), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(REPO / "skills" / "_lib" / "rate_gate.py"),
         "check", "--state-dir", str(tmp)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    check(proc.returncode == 0, f"check CLI exits 0 ({proc.stderr.strip()})")
    check("DECISION=" in proc.stdout, "check CLI prints DECISION=")
    check("USED_PCT=15.0" in proc.stdout, "check CLI prints USED_PCT")

    empty_dir = tmp / "empty"
    empty_dir.mkdir()
    interactive_env = {k: v for k, v in os.environ.items() if k not in (rg.UNATTENDED_ENV, rg.STALL_TIMEOUT_ENV)}

    def _run_check(*extra, env=interactive_env):
        return subprocess.run(
            [sys.executable, str(REPO / "skills" / "_lib" / "rate_gate.py"),
             "check", "--state-dir", str(empty_dir), *extra],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        ).stdout

    proc2 = _run_check()
    check("DECISION=UNKNOWN" in proc2, "check CLI with no cache file -> UNKNOWN")
    check("MODE=interactive" in proc2 and "REASON=cache_missing" in proc2, "check CLI names mode and reason")

    scheduled = _run_check(env={**interactive_env, rg.UNATTENDED_ENV: "1"})
    check("DECISION=PAUSE" in scheduled, f"check CLI under FLEET_SCHEDULED_RUN=1 with no cache -> PAUSE ({scheduled!r})")
    check("MODE=unattended" in scheduled, "check CLI under FLEET_SCHEDULED_RUN=1 prints MODE=unattended")
    check(f"WAIT_SECONDS={float(rg.UNKNOWN_WAIT_SECONDS)}" in scheduled, "unattended no-signal PAUSE prints its wait")
    check("DECISION=PAUSE" in _run_check("--unattended"), "--unattended flag forces the unattended decision")
    check("WATCHDOG_SECONDS=unknown" in scheduled, "unattended with no exported watchdog names it unknown")
    check("WATCHDOG_SECONDS=null" in proc2, "interactive run has no watchdog to report")

    over_dir = tmp / "over"
    over_dir.mkdir()
    reset_far = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=4)).isoformat(timespec="seconds").replace("+00:00", "Z")
    (over_dir / "rate-limits.json").write_text(json.dumps(_cache(95.0, reset_far, recent)), encoding="utf-8")
    watched = subprocess.run(
        [sys.executable, str(REPO / "skills" / "_lib" / "rate_gate.py"), "check", "--state-dir", str(over_dir)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**interactive_env, rg.UNATTENDED_ENV: "1", rg.STALL_TIMEOUT_ENV: "120.0"},
    ).stdout
    check("REASON=over_threshold" in watched and "WAIT_SECONDS=60.0" in watched,
          f"check CLI caps a 4h over-threshold wait under a 120s watchdog ({watched!r})")
    check("WATCHDOG_SECONDS=120.0" in watched, "check CLI names the watchdog it capped against")
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_rate_gate")
