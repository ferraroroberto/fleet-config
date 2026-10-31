"""chief_ops verify against throwaway git repos, chief-managed marking, the fail-closed worker cap, and say --verify's four distinct verdicts.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/chief_ops/test_verify_and_verdicts.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import contextlib
import io

from _suite import REPO, _h, check, co, make_upstream_and_clone, notify_on_idle, run_git, sd  # noqa: E402


# ---- verify: end-to-end against a throwaway git repo (mirrors test_dirty_tree_check) --

def _git(cwd: Path, *args: str) -> str:
    return run_git(cwd, *args, check=check)


def _run_verify(repo_path: Path, expect: str, branch: str | None = None) -> subprocess.CompletedProcess:
    argv = [sys.executable, str(REPO / "skills" / "_lib" / "chief_ops.py"),
            "verify", str(repo_path), "--expect", expect]
    if branch:
        argv += ["--branch", branch]
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace")


tmp = Path(tempfile.mkdtemp(prefix="chief_ops_verify_"))
try:
    upstream, work = make_upstream_and_clone(tmp, check)

    proc = _run_verify(work, "merged")
    check(proc.returncode == 0, f"verify CLI exits 0 on CLEAN ({proc.stderr.strip()})")
    check("STATUS=CLEAN" in proc.stdout, "verify CLI: merged mode, clean main -> STATUS=CLEAN")

    _git(work, "checkout", "-q", "-b", "feat/1-thing")
    proc = _run_verify(work, "built", "feat/1-thing")
    check(proc.returncode == 1, "verify CLI exits 1 on DIRTY (no commits/no changes)")
    check(
        "STATUS=DIRTY" in proc.stdout and "nothing found" in proc.stdout,
        "verify CLI: built mode, no commits/no changes -> STATUS=DIRTY, exit 1",
    )

    (work / "README.md").write_text("changed on branch\n", encoding="utf-8")
    _git(work, "add", "README.md")
    _git(work, "commit", "-q", "-m", "wip")
    proc = _run_verify(work, "built", "feat/1-thing")
    check(proc.returncode == 0, "verify CLI exits 0 once real work is committed")
    check("STATUS=CLEAN" in proc.stdout, "verify CLI: built mode, committed change -> STATUS=CLEAN")
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


# ---- cmd_dispatch marks the new session chief-managed (fleet-config#443) -----

_state_tmp = Path(tempfile.mkdtemp(prefix="chief_ops_dispatch_"))
_prior_state_dir = os.environ.get("CLAUDE_HOOKS_STATE_DIR")
_prior_launcher_sid = os.environ.get("APP_LAUNCHER_SESSION_ID")
os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(_state_tmp)
_prior_request = co.launcher._request
try:
    _calls = []

    def _fake_request(base_url, path, method="GET", body=None, timeout=10.0):
        _calls.append((path, method, body))
        if path == "/api/board":
            return {"columns": {"claude_turn": [], "your_turn": []}}
        if path == "/api/board/chief/settings":
            return {"settings": {"worker_cap": 3}}
        if path == "/api/board/issues/start":
            return {"session": {"session_id": "new-sid-99"}}
        if path == "/api/jobs":
            return {"jobs": []}
        raise AssertionError(f"unexpected path: {path}")

    co.launcher._request = _fake_request
    args = argparse.Namespace(
        repo="app-launcher", number=528, mode="start", model=None,
        brief_file=None, yolo_confirmed=False, base_url=co.DEFAULT_BASE_URL,
        allow_during_fleet_job=False, effort=None,
    )
    rc = co.cmd_dispatch(args)
    check(rc == 0, "cmd_dispatch (fake transport) exits 0 on a clear dispatch")
    # Read back through the hooks' production reader, as the spawned worker
    # would see itself (its launcher sid arrives via the environment) -- proves
    # dispatch's write and the guard's read agree on file and key (#835, #850).
    os.environ["APP_LAUNCHER_SESSION_ID"] = "new-sid-99"
    check(
        notify_on_idle.chief_managed_state() == (True, notify_on_idle.CHIEF_MANAGED),
        "cmd_dispatch marks the newly-spawned session chief-managed",
    )
    check(
        any(p == "/api/board/issues/start" for p, _, _ in _calls),
        "cmd_dispatch actually posted /api/board/issues/start",
    )
finally:
    co.launcher._request = _prior_request
    if _prior_state_dir is None:
        os.environ.pop("CLAUDE_HOOKS_STATE_DIR", None)
    else:
        os.environ["CLAUDE_HOOKS_STATE_DIR"] = _prior_state_dir
    if _prior_launcher_sid is None:
        os.environ.pop("APP_LAUNCHER_SESSION_ID", None)
    else:
        os.environ["APP_LAUNCHER_SESSION_ID"] = _prior_launcher_sid
    import shutil
    shutil.rmtree(_state_tmp, ignore_errors=True)


# ---- cmd_dispatch: an explicit worker_cap of 0 must fail closed (fleet-config#709) ----
# `int(x or 3)` treats `0` as falsy and silently substitutes the default, so an
# operator who set worker_cap: 0 to pause dispatch got fleet dispatch anyway.
_prior_request = co.launcher._request
try:
    def _zero_cap_request(base_url, path, method="GET", body=None, timeout=10.0):
        if path == "/api/board":
            return {"columns": {"claude_turn": [], "your_turn": []}}
        if path == "/api/board/chief/settings":
            return {"settings": {"worker_cap": 0}}
        raise AssertionError(f"unexpected path: {path}")

    co.launcher._request = _zero_cap_request
    args = argparse.Namespace(
        repo="app-launcher", number=528, mode="start", model=None,
        brief_file=None, yolo_confirmed=False, base_url=co.DEFAULT_BASE_URL,
    )
    rc = co.cmd_dispatch(args)
    check(rc == 1, "cmd_dispatch: worker_cap=0 -> refused, not silently defaulted to 3")
finally:
    co.launcher._request = _prior_request


# ---- verify: an unreadable repo is UNKNOWN, never DIRTY (fleet-config#570) ----
# chief relays this onward to Roberto, so a manufactured verdict here becomes a
# manufactured status report.


_verify_tmp = Path(tempfile.mkdtemp(prefix="chief_ops_verify_"))
try:
    for _label, _target in (("nonexistent path", _verify_tmp / "nope"),
                            ("a directory that is not a repo", _verify_tmp)):
        _buf = io.StringIO()
        with contextlib.redirect_stdout(_buf):
            _rc = co.cmd_verify(argparse.Namespace(
                repo=str(_target), expect="merged", branch=None, default_branch=None))
        _out = _buf.getvalue()
        check("STATUS=UNKNOWN" in _out and "STATUS=DIRTY" not in _out,
              f"cmd_verify: {_label} -> STATUS=UNKNOWN, never a DIRTY verdict")
        check(_rc == 1, f"cmd_verify: {_label} still exits 1 — unverified is not trusted onward")
        check("REASON=" in _out, f"cmd_verify: {_label} explains why in REASON")
finally:
    import shutil
    shutil.rmtree(_verify_tmp, ignore_errors=True)


# ---- say --verify: four distinct verdicts (fleet-config#643) ----------------
# UNKNOWN used to mean "busy target" and "unreadable exchange" at once. These
# pin the split, and — because `say` is how chief steers every worker — that
# the send happens exactly once on every path, whatever the verdict.

# 1. The pure classifier, over the conditions the live host can produce.
check(
    sd.finalize_delivery("pending", "working", post_reason="deferred") == "pending",
    "finalize_delivery: deferred submit in flight -> pending (app-launcher#763)",
)
check(
    sd.finalize_delivery("pending", "idle", post_reason="deferred") == "pending",
    "finalize_delivery: deferred outranks an idle status -> pending, not stranded",
)
for _neg in sorted(sd.INPUT_NEGATIVE_REASONS):
    check(
        sd.finalize_delivery("pending", "working", post_reason=_neg) == "stranded",
        f"finalize_delivery: authoritative negative {_neg!r} -> stranded even on a busy target",
    )
    check(
        sd.finalize_delivery("pending", "working", last_input={"reason": _neg}) == "stranded",
        f"finalize_delivery: watcher verdict {_neg!r} on last_input -> stranded",
    )
# Pinned by literal, not by the loop above: the loop covers whatever the set
# holds, so it cannot notice a verdict that was never added (fleet-config#1380).
check(
    sd.finalize_delivery(
        "pending", "working", post_reason="deferred",
        last_input={"reason": "defer_failed"},
    ) == "stranded",
    "finalize_delivery: watcher's defer_failed (raised before any CR) -> stranded",
)
check(
    sd.deferred_submit_outcome("deferred", {"reason": "defer_failed"}) == "failed",
    "deferred_submit_outcome: defer_failed -> failed, not in_flight",
)
check(
    sd.finalize_delivery(
        "pending", "working", post_reason="deferred",
        last_input={"reason": "defer_timeout"},
    ) == "stranded",
    "finalize_delivery: watcher's terminal failure outranks the deferred acceptance",
)
check(
    sd.finalize_delivery("pending", "needs-you", marker_available=False) == "unknown",
    "finalize_delivery: unreadable exchange -> unknown, not stranded",
)
# The narrowing has to be real, not just documented: readable-but-un-advanced
# is STRANDED, and must never land in UNKNOWN.
for _status in ("idle", "needs-you", None):
    check(
        sd.finalize_delivery("pending", _status, marker_available=True) != "unknown",
        f"finalize_delivery: readable-but-un-advanced exchange (status={_status!r}) is never unknown",
    )
check(
    sd.finalize_delivery("delivered", "working", post_reason="ok") == "delivered",
    "finalize_delivery: an advanced exchange after an immediately-submitted send is delivered",
)
# An advancing exchange only proves the *target* talked, never that this
# steer submitted — so it cannot outrank the endpoint saying it did not
# (fleet-config#856; this assertion used to pin the opposite).
check(
    sd.finalize_delivery("delivered", None, marker_available=False,
                         post_reason="not_ingested") == "stranded",
    "finalize_delivery: an authoritative not_ingested outranks an advanced exchange",
)

# 1a. A deferred submit is decided by the watcher's own report on
# `last_input`, never by exchange movement (fleet-config#856). A send is
# deferred *because* the target is mid-turn, so its exchange advances on its
# own — three lanes on 2026-09-12 verified DELIVERED that way while each paste
# sat unsubmitted in the composer. The four `last_input` shapes the
# session-host produces (app-launcher `src/session_host_input.py:180-189`):
_LI_CONFIRMED = {"delivered": True, "reason": "ok", "ingested": True,
                 "submitted": True, "submit_confirmed": True,
                 "waited_ms": 41000, "deferred": True}
_LI_IN_FLIGHT = {"delivered": True, "reason": "deferred", "ingested": True,
                 "submitted": False, "submit_confirmed": None,
                 "waited_ms": 3000, "deferred": True}
_LI_TIMEOUT = {"delivered": True, "reason": "defer_timeout", "ingested": True,
               "submitted": False, "submit_confirmed": False,
               "waited_ms": 120213, "deferred": True}
for _marker in ("delivered", "pending"):
    check(
        sd.finalize_delivery(_marker, "working", post_reason="deferred",
                             last_input=_LI_TIMEOUT) == "stranded",
        f"finalize_delivery: deferred + defer_timeout on last_input -> stranded "
        f"(exchange {_marker}) — the 2026-09-12 shape",
    )
    check(
        sd.finalize_delivery(_marker, "working", post_reason="deferred",
                             last_input=_LI_IN_FLIGHT) == "pending",
        f"finalize_delivery: deferred + watcher still in flight -> pending (exchange {_marker})",
    )
    for _unreadable in (None, {}, {"reason": None}):
        check(
            sd.finalize_delivery(_marker, "working", post_reason="deferred",
                                 last_input=_unreadable) == "pending",
            f"finalize_delivery: deferred + unreadable last_input {_unreadable!r} -> pending, "
            f"never delivered (exchange {_marker})",
        )
    check(
        sd.finalize_delivery(_marker, "idle", post_reason="deferred",
                             last_input=_LI_CONFIRMED) == "delivered",
        f"finalize_delivery: deferred + watcher-confirmed submit -> delivered (exchange {_marker})",
    )
# DELIVERED rests on the confirmation itself: take it away and the verdict moves.
for _field, _value in (("submit_confirmed", None), ("submit_confirmed", False),
                       ("reason", "deferred"), ("deferred", False)):
    check(
        sd.finalize_delivery("delivered", "working", post_reason="deferred",
                             last_input={**_LI_CONFIRMED, _field: _value}) != "delivered",
        f"finalize_delivery: confirmed submit with {_field}={_value!r} is no longer delivered",
    )
check(sd.deferred_submit_outcome("ok", _LI_TIMEOUT) is None,
      "deferred_submit_outcome: not a deferred send -> None, last_input not consulted")
check(sd.deferred_submit_outcome("deferred", _LI_CONFIRMED) == "confirmed",
      "deferred_submit_outcome: watcher ok + submit_confirmed -> confirmed")
check(sd.deferred_submit_outcome("deferred", _LI_TIMEOUT) == "failed",
      "deferred_submit_outcome: watcher terminal failure -> failed")
check(sd.deferred_submit_outcome("deferred", _LI_IN_FLIGHT) == "in_flight",
      "deferred_submit_outcome: watcher not reported -> in_flight")
check(sd.deferred_submit_outcome("deferred", None) == "in_flight",
      "deferred_submit_outcome: unreadable last_input -> in_flight, never resolved")

# 1b. Recent output as a busy-signal (fleet-config#662). #643's rule 4 read
# "busy" off the board's `status` alone, and that field demonstrably reports
# `awaiting-input` for sessions the exchange shows mid-turn — so a steer that
# was delivered and acted upon came back STRANDED, printing `last_output=0s
# ago` on the same line as the verdict that figure refutes.
_WINDOW = sd.DEFAULT_RECENT_OUTPUT_WINDOW
# The exact observed case: readable un-advanced exchange, status=awaiting-input,
# last output ~0s ago. Pre-fix this returns "stranded".
check(
    sd.finalize_delivery("pending", "awaiting-input", marker_available=True,
                         last_output_age=0.0) == "pending",
    "finalize_delivery: the 2026-08-16 16:04 case — un-advanced exchange, "
    "status=awaiting-input, output 0s ago -> pending, not stranded (#662)",
)
for _status in ("awaiting-input", "needs-you", "idle", None):
    check(
        sd.finalize_delivery("pending", _status, marker_available=True,
                             last_output_age=_WINDOW) == "pending",
        f"finalize_delivery: output inside the window on status={_status!r} -> pending",
    )
    check(
        sd.finalize_delivery("pending", _status, marker_available=True,
                             last_output_age=_WINDOW + 0.1) == "stranded",
        f"finalize_delivery: output older than the window on status={_status!r} -> "
        "stranded; the verdict is narrowed, not removed",
    )
# An unreadable output age is not evidence of silence — `stranded` needs
# positive grounds, so the residual case is `pending`, not a confident negative.
check(
    sd.finalize_delivery("pending", "needs-you", marker_available=True,
                         last_output_age=None) == "pending",
    "finalize_delivery: un-measurable output age -> pending, never a fallthrough stranded",
)
# Precedence is unchanged: an authoritative negative outranks recent output.
for _neg in sorted(sd.INPUT_NEGATIVE_REASONS):
    check(
        sd.finalize_delivery("pending", "awaiting-input", marker_available=True,
                             post_reason=_neg, last_output_age=0.0) == "stranded",
        f"finalize_delivery: authoritative negative {_neg!r} -> stranded even on a "
        "target that is actively emitting output",
    )
    check(
        sd.finalize_delivery("pending", "awaiting-input", marker_available=True,
                             last_input={"reason": _neg}, last_output_age=0.0) == "stranded",
        f"finalize_delivery: watcher verdict {_neg!r} -> stranded even with output 0s ago",
    )
# An unreadable exchange stays UNKNOWN whatever the output age says: recent
# output means "busy", never "delivered".
check(
    sd.finalize_delivery("pending", "needs-you", marker_available=False,
                         last_output_age=0.0) == "unknown",
    "finalize_delivery: recent output does not turn an unreadable exchange into a verdict",
)

# The window is derived from the poll budget, not picked by feel.
check(sd.recent_output_window(2.0) == 4.0,
      "recent_output_window: two poll intervals at the default")
check(sd.recent_output_window(0.1) == sd.DEFAULT_VERIFY_POLL_INTERVAL,
      "recent_output_window: floored at one default interval, so a fast poll "
      "cannot shrink it below the two round trips it has to cover")
check(sd.recent_output_window(10.0) == 20.0,
      "recent_output_window: scales with a slower poll")

# The PENDING reasons name which of the four situations the operator is in.
check(sd.pending_reason_key("deferred", "idle", 0.0) == "pending_deferred",
      "pending_reason_key: the deferred watcher outranks the rest")
check(sd.pending_reason_key(None, "working", 0.0) == "pending_busy",
      "pending_reason_key: board says working")
check(sd.pending_reason_key(None, "awaiting-input", 0.0) == "pending_talking",
      "pending_reason_key: still emitting output despite the status label")
check(sd.pending_reason_key(None, "awaiting-input", None) == "pending_unmeasured",
      "pending_reason_key: output age unreadable")
for _key in ("pending_deferred", "pending_busy", "pending_talking",
             "pending_unmeasured", "unknown", "stranded", "stranded_negative"):
    check(_key in sd.VERDICT_REASONS, f"VERDICT_REASONS carries {_key!r}")

# 1c. last_output_age_seconds — the classifier input behind all of the above.
check(sd.last_output_age_seconds(None) is None,
      "last_output_age_seconds: missing stamp -> None, never a large number")
check(sd.last_output_age_seconds("nonsense") is None,
      "last_output_age_seconds: unparseable stamp -> None")
check(sd.last_output_age_seconds(0) is None,
      "last_output_age_seconds: zero stamp -> None")
check(sd.last_output_age_seconds(1000.0, now=1004.0) == 4.0,
      "last_output_age_seconds: seconds since the stamp")
check(sd.last_output_age_seconds(1000.0, now=990.0) == 0.0,
      "last_output_age_seconds: a clock-skewed future stamp clamps to 0, never negative")

# 2. format_output_age — an unreadable stamp says so, never a number.
_age = sd.last_output_age_seconds
check(sd.format_output_age(_age(None)) == "unknown",
      "format_output_age: missing stamp -> unknown, never fabricated")
check(sd.format_output_age(_age("nonsense")) == "unknown",
      "format_output_age: unparseable stamp -> unknown")
check(sd.format_output_age(_age(0)) == "unknown",
      "format_output_age: zero stamp -> unknown")
check(sd.format_output_age(_age(1000.0, now=1004.0)) == "4s ago",
      "format_output_age: seconds")
check(sd.format_output_age(_age(1000.0, now=1000.0 + 300)) == "5m ago",
      "format_output_age: minutes")
check(sd.format_output_age(_age(1000.0, now=1000.0 + 7200)) == "2h ago",
      "format_output_age: hours")

# 3. cmd_say end-to-end against a stubbed transport. The live session-host is
# still the pre-#760 build (verified 2026-08-16: its /api/claude-code/sessions
# cards carry `last_output_at` but no `last_input`), so it cannot emit a 202
# `deferred` yet — that path is proven here against a synthetic 202 and is
# recorded as unproven end-to-end until the parked session-host restart.
import time  # noqa: E402

_say_tmp = Path(tempfile.mkdtemp(prefix="chief_ops_say_"))
try:
    _brief = _say_tmp / "brief.md"
    # Plain, unsigned steer text: the `CHIEF - ` marker was retired in
    # fleet-config#622, and an acceptance check now asserts it appears nowhere.
    _brief.write_text("gate failure on line 12 is pre-existing, see #622", encoding="utf-8")

    def _run_say(post_result, *, verify=True, marker=None, status="working",
                 card=None, timeout=0.0):
        """Drive cmd_say with every network edge stubbed. Returns
        (exit_code, stdout, post_call_count). `card` may be a list, consumed
        one per read with the last repeating — a watcher resolving mid-poll."""
        calls = {"n": 0, "card": 0}

        def _fake_card(*a, **k):
            if card is None:
                return {"last_output_at": time.time() - 4}
            if isinstance(card, list):
                idx = min(calls["card"], len(card) - 1)
                calls["card"] += 1
                return card[idx]
            return card

        def _fake_post(base_url, sid, text):
            calls["n"] += 1
            if isinstance(post_result, Exception):
                raise post_result
            return post_result

        _orig = (co.launcher.post_session_input, co.launcher.fetch_exchange_marker,
                 co.launcher._request, co.launcher.fetch_session_card)
        co.launcher.post_session_input = _fake_post
        co.launcher.fetch_exchange_marker = lambda *a, **k: (
            marker or {"available": False, "timestamp": None})
        co.launcher._request = lambda base, path, **k: (
            {"columns": {"claude_turn": [{"session_id": "sid123", "status": status}]}}
            if path == "/api/board" else {})
        co.launcher.fetch_session_card = _fake_card
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = co.cmd_say(argparse.Namespace(
                    sid="sid123", file=str(_brief), verify=verify,
                    timeout=timeout, poll_interval=0.0, base_url=co.DEFAULT_BASE_URL))
            return rc, buf.getvalue(), calls["n"]
        finally:
            (co.launcher.post_session_input, co.launcher.fetch_exchange_marker,
             co.launcher._request, co.launcher.fetch_session_card) = _orig

    # 202 deferred -> PENDING, exit 1, exactly one POST.
    _rc, _out, _n = _run_say({"ok": True, "reason": "deferred", "error": None})
    check("PENDING sid=sid123" in _out, "cmd_say: a deferred (202) submit reports PENDING")
    check(_rc == 1, "cmd_say: PENDING still exits non-zero — likely-delivered is not delivered")
    check(_n == 1, "cmd_say: PENDING path sent exactly once — never auto-retries")
    check("status=working" in _out and "last_output=" in _out,
          "cmd_say: PENDING line carries target status and last-output age")

    # HTTP 502 not_ingested -> STRANDED, not a traceback.
    _rc, _out, _n = _run_say({
        "ok": False, "reason": "not_ingested", "http_status": 502,
        "error": "HTTP 502: never echoed the input",
    })
    check("STRANDED sid=sid123" in _out,
          "cmd_say: the endpoint's authoritative 'not delivered' reports STRANDED")
    check(_rc == 1, "cmd_say: STRANDED exits non-zero")
    check(_n == 1, "cmd_say: STRANDED path sent exactly once — never auto-retries")

    # Watcher's terminal failure on last_input -> STRANDED.
    _rc, _out, _n = _run_say(
        {"ok": True, "reason": "deferred", "error": None},
        card={"last_output_at": time.time() - 10,
              "last_input": {"reason": "defer_timeout"}},
    )
    check("STRANDED sid=sid123" in _out and "last_input=defer_timeout" in _out,
          "cmd_say: the watcher's defer_timeout verdict reports STRANDED and names itself")
    check(_n == 1, "cmd_say: watcher-negative path sent exactly once")

    # Busy target, readable-but-un-advanced exchange -> PENDING.
    _rc, _out, _n = _run_say(
        {"ok": True, "reason": "ok", "error": None},
        marker={"available": True, "timestamp": None}, status="working")
    check("PENDING sid=sid123" in _out,
          "cmd_say: busy target with an un-advanced exchange reports PENDING")
    check("UNKNOWN" not in _out,
          "cmd_say: a readable exchange never reports UNKNOWN")

    # Unreadable exchange on an idle target -> UNKNOWN.
    _rc, _out, _n = _run_say(
        {"ok": True, "reason": "ok", "error": None},
        marker={"available": False, "timestamp": None}, status="needs-you")
    check("UNKNOWN sid=sid123" in _out,
          "cmd_say: an unreadable exchange reports UNKNOWN")
    check(_rc == 1 and _n == 1, "cmd_say: UNKNOWN exits 1 and sent exactly once")

    # Readable, un-advanced, *demonstrably quiet* target -> STRANDED (the
    # signal worth keeping). The stale stamp is the point: post-#662 the
    # verdict rests on measured silence, not on the status label alone.
    _rc, _out, _n = _run_say(
        {"ok": True, "reason": "ok", "error": None},
        marker={"available": True, "timestamp": None}, status="needs-you",
        card={"last_output_at": time.time() - 600})
    check("STRANDED sid=sid123" in _out,
          "cmd_say: un-advanced exchange on a quiet idle target stays STRANDED")
    check(_rc == 1 and _n == 1, "cmd_say: STRANDED still exits 1 and sends exactly once")

    # fleet-config#662, end to end: the exact live case — un-advanced readable
    # exchange, board status `awaiting-input`, output 0s ago. The verdict line
    # used to print STRANDED beside `last_output=0s ago`, its own refutation.
    _rc, _out, _n = _run_say(
        {"ok": True, "reason": "ok", "error": None},
        marker={"available": True, "timestamp": None}, status="awaiting-input",
        card={"last_output_at": time.time()})
    check("PENDING sid=sid123" in _out and "STRANDED" not in _out,
          "cmd_say: a target emitting output under a non-working status reports "
          "PENDING, not STRANDED (#662)")
    check("status=awaiting-input" in _out and "last_output=0s ago" in _out,
          "cmd_say: the #662 line still shows the status and age it was judged on")
    check("emitting output" in _out,
          "cmd_say: the PENDING reason names recent output as the grounds")
    check(_rc == 1 and _n == 1,
          "cmd_say: the narrowed verdict still exits non-zero and never resends")

    # An unreadable output age is not silence: no positive grounds -> PENDING.
    _rc, _out, _n = _run_say(
        {"ok": True, "reason": "ok", "error": None},
        marker={"available": True, "timestamp": None}, status="needs-you",
        card={})
    check("PENDING sid=sid123" in _out and "last_output=unknown" in _out,
          "cmd_say: an unreadable output age reports PENDING with the age named unknown")
    check(_rc == 1 and _n == 1, "cmd_say: that path still exits 1 and sends exactly once")

    # Delivered -> exit 0, one POST.
    _future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    _rc, _out, _n = _run_say(
        {"ok": True, "reason": "ok", "error": None},
        marker={"available": True, "timestamp": _future})
    check("DELIVERED sid=sid123" in _out and _rc == 0,
          "cmd_say: an advanced exchange reports DELIVERED and exits 0")
    check(_n == 1, "cmd_say: DELIVERED path sent exactly once")
    # ...and that DELIVERED rests on the advance: remove it and the verdict moves.
    _rc, _out, _n = _run_say(
        {"ok": True, "reason": "ok", "error": None},
        marker={"available": True, "timestamp": None})
    check("DELIVERED" not in _out and _rc == 1,
          "cmd_say: the same send with the exchange advance removed is not DELIVERED")

    # fleet-config#856, end to end: a deferred send on a busy target whose
    # exchange keeps advancing. Pre-fix every one of these printed DELIVERED
    # straight out of the poll loop, before `last_input` was ever read.
    _deferred = {"ok": True, "reason": "deferred", "error": None}
    _rc, _out, _n = _run_say(
        _deferred, marker={"available": True, "timestamp": _future},
        card={"last_output_at": time.time(), "last_input": _LI_TIMEOUT})
    check("STRANDED sid=sid123" in _out and "DELIVERED" not in _out
          and "last_input=defer_timeout" in _out,
          "cmd_say: deferred + advancing exchange + defer_timeout -> STRANDED, "
          "the exact 2026-09-12 stranded-brief shape (#856)")
    check(_rc == 1 and _n == 1, "cmd_say: that STRANDED exits 1 and sent exactly once")

    _rc, _out, _n = _run_say(
        _deferred, marker={"available": True, "timestamp": _future},
        card={"last_output_at": time.time(), "last_input": _LI_IN_FLIGHT})
    check("PENDING sid=sid123" in _out and "DELIVERED" not in _out,
          "cmd_say: deferred + advancing exchange + watcher still in flight -> PENDING (#856)")
    check("no verdict" in _out,
          "cmd_say: the in-flight PENDING says outright that no verdict was reached")
    check(_rc == 1 and _n == 1, "cmd_say: that PENDING exits 1 and sent exactly once")

    _rc, _out, _n = _run_say(
        _deferred, marker={"available": True, "timestamp": _future}, card={})
    check("PENDING sid=sid123" in _out and "DELIVERED" not in _out,
          "cmd_say: deferred + advancing exchange + unreadable card -> PENDING, never DELIVERED")

    # A watcher that confirms the submit mid-poll ends the wait with DELIVERED,
    # exchange movement or not.
    _rc, _out, _n = _run_say(
        _deferred, marker={"available": True, "timestamp": None},
        card=[{"last_output_at": time.time(), "last_input": _LI_IN_FLIGHT},
              {"last_output_at": time.time(), "last_input": _LI_CONFIRMED}],
        timeout=5.0)
    check("DELIVERED sid=sid123" in _out and _rc == 0,
          "cmd_say: deferred + watcher confirms the submit during the poll -> DELIVERED")
    check(_n == 1, "cmd_say: watcher-confirmed DELIVERED sent exactly once")

    # Plain `say` (no --verify): happy path unchanged, failure reported not raised.
    _rc, _out, _n = _run_say({"ok": True, "reason": "ok", "error": None}, verify=False)
    check("SENT sid=sid123" in _out and _rc == 0,
          "cmd_say: plain say still prints SENT and exits 0 — happy path unchanged")
    check(_n == 1, "cmd_say: plain say sent exactly once")
    _rc, _out, _n = _run_say(
        {"ok": False, "reason": "not_ingested", "http_status": 502,
         "error": "HTTP 502: never echoed the input"}, verify=False)
    check("FAILED sid=sid123" in _out and _rc == 1,
          "cmd_say: plain say reports a rejected POST instead of raising")
    check(_n == 1, "cmd_say: plain say failure path sent exactly once — never auto-retries")
finally:
    import shutil
    shutil.rmtree(_say_tmp, ignore_errors=True)

_h.report_and_exit("test_verify_and_verdicts")
