"""Selection: which files a repo/transcripts-tree/runtime-data leg backs up.

Three layers (git-ignored set, deny-list + size cap, bulk-directory guard) --
see the package docstring in `__init__.py` for the full rationale. Split out
of `hooks/backup_private.py` (fleet-config#731). Needs `snapshot.py` for the
runtime-data leg's SQLite side (`snapshot_sqlite`, `is_sqlite_sidecar`):
selecting a database *is* snapshotting it, via SQLite's online backup API,
because a live WAL-mode file cannot be enumerated as an ordinary byte copy.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import sqlite3
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _lib  # noqa: E402

from .config import BackupConfig, DB_SUFFIXES, RepoOverrides
from .snapshot import is_sqlite_sidecar, snapshot_sqlite

logger = logging.getLogger("backup_private")


def is_reparse_point(path: Path) -> bool:
    """True if `path` is a symlink **or a Windows junction**.

    The distinction is the whole point (#590's "never follow junctions" constraint,
    and the `_strip_junction` history in `skills/_lib/worktree_claim.py`): on
    Windows, `Path.is_symlink()` and `os.path.islink()` both return **False** for a
    directory junction, so a symlink-only guard descends into every sibling
    worktree's junctioned `.venv` and backs up the primary checkout's site-packages.
    Verified against a live `-wt-<N>` worktree while building this.

    Fails **closed**: a path we cannot stat is treated as a reparse point and
    skipped, because "unreadable" is not "safe to descend".
    """
    try:
        st = os.lstat(path)
    except OSError:
        return True
    if sys.platform == "win32":
        return bool(getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    return stat.S_ISLNK(st.st_mode)


@dataclass
class Selection:
    """What one source group (a repo, or the transcripts tree) contributes."""

    files: List[Tuple[Path, str, int]] = field(default_factory=list)  # abs, rel, size
    bulk_excluded: List[Dict[str, Any]] = field(default_factory=list)
    oversize: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    # Source files that existed at enumeration and were gone by the time we read
    # them. Its own state, deliberately — see `write_group`.
    vanished: List[str] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(size for _, _, size in self.files)


def _denied(rel: str, cfg: BackupConfig, overrides: Optional["RepoOverrides"] = None) -> bool:
    """True if any path segment is a denied directory or the name matches a deny glob.

    `overrides.include_globs`, when given, exempts a basename match from both
    checks — the repo-scoped opt-back-in for an otherwise-denied name, e.g. a
    `*.log` file that is actually security-relevant automation state
    (fleet-config#722).
    """
    parts = rel.rstrip("/").split("/")
    name = parts[-1].lower()
    if overrides is not None and overrides.include_globs and \
            any(fnmatch.fnmatch(name, glob.lower()) for glob in overrides.include_globs):
        return False
    # Case-insensitively, because NTFS is: a `.VENV` created by some other tool
    # is the same directory the deny-list means to exclude.
    if any(part.lower() in cfg._deny_dirs_lower for part in parts):
        return True
    return any(fnmatch.fnmatch(name, glob) for glob in cfg.deny_globs)


def _matches_any(rel: str, globs: Sequence[str]) -> bool:
    """True if `rel` matches any of `globs` — as a glob or as a directory prefix.

    A human writing `backup_exclude` reaches for `_local/vm/**` and for `_local/vm`
    interchangeably; both work here so a wrong guess is not a silent no-op.
    """
    rel_l = rel.lower()
    for glob in globs:
        g = glob.replace("\\", "/").lower().rstrip("/")
        if fnmatch.fnmatch(rel_l, g) or fnmatch.fnmatch(rel_l, g + "/*") or rel_l == g \
                or rel_l.startswith(g + "/"):
            return True
    return False


def walk_files(base: Path, rel_prefix: str, cfg: BackupConfig, errors: List[str],
               vanished: Optional[List[str]] = None,
               overrides: Optional["RepoOverrides"] = None) -> Iterator[Tuple[Path, str, int]]:
    """Yield `(abs_path, rel_path, size)` under `base`, never crossing a reparse point.

    A path that disappears mid-walk lands in `vanished` rather than `errors`, for
    the same reason as in `write_group`: live apps rotate their own artifacts.
    """
    vanished = vanished if vanished is not None else []
    stack = [(base, rel_prefix)]
    while stack:
        current, prefix = stack.pop()
        try:
            entries = list(os.scandir(current))
        except FileNotFoundError:
            vanished.append(str(current))
            continue
        except OSError as exc:
            errors.append(f"scandir {current}: {exc}")
            continue
        for entry in entries:
            entry_path = Path(entry.path)
            rel = f"{prefix}{entry.name}"
            if is_reparse_point(entry_path):
                continue
            if _denied(rel, cfg, overrides):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append((entry_path, rel + "/"))
                elif entry.is_file(follow_symlinks=False):
                    yield entry_path, rel, entry.stat().st_size
            except FileNotFoundError:
                vanished.append(rel)
            except OSError as exc:
                errors.append(f"stat {entry_path}: {exc}")


def _forced_include(rel: str, overrides: RepoOverrides) -> bool:
    """True if `rel` is exempted from the size cap or the deny-list by name.

    Such a file is also exempt from the bulk-directory guard (fleet-config#722):
    the whole point of `backup_always_include`/`backup_include_globs` is "back
    this up no matter what", so a sibling's bulk crossing must not undo it, and
    its own bytes must not count toward pushing that sibling over the threshold.
    """
    if _matches_any(rel, overrides.always_include):
        return True
    if not overrides.include_globs:
        return False
    name = rel.rstrip("/").split("/")[-1].lower()
    return any(fnmatch.fnmatch(name, glob.lower()) for glob in overrides.include_globs)


def _apply_bulk_guard(candidates: List[Tuple[Path, str, int]], cfg: BackupConfig,
                      overrides: RepoOverrides) -> Tuple[List[Tuple[Path, str, int]],
                                                         List[Dict[str, Any]]]:
    """Drop every top-level directory whose surviving subtree exceeds `bulk_dir_mb`.

    Top-level and one threshold, deliberately (see the module docstring): the rule
    an operator has to hold in their head is "a first-level directory that is big
    is out, and the report tells you which". Files sitting directly at the repo
    root are never subject to it — they are `.env`, `config.json`, and friends,
    which is exactly what this exists to save.

    `backup_include` re-admits a directory permanently regardless of size.
    `_forced_include` (backup_always_include / backup_include_globs) re-admits a
    single file, and is excluded from its siblings' bulk-size total.
    """
    by_top: Dict[str, List[Tuple[Path, str, int]]] = {}
    root_files: List[Tuple[Path, str, int]] = []
    forced: List[Tuple[Path, str, int]] = []
    for item in candidates:
        rel = item[1]
        if _forced_include(rel, overrides):
            forced.append(item)
        elif "/" in rel:
            by_top.setdefault(rel.split("/", 1)[0], []).append(item)
        else:
            root_files.append(item)

    kept = list(root_files) + forced
    excluded: List[Dict[str, Any]] = []
    for top, items in sorted(by_top.items()):
        total_bytes = sum(size for _, _, size in items)
        if total_bytes > cfg.bulk_dir_bytes and not _matches_any(top, overrides.include):
            excluded.append({
                "path": top + "/",
                "bytes": total_bytes,
                "mb": round(total_bytes / 1024 / 1024, 2),
                "files": len(items),
                "reason": "bulk-dir-guard",
            })
            continue
        kept.extend(items)
    return kept, excluded


def _dedupe_by_rel(items: List[Tuple[Path, str, int]]) -> List[Tuple[Path, str, int]]:
    """Collapse repeated relative paths, keeping the first occurrence.

    `git ls-files --directory` emits **overlapping** entries: a partially-ignored
    directory yields its individual ignored files *and* a separate collapsed entry
    for any wholly-ignored subdirectory beneath it, so walking every entry reaches
    the same file more than once. Left in, the duplicates inflate every count and —
    because the first write of a snapshot copies but the second must hardlink
    against a target that now exists — turn the *second* day's run into 516 failures
    across 11 repos while the first day looks clean (measured 2026-08-11;
    `shutil.copy2` overwrites silently, `os.link` does not).
    """
    seen = set()
    out = []
    for item in items:
        if item[1] in seen:
            continue
        seen.add(item[1])
        out.append(item)
    return out


def git_ignored_entries(repo_dir: Path) -> List[str]:
    """The repo's ignored paths, straight from git.

    `-z` because a filename may contain a newline; `core.quotePath=false` because
    git would otherwise C-escape every non-ASCII name and we would snapshot a path
    that does not exist. `--directory` collapses a wholly-ignored directory into a
    single `dir/` entry, which is what keeps this fast on a repo with a 40k-file
    `node_modules`.

    Via `_lib.run_git` (fleet-config#677): this walks *every repo in the fleet*
    on the nightly backup, so a hand-rolled spawn would strand a 0-byte
    `index.lock` in any of them if the run were killed mid-refresh — the exact
    failure `GIT_OPTIONAL_LOCKS=0` exists to prevent (fleet-config#667).
    """
    result = _lib.run_git(
        ["-c", "core.quotePath=false", "-C", str(repo_dir), "ls-files",
         "--others", "--ignored", "--exclude-standard", "--directory", "-z"],
    )
    if result.returncode != 0:
        raise OSError(f"git ls-files failed in {repo_dir}: {result.stderr.strip()}")
    return [entry for entry in result.stdout.split("\0") if entry.strip()]


def select_repo(repo_dir: Path, cfg: BackupConfig, overrides: RepoOverrides) -> Selection:
    """Apply all three selection layers to one repo."""
    sel = Selection()
    try:
        entries = git_ignored_entries(repo_dir)
    except OSError as exc:
        sel.errors.append(str(exc))
        return sel

    candidates: List[Tuple[Path, str, int]] = []
    for entry in entries:
        rel = entry.replace("\\", "/")
        if _denied(rel, cfg, overrides) or _matches_any(rel, overrides.exclude):
            continue
        target = repo_dir / rel
        if is_reparse_point(target):
            continue
        if rel.endswith("/"):
            if not target.is_dir():
                continue
            found = walk_files(target, rel, cfg, sel.errors, sel.vanished, overrides)
        else:
            if not target.is_file():
                continue
            try:
                found = iter([(target, rel, target.stat().st_size)])
            except FileNotFoundError:
                sel.vanished.append(rel)
                continue
            except OSError as exc:
                sel.errors.append(f"stat {target}: {exc}")
                continue
        for abs_path, rel_path, size in found:
            if _matches_any(rel_path, overrides.exclude):
                continue
            if size > cfg.max_file_bytes and not _matches_any(rel_path, overrides.always_include):
                sel.oversize.append({
                    "path": rel_path,
                    "bytes": size,
                    "mb": round(size / 1024 / 1024, 2),
                    "reason": "size-cap",
                })
                continue
            candidates.append((abs_path, rel_path, size))

    sel.files, sel.bulk_excluded = _apply_bulk_guard(
        _dedupe_by_rel(candidates), cfg, overrides)
    return sel


def select_transcripts(cfg: BackupConfig) -> Selection:
    """The transcripts leg: the whole `~/.claude/projects/` tree, deny-list applied.

    No bulk-directory guard here — this tree *is* the payload, and it is the leg
    that actually recovered the life-os content on 2026-08-10.
    """
    sel = Selection()
    if not cfg.transcripts_src.is_dir():
        sel.errors.append(f"transcripts source missing: {cfg.transcripts_src}")
        return sel
    for abs_path, rel, size in walk_files(cfg.transcripts_src, "", cfg, sel.errors,
                                          sel.vanished):
        if size > cfg.max_file_bytes:
            sel.oversize.append({
                "path": rel, "bytes": size, "mb": round(size / 1024 / 1024, 2),
                "reason": "size-cap",
            })
            continue
        sel.files.append((abs_path, rel, size))
    sel.files = _dedupe_by_rel(sel.files)
    return sel


def select_runtime_data(cfg: BackupConfig, staging: Path) -> Selection:
    """The runtime-data leg: `C:/sqlite`, with every database snapshotted.

    Modelled on `select_transcripts` — no bulk-directory guard, because this
    tree *is* the payload — with one deliberate divergence:

    **The `max_file_mb` cap is NOT applied here. Do not add it.** The transcripts
    leg drops an oversize file and reports it; on this leg that would silently
    drop exactly what the leg exists for — home-automation's `telemetry.sqlite3`
    is already 18 MB against a 10 MB cap, and a service database only ever grows.
    Losing it to a size threshold is the same defect fleet-config#722 fixed on
    the repos leg, and re-introducing it here would just move that bug.

    A database that cannot be snapshotted lands in `errors`, which makes its
    group `failed`, prints in the run report, and exits the process non-zero —
    never a quiet omission folded into a passing run.
    """
    sel = Selection()
    root = cfg.runtime_data_src
    if not root.is_dir():
        # Not an error: the fleet migration to <root>/<app>/<file> is in flight,
        # so the root may legitimately not exist yet. `run()` skips the leg
        # rather than writing an empty snapshot over a good one.
        return sel

    for abs_path, rel, size in walk_files(root, "", cfg, sel.errors, sel.vanished):
        if is_sqlite_sidecar(rel):
            continue
        if abs_path.suffix.lower() not in DB_SUFFIXES:
            # An ordinary file beside the databases (a config, a key, a README):
            # copied straight from source by the generic engine, uncapped.
            sel.files.append((abs_path, rel, size))
            continue
        staged = staging / rel
        try:
            mode = snapshot_sqlite(abs_path, staged)
            staged_size = staged.stat().st_size
        except (OSError, sqlite3.Error) as exc:
            sel.errors.append(f"sqlite-snapshot {rel}: {exc}")
            continue
        if mode != "read-only":
            logger.info(
                "   ℹ️ %s: read-only snapshot refused (a WAL database with no live "
                "reader); reopened read-write to recover its -shm", rel,
            )
        sel.files.append((staged, rel, staged_size))

    sel.files = _dedupe_by_rel(sel.files)
    return sel


def iter_repos(source_root: Path) -> Iterator[Path]:
    """Every real git repo directly under `source_root`, in name order.

    `.git` must be a **directory**: a linked worktree's `.git` is a *file*, and a
    sibling `<repo>-wt-<N>` tree would otherwise be snapshotted as a second repo
    (the same guard, and the same reasoning, as
    `skills/_lib/fleet_repo_scan.iter_fleet_repos`).
    """
    if not source_root.is_dir():
        return
    for entry in sorted(source_root.iterdir()):
        if entry.is_dir() and (entry / ".git").is_dir() and not is_reparse_point(entry):
            yield entry

