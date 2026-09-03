"""Backup engine configuration: exit/freshness/name constants, `BackupConfig`,
per-repo `RepoOverrides`, and their projects.toml loaders.

The lowest module in `hooks/backup/` (fleet-config#731) -- every sibling
module imports constants and/or `BackupConfig`/`RepoOverrides` from here, and
this module imports nothing from them. Split out of the former 1768-line
`hooks/backup_private.py` alongside `select.py`, `snapshot.py`, `retention.py`,
`report.py`, and the thin `cli.py` orchestrator -- the same shape `design_lint`
was split into (fleet-config#564).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _lib  # noqa: E402

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - older interpreters
    import tomli as tomllib  # type: ignore[no-redef]


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
RUN_MARKER_NAME = ".run-in-progress"
LATEST_DIR = "latest"
DATE_FMT = "%Y-%m-%d"

# The relocated-runtime-data leg (fleet-config#724). Its name is load-bearing in
# three places — the manifest's `leg`, `policy_summary`'s cap exemption, and the
# restore note's per-leg blurb — so it is a constant, not a repeated literal.
RUNTIME_DATA_LEG = "runtime-data"
RUNTIME_DATA_GROUP = "sqlite"

#: Extensions treated as a SQLite database: snapshotted through the online
#: backup API rather than copied byte-for-byte.
DB_SUFFIXES: Tuple[str, ...] = (".sqlite3", ".sqlite", ".db")
#: A database's companion files. Excluded from selection because the online
#: backup already folds their committed contents into the snapshot — and
#: because copying them out of step with the main file is what *creates* a
#: corrupt restore.
DB_SIDECAR_MARKERS: Tuple[str, ...] = ("-wal", "-shm", "-journal")
#: How long a snapshot waits on a writer's lock before giving up and reporting
#: the database as a failure. Long enough for an ordinary transaction, short
#: enough that one wedged service cannot stall the nightly run.
DB_LOCK_TIMEOUT_SECONDS = 30.0

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
    # The relocated runtime-data root (project-scaffolding#243) and its
    # destination. `runtime_data_dest` has to live on E: — the legs cross
    # volumes on purpose, and `_preflight` refuses a same-volume snapshot.
    runtime_data_src: Path
    runtime_data_dest: Path
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

    def policy_summary(self, leg: str = "repos") -> Dict[str, Any]:
        """The selection policy, recorded in every manifest.

        A snapshot that is smaller than yesterday's should be explainable by
        looking at the two manifests, without a git archaeology session over
        this file. Which is why the runtime-data leg reports its size cap as
        `null` plus a reason rather than echoing a number it does not apply:
        a manifest that claims a 10 MB cap over an 18 MB database it did in
        fact keep is a manifest nobody can reason from.
        """
        summary: Dict[str, Any] = {
            "max_file_mb": round(self.max_file_bytes / 1024 / 1024, 3),
            "bulk_dir_mb": round(self.bulk_dir_bytes / 1024 / 1024, 3),
            "keep_daily": self.keep_daily,
            "keep_weekly": self.keep_weekly,
            "deny_dirs": list(self.deny_dirs),
            "deny_globs": list(self.deny_globs),
        }
        if leg == RUNTIME_DATA_LEG:
            summary["max_file_mb"] = None
            summary["size_cap_exempt"] = (
                "runtime-data: the databases ARE the payload, so the global "
                "max_file_mb cap is deliberately not applied (fleet-config#724)"
            )
        return summary


@dataclass(frozen=True)
class RepoOverrides:
    """Per-repo `backup*` keys, read from that repo's own projects.toml table."""

    enabled: bool = True
    exclude: Tuple[str, ...] = ()
    include: Tuple[str, ...] = ()
    # Basename globs exempted from the global deny_dirs/deny_globs check, for
    # this repo only (fleet-config#722) — e.g. a *.log file that is actually
    # security-relevant automation state, not routine debug noise.
    include_globs: Tuple[str, ...] = ()
    # Relative paths (matched like `include`/`exclude`) exempted from the
    # global max_file_mb cap, for this repo only (fleet-config#722).
    always_include: Tuple[str, ...] = ()


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
        dest=_path("dest", "C:/Users/rober/backup/fleet-private"),
        transcripts_src=_path("transcripts_src", "~/.claude/projects"),
        transcripts_dest=_path("transcripts_dest", "E:/backup/claude-transcripts"),
        runtime_data_src=_path("runtime_data_src", "C:/sqlite"),
        runtime_data_dest=_path("runtime_data_dest", "E:/backup/fleet-runtime-data"),
        keep_daily=int(table.get("keep_daily", 14)),
        keep_weekly=int(table.get("keep_weekly", 8)),
        max_file_bytes=_mb("max_file_mb", 10 * 1024 * 1024),
        bulk_dir_bytes=_mb("bulk_dir_mb", 25 * 1024 * 1024),
        freshness_max_hours=int(table.get("freshness_max_hours", 48)),
        deny_dirs=tuple(table.get("deny_dirs", DEFAULT_DENY_DIRS)),
        deny_globs=tuple(table.get("deny_globs", DEFAULT_DENY_GLOBS)),
    )


def load_repo_overrides(repo_dir: Path, path: Optional[Path] = None) -> RepoOverrides:
    """Read `backup` / `backup_exclude` / `backup_include` / `backup_include_globs` /
    `backup_always_include` for one repo.

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
            include_globs=tuple(table.get("backup_include_globs", []) or []),
            always_include=tuple(table.get("backup_always_include", []) or []),
        )
    return RepoOverrides()

