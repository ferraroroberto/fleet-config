"""Detect a stranded `.git/index.lock` and report it as its own named state.

Why this exists
---------------
On 2026-08-01 a single crashed fleet-wide operation left a **0-byte**
`.git/index.lock` in nine repos, all stamped the same second. Every one of
those repos was then silently unable to `git add`, `git commit`, `git pull` or
`git stash` — for **fifteen days**, until a lane happened to trip over one
(fleet-config#667).

Fifteen days is the interesting number, and it is not a coverage gap: the
2026-08-13 fleet sweep enumerated all 39 repos, visited all nine, and filed
every one of them as a healthy `below_threshold`. Zero errors, zero
`UNKNOWN`s. It did that because **a stale lock is invisible to a read**:

    git status --porcelain   -> exit 0, correct output   (optional lock skipped)
    git fetch origin         -> exit 0                   (doesn't touch the index)
    git pull --ff-only       -> exit 0, "Already up to date."
    git rev-list --count     -> exit 0

Nothing the gate runs writes to the index, so nothing the gate runs fails.
`#570`'s raise-on-non-zero — the guard that stops an empty porcelain reading
as clean — never fires, because the command genuinely succeeded. The lock only
bites the *next* write, which for an up-to-date repo may be weeks away.

`git_run.git_env` stops this repo's own helpers creating such locks. This
module is the other half: finding the ones that already exist, anywhere, from
any cause.

**Report only. Never delete.** Deleting another process's lock is precisely
the destructive move `email-archiver`'s lane was right to refuse on chief
authority, and this repo's standing rule is that an unestablished fact gets
its own state rather than a silent repair.

The five verdicts
-----------------
- `absent` — no lock file. Nothing to say.
- `fresh` — a lock younger than `STALE_AFTER_SECONDS`. An established fact,
  not an unknown: some git operation is legitimately in flight. Callers skip
  the repo (its readings are unreliable mid-write) rather than report it.
- `stale` — older than the threshold **and** no `git` process is running on
  this machine at all. High confidence; this is the 2026-08-01 shape.
- `stale_unconfirmed` — older than the threshold, but the process probe could
  not establish that nobody holds it (a `git` is running somewhere, or the
  probe itself failed). Deliberately **not** folded into either `stale` or
  `fresh`: past the threshold something is wrong regardless, and which of the
  two it is has not been established.
- `unreadable` — the git dir could not be resolved, so the question was never
  answered. Also never folded into `absent`; an unasked question is not a
  clean bill of health.

Note the reported/not-reported split does not hinge on the fragile part: a
lock past the threshold is surfaced whether or not the process probe worked.

Pure/impure split, the way `audit_issue.is_fleet_repo` does it: `classify()`
takes plain values and is unit-tested with no filesystem or git anywhere near
it (`tests/test_index_lock.py`); `inspect()` is the I/O around it.

CLI
---
  index_lock.py <repo-path> [--stale-after SECONDS]

prints `INDEX_LOCK=<verdict>` plus `PATH=`/`AGE_SECONDS=`/`SIZE=`/`DETAIL=`.
Always exits 0 — this helper reports, it never blocks.

stdlib only (plus the sibling `no_window` / `git_run` modules).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import git_run  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402

# 15 minutes. Well past any plausible single git operation on this fleet
# (the 2026-08-01 locks had sat for fifteen *days*), and far enough from the
# millisecond-scale optional lock that a concurrent `git status` in another
# tool can't race us into a false report.
STALE_AFTER_SECONDS = 900

# Reported by callers; `absent`/`fresh` are the proceed-normally verdicts.
REPORTABLE_VERDICTS = ("stale", "stale_unconfirmed")


def classify(
    present: bool,
    age_seconds: Optional[float],
    git_running: Optional[bool],
    stale_after: float = STALE_AFTER_SECONDS,
) -> str:
    """The verdict, from plain values. No I/O — see the module docstring.

    `age_seconds is None` means the lock exists but its age could not be read
    (a stat race, a permissions oddity): that is an unestablished fact about a
    lock that definitely exists, so it reports as `stale_unconfirmed` rather
    than being assumed young.

    `git_running is None` means the process probe could not establish an
    answer; it only ever downgrades `stale` to `stale_unconfirmed`, never
    suppresses a report.
    """
    if not present:
        return "absent"
    if age_seconds is None:
        return "stale_unconfirmed"
    if age_seconds < stale_after:
        return "fresh"
    return "stale" if git_running is False else "stale_unconfirmed"


def git_processes_running() -> Optional[bool]:
    """Is any `git` process alive on this machine? `None` = couldn't establish.

    Coarse by design — Windows gives no cheap way to learn *which* process
    holds a given file, and "no `git.exe` anywhere on the box" is exactly the
    evidence the 2026-08-01 sweep used before clearing anything by hand.

    `encoding="oem"` is load-bearing, not cosmetic: `tasklist` emits the OEM
    code page (cp850 here), and this repo's skills run under `PYTHONUTF8=1`,
    where a plain `text=True` decodes the child as UTF-8 and hands back an
    empty string instead of raising — a dead query indistinguishable from a
    quiet system (the global `CLAUDE.md` gotcha; `app-launcher#743`). It
    reproduces only inside the app: from a terminal there is no `PYTHONUTF8`
    and identical code looks healthy.

    A failure returns `None` **and writes a breadcrumb to stderr** — a silent
    `None` is the whole reason that class of bug hides for weeks.
    """
    if sys.platform != "win32":
        # No portable equivalent worth hand-rolling for a Windows-only fleet.
        # Honest `None` (-> `stale_unconfirmed`) beats a guess.
        return None
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq git.exe", "/NH", "/FO", "CSV"],
            capture_output=True, encoding="oem", errors="replace",
            timeout=20, creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        sys.stderr.write(f"index_lock: tasklist probe failed ({type(exc).__name__}: {exc})\n")
        return None
    out = proc.stdout or ""
    if proc.returncode != 0 and not out.strip():
        sys.stderr.write(f"index_lock: tasklist probe exit {proc.returncode}, no output\n")
        return None
    if "git.exe" in out.lower():
        return True
    # `tasklist` prints an INFO line rather than nothing when a filter matches
    # no process; anything else means we did not actually learn the answer.
    if "no tasks" in out.lower():
        return False
    sys.stderr.write(f"index_lock: tasklist probe output not understood: {out.strip()[:120]!r}\n")
    return None


def index_lock_path(repo_path: Path) -> Optional[Path]:
    """Where *this* checkout's `index.lock` would live, or `None` if unknown.

    An ordinary checkout answers this from a single `is_dir()` — worth the
    special case because the fleet sweep calls this once per repo and a
    subprocess per repo, to learn something the directory layout already
    states, is 39 spawns a week for nothing.

    Everything else (a linked worktree, where `.git` is a *file*; a
    subdirectory of a repo; anything unusual) falls through to git. Then
    `--absolute-git-dir`, deliberately not `--git-common-dir`: a linked
    worktree keeps its own index under `.git/worktrees/<name>/`, and pointing
    at the shared common dir would read the primary's lock while reporting on
    the worktree.
    """
    dot_git = Path(repo_path) / ".git"
    if dot_git.is_dir():
        return dot_git / "index.lock"
    res = git_run.run_git(["-C", str(repo_path), "rev-parse", "--absolute-git-dir"])
    out = (res.stdout or "").strip()
    if res.returncode != 0 or not out:
        return None
    return Path(out) / "index.lock"


def inspect(repo_path: Path, stale_after: float = STALE_AFTER_SECONDS) -> dict:
    """`{verdict, path, age_seconds, size, detail}` for one checkout.

    The process probe is only run when a lock is actually present and already
    past `stale_after` — spawning `tasklist` once per repo across a 39-repo
    sweep, to answer a question that only matters for the handful that are
    locked, would be pure cost.
    """
    lock = index_lock_path(Path(repo_path))
    if lock is None:
        return {
            "verdict": "unreadable", "path": None, "age_seconds": None, "size": None,
            "detail": "could not resolve the git dir (rev-parse --absolute-git-dir failed)",
        }

    try:
        st = lock.stat()
    except FileNotFoundError:
        return {"verdict": "absent", "path": str(lock), "age_seconds": None, "size": None, "detail": None}
    except OSError as exc:
        # The lock exists enough to fail a stat. Not `absent`, not assumed young.
        sys.stderr.write(f"index_lock: stat({lock}) failed ({type(exc).__name__}: {exc})\n")
        return {
            "verdict": classify(True, None, None, stale_after),
            "path": str(lock), "age_seconds": None, "size": None,
            "detail": f"lock present but unstattable: {type(exc).__name__}: {exc}",
        }

    age = max(0.0, time.time() - st.st_mtime)
    git_running = git_processes_running() if age >= stale_after else None
    verdict = classify(True, age, git_running, stale_after)
    return {
        "verdict": verdict,
        "path": str(lock),
        "age_seconds": round(age, 1),
        "size": st.st_size,
        "detail": _detail(verdict, age, st.st_size, git_running),
    }


def _detail(verdict: str, age: float, size: int, git_running: Optional[bool]) -> Optional[str]:
    if verdict == "fresh":
        return f"{size}-byte lock {age:.0f}s old — a git operation is in flight"
    if verdict == "stale":
        return f"{size}-byte lock {age / 3600:.1f}h old, no git process running on this machine"
    if verdict == "stale_unconfirmed":
        why = "a git process is running somewhere" if git_running else "the process probe could not establish an answer"
        return f"{size}-byte lock {age / 3600:.1f}h old, but {why}"
    return None


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="Report a stranded .git/index.lock. Never deletes.")
    ap.add_argument("repo", type=Path)
    ap.add_argument("--stale-after", type=float, default=STALE_AFTER_SECONDS,
                    help=f"seconds before a held lock is reported (default {STALE_AFTER_SECONDS})")
    args = ap.parse_args(argv)

    info = inspect(args.repo, args.stale_after)
    print(f"INDEX_LOCK={info['verdict']}")
    print(f"PATH={info['path'] or ''}")
    print(f"AGE_SECONDS={'' if info['age_seconds'] is None else info['age_seconds']}")
    print(f"SIZE={'' if info['size'] is None else info['size']}")
    print(f"DETAIL={info['detail'] or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
