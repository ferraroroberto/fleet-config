"""Read one repo's ratchet tables and baselines from `origin/<default>` and count them.

The read is `git ls-tree` + `git show` against the default ref, never the working
tree, so a dirty or off-branch checkout cannot change the answer. Three states stay
distinct and none folds into a zero: `adopted` (at least one ratchet table enabled),
`not_adopted` (no `.fleet.toml`, or no enabled ratchet table) and `unknown` (no
default ref, a git failure, an unparseable `.fleet.toml` or baseline).

An enabled ratchet whose baseline file is absent counts zero hits: the ratchet
itself reads a missing baseline as empty.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import fleet_toml  # noqa: E402
import git_run  # noqa: E402
from structure_debt.baseline import BASELINE_VERSION, Unknown, read_ref_file  # noqa: E402

# Each vendored ratchet's `.fleet.toml` table and the baseline-entry field that holds its hits.
# None: every entry is one hit (a duplicate block, an unused import, an oversized file).
RATCHETS: dict[str, Optional[str]] = {
    "static_ratchet": None,
    "comment_ratchet": "count",
    "docs_ratchet": "markers",
    "structure_ratchet": None,
    "instruction_ratchet": "hits",
    "doc_refs_ratchet": "refs",
    "dead_code_ratchet": "names",
    "conventions_ratchet": "counts",
}


def default_baseline(table: str) -> str:
    """The ratchet's own default baseline path: `static_ratchet` -> `.static-ratchet-baseline.json`."""
    return "." + table.replace("_", "-") + "-baseline.json"


def read_config(toml_text: Optional[str]) -> dict:
    """`{"ratchets": {table: baseline path}, "rules": [...]}` for the enabled ratchet tables.

    Raises `Unknown` when the text does not parse or a table is malformed.
    """
    if not (toml_text or "").strip():
        return {"ratchets": {}, "rules": []}
    data = fleet_toml.parse(toml_text)
    if data is None:
        raise Unknown(f"{fleet_toml.FILENAME} does not parse")
    ratchets: dict[str, str] = {}
    for table in RATCHETS:
        entry = data.get(table)
        if entry is None:
            continue
        if not isinstance(entry, dict):
            raise Unknown(f"{fleet_toml.FILENAME} [{table}] is not a table")
        if entry.get("enabled") is not True:
            continue
        path = entry.get("baseline", default_baseline(table))
        if not isinstance(path, str):
            raise Unknown(f"{fleet_toml.FILENAME} [{table}] baseline is not a string")
        ratchets[table] = path
    rules: list[str] = []
    if "conventions_ratchet" in ratchets:
        raw = data["conventions_ratchet"].get("rules", [])
        if not isinstance(raw, list) or not all(isinstance(r, str) for r in raw):
            raise Unknown(f"{fleet_toml.FILENAME} [conventions_ratchet] rules is not a list of names")
        rules = sorted(raw)
    return {"ratchets": ratchets, "rules": rules}


def _entry_hits(entry: dict, field: Optional[str]) -> int:
    if field is None:
        return 1
    value = entry.get(field)
    if isinstance(value, bool):
        raise Unknown(f"baseline entry {entry['key']} has a malformed {field}")
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict) and all(isinstance(v, int) and not isinstance(v, bool) for v in value.values()):
        return sum(value.values())
    raise Unknown(f"baseline entry {entry['key']} has no countable {field}")


def count_hits(text: str, field: Optional[str]) -> int:
    """The baselined hit count in a ratchet baseline's text. Raises `Unknown` when it is malformed."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise Unknown(f"baseline is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != BASELINE_VERSION:
        raise Unknown(f"baseline is not an object with version {BASELINE_VERSION}")
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise Unknown("baseline has no entries list")
    total = 0
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("key"), str):
            raise Unknown("baseline has an entry without a string key")
        total += _entry_hits(entry, field)
    return total


def read_repo(repo_path: str) -> dict:
    """`{"state": "adopted", "hits": {table: n}, "rules": [...]}`, `not_adopted` or `unknown` with a reason."""
    repo = Path(repo_path)
    ref = git_run.resolve_default_branch_ref(repo, candidates=("origin/main", "origin/master"), final_fallback="")
    if not ref:
        return {"state": "unknown", "reason": "no origin default branch ref"}
    try:
        config = read_config(read_ref_file(repo, ref, fleet_toml.FILENAME))
        if not config["ratchets"]:
            return {"state": "not_adopted"}
        hits = {}
        for table, path in config["ratchets"].items():
            text = read_ref_file(repo, ref, path)
            try:
                hits[table] = 0 if text is None else count_hits(text, RATCHETS[table])
            except Unknown as exc:
                raise Unknown(f"{path}: {exc}") from exc
        return {"state": "adopted", "hits": hits, "rules": config["rules"]}
    except Unknown as exc:
        return {"state": "unknown", "reason": str(exc)}
