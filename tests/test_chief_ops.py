"""Unit tests for the pure logic in skills/_lib/chief_ops.py (fleet-config#445)
and skills/_lib/steer_delivery.py, the `say --verify` classifier it calls.

Exercises `repo_occupancy`, `alive_worker_count`, `refuse_dispatch`,
`assert_loopback`, `format_board_digest`, and `parse_issue_ref`/`_fmt_age`
directly against synthetic board payloads — no network, no `gh`, no live
launcher required. The delivery lattice (`sd.` below) is covered here rather
than in a file of its own: it is the same subsystem from the caller's side, and
splitting the module was never meant to split its evidence (fleet-config#680).

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
import steer_delivery as sd  # noqa: E402

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


# ---- the delivery classifier is its own module now (fleet-config#680) --------
#
# `say --verify`'s four-verdict subsystem moved to `steer_delivery.py`, so the
# checks below drive it as `sd.` -- `chief_ops` is the CLI, this is the
# classifier. Two structural facts worth asserting, because both would fail
# silently: the names `chief_ops` still calls are the *same objects* (an import,
# never a second copy), and the classifier stays pure -- importing it must not
# drag `chief_ops`'s network/`gh` half back the other way.
for _name in ("finalize_delivery", "classify_exchange_marker", "pending_reason_key",
              "last_output_age_seconds", "recent_output_window", "format_output_age",
              "format_verdict_line", "VERDICT_REASONS", "INPUT_NEGATIVE_REASONS",
              "DEFAULT_VERIFY_POLL_INTERVAL"):
    check(getattr(co, _name) is getattr(sd, _name),
          f"steer_delivery: chief_ops.{_name} is steer_delivery's own object, not a copy")
check("chief_ops" not in sd.__dict__,
      "steer_delivery: does not import chief_ops back -- the dependency runs one way")


# ---- say --verify delivery detection (fleet-config#453) ----------------------

_send_time = datetime(2026, 7, 27, 12, 0, 0, tzinfo=timezone.utc)
_before = (_send_time - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
_after = (_send_time + timedelta(seconds=5)).isoformat().replace("+00:00", "Z")

check(
    sd.parse_exchange_timestamp(_after) == _send_time + timedelta(seconds=5),
    "parse_exchange_timestamp parses a Z-suffixed ISO8601 timestamp",
)
check(sd.parse_exchange_timestamp(None) is None, "parse_exchange_timestamp tolerates None")
check(sd.parse_exchange_timestamp("not-a-timestamp") is None, "parse_exchange_timestamp tolerates garbage")

check(
    sd.classify_exchange_marker(True, _after, _send_time) == "delivered",
    "classify_exchange_marker: available + timestamp after send_time -> delivered",
)
check(
    sd.classify_exchange_marker(True, _before, _send_time) == "pending",
    "classify_exchange_marker: available + timestamp before send_time -> pending",
)
check(
    sd.classify_exchange_marker(False, None, _send_time) == "pending",
    "classify_exchange_marker: unavailable -> pending, never delivered",
)
check(
    sd.classify_exchange_marker(True, None, _send_time) == "pending",
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

check(sd.finalize_delivery("delivered", "working") == "delivered", "finalize_delivery: delivered stays delivered")
check(
    sd.finalize_delivery("pending", "working") == "pending",
    "finalize_delivery: non-movement on a busy target -> pending, not unknown (#643)",
)
check(
    sd.finalize_delivery("pending", "needs-you", last_output_age=600.0) == "stranded",
    "finalize_delivery: non-movement on an idle/needs-you target -> stranded",
)
check(
    sd.finalize_delivery("pending", None, last_output_age=600.0) == "stranded",
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
        if path == "/api/claude-code/sessions":
            # Read on every non-delivered verdict for `last_output_at` /
            # `last_input` (fleet-config#643). Shaped like the *live*
            # pre-app-launcher#760 host: `last_output_at` present, no
            # `last_input` key at all. The stamp is comfortably stale so these
            # cases turn on the board status alone — recent output would make
            # every one of them PENDING (fleet-config#662).
            return {"sessions": [{
                "session_id": "target-1",
                "last_output_at": __import__("time").time() - 600,
            }]}
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
check(rc == 1, "cmd_say --verify: unreadable exchange on an idle target -> failure (unknown since #643 — cannot read is not proof of loss)")

rc, _ = _run_say_verify([{"available": False}], board_status="working")
check(rc == 1, "cmd_say --verify: unreadable exchange on a working target -> failure (pending since #643, still non-zero)")

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


# ---- cmd_dispatch: an explicit worker_cap of 0 must fail closed (fleet-config#709) ----
# `int(x or 3)` treats `0` as falsy and silently substitutes the default, so an
# operator who set worker_cap: 0 to pause dispatch got fleet dispatch anyway.
_prior_request = co._request
try:
    def _zero_cap_request(base_url, path, method="GET", body=None, timeout=10.0):
        if path == "/api/board":
            return {"columns": {"claude_turn": [], "your_turn": []}}
        if path == "/api/board/chief/settings":
            return {"settings": {"worker_cap": 0}}
        raise AssertionError(f"unexpected path: {path}")

    co._request = _zero_cap_request
    args = argparse.Namespace(
        repo="app-launcher", number=528, mode="start", model=None,
        yolo_confirmed=False, base_url=co.DEFAULT_BASE_URL,
    )
    rc = co.cmd_dispatch(args)
    check(rc == 1, "cmd_dispatch: worker_cap=0 -> refused, not silently defaulted to 3")
finally:
    co._request = _prior_request


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
    sd.finalize_delivery("delivered", None, marker_available=False,
                         post_reason="not_ingested") == "delivered",
    "finalize_delivery: a genuinely advanced exchange still wins over everything",
)

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
                 card=None):
        """Drive cmd_say with every network edge stubbed. Returns
        (exit_code, stdout, post_call_count)."""
        calls = {"n": 0}

        def _fake_post(base_url, sid, text):
            calls["n"] += 1
            if isinstance(post_result, Exception):
                raise post_result
            return post_result

        _orig = (co.post_session_input, co.fetch_exchange_marker,
                 co._request, co.fetch_session_card)
        co.post_session_input = _fake_post
        co.fetch_exchange_marker = lambda *a, **k: (
            marker or {"available": False, "timestamp": None})
        co._request = lambda base, path, **k: (
            {"columns": {"claude_turn": [{"session_id": "sid123", "status": status}]}}
            if path == "/api/board" else {})
        co.fetch_session_card = lambda *a, **k: (
            card if card is not None else {"last_output_at": time.time() - 4})
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = co.cmd_say(argparse.Namespace(
                    sid="sid123", file=str(_brief), verify=verify,
                    timeout=0.0, poll_interval=0.0, base_url=co.DEFAULT_BASE_URL))
            return rc, buf.getvalue(), calls["n"]
        finally:
            (co.post_session_input, co.fetch_exchange_marker,
             co._request, co.fetch_session_card) = _orig

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


# ---- say/stop resolve the prefix too (fleet-config#681) ----------------------
# `board`/`sessions` only ever print the 8-char prefix, so that is the only
# form an operator (or chief, per SKILL.md) has. `exchange` resolved it and
# `say`/`stop` did not: `say <prefix>` POSTed to a session id that does not
# exist, and `--verify` then read the resulting empty card as PENDING —
# "delivery likely, unconfirmed" for a steer that was never accepted at all.

def _run_sid_cmd(fn, sid, *, kill=False):
    """Drive cmd_say (plain) or cmd_stop against a stubbed transport, returning
    (exit_code, stdout, the sid the endpoint was actually called with)."""
    seen = {"path": None}

    def _fake_request(base_url, path, method="GET", body=None, timeout=10.0):
        if path == "/api/board":
            return {"columns": _exchange_columns}
        seen["path"] = path
        return {}

    _orig = (co._request, co.post_session_input)
    co._request = _fake_request

    def _fake_post(base_url, s, text):
        seen["path"] = f"/api/claude-code/sessions/{s}/input"
        return {"ok": True, "reason": "ok", "error": None}

    co.post_session_input = _fake_post
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            args = argparse.Namespace(sid=sid, file=str(_prefix_brief), verify=False,
                                      timeout=0.0, poll_interval=0.0, kill=kill,
                                      base_url=co.DEFAULT_BASE_URL)
            rc = fn(args)
        return rc, buf.getvalue(), seen["path"]
    finally:
        co._request, co.post_session_input = _orig


_prefix_tmp = Path(tempfile.mkdtemp(prefix="chief_ops_prefix_"))
try:
    _prefix_brief = _prefix_tmp / "brief.md"
    _prefix_brief.write_text("steer text", encoding="utf-8")

    _rc, _out, _path = _run_sid_cmd(co.cmd_say, "ad3e8bbb")
    check(_path == "/api/claude-code/sessions/ad3e8bbbcccccccccccccccccccccccc/input",
          f"cmd_say: an 8-char board prefix is resolved before the POST — called {_path!r}")
    check("SENT sid=ad3e8bbbcccccccccccccccccccccccc" in _out and _rc == 0,
          "cmd_say: the reported sid is the resolved one, so the operator can correlate it")

    _rc, _out, _path = _run_sid_cmd(co.cmd_stop, "ad3e8bbb")
    check(_path == "/api/claude-code/sessions/ad3e8bbbcccccccccccccccccccccccc/stop",
          f"cmd_stop: an 8-char board prefix is resolved before the stop — called {_path!r}")
    check("STOPPED sid=ad3e8bbbcccccccccccccccccccccccc" in _out and _rc == 0,
          "cmd_stop: reports the resolved sid")

    _rc, _out, _path = _run_sid_cmd(co.cmd_say, "d235f29")
    check(_rc == 1 and "UNRESOLVABLE" in _out and _path is None,
          "cmd_say: an ambiguous prefix refuses and never sends — same rule as cmd_exchange")
    _rc, _out, _path = _run_sid_cmd(co.cmd_stop, "d235f29")
    check(_rc == 1 and "UNRESOLVABLE" in _out and _path is None,
          "cmd_stop: an ambiguous prefix refuses and never stops the wrong session")

    _rc, _out, _path = _run_sid_cmd(co.cmd_say, "deadbeefdeadbeefdeadbeefdeadbeef")
    check(_path == "/api/claude-code/sessions/deadbeefdeadbeefdeadbeefdeadbeef/input",
          "cmd_say: an id matching no live session is passed through unchanged (real endpoint decides)")
finally:
    shutil.rmtree(_prefix_tmp, ignore_errors=True)


_h.report_and_exit("test_chief_ops")
