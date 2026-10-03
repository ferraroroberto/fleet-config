"""Gate evidence bound to a working-tree content hash (fleet-config#957).

Why this exists
----------------
`/issue-finish` skipped the remote CI wait on the phrase "green this session":
the model's recollection that a gate or e2e run passed earlier. Nothing tied
that pass to the content about to merge, and the finish flow itself edits the
tree after the gate (design-drift fix commits, e2e suite upkeep). This helper
runs the gate, binds its exit code to a hash of the whole working tree, and
answers later whether that result still describes the tree.

Subcommands
-----------
  run --label <L> [--repo <path>] -- <command argv...>
        Hashes the tree, runs the command itself (inheriting stdout/stderr,
        so the exit code is observed rather than reported), hashes again, and
        records ``{cmd, cmd_sha256, exit, wtree, utc}`` for ``L`` only when
        both hashes agree. A tree that changed mid-run, or a hash that could
        not be computed, drops any earlier record for ``L`` -- never leaves a
        stale pass behind. Exits with the command's own exit code; the last
        stdout line is ``EVIDENCE=recorded ...`` or ``EVIDENCE=not_recorded
        reason=...``.

  check --label <L> [--repo <path>] [--expect-cmd "<command>"]
        Prints exactly one of:
          FRESH     a recorded exit-0 run of ``L`` matches the current tree (exit 0)
          STALE     one exists, but the tree changed since (exit 1)
          MISSING   no recorded passing run, or ``--expect-cmd`` differs (exit 1)
          UNKNOWN   the tree hash could not be computed (exit 2)
        STALE names the changed paths on stderr.

Tree hash = the content of the whole working tree, untracked-but-not-ignored
files included, without touching the real index: copy the index (resolved via
``rev-parse --git-path index``, so a linked worktree works) to a temp file,
point ``GIT_INDEX_FILE`` at it, ``git add -A``, ``git write-tree``, delete the
copy (mtime kept, see the comment in ``tree_hash``). Committing identical content therefore stays FRESH; any edit or new
untracked file goes STALE. Evidence lives at ``rev-parse --git-path
gate-evidence.json``, outside the working tree (writing it must not change
the hash it records) and per worktree. Ignored files (``.env``, ``.venv``)
stay out because nothing here ever passes ``-f``.

stdlib only. Pure-ish logic is exercised end to end against temp repos in
``tests/test_gate_evidence.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from git_run import run_git  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402
from utf8_stdio import ensure_utf8_stdio  # noqa: E402

EVIDENCE_NAME = "gate-evidence.json"

# `git add -A` over a huge untracked tree must not hang a finish: past this it
# reports UNKNOWN, which consumers treat like STALE (re-run / watch CI).
HASH_TIMEOUT_SECONDS = 120


def _cmd_text(argv: List[str]) -> str:
    return " ".join(argv).strip()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def git_path(repo: Path, name: str) -> Optional[Path]:
    """``rev-parse --git-path <name>`` resolved to an absolute path, or ``None``."""
    try:
        res = run_git(["-C", str(repo), "rev-parse", "--git-path", name], timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0 or not res.stdout.strip():
        return None
    path = Path(res.stdout.strip())
    return path if path.is_absolute() else repo / path


def tree_hash(repo: Path) -> Tuple[Optional[str], str]:
    """``(tree_sha, "")`` for the whole working tree, or ``(None, reason)``."""
    index = git_path(repo, "index")
    if index is None:
        return None, "not a git working tree"
    fd, tmp_name = tempfile.mkstemp(prefix="gate_evidence_index_")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        if index.exists():
            # copy2, not copyfile: git decides an entry is "racily clean" (its content must be re-read, its stat
            # cannot be trusted) by comparing the entry's mtime to the INDEX FILE's mtime, at one-second
            # resolution on Git for Windows. A copy stamped "now" erases that, so a same-size rewrite in the
            # same second as the last index write read as unchanged and FRESH (fleet-config#1192).
            shutil.copy2(index, tmp)
        else:
            tmp.unlink()  # a fresh repo has no index yet; git creates the temp one
        env = {"GIT_INDEX_FILE": str(tmp)}
        try:
            added = run_git(["-C", str(repo), "add", "-A"], timeout=HASH_TIMEOUT_SECONDS, extra_env=env)
            if added.returncode != 0:
                return None, "git add -A failed: " + (added.stderr.strip().splitlines() or ["?"])[0][:200]
            tree = run_git(["-C", str(repo), "write-tree"], timeout=HASH_TIMEOUT_SECONDS, extra_env=env)
        except subprocess.TimeoutExpired:
            return None, f"hashing the tree took over {HASH_TIMEOUT_SECONDS}s"
        except (OSError, subprocess.SubprocessError) as exc:
            return None, f"git failed: {type(exc).__name__}"
        if tree.returncode != 0 or not tree.stdout.strip():
            return None, "git write-tree failed: " + (tree.stderr.strip().splitlines() or ["?"])[0][:200]
        return tree.stdout.strip(), ""
    finally:
        for leftover in (tmp, Path(str(tmp) + ".lock")):
            try:
                leftover.unlink()
            except OSError:
                pass


def changed_paths(repo: Path, old: str, new: str, limit: int = 10) -> List[str]:
    """Paths that differ between two tree objects (best-effort, capped)."""
    try:
        res = run_git(["-C", str(repo), "diff-tree", "-r", "--name-only", old, new], timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    paths = [p for p in res.stdout.splitlines() if p.strip()] if res.returncode == 0 else []
    return paths[:limit] + ([f"… {len(paths) - limit} more"] if len(paths) > limit else [])


def _load(path: Path) -> Dict[str, dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _store(path: Path, data: Dict[str, dict]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _handle(stream) -> Optional[int]:
    """The OS file descriptor behind ``stream``, or ``None`` (a StringIO)."""
    try:
        return stream.fileno()
    except (AttributeError, OSError, ValueError):
        return None


def run(repo: Path, label: str, argv: List[str]) -> Tuple[int, str]:
    """Run ``argv`` in ``repo``; ``(exit_code, EVIDENCE=... line)``."""
    before, reason = tree_hash(repo)
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        # Explicit handles: with CREATE_NO_WINDOW and none passed, a console
        # child loses the inherited stdout/stderr and the gate's own FAIL
        # lines vanish while its exit code still arrives.
        proc = subprocess.run(argv, cwd=str(repo), creationflags=NO_WINDOW,
                              stdout=_handle(sys.stdout), stderr=_handle(sys.stderr))
        code = proc.returncode
    except OSError as exc:
        print(f"gate_evidence: could not start {argv[0]!r}: {exc}", file=sys.stderr)
        code = 127
    after, after_reason = tree_hash(repo) if before else (None, reason)

    evidence_path = git_path(repo, EVIDENCE_NAME)
    if evidence_path is None:
        return code, "EVIDENCE=not_recorded reason=not a git working tree"
    data = _load(evidence_path)
    if before is None or after is None:
        why = reason or after_reason
    elif before != after:
        why = "tree changed during the run: " + ", ".join(changed_paths(repo, before, after))
    else:
        why = ""
    if why:
        data.pop(label, None)
        _store(evidence_path, data)
        return code, f"EVIDENCE=not_recorded label={label} reason={why}"
    cmd = _cmd_text(argv)
    data[label] = {"cmd": cmd, "cmd_sha256": _sha(cmd), "exit": code, "wtree": after,
                   "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    _store(evidence_path, data)
    return code, f"EVIDENCE=recorded label={label} exit={code} wtree={after[:12]}"


def check(repo: Path, label: str, expect_cmd: Optional[str] = None) -> Tuple[str, str]:
    """``(FRESH|STALE|MISSING|UNKNOWN, detail)`` for ``label`` against the current tree."""
    current, reason = tree_hash(repo)
    if current is None:
        return "UNKNOWN", reason
    evidence_path = git_path(repo, EVIDENCE_NAME)
    record = _load(evidence_path).get(label) if evidence_path else None
    if not isinstance(record, dict) or record.get("exit") != 0:
        return "MISSING", "no recorded passing run for " + label
    if expect_cmd is not None and record.get("cmd_sha256") != _sha(expect_cmd.strip()):
        return "MISSING", f"recorded command differs: {record.get('cmd')!r}"
    if record.get("wtree") != current:
        paths = changed_paths(repo, str(record.get("wtree")), current)
        return "STALE", "changed since the recorded run: " + (", ".join(paths) or "unknown paths")
    return "FRESH", f"recorded {record.get('utc')}: {record.get('cmd')}"


_CHECK_EXIT = {"FRESH": 0, "STALE": 1, "MISSING": 1, "UNKNOWN": 2}


def main(argv: Optional[List[str]] = None) -> int:
    ensure_utf8_stdio()
    raw = list(sys.argv[1:] if argv is None else argv)
    command: List[str] = []
    if "--" in raw:
        split = raw.index("--")
        raw, command = raw[:split], raw[split + 1:]
    parser = argparse.ArgumentParser(description="Gate evidence bound to a tree hash (fleet-config#957).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run")
    run_p.add_argument("--label", required=True)
    run_p.add_argument("--repo", type=Path, default=Path.cwd())
    chk = sub.add_parser("check")
    chk.add_argument("--label", required=True)
    chk.add_argument("--repo", type=Path, default=Path.cwd())
    chk.add_argument("--expect-cmd")
    args = parser.parse_args(raw)
    repo = args.repo.resolve()

    if args.cmd == "run":
        if not command:
            parser.error("run needs a command after --")
        code, line = run(repo, args.label, command)
        sys.stdout.flush()
        print(line, flush=True)
        return code
    state, detail = check(repo, args.label, args.expect_cmd)
    print(state)
    print(detail, file=sys.stderr)
    return _CHECK_EXIT[state]


if __name__ == "__main__":
    sys.exit(main())
