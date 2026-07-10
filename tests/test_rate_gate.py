"""Unit tests for the pure logic in skills/_lib/rate_gate.py.

Exercises `decide` directly with synthetic cache dicts and a fixed `now`, plus
the `check` CLI end-to-end against a temp `--state-dir` file (no real
`~/.claude/hooks/state/rate-limits.json` is touched).

Run: `C:/Users/rober/AppData/Local/Python/bin/python.exe tests/test_rate_gate.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import datetime as dt
import json
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

# ---- decide: stale cache -> UNKNOWN regardless of usage ----
stale_cache = _cache(95.0, "2026-07-04T13:00:00Z", "2026-07-04T11:00:00Z")  # 1h old, default max_age=30min
check(rg.decide(stale_cache, NOW).status == "UNKNOWN", "stale captured_at -> UNKNOWN even if usage is high")

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
    proc2 = subprocess.run(
        [sys.executable, str(REPO / "skills" / "_lib" / "rate_gate.py"),
         "check", "--state-dir", str(empty_dir)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    check("DECISION=UNKNOWN" in proc2.stdout, "check CLI with no cache file -> UNKNOWN")
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_rate_gate")
