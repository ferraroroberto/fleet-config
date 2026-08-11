"""Daily snapshot of every fleet repo's gitignored-but-precious residue.

fleet-config#590, filed after the 2026-08-10 incident (ferraroroberto/life-os#72)
permanently deleted life-os's gitignored personal content — `identity/`, per-skill
`context/`/`memory/`/`conversations/`, `.env` — which by design exists in exactly
one place. Recovery succeeded only by luck. Anything git *tracks* is already safe
on GitHub; anything git *ignores* lives on this machine and nowhere else. That
ignored set is what this backs up.

Not a hook — a plain scheduled program (`run-backup-daily.bat` → an app-launcher
Job → Task Scheduler). It lives in `hooks/` because that is this repo's junctioned
Python-tool tier: `projects.toml`, `_lib.NO_WINDOW`, and the Slack transport it
needs are all already here, and the acceptance matrix's spawn-flag scanner and the
README-Layout gate already cover this directory.

Selection, in three layers
--------------------------
1. **git decides.** `git ls-files --others --ignored --exclude-standard` per repo
   is the exact ignored set. Nothing is hand-listed, so a personal file created
   tomorrow is covered without touching this file.
2. **A deny-list + per-file size cap** drops the obvious rubbish (`.venv`,
   `node_modules`, caches, `*.log`, `*.pyc`, anything over `max_file_mb`).
3. **A bulk-directory guard** drops what layers 1-2 cannot: the measurement behind
   #590 found layers 1-2 alone still select **11.8 GB across 108k files**, ~200x
   the genuinely irreplaceable set, because gitignore is also where every repo
   parks its generated output (content-management `planning/` 2.8 GB,
   voice-transcriber `archive/` 2.5 GB, app-launcher `webapp/` 2.3 GB). So any
   **top-level** directory in a repo whose surviving subtree exceeds
   `bulk_dir_mb` is excluded whole, **named with its size in the run report and
   the manifest**, and re-admitted permanently with a per-repo `backup_include`.
   Top-level only, and a single size threshold, on purpose: an operator has to be
   able to predict what this keeps without simulating an algorithm.

   A file-*count* threshold was tried alongside the size one and removed: measured
   over the real fleet, every directory it caught that size did not was ≤1.2 MB,
   and one of them was `whatsapp-radar/auth/` — 1,353 tiny files holding the
   WhatsApp session keys. "Small but numerous" is the shape of precious data here,
   not of bulk.

The guard is the one place this deviates from "no allow-list to go stale" — a
`backup_include` list is a small allow-list. It fails toward backing up *less*,
which is why every exclusion is reported rather than silent, and why a repo that
had files yesterday and has none today is a hard failure (`check_zero_file_regressions`).

Storage
-------
Dated snapshots `<dest>/<YYYY-MM-DD>/`, plus a `latest/` mirror. A file whose
content is unchanged since the previous snapshot is **hardlinked** to it rather
than copied (the `rsync --link-dest` shape), so every dated directory reads as a
complete plain-file tree in Explorer while 14 dailies cost about one copy plus
deltas. Plain files are deliberate: the #590 restore has to need zero tooling,
because the incident restore was done by hand from plain sources.

Two legs run each night, in opposite directions, so each volume holds the other's
crown jewels: repo residue E: -> C:, and Claude Code's session transcripts
(`~/.claude/projects/`, the actual recovery goldmine, and prunable by Claude Code
itself) C: -> E:.

Honesty rules
-------------
Every run re-hashes a random sample of what it just wrote against its own
manifest; a repo that lost all its files since the last manifest fails the run;
`--check-freshness` reports `ok` / `stale` / `unknown` as three distinct states
and never folds "couldn't tell" into "fine". Failures are aggregated, not fatal
on first error — every remaining repo is still attempted — and the process exits
non-zero, which is what the app-launcher Job's `alert_on_failure` keys off.

stdlib only. Never follows a junction or symlink (see `is_reparse_point` — on
Windows `Path.is_symlink()` returns **False** for a junction, so a symlink-based
guard walks straight into every worktree's `.venv`).
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import logging
import os
import random
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _lib  # noqa: E402
import slack_notify  # noqa: E402

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - older interpreters
    import tomli as tomllib  # type: ignore[no-redef]

logger = logging.getLogger("backup_private")

# ---------------------------------------------------------------------------
# Exit codes. Distinct conditions get distinct codes (global CLAUDE.md: "Distinct
# error messages for distinct conditions"), and the run exits with the most
# severe one it hit rather than the first.
# ---------------------------------------------------------------------------
EXIT_OK = 0
EXIT_REPO_FAILURE = 1          # one or more repos hit IO errors; the rest still ran
EXIT_VERIFY_FAILED = 2         # the written snapshot does not match its own manifest
EXIT_DEST_UNUSABLE = 3         # destination missing, unwritable, or on the source volume
EXIT_ZERO_FILES_REGRESSION = 4 # a repo that had files last run backed up none this run

_SEVERITY = {
    EXIT_DEST_UNUSABLE: 40,
    EXIT_VERIFY_FAILED: 30,
    EXIT_ZERO_FILES_REGRESSION: 20,
    EXIT_REPO_FAILURE: 10,
    EXIT_OK: 0,
}

# `--check-freshness` reports three states, never two (global CLAUDE.md: a check
# that cannot establish a fact must say so rather than pass).
FRESHNESS_OK = "ok"
FRESHNESS_STALE = "stale"
FRESHNESS_UNKNOWN = "unknown"
_FRESHNESS_EXIT = {FRESHNESS_OK: 0, FRESHNESS_STALE: 1, FRESHNESS_UNKNOWN: 2}

MANIFEST_NAME = "manifest.json"
LATEST_DIR = "latest"
DATE_FMT = "%Y-%m-%d"

# Defaults for every `[backup]` key. projects.toml overrides any of them; these
# exist so a fresh clone (or a test's throwaway TOML) is runnable with no config.
DEFAULT_DENY_DIRS: Tuple[str, ...] = (
    ".venv", "venv", "env", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".launcher-tmp", "site-packages", ".tox",
    "dist", "build", ".next", ".turbo", ".cache", ".parcel-cache", ".gradle",
    "target", "htmlcov", ".coverage", "playwright-report", "test-results",
    ".playwright", ".idea", ".vs", "logs", ".git",
)
DEFAULT_DENY_GLOBS: Tuple[str, ...] = (
    "*.log", "*.pyc", "*.pyo", "*.pyd", "*.dll", "*.exe", "*.so", "*.dylib",
    "*.zip", "*.7z", "*.rar", "*.tar", "*.gz", "*.iso", "*.vhdx", "*.avhdx",
    "*.vmcx", "*.vmrs", "*.gguf", "*.bin", "*.safetensors", "*.pt", "*.onnx",
    "*.mp4", "*.mov", "*.avi", "*.mkv", "*.wav", "*.mp3", "*.m4a",
    "*.sqlite-wal", "*.sqlite-shm", "*.lock", "*.pid",
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BackupConfig:
    """The `[backup]` table of projects.toml, with defaults filled in.

    A dedicated table rather than `[global]` keys: `_lib.load_registry` and
    `skills/_lib/fleet_repo_scan.fleet_repos` both enumerate projects.toml by
    "table carrying a `cwd_prefix`", so a `[backup]` table is invisible to them
    and adding one cannot perturb the fleet-membership list.
    """

    source_root: Path
    dest: Path
    transcripts_src: Path
    transcripts_dest: Path
    keep_daily: int = 14
    keep_weekly: int = 8
    max_file_bytes: int = 10 * 1024 * 1024
    bulk_dir_bytes: int = 25 * 1024 * 1024
    freshness_max_hours: int = 48
    deny_dirs: Tuple[str, ...] = DEFAULT_DENY_DIRS
    deny_globs: Tuple[str, ...] = DEFAULT_DENY_GLOBS

    @property
    def _deny_dirs_lower(self) -> frozenset:
        return frozenset(name.lower() for name in self.deny_dirs)

    def policy_summary(self) -> Dict[str, Any]:
        """The selection policy, recorded in every manifest.

        A snapshot that is smaller than yesterday's should be explainable by
        looking at the two manifests, without a git archaeology session over
        this file.
        """
        return {
            "max_file_mb": round(self.max_file_bytes / 1024 / 1024, 3),
            "bulk_dir_mb": round(self.bulk_dir_bytes / 1024 / 1024, 3),
            "keep_daily": self.keep_daily,
            "keep_weekly": self.keep_weekly,
            "deny_dirs": list(self.deny_dirs),
            "deny_globs": list(self.deny_globs),
        }


@dataclass(frozen=True)
class RepoOverrides:
    """Per-repo `backup*` keys, read from that repo's own projects.toml table."""

    enabled: bool = True
    exclude: Tuple[str, ...] = ()
    include: Tuple[str, ...] = ()


def _projects_toml_path(explicit: Optional[Path] = None) -> Path:
    """Resolve projects.toml, honouring the same env override the hooks use."""
    if explicit is not None:
        return explicit
    return Path(os.environ.get(_lib.PROJECTS_TOML_ENV_VAR) or _lib.PROJECTS_TOML)


def _read_toml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def load_backup_config(path: Optional[Path] = None) -> BackupConfig:
    """Build a :class:`BackupConfig` from projects.toml's `[backup]` table."""
    table = _read_toml(_projects_toml_path(path)).get("backup", {})
    if not isinstance(table, dict):
        table = {}

    def _mb(key: str, default_bytes: int) -> int:
        raw = table.get(key)
        return int(float(raw) * 1024 * 1024) if raw is not None else default_bytes

    def _path(key: str, default: str) -> Path:
        return Path(os.path.expanduser(str(table.get(key) or default)))

    return BackupConfig(
        source_root=_path("source_root", "E:/automation"),
        dest=_path("dest", "C:/Users/rober/backups/fleet-private"),
        transcripts_src=_path("transcripts_src", "~/.claude/projects"),
        transcripts_dest=_path("transcripts_dest", "E:/backups/claude-transcripts"),
        keep_daily=int(table.get("keep_daily", 14)),
        keep_weekly=int(table.get("keep_weekly", 8)),
        max_file_bytes=_mb("max_file_mb", 10 * 1024 * 1024),
        bulk_dir_bytes=_mb("bulk_dir_mb", 25 * 1024 * 1024),
        freshness_max_hours=int(table.get("freshness_max_hours", 48)),
        deny_dirs=tuple(table.get("deny_dirs", DEFAULT_DENY_DIRS)),
        deny_globs=tuple(table.get("deny_globs", DEFAULT_DENY_GLOBS)),
    )


def load_repo_overrides(repo_dir: Path, path: Optional[Path] = None) -> RepoOverrides:
    """Read `backup` / `backup_exclude` / `backup_include` for one repo.

    Follows the `capture = true` precedent: per-project nuance lives in that
    project's own projects.toml table, never in this module. Matching is by
    `cwd_prefix`, so a repo with no table simply takes the defaults (backed up,
    no overrides) — a new fleet repo is covered the day it is cloned.
    """
    data = _read_toml(_projects_toml_path(path))
    target = str(repo_dir.resolve()).replace("\\", "/").rstrip("/").lower()
    for name, table in data.items():
        if name == "backup" or not isinstance(table, dict):
            continue
        prefix = table.get("cwd_prefix")
        if not isinstance(prefix, str):
            continue
        if str(Path(prefix)).replace("\\", "/").rstrip("/").lower() != target:
            continue
        return RepoOverrides(
            enabled=bool(table.get("backup", True)),
            exclude=tuple(table.get("backup_exclude", []) or []),
            include=tuple(table.get("backup_include", []) or []),
        )
    return RepoOverrides()


# ---------------------------------------------------------------------------
# Filesystem primitives
# ---------------------------------------------------------------------------
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


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _same_volume(a: Path, b: Path) -> bool:
    """True when two paths sit on the same volume.

    Drive-letter comparison on Windows, device id elsewhere. Used to enforce
    #590's hard constraint — a snapshot on the source volume dies with it.
    """
    if sys.platform == "win32":
        return os.path.splitdrive(str(a.resolve()))[0].lower() == \
            os.path.splitdrive(str(b.resolve()))[0].lower()
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------
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


def _denied(rel: str, cfg: BackupConfig) -> bool:
    """True if any path segment is a denied directory or the name matches a deny glob."""
    parts = rel.rstrip("/").split("/")
    # Case-insensitively, because NTFS is: a `.VENV` created by some other tool
    # is the same directory the deny-list means to exclude.
    if any(part.lower() in cfg._deny_dirs_lower for part in parts):
        return True
    return any(fnmatch.fnmatch(parts[-1].lower(), glob) for glob in cfg.deny_globs)


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
               vanished: Optional[List[str]] = None) -> Iterator[Tuple[Path, str, int]]:
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
            if _denied(rel, cfg):
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
    """
    by_top: Dict[str, List[Tuple[Path, str, int]]] = {}
    root_files: List[Tuple[Path, str, int]] = []
    for item in candidates:
        rel = item[1]
        if "/" in rel:
            by_top.setdefault(rel.split("/", 1)[0], []).append(item)
        else:
            root_files.append(item)

    kept = list(root_files)
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
    """
    result = subprocess.run(
        ["git", "-c", "core.quotePath=false", "-C", str(repo_dir), "ls-files",
         "--others", "--ignored", "--exclude-standard", "--directory", "-z"],
        capture_output=True, encoding="utf-8", errors="replace",
        creationflags=_lib.NO_WINDOW,
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
        if _denied(rel, cfg) or _matches_any(rel, overrides.exclude):
            continue
        target = repo_dir / rel
        if is_reparse_point(target):
            continue
        if rel.endswith("/"):
            if not target.is_dir():
                continue
            found = walk_files(target, rel, cfg, sel.errors, sel.vanished)
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
            if size > cfg.max_file_bytes:
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


# ---------------------------------------------------------------------------
# Snapshot writing
# ---------------------------------------------------------------------------
def _previous_snapshot(dest_root: Path, today: str) -> Tuple[Optional[Path], Dict[str, Any]]:
    """The newest dated snapshot before `today` that has a readable manifest."""
    for snapshot in sorted(dated_snapshots(dest_root), key=lambda p: p.name, reverse=True):
        if snapshot.name >= today:
            continue
        manifest = read_manifest(snapshot)
        if manifest:
            return snapshot, manifest
    return None, {}


def _index_manifest(manifest: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """`{"<group>/<relpath>": entry}` for O(1) previous-snapshot lookups."""
    index: Dict[str, Dict[str, Any]] = {}
    for group, payload in (manifest.get("groups") or {}).items():
        for entry in payload.get("files", []):
            index[f"{group}/{entry['path']}"] = entry
    return index


def write_group(items: Sequence[Tuple[Path, str, int]], group: str, snapshot_dir: Path,
                prev_dir: Optional[Path], prev_index: Dict[str, Dict[str, Any]],
                errors: List[str],
                vanished: Optional[List[str]] = None) -> Tuple[List[Dict[str, Any]], int, int]:
    """Write one group's files into `snapshot_dir`, hardlinking what has not changed.

    Returns `(entries, linked, copied)`. Every source file is hashed on the way
    through — the cheap `(size, mtime)` shortcut rsync uses is deliberately not
    taken, because the manifest's sha256 is what the sample verification and every
    future restore are checked against, and a hash inherited from yesterday's
    manifest would only ever prove yesterday's read.

    A source file that **vanishes between enumeration and read** is recorded in
    `vanished`, not in `errors`. The fleet's apps write and rotate their own
    gitignored artifacts while this runs (whatsapp-radar dropped a `runs/` entry
    mid-snapshot on the very first two-day test), so treating that as a failure
    would turn the nightly alert red most nights — and an alert that fires most
    nights is one nobody reads, which costs more than the file did. A real IO
    error (permission, disk) still fails the run.
    """
    vanished = vanished if vanished is not None else []
    entries: List[Dict[str, Any]] = []
    linked = copied = 0
    for abs_path, rel, size in items:
        target = snapshot_dir / group / rel
        try:
            digest = sha256_file(abs_path)
            mtime = abs_path.stat().st_mtime
            target.parent.mkdir(parents=True, exist_ok=True)
            # Idempotent per target: a re-run on the same date, or a retry after a
            # partial run, must overwrite rather than fail. `os.link` refuses an
            # existing target where `copy2` would silently overwrite it, so the
            # two paths below are only equivalent once this is cleared.
            if target.exists():
                target.unlink()
            previous = prev_index.get(f"{group}/{rel}")
            prev_file = (prev_dir / group / rel) if (prev_dir and previous) else None
            if previous and previous.get("sha256") == digest and prev_file and prev_file.is_file():
                os.link(prev_file, target)
                linked += 1
            else:
                shutil.copy2(abs_path, target)
                copied += 1
        except FileNotFoundError:
            vanished.append(rel)
            continue
        except OSError as exc:
            errors.append(f"copy {abs_path}: {exc}")
            continue
        entries.append({"path": rel, "sha256": digest, "size": size, "mtime": mtime})
    return entries, linked, copied


def verify_sample(snapshot_dir: Path, manifest: Dict[str, Any],
                  rng: Optional[random.Random] = None) -> Dict[str, Any]:
    """Re-hash a random sample of what was just written, against the manifest.

    A backup nobody has ever read back is a hope, not a backup. 5% of the run,
    floor 20 files, capped at 500 so a huge run stays inside its window.
    """
    rng = rng or random.Random()
    population = [
        (group, entry)
        for group, payload in (manifest.get("groups") or {}).items()
        for entry in payload.get("files", [])
    ]
    if not population:
        return {"status": "empty", "sampled": 0, "mismatches": []}

    size = min(len(population), max(20, min(500, len(population) // 20)))
    mismatches: List[Dict[str, Any]] = []
    for group, entry in rng.sample(population, size):
        path = snapshot_dir / group / entry["path"]
        try:
            actual = sha256_file(path)
        except OSError as exc:
            mismatches.append({"path": f"{group}/{entry['path']}", "error": str(exc)})
            continue
        if actual != entry["sha256"]:
            mismatches.append({
                "path": f"{group}/{entry['path']}",
                "expected": entry["sha256"], "actual": actual,
            })
    return {
        "status": "pass" if not mismatches else "fail",
        "sampled": size,
        "mismatches": mismatches,
    }


def check_zero_file_regressions(manifest: Dict[str, Any],
                                prev_manifest: Dict[str, Any]) -> List[str]:
    """Groups that had files in the previous manifest and have none now.

    The failure mode this exists for is silent: a `.gitignore` edit, a moved
    directory, or a mistyped `backup_exclude` can stop backing a repo up while
    every other repo keeps succeeding and the run stays green. A repo that was
    deliberately opted out is absent from `groups` entirely, not present-and-empty,
    so it does not trip this.
    """
    previous = {
        group: len(payload.get("files", []))
        for group, payload in (prev_manifest.get("groups") or {}).items()
    }
    regressed = []
    for group, payload in (manifest.get("groups") or {}).items():
        if previous.get(group, 0) > 0 and not payload.get("files"):
            regressed.append(group)
    return sorted(regressed)


# ---------------------------------------------------------------------------
# Snapshot lifecycle: latest mirror, retention, freshness
# ---------------------------------------------------------------------------
def dated_snapshots(dest_root: Path) -> List[Path]:
    """Every `YYYY-MM-DD` directory under `dest_root`, oldest first."""
    if not dest_root.is_dir():
        return []
    out = []
    for entry in dest_root.iterdir():
        if not entry.is_dir() or entry.name == LATEST_DIR:
            continue
        try:
            datetime.strptime(entry.name, DATE_FMT)
        except ValueError:
            continue
        out.append(entry)
    return sorted(out, key=lambda p: p.name)


def read_manifest(snapshot_dir: Path) -> Dict[str, Any]:
    path = snapshot_dir / MANIFEST_NAME
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


RESTORE_NOTE_NAME = "HOW-TO-RESTORE.txt"

_RESTORE_NOTE = """\
Fleet private backup — {leg}
============================================================
Written by hooks/backup_private.py in E:/automation/fleet-config
(fleet-config#590), daily at 03:00 via an app-launcher Job.

WHAT THIS IS
  A copy of the files git deliberately ignores in {source} — the ones
  that exist on this machine and nowhere else. Anything git tracks is
  already safe on GitHub and is NOT here.

HOW TO RESTORE
  Copy the files back. There is nothing to install, unpack or decrypt —
  every folder below is an ordinary tree you can browse in Explorer.

    latest\\        the newest copy (start here)
    YYYY-MM-DD\\    that day's copy, kept 14 days + one per week for 8 weeks

  Example, restoring one project's private files:
    Copy-Item -Recurse "{example_src}" "{example_dst}"

  Files identical to the previous day are hardlinked, not duplicated:
  each dated folder is still a COMPLETE tree, it just costs no extra disk.
  Deleting an old dated folder never damages another one.

WHAT WAS LEFT OUT, AND WHY
  manifest.json in each dated folder lists every file with its sha256 and
  size, plus every directory skipped for being generated output and every
  file skipped for exceeding the size cap. If something you expected is
  missing, that file says why.

  Written {stamp}.
"""


def write_restore_note(dest_root: Path, leg: str, source: Path) -> None:
    """Drop a plain-text restore note at the destination root.

    The whole premise is that a restore needs zero tooling — but a folder full
    of repo names explains neither what wrote it nor how to use it, and the
    README that does explain it lives on the volume this exists to survive the
    loss of. So the instructions ship next to the data.
    """
    if leg == "transcripts":
        example_src = str(dest_root / LATEST_DIR / "projects" / "*")
        example_dst = str(source)
    else:
        example_src = str(dest_root / LATEST_DIR / "life-os" / "*")
        example_dst = str(source / "life-os")
    try:
        (dest_root / RESTORE_NOTE_NAME).write_text(
            _RESTORE_NOTE.format(
                leg=leg, source=source, example_src=example_src, example_dst=example_dst,
                stamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
            ),
            encoding="utf-8",
        )
    except OSError:  # a note we cannot write must never fail the backup itself
        logger.warning("⚠️ could not write %s to %s", RESTORE_NOTE_NAME, dest_root)


def rebuild_latest(dest_root: Path, snapshot_dir: Path) -> int:
    """Rebuild `latest/` as a hardlink mirror of `snapshot_dir`.

    A mirror rather than a junction to the newest dated directory: hardlinks keep
    `latest/` valid after retention prunes the snapshot it was built from, and cost
    no bytes. Built beside the live one and swapped in, so an interrupted run
    cannot leave a half-built `latest/`.
    """
    latest = dest_root / LATEST_DIR
    staging = dest_root / (LATEST_DIR + ".new")
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)

    count = 0
    for source in snapshot_dir.rglob("*"):
        if source.is_dir():
            continue
        target = staging / source.relative_to(snapshot_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source, target)
        except OSError:
            shutil.copy2(source, target)
        count += 1

    if latest.exists():
        shutil.rmtree(latest, ignore_errors=True)
    staging.rename(latest)
    return count


def plan_retention(snapshot_names: Sequence[str], keep_daily: int,
                   keep_weekly: int) -> List[str]:
    """Names to delete: keep the newest `keep_daily`, then one per ISO week.

    Weeklies are chosen from what the daily window does not already cover, newest
    snapshot per ISO week, for the most recent `keep_weekly` weeks.
    """
    ordered = sorted(snapshot_names, reverse=True)
    keep = set(ordered[:keep_daily])

    weekly: Dict[Tuple[int, int], str] = {}
    for name in ordered[keep_daily:]:
        try:
            parsed = datetime.strptime(name, DATE_FMT)
        except ValueError:
            continue
        iso = parsed.isocalendar()
        weekly.setdefault((iso[0], iso[1]), name)

    for _, name in sorted(weekly.items(), reverse=True)[:keep_weekly]:
        keep.add(name)
    return sorted(name for name in ordered if name not in keep)


def prune(dest_root: Path, cfg: BackupConfig) -> List[str]:
    names = [p.name for p in dated_snapshots(dest_root)]
    removed = []
    for name in plan_retention(names, cfg.keep_daily, cfg.keep_weekly):
        shutil.rmtree(dest_root / name, ignore_errors=True)
        removed.append(name)
    return removed


def freshness(dest_root: Path, cfg: BackupConfig,
              now: Optional[datetime] = None) -> Tuple[str, Dict[str, Any]]:
    """Three-state freshness of `dest_root`'s newest successful snapshot.

    `unknown` is a real answer, not a soft pass: no destination, no snapshot, an
    unreadable manifest, or a manifest with no finish time all mean this check
    could not establish the fact — which the caller must be able to tell apart
    from "checked, and it is fine".
    """
    now = now or datetime.now(timezone.utc)
    snapshots = dated_snapshots(dest_root)
    if not snapshots:
        return FRESHNESS_UNKNOWN, {"reason": f"no snapshots under {dest_root}"}

    for snapshot in reversed(snapshots):
        manifest = read_manifest(snapshot)
        if not manifest:
            continue
        finished = manifest.get("finished_at")
        if not finished:
            continue
        try:
            stamp = datetime.fromisoformat(finished)
        except ValueError:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        age_hours = (now - stamp).total_seconds() / 3600
        detail = {
            "snapshot": snapshot.name,
            "age_hours": round(age_hours, 1),
            "status": manifest.get("status"),
            "files": (manifest.get("totals") or {}).get("files"),
        }
        if manifest.get("status") != "ok":
            return FRESHNESS_STALE, {**detail, "reason": "last run did not report ok"}
        if age_hours > cfg.freshness_max_hours:
            return FRESHNESS_STALE, {**detail, "reason": f"older than {cfg.freshness_max_hours}h"}
        return FRESHNESS_OK, detail

    return FRESHNESS_UNKNOWN, {"reason": "no snapshot carries a readable manifest"}


# ---------------------------------------------------------------------------
# Legs
# ---------------------------------------------------------------------------
def _preflight(source: Path, dest: Path) -> Optional[str]:
    """Refuse to write a snapshot that shares a volume with its source."""
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"destination {dest} is not writable: {exc}"
    if _same_volume(source, dest):
        return (f"destination {dest} is on the same volume as {source} — "
                "a same-volume snapshot dies with the volume")
    return None


def run_leg(leg: str, groups: Dict[str, Selection], source: Path, dest_root: Path,
            cfg: BackupConfig, today: str, dry_run: bool) -> Dict[str, Any]:
    """Snapshot one leg's already-selected groups and return its manifest."""
    started = datetime.now(timezone.utc)
    manifest: Dict[str, Any] = {
        "leg": leg,
        "date": today,
        "started_at": started.isoformat(),
        "finished_at": None,
        "source": str(source),
        "dest": str(dest_root),
        "policy": cfg.policy_summary(),
        "groups": {},
        "totals": {},
        "verification": {},
        "status": "unknown",
    }

    snapshot_dir = dest_root / today
    prev_dir, prev_manifest = (None, {}) if dry_run else _previous_snapshot(dest_root, today)
    prev_index = _index_manifest(prev_manifest)

    linked = copied = 0
    for name, selection in sorted(groups.items()):
        errors = list(selection.errors)
        vanished = list(selection.vanished)
        if dry_run:
            entries = [
                {"path": rel, "sha256": None, "size": size, "mtime": None}
                for _, rel, size in selection.files
            ]
        else:
            entries, group_linked, group_copied = write_group(
                selection.files, name, snapshot_dir, prev_dir, prev_index, errors, vanished,
            )
            linked += group_linked
            copied += group_copied
        manifest["groups"][name] = {
            "status": "failed" if errors else "ok",
            "files": entries,
            "bytes": sum(entry["size"] for entry in entries),
            "bulk_excluded": selection.bulk_excluded,
            "oversize": selection.oversize,
            "errors": errors,
            "vanished": vanished,
        }

    total_files = sum(len(payload["files"]) for payload in manifest["groups"].values())
    manifest["totals"] = {
        "files": total_files,
        "bytes": sum(payload["bytes"] for payload in manifest["groups"].values()),
        "linked": linked,
        "copied": copied,
        "groups": len(manifest["groups"]),
        "bulk_excluded_dirs": sum(
            len(payload["bulk_excluded"]) for payload in manifest["groups"].values()
        ),
        "vanished": sum(len(payload["vanished"]) for payload in manifest["groups"].values()),
    }

    if dry_run:
        manifest["status"] = "dry-run"
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        return manifest

    manifest["verification"] = verify_sample(snapshot_dir, manifest)
    manifest["regressions"] = check_zero_file_regressions(manifest, prev_manifest)
    failed_groups = [
        name for name, payload in manifest["groups"].items() if payload["status"] == "failed"
    ]
    manifest["status"] = "ok" if (
        manifest["verification"]["status"] in {"pass", "empty"}
        and not manifest["regressions"]
        and not failed_groups
    ) else "failed"
    manifest["finished_at"] = datetime.now(timezone.utc).isoformat()

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    manifest["latest_files"] = rebuild_latest(dest_root, snapshot_dir)
    manifest["pruned"] = prune(dest_root, cfg)
    write_restore_note(dest_root, leg, source)
    return manifest


def collect_repos(cfg: BackupConfig, only: Optional[str],
                  toml_path: Optional[Path]) -> Tuple[Dict[str, Selection], List[str]]:
    groups: Dict[str, Selection] = {}
    skipped: List[str] = []
    for repo_dir in iter_repos(cfg.source_root):
        if only and repo_dir.name != only:
            continue
        overrides = load_repo_overrides(repo_dir, toml_path)
        if not overrides.enabled:
            skipped.append(repo_dir.name)
            continue
        selection = select_repo(repo_dir, cfg, overrides)
        if selection.files or selection.errors or selection.bulk_excluded:
            groups[repo_dir.name] = selection
    return groups, skipped


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _mb(value: int) -> str:
    return f"{value / 1024 / 1024:.1f} MB"


def report(manifest: Dict[str, Any]) -> None:
    totals = manifest["totals"]
    logger.info(
        "ℹ️ %s: %d files, %s (%d linked, %d copied) across %d groups",
        manifest["leg"], totals["files"], _mb(totals["bytes"]),
        totals["linked"], totals["copied"], totals["groups"],
    )
    for name, payload in sorted(manifest["groups"].items()):
        for excluded in payload["bulk_excluded"]:
            logger.info(
                "   ⚠️ %s/%s excluded by the bulk-dir guard (%.1f MB, %d files) "
                "— add backup_include to keep it",
                name, excluded["path"], excluded["mb"], excluded["files"],
            )
        if payload.get("vanished"):
            logger.info(
                "   ℹ️ %s: %d source file(s) vanished mid-run (a live app rotating "
                "its own artifacts) — not a failure",
                name, len(payload["vanished"]),
            )
        for error in payload["errors"]:
            logger.error("   ❌ %s: %s", name, error)
    verification = manifest.get("verification") or {}
    if verification:
        logger.info("   🔎 verification: %s (%d sampled)",
                    verification.get("status"), verification.get("sampled", 0))
    for group in manifest.get("regressions") or []:
        logger.error("   ❌ %s backed up 0 files but had files last run", group)
    if manifest.get("pruned"):
        logger.info("   🧹 pruned %d old snapshots: %s",
                    len(manifest["pruned"]), ", ".join(manifest["pruned"]))


def _slack_summary(manifests: Sequence[Dict[str, Any]], exit_code: int) -> str:
    """One ASCII-only line per leg (fleet-config#507: a Windows command line is
    not a UTF-8-safe channel, and this text also reaches Slack via argv)."""
    icon = "OK" if exit_code == EXIT_OK else "FAILED"
    parts = []
    for manifest in manifests:
        totals = manifest["totals"]
        parts.append(
            f"{manifest['leg']}: {totals['files']} files | "
            f"{totals['bytes'] / 1024 / 1024:.0f} MB | "
            f"{totals['bulk_excluded_dirs']} bulk dirs skipped"
        )
    # A leg that failed preflight never produced a manifest. Saying so beats a
    # ping that trails off after the dash and reads like a truncated message.
    detail = " ; ".join(parts) if parts else f"no leg completed (exit {exit_code})"
    return f"Fleet private backup {icon} - {detail}"


def _notify(manifests: Sequence[Dict[str, Any]], exit_code: int) -> None:
    category = "log" if exit_code == EXIT_OK else "attention"
    channel, user, _ = _lib.resolve_slack_target(Path.cwd(), category=category)
    if not channel:
        return
    prefix = "✅" if exit_code == EXIT_OK else "❌"
    slack_notify.notify(f"{prefix} {_slack_summary(manifests, exit_code)}", channel, user=user)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def _worst(codes: Sequence[int]) -> int:
    return max(codes, key=lambda code: _SEVERITY.get(code, 0), default=EXIT_OK)


def run(cfg: BackupConfig, *, dry_run: bool = False, only: Optional[str] = None,
        toml_path: Optional[Path] = None, notify: bool = True,
        today: Optional[str] = None) -> Tuple[int, List[Dict[str, Any]]]:
    """Run both legs. Returns `(exit_code, manifests)`."""
    today = today or datetime.now().strftime(DATE_FMT)
    codes: List[int] = []
    manifests: List[Dict[str, Any]] = []

    repo_groups, skipped = collect_repos(cfg, only, toml_path)
    if skipped:
        logger.info("ℹ️ opted out via projects.toml: %s", ", ".join(skipped))

    legs = [("repos", repo_groups, cfg.source_root, cfg.dest)]
    if not only:
        legs.append(("transcripts", {"projects": select_transcripts(cfg)},
                     cfg.transcripts_src, cfg.transcripts_dest))

    for leg, groups, source, dest_root in legs:
        if not dry_run:
            problem = _preflight(source, dest_root)
            if problem:
                logger.error("❌ %s leg: %s", leg, problem)
                codes.append(EXIT_DEST_UNUSABLE)
                continue
        manifest = run_leg(leg, groups, source, dest_root, cfg, today, dry_run)
        manifests.append(manifest)
        report(manifest)
        if dry_run:
            continue
        if manifest["verification"]["status"] == "fail":
            codes.append(EXIT_VERIFY_FAILED)
        if manifest.get("regressions"):
            codes.append(EXIT_ZERO_FILES_REGRESSION)
        if any(payload["status"] == "failed" for payload in manifest["groups"].values()):
            codes.append(EXIT_REPO_FAILURE)

    exit_code = _worst(codes)
    if notify and not dry_run:
        _notify(manifests, exit_code)
    return exit_code, manifests


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be snapshotted; write nothing.")
    parser.add_argument("--check-freshness", action="store_true",
                        help="Report BACKUP_FRESHNESS=ok|stale|unknown and exit.")
    parser.add_argument("--only", help="Limit the repo leg to one repo (debugging).")
    parser.add_argument("--config", type=Path, help="Path to projects.toml.")
    parser.add_argument("--no-slack", action="store_true", help="Suppress the Slack ping.")
    parser.add_argument("--json", action="store_true", help="Print the run summary as JSON.")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    cfg = load_backup_config(args.config)

    if args.check_freshness:
        worst = FRESHNESS_OK
        details = {}
        for leg, dest_root in (("repos", cfg.dest), ("transcripts", cfg.transcripts_dest)):
            state, detail = freshness(dest_root, cfg)
            details[leg] = {"state": state, **detail}
            if _FRESHNESS_EXIT[state] > _FRESHNESS_EXIT[worst]:
                worst = state
        print(f"BACKUP_FRESHNESS={worst}")
        for leg, detail in details.items():
            print(f"  {leg}: {json.dumps(detail, ensure_ascii=False)}")
        return _FRESHNESS_EXIT[worst]

    started = time.monotonic()
    exit_code, manifests = run(
        cfg, dry_run=args.dry_run, only=args.only, toml_path=args.config,
        notify=not args.no_slack,
    )
    if args.json:
        print(json.dumps(
            [{k: v for k, v in m.items() if k != "groups"} for m in manifests],
            indent=2, ensure_ascii=False,
        ))
    logger.info("%s in %.1fs (exit %d)",
                "✅ backup complete" if exit_code == EXIT_OK else "❌ backup FAILED",
                time.monotonic() - started, exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
