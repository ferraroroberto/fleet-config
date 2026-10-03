"""Tests for skills/_lib/gate_evidence.py (fleet-config#957), against real temp repos.

Covers the acceptance cases: an edit or a new untracked file after a recorded
pass turns FRESH into STALE, committing identical content stays FRESH, a
mismatched --expect-cmd or a failing run is MISSING, a tree that changes
mid-run records nothing, the real index is byte-identical afterwards (in a
plain checkout and in a linked worktree, where `.git` is a file), and an
unreadable repo is UNKNOWN.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_gate_evidence.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import gate_evidence as ge  # noqa: E402
from git_run import run_git  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import init_repo  # noqa: E402

_h = CheckHarness()
check = _h.check

PY = sys.executable
OK_CMD = [PY, "-c", "pass"]


def git(repo: Path, *args: str) -> str:
    res = run_git(["-C", str(repo), *args])
    assert res.returncode == 0, f"git {args}: {res.stderr}"
    return res.stdout


def quiet_run(repo: Path, label: str, argv):
    with contextlib.redirect_stdout(io.StringIO()):
        return ge.run(repo, label, argv)


def index_bytes(repo: Path) -> bytes:
    return ge.git_path(repo, "index").read_bytes()


tmp = Path(tempfile.mkdtemp(prefix="gate_evidence_"))
repo = init_repo(empty_commit=False)
try:
    (repo / ".gitignore").write_text(".env\n", encoding="utf-8")
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")

    check(ge.check(repo, "gate")[0] == "MISSING", "no record yet -> MISSING")

    before_index = index_bytes(repo)
    code, line = quiet_run(repo, "gate", OK_CMD)
    check(code == 0 and line.startswith("EVIDENCE=recorded label=gate exit=0"),
          f"run: a passing command is recorded ({line})")
    check(ge.check(repo, "gate")[0] == "FRESH", "unchanged tree -> FRESH")
    check(index_bytes(repo) == before_index, "real index byte-identical after run + check")
    check(not any(p.name == ge.EVIDENCE_NAME for p in repo.iterdir()),
          "evidence lives outside the working tree")

    check(ge.check(repo, "gate", expect_cmd=ge._cmd_text(OK_CMD))[0] == "FRESH",
          "--expect-cmd matching the recorded command -> FRESH")
    check(ge.check(repo, "gate", expect_cmd="echo ok")[0] == "MISSING",
          "--expect-cmd differing from the recorded command -> MISSING, not FRESH")
    check(ge.check(repo, "e2e-full")[0] == "MISSING", "labels are independent")

    (repo / "a.txt").write_text("two\n", encoding="utf-8")
    state, detail = ge.check(repo, "gate")
    check(state == "STALE" and "a.txt" in detail, f"editing a tracked file -> STALE naming it ({detail})")
    git(repo, "checkout", "-q", "--", "a.txt")
    check(ge.check(repo, "gate")[0] == "FRESH", "reverting the edit -> FRESH again")

    # fleet-config#1192: a same-size rewrite in the same second as the last index write. Git for Windows compares stat
    # times at one-second resolution, so such an edit is invisible to the stat cache unless git treats the entry as
    # "racily clean" (entry mtime >= the index file's mtime) and re-reads it. The hash works on a COPY of the index, so
    # the copy must keep the real index's mtime. Forced here rather than raced: file and index are both stamped to
    # the same instant, well in the past, so the outcome never depends on how fast the box ran this test.
    racy = init_repo(empty_commit=False)
    try:
        racy_file = racy / "a.txt"
        racy_file.write_text("one\n", encoding="utf-8")
        long_ago = time.time_ns() - 60 * 1_000_000_000
        os.utime(racy_file, ns=(long_ago, long_ago))
        git(racy, "add", "-A")
        git(racy, "commit", "-q", "-m", "init")
        os.utime(ge.git_path(racy, "index"), ns=(long_ago, long_ago))
        quiet_run(racy, "gate", OK_CMD)
        check(ge.check(racy, "gate")[0] == "FRESH", "same-second fixture: unchanged tree -> FRESH")
        racy_file.write_text("two\n", encoding="utf-8")  # same size as "one\n"
        os.utime(racy_file, ns=(long_ago, long_ago))        # and the very mtime the index entry recorded
        state, detail = ge.check(racy, "gate")
        check(state == "STALE" and "a.txt" in detail,
              f"a same-size edit in the same second as the index write -> STALE naming it, not a stat-cache FRESH ({state}: {detail})")
    finally:
        shutil.rmtree(racy, ignore_errors=True)

    (repo / "new.txt").write_text("fresh file\n", encoding="utf-8")
    check(ge.check(repo, "gate")[0] == "STALE", "adding an untracked non-ignored file -> STALE")
    (repo / "new.txt").unlink()
    (repo / ".env").write_text("SECRET=x\n", encoding="utf-8")
    check(ge.check(repo, "gate")[0] == "FRESH", "an ignored file (.env) does not change the hash")

    (repo / "b.txt").write_text("committed\n", encoding="utf-8")
    quiet_run(repo, "gate", OK_CMD)
    git(repo, "add", "b.txt")
    git(repo, "commit", "-q", "-m", "same content")
    check(ge.check(repo, "gate")[0] == "FRESH", "committing identical content keeps FRESH")

    code, line = quiet_run(repo, "gate", [PY, "-c", "import sys; sys.exit(3)"])
    check(code == 3, "run: exits with the command's own exit code")
    check(ge.check(repo, "gate")[0] == "MISSING", "a failing run leaves no passing record -> MISSING")

    quiet_run(repo, "gate", OK_CMD)
    code, line = quiet_run(repo, "gate", [PY, "-c", "open('mid.txt','w').write('x')"])
    check(code == 0 and line.startswith("EVIDENCE=not_recorded") and "mid.txt" in line,
          f"a tree that changes mid-run is not recorded, naming the path ({line})")
    (repo / "mid.txt").unlink()
    check(ge.check(repo, "gate")[0] == "MISSING",
          "the unrecordable run also dropped the earlier pass -- no stale FRESH left behind")

    # A linked worktree: `.git` is a file, the index and evidence are per worktree.
    wt = tmp / "repo-wt"
    git(repo, "worktree", "add", "-q", "-b", "side", str(wt))
    check((wt / ".git").is_file(), "fixture: the linked worktree's .git is a file")
    wt_index = index_bytes(wt)
    quiet_run(wt, "gate", OK_CMD)
    check(ge.check(wt, "gate")[0] == "FRESH", "worktree: record + check -> FRESH")
    check(index_bytes(wt) == wt_index, "worktree: its real index is byte-identical afterwards")
    (wt / "a.txt").write_text("wt edit\n", encoding="utf-8")
    check(ge.check(wt, "gate")[0] == "STALE", "worktree: an edit -> STALE")
    check(ge.check(repo, "gate")[0] == "MISSING", "worktree evidence does not leak into the primary")

    # The gate's own output must reach the caller: under CREATE_NO_WINDOW a
    # child spawned without explicit handles printed nothing at all.
    cli = subprocess.run(
        [PY, str(REPO / "skills" / "_lib" / "gate_evidence.py"), "run", "--label", "cli",
         "--repo", str(repo), "--", PY, "-c", "import sys; print('GATE-OUT'); print('GATE-ERR', file=sys.stderr)"],
        capture_output=True, text=True, timeout=120, creationflags=NO_WINDOW,
    )
    check(cli.returncode == 0 and "GATE-OUT" in cli.stdout and "GATE-ERR" in cli.stderr,
          f"run CLI: the command's stdout/stderr reach the caller (out={cli.stdout!r} err={cli.stderr!r})")
    check(cli.stdout.strip().splitlines()[-1].startswith("EVIDENCE=recorded label=cli"),
          "run CLI: the EVIDENCE line comes last, after the command's output")

    plain = tmp / "not-a-repo"
    plain.mkdir()
    state, detail = ge.check(plain, "gate")
    check(state == "UNKNOWN", f"not a git tree -> UNKNOWN, its own state ({detail})")

    # CLI wiring: exactly one word on stdout, exit codes per state.
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = ge.main(["check", "--label", "gate", "--repo", str(plain)])
    check(code == 2 and out.getvalue().strip() == "UNKNOWN", "check CLI: UNKNOWN exits 2")
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = ge.main(["check", "--label", "gate", "--repo", str(wt)])
    check(code == 1 and out.getvalue().strip() == "STALE", "check CLI: STALE exits 1, one word on stdout")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(repo, ignore_errors=True)

_h.report_and_exit("test_gate_evidence")
