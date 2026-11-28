"""Read one repo's structure-ratchet baseline from its default branch and count it.

The read is `git ls-tree` + `git show` against `origin/<default>`, never the
working tree, so a dirty or off-branch checkout cannot change the answer. A
baseline lives at `.ratchets/<name>.json`; a ref from before that folder (or a repo
not yet re-vendored) keeps it at the root as `.<name>-ratchet-baseline.json`, which
`read_baseline` falls back to. Three
states stay distinct and none folds into a zero count: `adopted` (counts),
`not_adopted` (the baseline file is not on the default branch) and `unknown` (a
fact that could not be established: no default ref, a git failure, an
unparseable `.fleet.toml` or baseline).
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path, PurePosixPath
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import git_run  # noqa: E402

BASELINE_FOLDER = ".ratchets"
DEFAULT_BASELINE = f"{BASELINE_FOLDER}/structure.json"
FLEET_TOML = ".fleet.toml"
BASELINE_VERSION = 1
# The ratchet's own defaults (scripts/structure_ratchet.py); a repo overrides them in `.fleet.toml`.
DEFAULT_MAX_FILE_LINES = 1000
DEFAULT_MAX_FUNCTION_LINES = 100
KIND_FIELDS = {"size": "files", "fn": "fns", "dir": "dirs"}


class Unknown(Exception):
    """A fact this read could not establish; the message is the reason."""


def read_first_ref_file(repo: Path, ref: str, paths: list[str]) -> str | None:
    """The text of the first of `paths` present on `ref`, or None when none is. Raises `Unknown` on a git failure.

    One `git ls-tree` lists every candidate, so a missing file costs the same single spawn as before.
    """
    listing = git_run.run_git(["-C", str(repo), "ls-tree", "--name-only", ref, "--", *paths])
    if listing.returncode != 0:
        detail = (listing.stderr or "").strip().splitlines()
        raise Unknown(f"git ls-tree {ref} failed" + (f": {detail[0]}" if detail else ""))
    present = {line.strip() for line in listing.stdout.splitlines()}
    path = next((p for p in paths if p in present), None)
    if path is None:
        return None
    shown = git_run.run_git(["-C", str(repo), "show", f"{ref}:{path}"])
    if shown.returncode != 0:
        raise Unknown(f"git show {ref}:{path} failed")
    return shown.stdout


def read_ref_file(repo: Path, ref: str, path: str) -> str | None:
    """The file's text on `ref`, or None when the path is absent there. Raises `Unknown` on a git failure."""
    return read_first_ref_file(repo, ref, [path])


def default_baseline(name: str) -> str:
    """The ratchet's baseline path, from its file stem or `.fleet.toml` table name:
    `dead-code` or `dead_code_ratchet` -> `.ratchets/dead-code.json`."""
    return f"{BASELINE_FOLDER}/{name.removesuffix('_ratchet').replace('_', '-')}.json"


def legacy_baseline(default: str) -> str:
    """The root path a ratchet used before `.ratchets/`: `.ratchets/dead-code.json` -> `.dead-code-ratchet-baseline.json`."""
    return f".{PurePosixPath(default).stem}-ratchet-baseline.json"


def configured_baseline(value: object, default: str) -> object:
    """A `.fleet.toml` `baseline` value as the ratchet reads it: the old root default means the new default."""
    return default if value == legacy_baseline(default) else value


def read_baseline(repo: Path, ref: str, path: str, default: str) -> Optional[str]:
    """The baseline's text on `ref`, or None when absent. A default path falls back to the old root name.

    An explicitly configured path is read as written, like the ratchet itself does, so a repo that
    chose its own location is never answered from a different file.
    """
    return read_first_ref_file(repo, ref, [path, legacy_baseline(default)] if path == default else [path])


def _structure_config(repo: Path, ref: str) -> tuple[str, int, int]:
    """(baseline path, max file lines, max function lines) from the repo's `[structure_ratchet]` table."""
    text = read_ref_file(repo, ref, FLEET_TOML)
    if text is None:
        return DEFAULT_BASELINE, DEFAULT_MAX_FILE_LINES, DEFAULT_MAX_FUNCTION_LINES
    try:
        table = tomllib.loads(text).get("structure_ratchet", {})
    except tomllib.TOMLDecodeError as exc:
        raise Unknown(f"{FLEET_TOML} does not parse: {exc}") from exc
    path = configured_baseline(table.get("baseline", DEFAULT_BASELINE), DEFAULT_BASELINE)
    max_file = table.get("max_file_lines", DEFAULT_MAX_FILE_LINES)
    max_fn = table.get("max_function_lines", DEFAULT_MAX_FUNCTION_LINES)
    if not (isinstance(path, str) and isinstance(max_file, int) and isinstance(max_fn, int)):
        raise Unknown(f"{FLEET_TOML} [structure_ratchet] has a malformed baseline or limit")
    return path, max_file, max_fn


def load_baseline_entries(text: str) -> list[dict]:
    """The entries of a ratchet baseline's text, each with a string `key`. Raises `Unknown` when it is malformed."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise Unknown(f"baseline is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != BASELINE_VERSION:
        raise Unknown(f"baseline is not an object with version {BASELINE_VERSION}")
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise Unknown("baseline has no entries list")
    if not all(isinstance(e, dict) and isinstance(e.get("key"), str) for e in entries):
        raise Unknown("baseline has an entry without a string key")
    return entries


def count_entries(text: str, max_file: int, max_fn: int) -> dict:
    """`{files, fns, dirs, excess}` for a baseline's text. Raises `Unknown` when it is malformed.

    `excess` is the sum of lines over the limit across the `size:` and `fn:` entries, the
    real measure of debt; a `layer:`/`unmeasured:` entry is not part of any of the three counts.
    """
    entries = load_baseline_entries(text)
    counts = {"files": 0, "fns": 0, "dirs": 0}
    excess = 0
    limits = {"size": max_file, "fn": max_fn}
    for entry in entries:
        key = entry["key"]
        if ":" not in key:
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
        text = read_baseline(repo, ref, path, DEFAULT_BASELINE)
        if text is None:
            return {"state": "not_adopted"}
        return {"state": "adopted", **count_entries(text, max_file, max_fn)}
    except Unknown as exc:
        return {"state": "unknown", "reason": str(exc)}
