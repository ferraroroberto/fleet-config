"""Read one repo's structure-ratchet baseline from its default branch and count it.

The read is `git ls-tree` + `git show` against `origin/<default>`, never the
working tree, so a dirty or off-branch checkout cannot change the answer. Three
states stay distinct and none folds into a zero count: `adopted` (counts),
`not_adopted` (the baseline file is not on the default branch) and `unknown` (a
fact that could not be established: no default ref, a git failure, an
unparseable `.fleet.toml` or baseline).
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import git_run  # noqa: E402

DEFAULT_BASELINE = ".structure-ratchet-baseline.json"
FLEET_TOML = ".fleet.toml"
BASELINE_VERSION = 1
# The ratchet's own defaults (scripts/structure_ratchet.py); a repo overrides them in `.fleet.toml`.
DEFAULT_MAX_FILE_LINES = 1000
DEFAULT_MAX_FUNCTION_LINES = 100
KIND_FIELDS = {"size": "files", "fn": "fns", "dir": "dirs"}


class Unknown(Exception):
    """A fact this read could not establish; the message is the reason."""


def read_ref_file(repo: Path, ref: str, path: str) -> str | None:
    """The file's text on `ref`, or None when the path is absent there. Raises `Unknown` on a git failure."""
    listing = git_run.run_git(["-C", str(repo), "ls-tree", "--name-only", ref, "--", path])
    if listing.returncode != 0:
        detail = (listing.stderr or "").strip().splitlines()
        raise Unknown(f"git ls-tree {ref} failed" + (f": {detail[0]}" if detail else ""))
    if not listing.stdout.strip():
        return None
    shown = git_run.run_git(["-C", str(repo), "show", f"{ref}:{path}"])
    if shown.returncode != 0:
        raise Unknown(f"git show {ref}:{path} failed")
    return shown.stdout


def _structure_config(repo: Path, ref: str) -> tuple[str, int, int]:
    """(baseline path, max file lines, max function lines) from the repo's `[structure_ratchet]` table."""
    text = read_ref_file(repo, ref, FLEET_TOML)
    if text is None:
        return DEFAULT_BASELINE, DEFAULT_MAX_FILE_LINES, DEFAULT_MAX_FUNCTION_LINES
    try:
        table = tomllib.loads(text).get("structure_ratchet", {})
    except tomllib.TOMLDecodeError as exc:
        raise Unknown(f"{FLEET_TOML} does not parse: {exc}") from exc
    path = table.get("baseline", DEFAULT_BASELINE)
    max_file = table.get("max_file_lines", DEFAULT_MAX_FILE_LINES)
    max_fn = table.get("max_function_lines", DEFAULT_MAX_FUNCTION_LINES)
    if not (isinstance(path, str) and isinstance(max_file, int) and isinstance(max_fn, int)):
        raise Unknown(f"{FLEET_TOML} [structure_ratchet] has a malformed baseline or limit")
    return path, max_file, max_fn


def count_entries(text: str, max_file: int, max_fn: int) -> dict:
    """`{files, fns, dirs, excess}` for a baseline's text. Raises `Unknown` when it is malformed.

    `excess` is the sum of lines over the limit across the `size:` and `fn:` entries, the
    real measure of debt; a `layer:`/`unmeasured:` entry is not part of any of the three counts.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise Unknown(f"baseline is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != BASELINE_VERSION:
        raise Unknown(f"baseline is not an object with version {BASELINE_VERSION}")
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise Unknown("baseline has no entries list")
    counts = {"files": 0, "fns": 0, "dirs": 0}
    excess = 0
    limits = {"size": max_file, "fn": max_fn}
    for entry in entries:
        key = entry.get("key") if isinstance(entry, dict) else None
        if not isinstance(key, str) or ":" not in key:
            raise Unknown("baseline has an entry without a string key")
        kind = key.split(":", 1)[0]
        if kind not in KIND_FIELDS:
            continue
        counts[KIND_FIELDS[kind]] += 1
        if kind in limits:
            size = entry.get("lines")
            if not isinstance(size, int):
                raise Unknown(f"baseline entry {key} has no integer lines")
            excess += max(0, size - limits[kind])
    return {**counts, "excess": excess}


def read_repo(repo_path: str) -> dict:
    """`{"state": "adopted"|"not_adopted"|"unknown", ...}` for one repo, from its default branch."""
    repo = Path(repo_path)
    ref = git_run.resolve_default_branch_ref(repo, candidates=("origin/main", "origin/master"), final_fallback="")
    if not ref:
        return {"state": "unknown", "reason": "no origin default branch ref"}
    try:
        path, max_file, max_fn = _structure_config(repo, ref)
        text = read_ref_file(repo, ref, path)
        if text is None:
            return {"state": "not_adopted"}
        return {"state": "adopted", **count_entries(text, max_file, max_fn)}
    except Unknown as exc:
        return {"state": "unknown", "reason": str(exc)}
