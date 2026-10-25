"""chief_ops' board reading and message delivery: repo occupancy, standing sessions, the caller's id, worker counts, find_chief, and the say --verify / exchange classifier in steer_delivery.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/chief_ops/test_board_and_delivery.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from _suite import _CHIEF_SID, _WORKER_SID, _exchange_columns, _h, _session_card, check, co, sd  # noqa: E402


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


# ---- the caller's own card never occupies a repo (fleet-config#838) ---------
#
# The standing chief is an ordinary launcher PTY sitting in `fleet-config`, so
# before the fix its own card made that repo permanently occupied and chief
# could never dispatch its own queue. The board fields below are the real ones:
# `session_id` is the launcher's `uuid4().hex`, byte-identical to the value it
# stamps as `APP_LAUNCHER_SESSION_ID` in the session it spawns.


chief_only = {
    "claude_turn": [_session_card(project="fleet-config", label="chief", session_id=_CHIEF_SID)],
    "your_turn": [],
}
check(
    co.repo_occupancy(chief_only, exclude_sid=_CHIEF_SID) == {},
    "repo_occupancy: the caller's own card does not occupy its repo (#838)",
)
check(
    "fleet-config" in co.repo_occupancy(chief_only),
    "repo_occupancy: with no exclude_sid the caller's card still occupies -- "
    "the exclusion is opt-in, so every non-dispatch reader is unchanged",
)

chief_plus_worker = {
    "claude_turn": [
        _session_card(project="fleet-config", label="chief", session_id=_CHIEF_SID),
        _session_card(project="fleet-config", label="", session_id=_WORKER_SID),
    ],
    "your_turn": [],
}
occ_cw = co.repo_occupancy(chief_plus_worker, exclude_sid=_CHIEF_SID)
check(
    occ_cw.get("fleet-config", {}).get("session_id") == _WORKER_SID,
    "repo_occupancy: excluding the caller still surfaces a genuine second "
    "worker in the same repo -- the collision guard is intact (#838)",
)
check(
    co.repo_occupancy(chief_plus_worker, exclude_sid=_WORKER_SID)
      .get("fleet-config", {}).get("session_id") == _CHIEF_SID,
    "repo_occupancy: the exclusion is by session id, not by label -- a "
    "non-chief caller excludes only itself",
)

check(
    co.repo_occupancy(chief_only, exclude_sid="").get("fleet-config") is not None,
    "repo_occupancy: an empty exclude_sid excludes nothing (a card with no "
    "session_id must never match)",
)
check(
    co.repo_occupancy(
        {"claude_turn": [_session_card(project="fleet-config", session_id=None)], "your_turn": []},
        exclude_sid="",
    ) != {},
    "repo_occupancy: a card with session_id None is not excluded by a blank id",
)


# ---- Telegram channel sessions are standing, not workers (fleet-config#1212) --
#
# The session-host tags the three always-on Telegram channel sessions (cwd
# life-os) with a `telegram:<profile>` board label. They never claim the
# checkout, so before the fix they made life-os permanently undispatchable and
# also ate three slots of the worker cap.

_TG_LABELS = ("telegram:health", "telegram:school", "telegram:house")
_tg_only = {
    "claude_turn": [
        _session_card(project="life-os", label=lbl, session_id=f"tg-{i}")
        for i, lbl in enumerate(_TG_LABELS)
    ],
    "your_turn": [],
}
check(
    co.repo_occupancy(_tg_only) == {},
    "repo_occupancy: telegram:* channel sessions do not occupy their repo (#1212)",
)
check(
    co.alive_worker_count(_tg_only) == 0,
    "alive_worker_count: telegram:* channel sessions do not use up lane capacity (#1212)",
)
check(
    co.refuse_dispatch("life-os", "start", co.repo_occupancy(_tg_only),
                       co.alive_worker_count(_tg_only), 3, False) is None,
    "dispatch into life-os is not refused by its standing telegram sessions (#1212)",
)

_tg_plus_worker = {
    "claude_turn": [
        _session_card(project="life-os", label="telegram:health", session_id="tg-0"),
        _session_card(project="life-os", label="", session_id=_WORKER_SID),
    ],
    "your_turn": [],
}
check(
    co.repo_occupancy(_tg_plus_worker).get("life-os", {}).get("session_id") == _WORKER_SID,
    "repo_occupancy: a normal life-os session still occupies it beside a telegram one (#1212)",
)
check(
    co.alive_worker_count(_tg_plus_worker) == 1,
    "alive_worker_count: only the normal session counts beside a telegram one (#1212)",
)
check(
    "life-os" in co.repo_occupancy({
        "claude_turn": [_session_card(project="life-os", label="my-telegram:notes")],
        "your_turn": [],
    }),
    "repo_occupancy: the exclusion is the `telegram:` prefix, not a substring (#1212)",
)
check(
    "life-os" in co.repo_occupancy({
        "claude_turn": [_session_card(project="life-os", label=None)],
        "your_turn": [],
    }),
    "repo_occupancy: a card with no label (None) still occupies (#1212)",
)


# ---- caller_session_id: the launcher's own id space (fleet-config#838/#848) --

check(
    co.rails.caller_session_id({"APP_LAUNCHER_SESSION_ID": _CHIEF_SID}) == _CHIEF_SID,
    "caller_session_id reads APP_LAUNCHER_SESSION_ID",
)
check(
    co.rails.caller_session_id({}) is None,
    "caller_session_id is None outside a launcher session -- no exclusion, "
    "so a hand-run chief_ops keeps the pre-#838 behaviour",
)
check(
    co.rails.caller_session_id({"APP_LAUNCHER_SESSION_ID": "   "}) is None,
    "caller_session_id treats a blank stamp as absent, never as a sid",
)


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

# An `external` card is state-file-only and unverifiable, so it must not count
# against the cap when repo_occupancy already refuses to let it hold a repo:
# otherwise a board of alive external cards reads "repo free" and "cap full"
# in the same dispatch (fleet-config#932).
_external_board = {
    "claude_turn": [_session_card(kind="external", project=f"repo-{i}") for i in range(3)],
    "your_turn": [],
}
check(co.alive_worker_count(_external_board) == 0,
      "alive_worker_count excludes an external (state-only) card, like repo_occupancy (#932)")
check(
    co.refuse_dispatch("whatsapp-radar", "start", co.repo_occupancy(_external_board),
                       co.alive_worker_count(_external_board), 3, False) is None,
    "dispatch is not capped out by external cards the occupancy gate ignores (#932)",
)

count_self = co.alive_worker_count(
    {
        "claude_turn": [
            _session_card(project="fleet-config", label="", session_id=_CHIEF_SID),
            _session_card(project="photo-ocr", session_id="other-1"),
        ],
        "your_turn": [],
    },
    exclude_sid=_CHIEF_SID,
)
check(
    count_self == 1,
    "alive_worker_count: the caller's own card is not a worker against the "
    "cap, even when its label is not 'chief' (#838)",
)


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
    check(getattr(_owner := co.cli if _name == "DEFAULT_VERIFY_POLL_INTERVAL" else co.commands, _name) is getattr(sd, _name),
          f"steer_delivery: {_owner.__name__}.{_name} is steer_delivery's own object, not a copy")
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

    prior = co.launcher._request
    co.launcher._request = _fake_request
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = co.cmd_exchange(argparse.Namespace(sid=sid, tail=2000, base_url=co.DEFAULT_BASE_URL))
    finally:
        co.launcher._request = prior
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

    prior = co.launcher._request
    co.launcher._request = _fake_request
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
        co.launcher._request = prior
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

_h.report_and_exit("test_board_and_delivery")
