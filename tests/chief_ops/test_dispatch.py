"""chief_ops dispatch: the refusals, cmd_dispatch end to end with briefs, timeouts and fleet-wide jobs, assert_loopback, the board digest, issue refs and repo paths.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/chief_ops/test_dispatch.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import argparse
import os
import shutil
import tempfile
from pathlib import Path
import contextlib
import io

from _suite import _CHIEF_SID, _WORKER_SID, _h, _session_card, check, co  # noqa: E402


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


# ---- cmd_dispatch end to end: chief can dispatch its own repo (#838) --------
#
# The unit checks above prove the predicate; these prove the wiring, which is
# where #838 actually lived -- `refuse_dispatch` was always correct, it was
# handed an occupancy map that counted the caller. Driven through the real
# `cmd_dispatch` against a stubbed transport, with the launcher's env stamp
# set exactly as a live session carries it.

def _run_dispatch(cards, env_sid, repo="fleet-config", number=838, brief_file=None,
                  jobs=None, allow_during_fleet_job=False, start_exc=None, model=None,
                  catalog=(), effort=None, echo_effort=False):
    posted = []
    requested = []
    _run_dispatch.start_timeout = None

    def _fake_request(base_url, path, method="GET", body=None, timeout=10.0):
        requested.append(path)
        if path == "/api/jobs":
            if isinstance(jobs, Exception):
                raise jobs
            return jobs if jobs is not None else {"jobs": []}
        if path == "/api/config":
            if isinstance(catalog, Exception):
                raise catalog
            return {"model_catalog": {"codex": catalog}}
        if path == "/api/board":
            return {"columns": {"claude_turn": cards, "your_turn": []}}
        if path == "/api/board/chief/settings":
            return {"settings": {"worker_cap": 3}}
        if path == "/api/board/issues/start":
            posted.append(body)
            _run_dispatch.start_timeout = timeout
            if start_exc is not None:
                raise start_exc
            launched = f"/issue-{body['mode']} {body['number']}"
            if "brief" in body:
                launched += " --brief E:/automation/app-launcher/webapp/briefs/0f.md"
            result = {"session": {"session_id": "spawned-1"}, "launched": launched}
            if echo_effort and "effort" in body:
                result["effort"] = body["effort"]
            return result
        raise AssertionError(f"unexpected path: {path}")

    prior_request, prior_mark = co.launcher._request, co.chief_managed.mark
    prior_env = os.environ.get(co.LAUNCHER_SESSION_ID_ENV_VAR)
    co.launcher._request = _fake_request
    co.chief_managed.mark = lambda *a, **k: {}
    if env_sid is None:
        os.environ.pop(co.LAUNCHER_SESSION_ID_ENV_VAR, None)
    else:
        os.environ[co.LAUNCHER_SESSION_ID_ENV_VAR] = env_sid
    try:
        args = argparse.Namespace(
            repo=repo, number=number, mode="start", model=model,
            brief_file=brief_file, yolo_confirmed=False, base_url=co.DEFAULT_BASE_URL,
            allow_during_fleet_job=allow_during_fleet_job, effort=effort,
        )
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = co.cmd_dispatch(args)
        _run_dispatch.last_output = out.getvalue()
        _run_dispatch.last_requested = requested
    finally:
        co.launcher._request, co.chief_managed.mark = prior_request, prior_mark
        if prior_env is None:
            os.environ.pop(co.LAUNCHER_SESSION_ID_ENV_VAR, None)
        else:
            os.environ[co.LAUNCHER_SESSION_ID_ENV_VAR] = prior_env
    return rc, posted


_chief_card = _session_card(project="fleet-config", label="chief", session_id=_CHIEF_SID)
_worker_card = _session_card(project="fleet-config", label="", session_id=_WORKER_SID)

rc, posted = _run_dispatch([_chief_card], _CHIEF_SID)
check(rc == 0 and len(posted) == 1,
      "cmd_dispatch: chief dispatches fleet-config while its own session is live (#838)")

rc, posted = _run_dispatch([_chief_card, _worker_card], _CHIEF_SID)
check(rc == 1 and posted == [],
      "cmd_dispatch: a genuine second worker in the repo is still refused, no POST (#838)")

rc, posted = _run_dispatch([_chief_card], None)
check(rc == 1 and posted == [],
      "cmd_dispatch: outside a launcher session nothing is excluded -- the "
      "guard does not weaken for a hand-run chief_ops")

rc, posted = _run_dispatch([_chief_card], _CHIEF_SID, repo="photo-ocr")
check(rc == 0 and len(posted) == 1,
      "cmd_dispatch: an unoccupied repo is unaffected by the exclusion")


# ---- cmd_dispatch --model: the grok alias reaches the launcher qualified ---

rc, posted = _run_dispatch([_chief_card], _CHIEF_SID, model="grok")
check(rc == 0 and posted[0].get("model") == "grok:grok-4.5",
      "cmd_dispatch: --model grok posts the launcher's grok:grok-4.5")

_CATALOG = [
    {"value": "gpt-6-luna", "label": "Luna", "available": True},
    {"value": "gpt-5.6-sol", "label": "Sol", "available": True},
    {"value": "gpt-6-astra", "label": "Astra", "available": False},
]

rc, posted = _run_dispatch([_chief_card], _CHIEF_SID, model="luna", catalog=_CATALOG)
check(rc == 0 and posted[0].get("model") == "codex:gpt-6-luna",
      "cmd_dispatch: --model luna posts the catalog's Luna slug, not a hard-coded one")

renamed = [{"value": "gpt-7-luna", "label": "Luna", "available": True}]
rc, posted = _run_dispatch([_chief_card], _CHIEF_SID, model="Luna", catalog=renamed)
check(rc == 0 and posted[0].get("model") == "codex:gpt-7-luna",
      "cmd_dispatch: a new Luna release resolves with no code change (case-insensitive)")

rc, posted = _run_dispatch([_chief_card], _CHIEF_SID, model="luna", catalog=[_CATALOG[1], _CATALOG[2]])
check(rc == 1 and posted == [] and "offered: Sol" in _run_dispatch.last_output,
      "cmd_dispatch: a family missing from the catalog is refused with the available list")

rc, posted = _run_dispatch([_chief_card], _CHIEF_SID, model="luna", catalog=OSError("down"))
check(rc == 1 and posted == [] and "unreadable" in _run_dispatch.last_output,
      "cmd_dispatch: an unreadable catalog is refused, never guessed")

rc, posted = _run_dispatch([_chief_card], _CHIEF_SID, model="gpt5.6", catalog=_CATALOG)
check(rc == 1 and posted == [] and "use --model luna" in _run_dispatch.last_output
      and "/api/board" not in _run_dispatch.last_requested,
      "cmd_dispatch: the retired gpt5.6 name is refused with the replacement, before any board read")

rc, posted = _run_dispatch([_chief_card], _CHIEF_SID, model="opus")
check("/api/config" not in _run_dispatch.last_requested,
      "cmd_dispatch: a Claude tier never reads the Codex catalog")
rc, posted = _run_dispatch([_chief_card], _CHIEF_SID, model="opus")
check(rc == 0 and posted[0].get("model") == "opus",
      "cmd_dispatch: a non-alias model is posted as typed")

rc, posted = _run_dispatch([_chief_card], _CHIEF_SID)
check(rc == 0 and "model" not in posted[0],
      "cmd_dispatch: no --model sends no model key")


# ---- cmd_dispatch --effort: posted, and applied only when the launcher echoes it

rc, posted = _run_dispatch([_chief_card], _CHIEF_SID, model="opus", effort="high")
check(rc == 0 and posted[0].get("effort") == "high"
      and "EFFORT=high applied=not-confirmed reason=launcher did not echo effort" in _run_dispatch.last_output,
      "cmd_dispatch: --effort is posted; a launcher that ignores it is reported not-confirmed, never applied")

rc, posted = _run_dispatch([_chief_card], _CHIEF_SID, model="opus", effort="medium", echo_effort=True)
check(rc == 0 and "EFFORT=medium applied=yes" in _run_dispatch.last_output,
      "cmd_dispatch: an effort the launcher echoes is reported applied")

check(co.commands.effort_outcome("high", {"effort": "medium"})
      == "EFFORT=high applied=not-confirmed reason=launcher reported medium",
      "effort_outcome: a different echoed effort is a mismatch, not applied")

rc, posted = _run_dispatch([_chief_card], _CHIEF_SID)
check(rc == 0 and "effort" not in posted[0] and "EFFORT=" not in _run_dispatch.last_output,
      "cmd_dispatch: no --effort sends no effort key and prints no EFFORT line")

try:
    with contextlib.redirect_stderr(io.StringIO()):
        co.cli.build_parser(co.__doc__).parse_args(["dispatch", "x", "1", "--effort", "max"])
    _max_refused = False
except SystemExit:
    _max_refused = True
check(_max_refused, "dispatch --effort max is rejected at parse time (max stays off)")


# ---- cmd_dispatch --brief-file: the brief rides the launch command (#944) ---
#
# A lane trusts only its launch command, so the brief has to reach the
# launcher in the dispatch POST itself. A missing/empty file is refused before
# any board read: spawning anyway would start a lane on the bare issue.

_brief_tmp = Path(tempfile.mkdtemp(prefix="chief-brief-"))
try:
    _brief = _brief_tmp / "brief.md"
    _brief.write_text("Queue: #944 then #953.\nShip each on green.\n", encoding="utf-8")
    rc, posted = _run_dispatch([_chief_card], _CHIEF_SID, repo="photo-ocr",
                               brief_file=str(_brief))
    check(rc == 0 and len(posted) == 1
          and posted[0].get("brief") == "Queue: #944 then #953.\nShip each on green.\n",
          "cmd_dispatch --brief-file: the file's text is POSTed verbatim as `brief`")
    check("LAUNCHED=/issue-start 838 --brief " in _run_dispatch.last_output,
          "cmd_dispatch --brief-file: echoes the launcher's actual launch command")

    rc, posted = _run_dispatch([_chief_card], _CHIEF_SID, repo="photo-ocr")
    check(rc == 0 and "brief" not in posted[0],
          "cmd_dispatch: no --brief-file -> no `brief` key, POST body unchanged")

    (_brief_tmp / "empty.md").write_text("", encoding="utf-8")
    (_brief_tmp / "blank.md").write_text("  \n\t\n", encoding="utf-8")
    for _name, _expect in (("absent.md", "REFUSED=brief file unreadable"),
                           ("empty.md", "REFUSED=brief file is empty"),
                           ("blank.md", "REFUSED=brief file is empty")):
        rc, posted = _run_dispatch([_chief_card], _CHIEF_SID, repo="photo-ocr",
                                   brief_file=str(_brief_tmp / _name))
        check(rc == 1 and posted == [] and _run_dispatch.last_requested == []
              and _expect in _run_dispatch.last_output,
              f"cmd_dispatch --brief-file: {_name} is refused before any request")
finally:
    shutil.rmtree(_brief_tmp, ignore_errors=True)


# ---- cmd_dispatch timeout: an unconfirmed spawn is not a failure (#1329) ----
#
# The launcher spawns the lane even after a slow client gives up (22 s and 41 s
# observed), so a timed-out dispatch must say "poll sessions before retrying"
# instead of tracebacking -- the blind retry spawned a second lane. The POST
# gets its own long timeout, like JOBS_TIMEOUT, well above the observed 41 s.

_run_dispatch([_chief_card], _CHIEF_SID, repo="photo-ocr")
check(getattr(co.commands, "DISPATCH_TIMEOUT", 0) >= 90.0
      and _run_dispatch.start_timeout == getattr(co.commands, "DISPATCH_TIMEOUT", None),
      "cmd_dispatch: the start POST uses DISPATCH_TIMEOUT (>= 90 s), not the 10 s default (#1329)")

for _label, _exc in (
    ("read timeout", TimeoutError("timed out")),
    ("connect timeout (URLError wrapping one)", co.urllib.error.URLError(TimeoutError("timed out"))),
):
    try:
        rc, posted = _run_dispatch([_chief_card], _CHIEF_SID, repo="photo-ocr", start_exc=_exc)
    except Exception as _raised:  # noqa: BLE001 - the pre-fix traceback is what's under test
        check(False, f"cmd_dispatch: {_label} must not raise ({type(_raised).__name__})")
        continue
    _out = _run_dispatch.last_output
    check(rc == 1 and len(posted) == 1 and "DISPATCH=unconfirmed reason=timeout" in _out
          and "sessions" in _out and "DISPATCHED" not in _out and "REFUSED" not in _out,
          f"cmd_dispatch: {_label} -> DISPATCH=unconfirmed with poll-sessions guidance (#1329)")

# A refused connection is a real failure, not an unconfirmed spawn: it still raises.
try:
    _run_dispatch([_chief_card], _CHIEF_SID, repo="photo-ocr",
                  start_exc=co.urllib.error.URLError(ConnectionRefusedError("refused")))
    check(False, "cmd_dispatch: a refused connection must still raise, not read as unconfirmed")
except co.urllib.error.URLError:
    check(True, "cmd_dispatch: a refused connection is not mislabelled unconfirmed (#1329)")


# ---- fleet-wide jobs: warn on dispatch, refuse fleet-wide lanes (#1078) -----
#
# On 2026-09-27 a fleet-wide `/propagate-vendored` lane was dispatched while
# the scheduled cleanup-fleet-all run was mid-flight; its worktrees halted
# that run after 9 of 50 issues. `/api/jobs` is stubbed: every job carries
# `id` and `running`, exactly as the launcher reports them.

def _job(job_id, running):
    return {"id": job_id, "name": job_id, "running": running}


_JOBS_CLEANUP_RUNNING = {"jobs": [
    _job("insights-weekly", True),              # not fleet-wide: never warns
    _job("cleanup-fleet-all-weekly", True),
    _job("codebase-audit-fleet", False),
]}

check(co.running_fleet_jobs(_JOBS_CLEANUP_RUNNING) == ["cleanup-fleet-all-weekly"],
      "#1078 running_fleet_jobs: only running fleet-wide jobs, never a per-repo one")
check(co.running_fleet_jobs({"jobs": [_job(j, True) for j in (
          "codebase-audit-fleet", "design-sweep-fleet", "prompt-audit-fleet", "cleanup-fleet-docs")]})
      == ["codebase-audit-fleet", "design-sweep-fleet", "prompt-audit-fleet", "cleanup-fleet-docs"],
      "#1078 running_fleet_jobs: every fleet-wide id the issue names, cleanup-fleet-* included")
check(co.running_fleet_jobs({"jobs": []}) == [],
      "#1078 running_fleet_jobs: no jobs running is an empty list")

for _text, _expect in (("Run /propagate-vendored nav across adopters.", True),
                       ("Then run /cleanup-fleet documentation.", True),
                       ("Queue: #1082 then #1078.", False)):
    check(co.is_fleet_wide_brief(_text) is _expect,
          f"#1078 is_fleet_wide_brief({_text!r}) is {_expect}")

_fw_tmp = Path(tempfile.mkdtemp(prefix="chief-fleetjob-"))
try:
    _fw_brief = _fw_tmp / "fleet.md"
    _fw_brief.write_text("Run /propagate-vendored nav to every adopter.\n", encoding="utf-8")
    _plain_brief = _fw_tmp / "plain.md"
    _plain_brief.write_text("Queue: #12 then #13.\n", encoding="utf-8")

    rc, posted = _run_dispatch([_chief_card], _CHIEF_SID, repo="photo-ocr",
                               brief_file=str(_plain_brief), jobs=_JOBS_CLEANUP_RUNNING)
    check(rc == 0 and len(posted) == 1
          and "WARNING=fleet-job-running job=cleanup-fleet-all-weekly" in _run_dispatch.last_output,
          "#1078 dispatch: a per-issue lane during a fleet job still dispatches, with the warning")

    rc, posted = _run_dispatch([_chief_card], _CHIEF_SID, repo="photo-ocr",
                               brief_file=str(_fw_brief), jobs=_JOBS_CLEANUP_RUNNING)
    check(rc == 1 and posted == [] and "REFUSED=" in _run_dispatch.last_output
          and "cleanup-fleet-all-weekly" in _run_dispatch.last_output
          and "--allow-during-fleet-job" in _run_dispatch.last_output,
          "#1078 dispatch: a fleet-wide brief during a fleet job is refused, no POST, override named")

    rc, posted = _run_dispatch([_chief_card], _CHIEF_SID, repo="photo-ocr",
                               brief_file=str(_fw_brief), jobs=_JOBS_CLEANUP_RUNNING,
                               allow_during_fleet_job=True)
    check(rc == 0 and len(posted) == 1
          and "WARNING=fleet-job-running" in _run_dispatch.last_output,
          "#1078 dispatch: --allow-during-fleet-job lets a fleet-wide brief through, still warned")

    rc, posted = _run_dispatch([_chief_card], _CHIEF_SID, repo="photo-ocr",
                               brief_file=str(_fw_brief), jobs={"jobs": [_job("insights-weekly", True)]})
    check(rc == 0 and len(posted) == 1 and "WARNING=" not in _run_dispatch.last_output,
          "#1078 dispatch: no fleet job running -> no warning, fleet-wide brief dispatches")

    rc, posted = _run_dispatch([_chief_card], _CHIEF_SID, repo="photo-ocr",
                               brief_file=str(_plain_brief), jobs=OSError("connection refused"))
    check(rc == 0 and len(posted) == 1
          and "WARNING=fleet-jobs-unknown" in _run_dispatch.last_output
          and "connection refused" in _run_dispatch.last_output,
          "#1078 dispatch: an unreadable jobs API is its own `unknown` warning, never 'none running'")

    rc, posted = _run_dispatch([_chief_card], _CHIEF_SID, repo="photo-ocr",
                               brief_file=str(_fw_brief), jobs=OSError("connection refused"))
    check(rc == 1 and posted == [],
          "#1078 dispatch: a fleet-wide brief with fleet-job state unknown is refused, not waved through")
finally:
    shutil.rmtree(_fw_tmp, ignore_errors=True)

_digest_board = {"columns": {"claude_turn": [], "your_turn": [], "other": []}}
check("fleet jobs running: cleanup-fleet-all-weekly"
      in co.format_board_digest(_digest_board, fleet_jobs=(["cleanup-fleet-all-weekly"], None)),
      "#1078 board digest lists running fleet jobs")
check("fleet jobs running: none"
      in co.format_board_digest(_digest_board, fleet_jobs=([], None)),
      "#1078 board digest says so when no fleet job is running")
check("fleet jobs running: ? (timed out)"
      in co.format_board_digest(_digest_board, fleet_jobs=(None, "timed out")),
      "#1078 board digest renders an unreadable jobs API as `?` with its reason")


def _run_board(jobs):
    def _fake_request(base_url, path, method="GET", body=None, timeout=10.0):
        if path == "/api/board":
            return _digest_board
        if path == "/api/jobs":
            return jobs
        raise AssertionError(f"unexpected path: {path}")
    prior = co.launcher._request
    co.launcher._request = _fake_request
    try:
        with contextlib.redirect_stdout(io.StringIO()) as out:
            co.cmd_board(argparse.Namespace(base_url=co.DEFAULT_BASE_URL, json=False))
    finally:
        co.launcher._request = prior
    return out.getvalue()


check("fleet jobs running: cleanup-fleet-all-weekly" in _run_board(_JOBS_CLEANUP_RUNNING),
      "#1078 `board` reads /api/jobs so every poll sees running fleet jobs")


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
    "github": {"available": True, "fetched_at": "2026-07-27T17:00:00Z", "error": None},
    "live_sessions": {"available": True, "error": None},
    "quota_lines": [
        {"harness": "claude", "state": "available", "stale": False,
         "five_hour": {"used_percentage": 42.0, "resets_at": "2026-07-27T18:00:00Z"}},
        {"harness": "codex", "state": "available", "stale": False,
         "five_hour": {"used_percentage": 7.0, "resets_at": "2026-07-27T19:00:00Z"}},
    ],
}
digest = co.format_board_digest(board)
lines = digest.splitlines()
check(len(lines) <= 12, f"format_board_digest stays within ~12 lines (got {len(lines)})")
check(lines[0] == "backlog=2 claude_turn=1 your_turn=1 other=2 done=1",
      f"format_board_digest leads with the counts line (got {lines[0]!r})")
check(any("app-launcher" in l for l in lines), "format_board_digest lists the live session")
check(any("audit-fleet" in l for l in lines), "format_board_digest lists the job card")
check(any(l == "rate_limit_5h=42.0% resets=2026-07-27T18:00:00Z" for l in lines),
      "format_board_digest carries the claude 5h rate-limit line from quota_lines")
check("None" not in digest, "a fully-readable board prints no `None` anywhere")
check(not any(l.startswith("unknown:") for l in lines), "a fully-readable board carries no unknown line")

# fleet-config#840: an unfetched GitHub cache reads as empty lists — the digest
# must say unknown, never a plausible `backlog=0`.
unfetched = {
    "columns": {"backlog": [], "claude_turn": [_session_card()], "your_turn": [],
                "other": [{"kind": "job", "job_name": "audit-fleet", "state": "failed"}], "done": []},
    "github": {"available": False, "fetched_at": None, "error": None},
    "live_sessions": {"available": True, "error": None},
    "quota_lines": board["quota_lines"],
}
digest = co.format_board_digest(unfetched)
lines = digest.splitlines()
check("backlog=0" not in digest, "an unfetched github cache never renders as backlog=0")
check(lines[0] == "backlog=? claude_turn=1 your_turn=0 other=? done=?",
      f"github-sourced counts render `?`, session counts stay real (got {lines[0]!r})")
check(any(l.startswith("unknown:") and "github" in l and "never fetched" in l for l in lines),
      "the digest says why the github counts are unknown (never fetched)")
first_fetch_failed = dict(unfetched, github={"available": False, "fetched_at": None, "error": "gh: auth"})
lines = co.format_board_digest(first_fetch_failed).splitlines()
check(lines[0].startswith("backlog=? "), "a first fetch that failed still renders the github counts `?`")
check(any("last attempt failed: gh: auth" in l for l in lines),
      "a failed first fetch is distinguished from never fetched")

# A refresh that failed after an earlier good fetch: the lists are real (older)
# data, so the counts stand, but the failure is named.
failed_refresh = dict(board, github={"available": True, "fetched_at": "2026-07-27T17:00:00Z",
                                     "error": "gh: HTTP 502"})
digest = co.format_board_digest(failed_refresh)
lines = digest.splitlines()
check(lines[0].startswith("backlog=2 "), "a failed refresh over good cached data keeps the real counts")
check(any("last fetch failed" in l and "HTTP 502" in l and "2026-07-27T17:00:00Z" in l for l in lines),
      "a failed refresh is named, with the error and the age of the data shown")

# A genuine zero is still a zero.
empty_but_fetched = dict(board, columns={"backlog": [], "claude_turn": [], "your_turn": [],
                                         "other": [], "done": []})
lines = co.format_board_digest(empty_but_fetched).splitlines()
check(lines[0] == "backlog=0 claude_turn=0 your_turn=0 other=0 done=0",
      f"a fetched, genuinely empty board renders real zeros (got {lines[0]!r})")

# #840 comment: an unreadable session-host must not read as `claude_turn=0 your_turn=0`.
no_host = dict(board, columns=dict(board["columns"], claude_turn=[], your_turn=[]),
               live_sessions={"available": False, "error": "session-host unreachable"})
digest = co.format_board_digest(no_host)
lines = digest.splitlines()
check(lines[0] == "backlog=2 claude_turn=? your_turn=? other=2 done=1",
      f"an unreadable session-host renders the turn counts `?` (got {lines[0]!r})")
check(any("live_sessions" in l and "session-host unreachable" in l for l in lines),
      "the digest names the session-host error")

# An older launcher build that doesn't send a source section at all: absent is
# unknown, never "fetched". Every count token must be gated on some source, so
# a bare `{}` payload can't produce a single plausible number — the property
# that stops a future count from being added without an availability check.
digest = co.format_board_digest({})
lines = digest.splitlines()
check(lines[0] == "backlog=? claude_turn=? your_turn=? other=? done=?",
      f"a payload with no source sections renders every count `?` (got {lines[0]!r})")
check(any("not reported" in l for l in lines), "an absent source section is reported as not reported")
check("None" not in digest, "an empty payload prints no `None` anywhere")
check(any(l.startswith("rate_limit_5h=? ") for l in lines), "no quota_lines renders the rate limit `?`")

# The quota row exists but carries no reading.
unread_quota = dict(board, quota_lines=[{"harness": "claude", "state": "error",
                                         "reason": "consumer_contract_unavailable",
                                         "five_hour": {"used_percentage": None, "resets_at": None}}])
digest = co.format_board_digest(unread_quota)
check("None" not in digest, "an unreadable quota row prints no `None%`")
no_reason = dict(board, quota_lines=[{"harness": "claude", "five_hour": {}}])
check("None" not in co.format_board_digest(no_reason), "a quota row with no state/reason prints no `None`")
check(any(l.startswith("rate_limit_5h=? resets=?") and "consumer_contract_unavailable" in l
          for l in digest.splitlines()),
      "an unreadable quota row renders `?` and names its reason")
stale_quota = dict(board, quota_lines=[dict(board["quota_lines"][0], stale=True)])
check(any(l.startswith("rate_limit_5h=42.0% ") and "stale" in l
          for l in co.format_board_digest(stale_quota).splitlines()),
      "a stale quota reading keeps its value but is marked stale")


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

_h.report_and_exit("test_dispatch")
