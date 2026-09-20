"""SessionStart hook — report any fleet repo sitting on a stranded
``.git/index.lock`` before the session does anything (fleet-config#939).

Why a session-start tripwire at all
-----------------------------------
A stale lock is **invisible to a read**. ``git status --porcelain``,
``git fetch``, ``git rev-list`` and an already-up-to-date
``git pull --ff-only`` all exit 0 with correct output while the repo cannot
``add``, ``commit``, ``pull`` or ``stash``. That is the whole reason
fleet-config#667 went unnoticed for **fifteen days** across nine repos, and
why the 2026-09-20 recurrence — 23 repos locked at 13:57:41 — still did real
damage in its first thirty minutes: two ``git merge --ff-only`` calls printed
``Updating <old>..<new>`` and *failed*, the next command in the chain read the
old HEAD, and two tray restarts then verified and reported the **old**
``git_sha`` as live. For twenty minutes the operator believed two merges were
deployed when they were not.

``/audit-fleet``'s weekly ``stale_lock`` bucket is the right *report* and the
wrong *cadence*: a week of invisible breakage is most of a #667. This hook is
the short-interval half — it fires at the one moment that reliably precedes an
agent writing to a repo, which is an agent session starting.

Why it sweeps the whole fleet, not just this session's repo
-----------------------------------------------------------
The incident's own shape rules the narrow version out. A fleet-wide sweep is
what strands these, so the locked set is fleet-wide — and on 2026-09-20
``fleet-config`` was the *one* repo left clean, which is exactly where chief
was cwd'd. A cwd-only tripwire would have reported all-clear through the
entire incident. The sweep is affordable precisely because the common answer
is "nothing": one ``stat()`` per repo, ~40 of them, no subprocess at all
unless something is actually found.

What it claims, and what it deliberately does not
--------------------------------------------------
It reports **present and past the threshold** — a fact it establishes itself
from the filesystem. It does *not* claim ``skills/_lib/index_lock.py``'s
``stale`` vs ``stale_unconfirmed`` distinction, because that rests on a
``tasklist`` probe this hook has no business running at session start. So the
nudge names the repos and points at that module's CLI for the authoritative
verdict, rather than folding an unestablished fact into a confident one.

**Report only. Never delete.** Same standing rule as ``index_lock.py``: a lock
is another process's file, and nothing removes one on agent authority. The
2026-09-20 clear-out was 22 removals on the owner's explicit instruction, each
re-verified 0-byte and ``stale`` with no ``git`` process running.

Wired by the ``SessionStart`` hook in ``settings.template.json``.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402

# Hooks-tier copy of ``skills/_lib/index_lock.STALE_AFTER_SECONDS``, the same
# way :data:`_lib.NO_WINDOW` copies ``skills/_lib/no_window`` — the two trees
# are junctioned into the agent homes independently, so a hook must stay
# importable with nothing but its own directory on ``sys.path``.
# ``tests/run_acceptance.py`` asserts the two constants agree.
STALE_AFTER_SECONDS = 900

# A nudge nobody reads is a guard that only reports. Name enough repos to act
# on and count the rest, rather than pasting forty paths into every session.
MAX_NAMED = 12


def lock_path(repo: Path) -> Optional[Path]:
    """Where *this* checkout's ``index.lock`` would live, or ``None``.

    Deliberately filesystem-only, where ``index_lock.index_lock_path`` falls
    back to ``git rev-parse --absolute-git-dir``. That fallback exists to
    handle a path *inside* a repo, which this sweep never passes — it walks
    known checkout roots — and one ``git`` spawn per repo across a ~40-repo
    sweep, on every session start, is exactly the cost that would make this
    tripwire too expensive to keep.

    A linked worktree's ``.git`` is a **file** holding ``gitdir: <path>``, and
    its index lives under that path (``.git/worktrees/<name>/``), not in the
    primary's git dir — reading the pointer is what keeps a lane's own
    worktree covered without reporting the primary's lock against it.
    """
    dot_git = repo / ".git"
    if dot_git.is_dir():
        return dot_git / "index.lock"
    try:
        first = dot_git.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except OSError:
        return None
    for line in first:
        if line.startswith("gitdir:"):
            return Path(line.split(":", 1)[1].strip()) / "index.lock"
    return None


def held_locks(
    repos: Sequence[Tuple[str, Path]],
    now: Optional[float] = None,
    stale_after: float = STALE_AFTER_SECONDS,
) -> List[Tuple[str, Path, Optional[float]]]:
    """``[(name, lock_path, age_seconds)]`` for every repo holding a lock that
    is past ``stale_after`` — or whose age could not be read at all.

    An unstattable lock yields ``age_seconds=None`` and is **reported**, not
    dropped: the file exists, so the question "is this stranded?" was asked
    and went unanswered, which is the one thing this repo never folds into a
    clean bill of health. A lock younger than the threshold is silently
    skipped — some git operation is legitimately in flight, an established
    fact rather than an unknown.
    """
    now = time.time() if now is None else now
    found: List[Tuple[str, Path, Optional[float]]] = []
    for name, repo in repos:
        try:
            lock = lock_path(Path(repo))
        except OSError:
            continue  # unreachable repo (offline junction, permissions) — fail open
        if lock is None:
            continue
        try:
            age: Optional[float] = max(0.0, now - lock.stat().st_mtime)
        except FileNotFoundError:
            continue  # the overwhelmingly common answer, and the cheap one
        except OSError:
            age = None  # exists enough to fail a stat; never assumed young
        if age is None or age >= stale_after:
            found.append((name, lock, age))
    return found


def build_context(found: Sequence[Tuple[str, Path, Optional[float]]]) -> str:
    """The nudge for a non-empty sweep."""
    lines = []
    for name, lock, age in sorted(found)[:MAX_NAMED]:
        when = "age unreadable" if age is None else f"{age / 3600:.1f}h old"
        lines.append(f"  - {name}: {lock} ({when})")
    if len(found) > MAX_NAMED:
        lines.append(f"  - … and {len(found) - MAX_NAMED} more")
    plural = "repo holds" if len(found) == 1 else "repos hold"
    return (
        f"⚠️ Stranded git index lock (fleet-config#939): {len(found)} fleet {plural} a "
        f"`.git/index.lock` older than {STALE_AFTER_SECONDS // 60} minutes.\n\n"
        + "\n".join(lines)
        + "\n\nThis is invisible to every read: `git status`, `git fetch`, `git rev-list` "
        "and an up-to-date `git pull --ff-only` all still exit 0 with correct output, "
        "while `add`/`commit`/`pull`/`stash` refuse. A `git merge --ff-only` against one "
        "prints `Updating <old>..<new>` and *fails*, so the next command in the chain "
        "reads the old HEAD — that is how two merges were believed deployed for twenty "
        "minutes on 2026-09-20.\n"
        "Confirm before acting: `skills/_lib/index_lock.py <repo>` gives the authoritative "
        "verdict (`stale` vs `stale_unconfirmed`) — this sweep only establishes that the "
        "file is present and old.\n"
        "**Never delete one on your own authority** — it is another process's file. Report "
        "it and let the owner decide."
    )


def sweep_targets(session_cwd: Path) -> List[Tuple[str, Path]]:
    """``[(name, repo_dir)]`` — the fleet from ``projects.toml``, plus this
    session's own checkout.

    The session's own root is added explicitly because a lane works in a
    sibling ``<repo>-wt-<N>`` worktree, which by design is *not* a
    ``projects.toml`` member (it would double-count the repo it belongs to,
    per ``fleet_repo_scan.is_linked_worktree``) — and a lane's own tree is the
    one whose lock bites it first.
    """
    targets: List[Tuple[str, Path]] = []
    seen: set = set()

    def add(name: str, path: Path) -> None:
        key = str(path).replace("\\", "/").rstrip("/").lower()
        if key not in seen:
            seen.add(key)
            targets.append((name, path))

    try:
        for project in _lib.load_registry().projects:
            add(project.name, Path(project.cwd_prefix))
    except Exception:  # pragma: no cover - a malformed registry must not break startup
        pass

    for candidate in [session_cwd, *session_cwd.parents]:
        if (candidate / ".git").exists():
            add(candidate.name, candidate)
            break
    return targets


def main() -> int:
    payload = _lib.read_stdin_json()
    try:
        found = held_locks(sweep_targets(_lib.cwd(payload)))
    except Exception:  # pragma: no cover - a tripwire must never block a session
        return 0
    if not found:
        return 0
    # `_lib.warn()` owns the per-harness SessionStart dialect (fleet-config#818):
    # Claude gets the hookSpecificOutput.additionalContext envelope, a foreign
    # harness the plain-stdout form it actually reads.
    _lib.warn(build_context(found))


if __name__ == "__main__":
    sys.exit(main())
