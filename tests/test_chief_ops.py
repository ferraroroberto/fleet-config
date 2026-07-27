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
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import chief_managed  # noqa: E402
import chief_ops as co  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

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
    proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    check(proc.returncode == 0, f"git {' '.join(args)} in {cwd} failed: {proc.stderr}")
    return proc.stdout.strip()


def _run_verify(repo_path: Path, expect: str, branch: str | None = None) -> subprocess.CompletedProcess:
    argv = [sys.executable, str(REPO / "skills" / "_lib" / "chief_ops.py"),
            "verify", str(repo_path), "--expect", expect]
    if branch:
        argv += ["--branch", branch]
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace")


tmp = Path(tempfile.mkdtemp(prefix="chief_ops_verify_"))
try:
    upstream = tmp / "upstream"
    work = tmp / "work"
    upstream.mkdir()
    _git(upstream, "init", "-q")
    _git(upstream, "checkout", "-q", "-b", "main")
    _git(upstream, "config", "user.email", "35553560+ferraroroberto@users.noreply.github.com")
    _git(upstream, "config", "user.name", "Test")
    (upstream / "README.md").write_text("hello\n", encoding="utf-8")
    _git(upstream, "add", "README.md")
    _git(upstream, "commit", "-q", "-m", "initial")

    _git(tmp, "clone", "-q", str(upstream), str(work))
    _git(work, "config", "user.email", "35553560+ferraroroberto@users.noreply.github.com")
    _git(work, "config", "user.name", "Test")

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


_h.report_and_exit("test_chief_ops")
