"""Unit tests for the pure logic in skills/_lib/chief_ops.py (fleet-config#445).

Exercises `repo_occupancy`, `alive_worker_count`, `refuse_dispatch`,
`assert_loopback`, `format_board_digest`, and `parse_issue_ref`/`_fmt_age`
directly against synthetic board payloads — no network, no `gh`, no live
launcher required.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_chief_ops.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import chief_managed  # noqa: E402
import chief_ops as co  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import make_upstream_and_clone, run_git  # noqa: E402

_h = CheckHarness()
check = _h.check


def _session_card(**over):
    base = {
        "session_id": "abc12345-0000",
        "agent": "claude",
        "label": "",
        "project": "app-launcher",
        "status": "working",
        "age_seconds": 120,
        "alive": True,
        "kind": "pty",
    }
    base.update(over)
    return base


# ---- repo_occupancy ---------------------------------------------------------

occ = co.repo_occupancy({
    "claude_turn": [_session_card()],
    "your_turn": [_session_card(project="whatsapp-radar", status="needs-you")],
})
check(set(occ) == {"app-launcher", "whatsapp-radar"}, "repo_occupancy keys both alive cards")
check(occ["app-launcher"]["session_id"] == "abc12345-0000", "repo_occupancy carries session_id")

occ_dead = co.repo_occupancy({
    "claude_turn": [_session_card(alive=False)],
    "your_turn": [],
})
check(occ_dead == {}, "repo_occupancy drops a dead (alive=False) card")

occ_external = co.repo_occupancy({
    "claude_turn": [_session_card(kind="external")],
    "your_turn": [],
})
check(occ_external == {}, "repo_occupancy drops an external (state-only) card")

occ_case = co.repo_occupancy({
    "claude_turn": [_session_card(project="App-Launcher")],
    "your_turn": [],
})
check("app-launcher" in occ_case, "repo_occupancy lowercases the repo key")


# ---- alive_worker_count ------------------------------------------------------

count = co.alive_worker_count({
    "claude_turn": [_session_card(label="chief"), _session_card(project="photo-ocr")],
    "your_turn": [_session_card(project="voice-transcriber", status="needs-you")],
})
check(count == 2, "alive_worker_count excludes the chief's own card")

count_dead = co.alive_worker_count({
    "claude_turn": [_session_card(alive=False)],
    "your_turn": [],
})
check(count_dead == 0, "alive_worker_count excludes dead cards")


# ---- find_chief_session (fleet-config#443) -----------------------------------

chief_sid = co.find_chief_session({
    "claude_turn": [
        _session_card(project="fleet-config", label="chief", session_id="chief-sid-1"),
        _session_card(project="fleet-config", label="", session_id="dev-sid-2"),
    ],
    "your_turn": [],
})
check(
    chief_sid == "chief-sid-1",
    "find_chief_session picks the label=='chief' card even when a plain "
    "dev session shares the same repo",
)

check(
    co.find_chief_session({"claude_turn": [_session_card(label="chief", alive=False)], "your_turn": []}) is None,
    "find_chief_session ignores a dead chief card",
)
check(
    co.find_chief_session({"claude_turn": [_session_card(label="")], "your_turn": []}) is None,
    "find_chief_session returns None when no chief card is present",
)


# ---- say --verify delivery detection (fleet-config#453) ----------------------

_send_time = datetime(2026, 7, 27, 12, 0, 0, tzinfo=timezone.utc)
_before = (_send_time - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
_after = (_send_time + timedelta(seconds=5)).isoformat().replace("+00:00", "Z")

check(
    co.parse_exchange_timestamp(_after) == _send_time + timedelta(seconds=5),
    "parse_exchange_timestamp parses a Z-suffixed ISO8601 timestamp",
)
check(co.parse_exchange_timestamp(None) is None, "parse_exchange_timestamp tolerates None")
check(co.parse_exchange_timestamp("not-a-timestamp") is None, "parse_exchange_timestamp tolerates garbage")

check(
    co.classify_exchange_marker(True, _after, _send_time) == "delivered",
    "classify_exchange_marker: available + timestamp after send_time -> delivered",
)
check(
    co.classify_exchange_marker(True, _before, _send_time) == "pending",
    "classify_exchange_marker: available + timestamp before send_time -> pending",
)
check(
    co.classify_exchange_marker(False, None, _send_time) == "pending",
    "classify_exchange_marker: unavailable -> pending, never delivered",
)
check(
    co.classify_exchange_marker(True, None, _send_time) == "pending",
    "classify_exchange_marker: available with no timestamp (launcher fallback) -> pending",
)

check(
    co.find_session_status(
        {"claude_turn": [_session_card(session_id="target-1", status="needs-you")], "your_turn": []},
        "target-1",
    ) == "needs-you",
    "find_session_status finds a matching card by session_id",
)
check(
    co.find_session_status({"claude_turn": [], "your_turn": []}, "no-such-sid") is None,
    "find_session_status returns None when no card matches",
)

check(co.finalize_delivery("delivered", "working") == "delivered", "finalize_delivery: delivered stays delivered")
check(
    co.finalize_delivery("pending", "working") == "unknown",
    "finalize_delivery: non-movement on a busy target -> unknown, not stranded",
)
check(
    co.finalize_delivery("pending", "needs-you") == "stranded",
    "finalize_delivery: non-movement on an idle/needs-you target -> stranded",
)
check(
    co.finalize_delivery("pending", None) == "stranded",
    "finalize_delivery: non-movement with no matching card -> stranded",
)


# ---- resolve_session_id / cmd_exchange (fleet-config#613) --------------------

import contextlib  # noqa: E402
import io  # noqa: E402

_exchange_columns = {
    "claude_turn": [
        _session_card(project="life-os", session_id="d235f290aaaaaaaaaaaaaaaaaaaaaaaa"),
        _session_card(project="minecraft-bedrock-bot", session_id="d235f299bbbbbbbbbbbbbbbbbbbbbbbb"),
    ],
    "your_turn": [_session_card(project="local-llm-hub", session_id="ad3e8bbbcccccccccccccccccccccccc")],
    "other": [],
}

check(
    co.resolve_session_id("ad3e8bbbcccccccccccccccccccccccc", _exchange_columns)
    == ("ad3e8bbbcccccccccccccccccccccccc", None),
    "resolve_session_id: exact full-id match passes through unchanged",
)
check(
    co.resolve_session_id("ad3e8bbb", _exchange_columns) == ("ad3e8bbbcccccccccccccccccccccccc", None),
    "resolve_session_id: unambiguous 8-char prefix (the form board/sessions print) resolves to the full live id",
)
_amb_resolved, _amb_reason = co.resolve_session_id("d235f29", _exchange_columns)
check(
    _amb_resolved is None and _amb_reason is not None,
    "resolve_session_id: a prefix matching 2 live sessions refuses to pick either",
)
check(
    co.resolve_session_id("deadbeefdeadbeefdeadbeefdeadbeef", _exchange_columns)
    == ("deadbeefdeadbeefdeadbeefdeadbeef", None),
    "resolve_session_id: no live match passes the id through unchanged (real endpoint decides)",
)


def _run_exchange(sid, board_columns, exchange_result=None):
    """Drive `cmd_exchange` against a stubbed transport, capturing stdout."""
    calls = {"board": 0, "exchange": 0}

    def _fake_request(base_url, path, method="GET", body=None, timeout=10.0):
        if path == "/api/board":
            calls["board"] += 1
            return {"columns": board_columns}
        if path.endswith("/exchange"):
            calls["exchange"] += 1
            return exchange_result
        raise AssertionError(f"unexpected path: {path}")

    prior = co._request
    co._request = _fake_request
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = co.cmd_exchange(argparse.Namespace(sid=sid, tail=2000, base_url=co.DEFAULT_BASE_URL))
    finally:
        co._request = prior
    return rc, buf.getvalue(), calls


_rc, _out, _calls = _run_exchange(
    "ad3e8bbb", _exchange_columns,
    exchange_result={"available": True, "source": "pty", "assistant": {"timestamp": "t", "text": "hi"}},
)
check(_rc == 0, "cmd_exchange: unambiguous 8-char prefix succeeds against the live session")
check(_calls["exchange"] == 1, "cmd_exchange resolved before calling the exchange endpoint")

_rc, _out, _calls = _run_exchange("d235f29", _exchange_columns)
check(_rc == 1, "cmd_exchange: ambiguous prefix fails")
check(_out.startswith("UNRESOLVABLE reason="), "cmd_exchange: ambiguous prefix reports UNRESOLVABLE, not UNAVAILABLE")
check(_calls["exchange"] == 0, "cmd_exchange never calls the exchange endpoint on an ambiguous prefix (no silent pick)")

_rc, _out, _calls = _run_exchange(
    "deadbeefdeadbeefdeadbeefdeadbeef", _exchange_columns,
    exchange_result={"available": False, "reason": "session_not_found"},
)
check(_rc == 1, "cmd_exchange: an id matching no live session still fails")
check(
    "UNAVAILABLE reason=session_not_found" in _out,
    "cmd_exchange: no-match path is unchanged -- still the real UNAVAILABLE reason=session_not_found",
)
check(_calls["exchange"] == 1, "cmd_exchange: no-match path still queries the real endpoint")


def _run_say_verify(exchange_responses, board_status="needs-you", timeout=0.15, poll_interval=0.05):
    """Drive `cmd_say --verify` against a stubbed transport. `exchange_responses`
    is consumed in order (one per poll iteration); the last value repeats once
    exhausted so a short timeout never IndexErrors."""
    calls = {"input": 0, "exchange": 0}

    def _fake_request(base_url, path, method="GET", body=None, timeout=10.0):
        if path.endswith("/input"):
            calls["input"] += 1
            return {"ok": True}
        if path.endswith("/exchange"):
            idx = min(calls["exchange"], len(exchange_responses) - 1)
            calls["exchange"] += 1
            return exchange_responses[idx]
        if path == "/api/board":
            return {"columns": {
                "claude_turn": [_session_card(session_id="target-1", status=board_status)],
                "your_turn": [],
            }}
        raise AssertionError(f"unexpected path: {path}")

    prior = co._request
    co._request = _fake_request
    try:
        args = argparse.Namespace(
            sid="target-1", file=None, verify=True, timeout=timeout,
            poll_interval=poll_interval, base_url=co.DEFAULT_BASE_URL,
        )
        import io
        prior_stdin = sys.stdin
        sys.stdin = io.StringIO("brief text")
        try:
            rc = co.cmd_say(args)
        finally:
            sys.stdin = prior_stdin
    finally:
        co._request = prior
    return rc, calls


# `cmd_say` stamps `send_time` from the real wall clock, so these must be
# real-clock-relative too -- comfortably past/before "now" regardless of how
# long the test process takes to reach the comparison.
_now = datetime.now(timezone.utc)
_real_after = (_now + timedelta(days=1)).isoformat().replace("+00:00", "Z")
_real_before = (_now - timedelta(days=1)).isoformat().replace("+00:00", "Z")

rc, calls = _run_say_verify([{"available": True, "assistant": {"timestamp": _real_after}}])
check(rc == 0, "cmd_say --verify exits 0 when the exchange advances past send time")
check(calls["input"] == 1, "cmd_say --verify posts the input exactly once (no auto-retry)")

rc, _ = _run_say_verify([{"available": False}], board_status="needs-you")
check(rc == 1, "cmd_say --verify: unreadable exchange on an idle target -> failure (stranded)")

rc, _ = _run_say_verify([{"available": False}], board_status="working")
check(rc == 1, "cmd_say --verify: unreadable exchange on a working target -> failure (unknown, not stranded)")

rc, calls = _run_say_verify(
    [{"available": True, "assistant": {"timestamp": _real_before}}], board_status="needs-you",
)
check(rc == 1, "cmd_say --verify: exchange present but never advances -> failure")
check(calls["input"] == 1, "cmd_say --verify still posts exactly once even on a stranded result")


# ---- refuse_dispatch: the three acceptance-criteria refusals -----------------

occupied = {"app-launcher": {"session_id": "s1", "status": "working", "agent": "claude"}}

check(
    co.refuse_dispatch("app-launcher", "start", occupied, 1, 3, False) is not None,
    "refuse_dispatch refuses an occupied repo",
)
check(
    co.refuse_dispatch("App-Launcher", "start", occupied, 1, 3, False) is not None,
    "refuse_dispatch's occupied check is case-insensitive",
)
check(
    co.refuse_dispatch("whatsapp-radar", "start", occupied, 3, 3, False) is not None,
    "refuse_dispatch refuses at/over the worker cap",
)
check(
    co.refuse_dispatch("whatsapp-radar", "start", occupied, 4, 3, False) is not None,
    "refuse_dispatch refuses over the worker cap",
)
check(
    co.refuse_dispatch("whatsapp-radar", "yolo", occupied, 1, 3, False) is not None,
    "refuse_dispatch refuses yolo without --yolo-confirmed",
)
check(
    co.refuse_dispatch("whatsapp-radar", "yolo", occupied, 1, 3, True) is None,
    "refuse_dispatch allows yolo with --yolo-confirmed",
)
check(
    co.refuse_dispatch("whatsapp-radar", "start", occupied, 1, 3, False) is None,
    "refuse_dispatch allows a clear dispatch",
)


# ---- assert_loopback ---------------------------------------------------------

for good in ("https://127.0.0.1:8445/api/board", "http://localhost:8000/x", "http://[::1]:8445/y"):
    try:
        co.assert_loopback(good)
        ok = True
    except ValueError:
        ok = False
    check(ok, f"assert_loopback accepts {good!r}")

for bad in ("https://93.184.216.34:8445/api/board", "https://evil.example.com/api/board"):
    try:
        co.assert_loopback(bad)
        ok = False
    except ValueError:
        ok = True
    check(ok, f"assert_loopback rejects {bad!r}")


# ---- format_board_digest ------------------------------------------------------

board = {
    "columns": {
        "backlog": [{"number": 1}, {"number": 2}],
        "claude_turn": [_session_card()],
        "your_turn": [_session_card(project="whatsapp-radar", status="needs-you")],
        "other": [
            {"kind": "job", "job_name": "audit-fleet", "state": "failed"},
            {"repo": "app-launcher", "number": 528, "title": "tint follow-up"},
        ],
        "done": [{"number": 3}],
    },
    "rate_limits": {"five_hour": {"used_percentage": 42.0, "resets_at": "2026-07-27T18:00:00Z"}},
}
digest = co.format_board_digest(board)
lines = digest.splitlines()
check(len(lines) <= 12, f"format_board_digest stays within ~12 lines (got {len(lines)})")
check(lines[0].startswith("backlog=2 "), "format_board_digest leads with the counts line")
check(any("app-launcher" in l for l in lines), "format_board_digest lists the live session")
check(any("audit-fleet" in l for l in lines), "format_board_digest lists the job card")
check(any("rate_limit_5h=42.0%" in l for l in lines), "format_board_digest carries the rate-limit line")


# ---- parse_issue_ref / _fmt_age -----------------------------------------------

check(co.parse_issue_ref("app-launcher#528") == ("app-launcher", 528), "parse_issue_ref splits repo#number")
try:
    co.parse_issue_ref("no-hash-here")
    bad_ref_raised = False
except ValueError:
    bad_ref_raised = True
check(bad_ref_raised, "parse_issue_ref raises on a malformed ref")

check(co._fmt_age(90) == "1m", "_fmt_age renders sub-hour as minutes")
check(co._fmt_age(3700) == "1h", "_fmt_age renders sub-day as hours")
check(co._fmt_age(90000) == "1d", "_fmt_age renders multi-day as days")
check(co._fmt_age(None) == "?", "_fmt_age tolerates unparseable input")


# ---- resolve_repo_path --------------------------------------------------------

fake_repos = {"app-launcher": "/fleet/app-launcher", "photo-ocr": "/fleet/photo-ocr"}

check(
    co.resolve_repo_path("app-launcher", fake_repos) == Path("/fleet/app-launcher").resolve(),
    "resolve_repo_path resolves an exact registry name",
)
check(
    co.resolve_repo_path("App-Launcher", fake_repos) == Path("/fleet/app-launcher").resolve(),
    "resolve_repo_path resolves a registry name case-insensitively",
)
try:
    co.resolve_repo_path("no-such-repo", fake_repos)
    unknown_raised = False
except ValueError:
    unknown_raised = True
check(unknown_raised, "resolve_repo_path raises on an unknown repo name")


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
os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(_state_tmp)
_prior_request = co._request
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
        raise AssertionError(f"unexpected path: {path}")

    co._request = _fake_request
    args = argparse.Namespace(
        repo="app-launcher", number=528, mode="start", model=None,
        yolo_confirmed=False, base_url=co.DEFAULT_BASE_URL,
    )
    rc = co.cmd_dispatch(args)
    check(rc == 0, "cmd_dispatch (fake transport) exits 0 on a clear dispatch")
    check(
        chief_managed.is_managed("new-sid-99"),
        "cmd_dispatch marks the newly-spawned session chief-managed",
    )
    check(
        any(p == "/api/board/issues/start" for p, _, _ in _calls),
        "cmd_dispatch actually posted /api/board/issues/start",
    )
finally:
    co._request = _prior_request
    if _prior_state_dir is None:
        os.environ.pop("CLAUDE_HOOKS_STATE_DIR", None)
    else:
        os.environ["CLAUDE_HOOKS_STATE_DIR"] = _prior_state_dir
    import shutil
    shutil.rmtree(_state_tmp, ignore_errors=True)



# ---- verify: an unreadable repo is UNKNOWN, never DIRTY (fleet-config#570) ----
# chief relays this onward to Roberto, so a manufactured verdict here becomes a
# manufactured status report.

import contextlib  # noqa: E402
import io  # noqa: E402

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


_h.report_and_exit("test_chief_ops")
