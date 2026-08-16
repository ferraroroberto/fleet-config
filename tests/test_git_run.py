"""Unit tests for skills/_lib/git_run.py (fleet-config#485, #667).

Exercises the shared default-branch resolver against a real throwaway git
repo (upstream + clone, so `origin/HEAD` resolves like it would in a real
fleet repo) -- the same pattern `test_dirty_tree_check.py` uses. Covers the
`symbolic-ref` success path, the candidate-probing fallback, the
terminal-fallback case, and the `candidates=()` shape `dirty_tree_check.py`
depends on to reproduce its own no-probing quirk.

Then `GIT_OPTIONAL_LOCKS=0` (fleet-config#667), against real git rather than a
mock, because the whole change rests on a claim about git's own semantics:

  * a read through `run_git` does not take the optional index lock -- observed
    as `.git/index` not being rewritten when a stat-dirty tree is read, which
    is exactly the write that strands a 0-byte `index.lock` when the process
    dies mid-refresh;
  * the porcelain output is byte-identical with and without the variable, so
    suppressing the lock cannot change any caller's answer;
  * a *write* through the same wrapper still takes the real lock and still
    refuses when one is already held -- the half that would silently break 62
    callers if `GIT_OPTIONAL_LOCKS=0` were the blunt instrument it looks like.
    Driven across `add`, `commit`, `checkout --` and a **real fast-forward
    `pull`** between two local repos, each proven twice: it succeeds under the
    flag, and it is still refused with a lock held. The pull has to really
    merge -- an already-up-to-date pull short-circuits before touching the
    index, which is precisely why the fleet sweep's own `pull --ff-only`
    exited 0 against nine frozen repos and nobody noticed for fifteen days.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_git_run.py`
(also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import git_run  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import make_upstream_and_clone, run_git  # noqa: E402

_h = CheckHarness()
check = _h.check


def _git(cwd: Path, *args: str) -> str:
    return run_git(cwd, *args, check=check)


tmp = Path(tempfile.mkdtemp(prefix="test_git_run_"))
try:
    upstream, work = make_upstream_and_clone(tmp, check)

    # ---- symbolic-ref succeeds: origin/HEAD -> origin/main ----
    ref = git_run.resolve_default_branch_ref(work)
    check(ref == "origin/main", f"symbolic-ref success: expected 'origin/main', got {ref!r}")

    # candidates=() must not change the success path -- only the fallback.
    ref = git_run.resolve_default_branch_ref(work, candidates=(), final_fallback="main")
    check(ref == "origin/main", f"symbolic-ref success ignores candidates=(): got {ref!r}")

    # ---- symbolic-ref fails (no remote at all): probe candidates in order ----
    bare = tmp / "bare_no_remote"
    bare.mkdir()
    _git(bare, "init", "-q")
    _git(bare, "checkout", "-q", "-b", "main")
    _git(bare, "config", "user.email", "35553560+ferraroroberto@users.noreply.github.com")
    _git(bare, "config", "user.name", "Test")
    (bare / "README.md").write_text("hello\n", encoding="utf-8")
    _git(bare, "add", "README.md")
    _git(bare, "commit", "-q", "-m", "initial")

    ref = git_run.resolve_default_branch_ref(bare)
    check(ref == "main", f"no remote, local 'main' exists: expected 'main' (2nd candidate), got {ref!r}")

    # ---- symbolic-ref fails, none of the default candidates exist ----
    _git(bare, "checkout", "-q", "-b", "trunk")
    _git(bare, "branch", "-D", "main")
    ref = git_run.resolve_default_branch_ref(bare)
    check(ref == "main", f"no remote, no main/master: falls to final_fallback 'main', got {ref!r}")

    ref = git_run.resolve_default_branch_ref(bare, final_fallback="develop")
    check(ref == "develop", f"custom final_fallback is honored: got {ref!r}")

    # ---- candidates=() skips probing entirely, even when 'main' exists ----
    _git(bare, "checkout", "-q", "-b", "main")
    ref = git_run.resolve_default_branch_ref(bare, candidates=(), final_fallback="zzz-unprobed")
    check(
        ref == "zzz-unprobed",
        f"candidates=() returns final_fallback even though 'main' exists (no probing occurred): got {ref!r}",
    )

    # ---- run_git_checked: success returns stripped stdout, failure raises SystemExit ----
    sha = git_run.run_git_checked(["-C", str(bare), "rev-parse", "HEAD"])
    check(len(sha) == 40 and sha.strip() == sha,
          f"run_git_checked: success returns the stripped stdout (a 40-char sha), got {sha!r}")

    raised = False
    try:
        git_run.run_git_checked(["-C", str(bare), "rev-parse", "does-not-exist"])
    except SystemExit as exc:
        raised = True
        check("git" in str(exc) and "failed" in str(exc),
              f"run_git_checked: SystemExit message names the failed git command, got {exc!r}")
    check(raised, "run_git_checked: a non-zero exit raises SystemExit instead of returning")

    # ---- git_env: the variable is set, on top of the ambient environment ----
    env = git_run.git_env()
    check(env.get("GIT_OPTIONAL_LOCKS") == "0", "git_env: sets GIT_OPTIONAL_LOCKS=0")
    check(all(k in env for k in os.environ), "git_env: keeps the whole ambient environment")
    check(git_run.git_env({"FOO": "bar"}) == {"FOO": "bar", "GIT_OPTIONAL_LOCKS": "0"},
          "git_env: an explicit base is used verbatim, plus the variable")
    check("GIT_OPTIONAL_LOCKS" not in os.environ,
          "git_env: builds a copy — never mutates this process's own environment")

    # ---- a read through run_git does not take the optional index lock ----
    # Make the index stat-dirty (same content, new mtime) so a plain `git
    # status` *would* want to write the refreshed cache back. `.git/index`
    # being rewritten is the observable proxy for "the optional lock was
    # taken"; that write is what strands a 0-byte lock on a mid-refresh kill.
    lockrepo = tmp / "optional_locks"
    lockrepo.mkdir()
    _git(lockrepo, "init", "-q")
    _git(lockrepo, "config", "user.email", "35553560+ferraroroberto@users.noreply.github.com")
    _git(lockrepo, "config", "user.name", "Test")
    (lockrepo / "a.txt").write_text("hello\n", encoding="utf-8")
    _git(lockrepo, "add", "a.txt")
    _git(lockrepo, "commit", "-q", "-m", "initial")

    index_file = lockrepo / ".git" / "index"
    lock_file = lockrepo / ".git" / "index.lock"

    def _touch_tracked() -> None:
        """Rewrite a.txt with identical content, a second later — a stat
        change with no content change, which is what makes the cache stale."""
        time.sleep(1.1)
        (lockrepo / "a.txt").write_text("hello\n", encoding="utf-8")

    # Baseline: raw git, no suppression -> the index IS rewritten. This is the
    # pre-fix behaviour, asserted here so the test below can't pass vacuously
    # on some future git that stopped refreshing at all.
    _touch_tracked()
    before = index_file.stat().st_mtime_ns
    subprocess.run(["git", "-C", str(lockrepo), "status", "--porcelain"],
                   capture_output=True, text=True, check=True)
    check(index_file.stat().st_mtime_ns != before,
          "baseline: raw `git status` rewrites .git/index (it does take the optional lock)")

    # The fix: the same read through run_git leaves the index untouched.
    _touch_tracked()
    before = index_file.stat().st_mtime_ns
    r = git_run.run_git(["-C", str(lockrepo), "status", "--porcelain"])
    check(r.returncode == 0, f"run_git status succeeds under GIT_OPTIONAL_LOCKS=0 (exit {r.returncode})")
    check(index_file.stat().st_mtime_ns == before,
          "run_git: a read does NOT rewrite .git/index — no optional lock taken (fleet-config#667)")
    check(not lock_file.exists(), "run_git: a read leaves no .git/index.lock behind")

    # ---- output parity: suppression must not change any caller's answer ----
    (lockrepo / "a.txt").write_text("changed\n", encoding="utf-8")
    (lockrepo / "untracked.txt").write_text("new\n", encoding="utf-8")
    raw = subprocess.run(["git", "-C", str(lockrepo), "status", "--porcelain"],
                         capture_output=True, text=True, check=True).stdout
    ours = git_run.run_git(["-C", str(lockrepo), "status", "--porcelain"]).stdout
    check(raw == ours and raw.strip() != "",
          f"porcelain output is identical with and without GIT_OPTIONAL_LOCKS (raw={raw!r} ours={ours!r})")

    # ---- writes still take the REAL lock ----
    # Not asserted in prose: plant a lock, and a write through the same wrapper
    # must still refuse. If GIT_OPTIONAL_LOCKS=0 suppressed this one, every
    # concurrency guarantee under all 62 callers would be gone.
    lock_file.write_bytes(b"")
    blocked = git_run.run_git(["-C", str(lockrepo), "add", "a.txt"])
    check(blocked.returncode != 0 and "index.lock" in (blocked.stderr or ""),
          f"run_git: a WRITE still takes the real index lock and refuses when held "
          f"(exit {blocked.returncode}, stderr {(blocked.stderr or '')[:80]!r})")

    lock_file.unlink()
    added = git_run.run_git(["-C", str(lockrepo), "add", "a.txt"])
    committed = git_run.run_git(["-C", str(lockrepo), "commit", "-q", "-m", "write under no-optional-locks"])
    check(added.returncode == 0 and committed.returncode == 0,
          f"run_git: add+commit succeed under GIT_OPTIONAL_LOCKS=0 "
          f"(add {added.returncode}, commit {committed.returncode}: {(committed.stderr or '')[:80]!r})")
    check(not lock_file.exists(), "run_git: a completed write leaves no stray .git/index.lock")
    check(git_run.run_git_checked(["-C", str(lockrepo), "log", "-1", "--format=%s"])
          == "write under no-optional-locks",
          "run_git: the commit made under GIT_OPTIONAL_LOCKS=0 really landed")

    # `checkout` — the other index-rewriting workhorse, and the one that bit
    # hardest in practice: with a lock held it is frozen, which is why the
    # 2026-08-01 repos could not even discard a local edit.
    git_run.run_git(["-C", str(lockrepo), "checkout", "-q", "-b", "side"])
    (lockrepo / "a.txt").write_text("side edit\n", encoding="utf-8")
    reverted = git_run.run_git(["-C", str(lockrepo), "checkout", "-q", "--", "a.txt"])
    check(reverted.returncode == 0 and (lockrepo / "a.txt").read_text(encoding="utf-8") == "changed\n",
          f"run_git: `checkout --` still rewrites the index under GIT_OPTIONAL_LOCKS=0 "
          f"(exit {reverted.returncode}, file {(lockrepo / 'a.txt').read_text(encoding='utf-8')!r})")

    lock_file.write_bytes(b"")
    (lockrepo / "a.txt").write_text("side edit\n", encoding="utf-8")
    frozen = git_run.run_git(["-C", str(lockrepo), "checkout", "-q", "--", "a.txt"])
    check(frozen.returncode != 0,
          f"run_git: ...and is still refused when a lock is held (exit {frozen.returncode})")
    lock_file.unlink()
    git_run.run_git(["-C", str(lockrepo), "checkout", "-q", "--", "a.txt"])
    git_run.run_git(["-C", str(lockrepo), "checkout", "-q", "master"])

    # `pull` — a real fast-forward between two local repos, no network. An
    # *up-to-date* pull is not a proof: it short-circuits before touching the
    # index (which is exactly why the fleet sweep's `pull --ff-only` also
    # exited 0 against nine frozen repos), so this pull must really merge.
    downstream = tmp / "downstream"
    _git(tmp, "clone", "-q", str(lockrepo), str(downstream))
    _git(downstream, "config", "user.email", "35553560+ferraroroberto@users.noreply.github.com")
    _git(downstream, "config", "user.name", "Test")
    (lockrepo / "b.txt").write_text("upstream commit\n", encoding="utf-8")
    _git(lockrepo, "add", "b.txt")
    _git(lockrepo, "commit", "-q", "-m", "upstream moves")

    pulled = git_run.run_git(["-C", str(downstream), "pull", "--ff-only", "-q"])
    check(pulled.returncode == 0 and (downstream / "b.txt").exists(),
          f"run_git: a real fast-forward `pull` succeeds under GIT_OPTIONAL_LOCKS=0 "
          f"(exit {pulled.returncode}, stderr {(pulled.stderr or '')[:80]!r})")
    check(not (downstream / ".git" / "index.lock").exists(),
          "run_git: the pull released its lock — no stray index.lock afterwards")

    (lockrepo / "c.txt").write_text("another\n", encoding="utf-8")
    _git(lockrepo, "add", "c.txt")
    _git(lockrepo, "commit", "-q", "-m", "upstream moves again")
    (downstream / ".git" / "index.lock").write_bytes(b"")
    blocked_pull = git_run.run_git(["-C", str(downstream), "pull", "--ff-only", "-q"])
    check(blocked_pull.returncode != 0 and not (downstream / "c.txt").exists(),
          f"run_git: a real `pull` is still refused when the index lock is held "
          f"(exit {blocked_pull.returncode}) — GIT_OPTIONAL_LOCKS=0 did NOT weaken it")
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_git_run")
